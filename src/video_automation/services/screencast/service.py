"""画面録画の自動ズーム: 録画の素材から、操作している所へ寄った映像を作って素材に登録する。

- 見せたい所: クリックの記録（任意。録画の画素の座標と秒）と、画面が変わった範囲（自動）
- カメラは camera.plan_shots / camera_at（ポーズからポーズへ、イーズで移る）
- クリックには波紋を重ねる。音声はそのまま残す
"""

from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

from video_automation.app.logging import get_logger
from video_automation.app.settings import Settings
from video_automation.database.database import Database
from video_automation.database.models import Asset
from video_automation.integrations.ffmpeg.runner import FFmpeg
from video_automation.schemas.timeline import AssetKind, AssetRead
from video_automation.services.screencast.camera import (
    CameraParams,
    Focus,
    camera_at,
    change_regions,
    plan_shots,
)
from video_automation.services.timeline.timeline_service import CommandError, TimelineService

log = get_logger("screencast")
ProgressFn = Callable[[float], Awaitable[None]]
SAMPLE_FPS = 5.0
SAMPLE_SCALE = 0.25
RIPPLE_SEC = 0.55


def _gray_frames(ffmpeg: str, src: Path, w: int, h: int) -> Iterator[tuple[float, Any]]:
    import numpy as np

    sw, sh = max(2, int(w * SAMPLE_SCALE) // 2 * 2), max(2, int(h * SAMPLE_SCALE) // 2 * 2)
    proc = subprocess.Popen(
        [
            ffmpeg,
            "-v",
            "error",
            "-i",
            str(src),
            "-vf",
            f"fps={SAMPLE_FPS},scale={sw}:{sh},format=gray",
            "-f",
            "rawvideo",
            "-",
        ],
        stdout=subprocess.PIPE,
    )
    assert proc.stdout is not None
    n, i = sw * sh, 0
    try:
        while True:
            buf = proc.stdout.read(n)
            if len(buf) < n:
                break
            yield i / SAMPLE_FPS, np.frombuffer(buf, np.uint8).reshape(sh, sw)
            i += 1
    finally:
        proc.kill()
        proc.wait()


def analyze(ffmpeg: str, src: Path, size: tuple[int, int]) -> list[Focus]:
    """画面が変わった範囲（縮小した灰色の映像の差分）。"""
    sx = size[0] * SAMPLE_SCALE
    scale = (max(2, int(sx) // 2 * 2)) / size[0]
    frames = list(_gray_frames(ffmpeg, src, *size))
    return change_regions(frames, scale)


def render_zoom(
    ffmpeg: str,
    src: Path,
    out: Path,
    size: tuple[int, int],
    fps: float,
    duration: float,
    shots: Any,
    clicks: list[Focus],
    params: CameraParams,
    color: tuple[int, int, int] = (0x5F, 0xB3, 0xA1),
    progress: Callable[[float], None] | None = None,
) -> None:
    from PIL import Image, ImageDraw

    w, h = size
    dec = subprocess.Popen(
        [ffmpeg, "-v", "error", "-i", str(src), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        stdout=subprocess.PIPE,
    )
    enc = subprocess.Popen(
        [
            ffmpeg,
            "-v",
            "error",
            "-y",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s",
            f"{w}x{h}",
            "-r",
            f"{fps:.6f}",
            "-i",
            "-",
            "-i",
            str(src),
            "-map",
            "0:v",
            "-map",
            "1:a?",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(out),
        ],
        stdin=subprocess.PIPE,
    )
    assert dec.stdout is not None and enc.stdin is not None
    n, f = w * h * 3, 0
    total = max(1, round(duration * fps))
    try:
        while True:
            buf = dec.stdout.read(n)
            if len(buf) < n:
                break
            t = f / fps
            cx, cy, z = camera_at(shots, t, size, params)
            im = Image.frombytes("RGB", (w, h), buf)
            if z > 1.001:
                vw, vh = w / z, h / z
                box = (cx - vw / 2, cy - vh / 2, cx + vw / 2, cy + vh / 2)
                im = im.resize((w, h), Image.Resampling.BICUBIC, box=box)
            # クリックの波紋（出力の座標へ写す）
            rip = [c for c in clicks if 0 <= t - c.t < RIPPLE_SEC]
            if rip:
                ov = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                d = ImageDraw.Draw(ov)
                for c in rip:
                    u = (t - c.t) / RIPPLE_SEC
                    e = 1 - (1 - u) ** 3
                    px = (c.x - cx) * z + w / 2
                    py = (c.y - cy) * z + h / 2
                    r = (14 + 46 * e) * h / 1080
                    a = round(220 * (1 - u))
                    d.ellipse(
                        [px - r, py - r, px + r, py + r],
                        outline=(*color, a),
                        width=max(2, round(5 * h / 1080)),
                    )
                    r2 = (8 + 8 * e) * h / 1080
                    d.ellipse([px - r2, py - r2, px + r2, py + r2], fill=(*color, a // 2))
                im = Image.alpha_composite(im.convert("RGBA"), ov).convert("RGB")
            enc.stdin.write(im.tobytes())
            f += 1
            if progress and f % 30 == 0:
                progress(min(0.99, f / total))
        enc.stdin.close()
        if enc.wait() != 0:
            raise RuntimeError("ffmpeg (encode) failed")
    finally:
        dec.kill()
        dec.wait()
        if enc.poll() is None:
            enc.kill()


class ScreencastService:
    def __init__(
        self, settings: Settings, db: Database, timeline: TimelineService, ffmpeg: FFmpeg
    ) -> None:
        self.settings, self.db, self.timeline, self.ffmpeg = settings, db, timeline, ffmpeg

    async def zoom(
        self,
        project_id: str,
        asset_id: str,
        clicks: list[tuple[float, float, float]] | None = None,
        zoom: float = 1.8,
        progress: ProgressFn | None = None,
    ) -> AssetRead:
        """録画の素材から、操作している所へ寄った映像を作り、新しい素材として登録する。"""
        async with self.db.session_factory() as s:
            a = await s.get(Asset, asset_id)
            if a is None or a.project_id != project_id:
                raise CommandError("asset not found")
            if a.kind != AssetKind.VIDEO.value:
                raise CommandError("asset is not a video")
        ff = self.ffmpeg.ffmpeg
        if not ff:
            raise CommandError("ffmpeg not found")
        src = self.timeline.asset_path(a)
        info = await self.ffmpeg.probe(src)
        w, h = (info.width or 0) // 2 * 2, (info.height or 0) // 2 * 2
        if not w or not h:
            raise CommandError("video size unknown")
        fps = info.fps or 30.0
        dur = info.duration_sec or 0.0
        params = CameraParams(zoom=zoom, max_zoom=max(zoom, 2.2))
        click_f = [Focus(t, x, y) for t, x, y in (clicks or [])]
        changes = await asyncio.to_thread(analyze, ff, src, (w, h))
        # クリックの前後 1 秒の画面の変化はクリックのもの（重ねて寄らない）
        changes = [c for c in changes if all(abs(c.t - k.t) > 1.0 for k in click_f)]
        shots = plan_shots(click_f + changes, dur, (w, h), params)
        out_dir = self.settings.data_dir / "projects" / project_id / "screencast"
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"{uuid4().hex[:12]}.mp4"
        loop = asyncio.get_running_loop()

        async def report(x: float) -> None:
            if progress:
                await progress(x)

        def prog(x: float) -> None:
            asyncio.run_coroutine_threadsafe(report(x), loop)

        await asyncio.to_thread(
            render_zoom,
            ff,
            src,
            out,
            (w, h),
            fps,
            dur,
            shots,
            click_f,
            params,
            (0x5F, 0xB3, 0xA1),
            prog,
        )
        row = await self.timeline.register_generated(
            project_id, out, AssetKind.VIDEO, "screencast", f"{a.name}（自動ズーム）"
        )
        log.info("screencast_zoom", asset=asset_id, shots=len(shots), clicks=len(click_f))
        return AssetRead.model_validate(row)
