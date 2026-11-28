"""画面録画の自動ズーム: 録画の素材から、操作している所へ寄った映像を作って素材に登録する。

- 見せたい所: クリックの記録（任意。録画の画素の座標と秒）と、画面が変わった範囲（自動）
- カメラは camera.plan_shots / camera_at（ポーズからポーズへ、イーズで移る）
- クリックには波紋を重ねる。音声はそのまま残す（早送りの区間は 4 倍速）
- 仕上げ（W3、finish.py）: 個人情報のぼかし（OCR）・早送り・キー操作・カーソルの光
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import tempfile
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from video_automation.app.logging import get_logger
from video_automation.app.settings import Settings
from video_automation.database.database import Database
from video_automation.database.models import Asset, Project
from video_automation.integrations.ffmpeg.runner import FFmpeg
from video_automation.integrations.ocr.engine import OcrEngine, OcrUnavailable, default_engine
from video_automation.schemas.timeline import AssetKind, AssetRead
from video_automation.services.screencast import finish as fin
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


def analyze(
    ffmpeg: str, src: Path, size: tuple[int, int]
) -> tuple[list[Focus], list[tuple[float, float]]]:
    """画面が変わった範囲（縮小した灰色の映像の差分）と、サンプルごとの変わった画素の割合。"""
    import numpy as np

    sx = size[0] * SAMPLE_SCALE
    scale = (max(2, int(sx) // 2 * 2)) / size[0]
    frames = list(_gray_frames(ffmpeg, src, *size))
    motion = [
        (t, float((np.abs(a.astype(np.int16) - b.astype(np.int16)) > 18).mean()))
        for (t, a), (_, b) in zip(frames[1:], frames, strict=False)
    ]
    return change_regions(frames, scale), motion


def silences(ffmpeg: str, src: Path, duration: float, has_audio: bool) -> list[tuple[float, float]]:
    """声の無い区間（silencedetect: -35dB 未満が 0.3 秒以上）。音声が無ければ全体。"""
    if not has_audio:
        return [(0.0, duration)]
    p = subprocess.run(
        [
            ffmpeg,
            "-v",
            "info",
            "-i",
            str(src),
            "-vn",
            "-af",
            "silencedetect=noise=-35dB:d=0.3",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        check=False,
    )
    log_ = p.stderr.decode("utf-8", "replace")
    out: list[tuple[float, float]] = []
    start: float | None = None
    for m in re.finditer(r"silence_(start|end): (-?[\d.]+)", log_):
        v = max(0.0, float(m.group(2)))
        if m.group(1) == "start":
            start = v
        elif start is not None:
            out.append((start, v))
            start = None
    if start is not None:
        out.append((start, duration))
    return out


def ocr_samples(
    ffmpeg: str, src: Path, engine: OcrEngine, step: float = fin.OCR_STEP
) -> list[tuple[float, list[fin.Hit]]]:
    """step 秒ごとのフレームを OCR し、個人情報を探す。"""
    with tempfile.TemporaryDirectory(prefix="va_pii_") as d:
        subprocess.run(
            [
                ffmpeg,
                "-v",
                "error",
                "-i",
                str(src),
                "-vf",
                f"fps={1 / step:.6f}",
                "-q:v",
                "2",
                str(Path(d) / "f%06d.jpg"),
            ],
            check=True,
        )
        imgs = sorted(Path(d).glob("f*.jpg"))
        res = engine.read(imgs)
    # fps フィルタの n 枚目は n*step 秒（最初の 1 枚は 0 秒）
    return [(i * step, fin.find_pii(r)) for i, r in enumerate(res)]


@dataclass
class Finish:
    """仕上げの計画（録画の時刻・画素）。"""

    pii: list[fin.Track] = field(default_factory=list)  # ぼかす個人情報
    found: list[fin.Track] = field(default_factory=list)  # 見つけた個人情報（外した物も含む）
    segs: list[tuple[float, float, int]] = field(default_factory=list)
    keys: list[tuple[float, str]] = field(default_factory=list)
    cursor: list[tuple[float, float, float]] = field(default_factory=list)
    font: str | None = None
    spotlight: bool = False  # 統合版 N14: クリックの前後に、クリックの位置の外を暗くする

    @property
    def fast(self) -> bool:
        return any(sp != 1 for _, _, sp in self.segs)

    def speed_at(self, t: float) -> tuple[int, float]:
        for a, b, sp in self.segs:
            if a <= t < b:
                return sp, a
        return 1, 0.0


def _font(path: str | None, size: int) -> Any:
    from PIL import ImageFont

    if path and Path(path).is_file():
        return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


def _glow(r: int) -> Any:
    """カーソルの光（中心が明るく縁へ消える円）。"""
    import numpy as np
    from PIL import Image

    yy, xx = np.mgrid[-r : r + 1, -r : r + 1]
    d = np.sqrt(xx**2 + yy**2) / r
    a = np.clip(1 - d, 0, 1) ** 0.6 * 255 * fin.CURSOR_OPACITY
    rgba = np.zeros((2 * r + 1, 2 * r + 1, 4), np.uint8)
    rgba[..., :3] = (255, 236, 140)
    rgba[..., 3] = a.astype(np.uint8)
    return Image.fromarray(rgba, "RGBA")


def _badge(im: Any, text: str, center: tuple[float, float], h: int, font: Any) -> None:
    """角の丸い黒い札に白い文字（キー操作・早送り）。"""
    from PIL import ImageDraw

    d = ImageDraw.Draw(im)
    x0, y0, x1, y1 = d.textbbox((0, 0), text, font=font)
    pw, ph = round(0.6 * h * 0.045), round(0.3 * h * 0.045)
    tw, th = x1 - x0, y1 - y0
    cx, cy = center
    box = [cx - tw / 2 - pw, cy - th / 2 - ph, cx + tw / 2 + pw, cy + th / 2 + ph]
    d.rounded_rectangle(box, radius=round(h * 0.012), fill=(0, 0, 0, 170))
    d.text((cx - tw / 2 - x0, cy - th / 2 - y0), text, font=font, fill=(255, 255, 255, 255))


def _fast_mark(im: Any, h: int, font: Any) -> None:
    """右上の「⏩ ×4」（記号は書体に無いことがあるので三角を 2 つ描く）。"""
    from PIL import ImageDraw

    w = im.size[0]
    d = ImageDraw.Draw(im)
    s = h * 0.045
    x, y = w - h * 0.05 - s * 3.6, h * 0.05
    d.rounded_rectangle(
        [x - s * 0.4, y - s * 0.3, x + s * 3.6, y + s * 1.3],
        radius=round(s * 0.3),
        fill=(0, 0, 0, 170),
    )
    for k in (0, 0.55):
        d.polygon(
            [(x + s * k, y), (x + s * (k + 0.6), y + s / 2), (x + s * k, y + s)],
            fill=(255, 255, 255, 255),
        )
    d.text(
        (x + s * 1.4, y + s / 2),
        f"×{fin.FAST_SPEED}",
        font=font,
        anchor="lm",
        fill=(255, 255, 255, 255),
    )


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
    finish: Finish | None = None,
    has_audio: bool = True,
) -> None:
    from PIL import Image, ImageDraw, ImageFilter

    w, h = size
    fx = finish or Finish()
    glow = _glow(max(4, round(fin.CURSOR_RADIUS * h / 1080))) if fx.cursor else None
    font = _font(fx.font, max(12, round(h * 0.034))) if (fx.keys or fx.fast) else None
    if fx.fast and has_audio:
        amap = ["-filter_complex", fin.audio_filter(fx.segs), "-map", "0:v", "-map", "[aout]"]
    else:
        amap = ["-map", "0:v", "-map", "1:a?"]
    dec = subprocess.Popen(
        # 画面録画は可変フレームレートのことが多い。出力と同じ一定の間隔に揃えてから読む
        # （揃えないとフレームの時刻がずれ、寄る・引くの動きが不規則に飛んで見える）
        [
            ffmpeg,
            "-v",
            "error",
            "-i",
            str(src),
            "-vf",
            f"fps={fps:.6f},scale={w}:{h}:flags=bicubic",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
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
            *amap,
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
    n, f, i = w * h * 3, 0, -1
    total = max(1, round(duration * fps))
    try:
        while True:
            buf = dec.stdout.read(n)
            if len(buf) < n:
                break
            i += 1
            t = i / fps  # 録画の時刻（早送りで飛ばしたフレームも数える）
            sp, a0 = fx.speed_at(t)
            if sp > 1 and (i - round(a0 * fps)) % sp:
                continue
            cx, cy, z = camera_at(shots, t, size, params)
            im = Image.frombytes("RGB", (w, h), buf)
            for bx, by, bw, bh in fin.boxes_at(fx.pii, t):  # 個人情報は寄る前の画素でぼかす
                pb = (
                    max(0, int(bx)),
                    max(0, int(by)),
                    min(w, int(bx + bw + 1)),
                    min(h, int(by + bh + 1)),
                )
                if pb[2] > pb[0] and pb[3] > pb[1]:
                    part = im.crop(pb).filter(ImageFilter.GaussianBlur(fin.BLUR_RADIUS))
                    im.paste(part, pb[:2])
            if z > 1.0005:  # 1 画素未満の精度で切り出す（box は小数のまま渡す）
                vw, vh = w / z, h / z
                box = (cx - vw / 2, cy - vh / 2, cx + vw / 2, cy + vh / 2)
                im = im.resize((w, h), Image.Resampling.BICUBIC, box=box)
            if fx.spotlight:  # 統合版 N14（寄った後の画面に。クリックの位置を中心に）
                lit = [(c, k) for c in clicks if (k := fin.spotlight_level(t - c.t, fps)) > 0]
                if lit:
                    c, k = max(lit, key=lambda x: x[1])
                    px = (c.x - cx) * z + w / 2
                    py = (c.y - cy) * z + h / 2
                    im = fin.spotlight(im, px, py, k)
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
            pos = fin.cursor_at(fx.cursor, t) if glow is not None else None
            key = fin.key_at(fx.keys, t) if fx.keys else None
            if pos is not None or key or sp > 1:
                ov = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                if pos is not None and glow is not None:
                    px = (pos[0] - cx) * z + w / 2
                    py = (pos[1] - cy) * z + h / 2
                    # 空の重ね絵へ置くので paste でよい（画面の端で一部がはみ出しても置ける）
                    gx, gy = round(px) - glow.size[0] // 2, round(py) - glow.size[1] // 2
                    ov.paste(glow, (gx, gy), glow)
                if key:
                    _badge(ov, key, (w / 2, h * 0.9), h, font)
                if sp > 1:
                    _fast_mark(ov, h, font)
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


_UNSET: Any = object()


class ScreencastService:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        timeline: TimelineService,
        ffmpeg: FFmpeg,
        ocr: OcrEngine | None = _UNSET,
    ) -> None:
        self.settings, self.db, self.timeline, self.ffmpeg = settings, db, timeline, ffmpeg
        self.ocr: OcrEngine | None = default_engine() if ocr is _UNSET else ocr
        # 仕上げの報告（素材の id → 個人情報の一覧・早送りの区間）。利用者が確かめる
        self.reports: dict[str, dict[str, Any]] = {}

    async def zoom(
        self,
        project_id: str,
        asset_id: str,
        clicks: list[tuple[float, float, float]] | None = None,
        zoom: float = 1.8,
        progress: ProgressFn | None = None,
        cursor: list[tuple[float, float, float]] | None = None,
        keys: list[tuple[float, str]] | None = None,
        speedup: bool = True,
        blur_pii: bool = True,
        spotlight: bool | None = None,
        unblur: list[int] | None = None,
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
        changes, motion = await asyncio.to_thread(analyze, ff, src, (w, h))
        # クリックの前後 1 秒の画面の変化はクリックのもの（重ねて寄らない）
        changes = [c for c in changes if all(abs(c.t - k.t) > 1.0 for k in click_f)]
        shots = plan_shots(click_f + changes, dur, (w, h), params)
        fx, notes = await self._finish(
            ff, src, dur, info.has_audio, motion, cursor, keys, speedup, blur_pii, unblur
        )
        if spotlight is None:  # 既定は用途が操作説明のときだけ（統合版 N14）
            spotlight = await self._purpose(project_id) == "tutorial"
        fx.spotlight = bool(spotlight and click_f)
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
            fx,
            info.has_audio,
        )
        row = await self.timeline.register_generated(
            project_id, out, AssetKind.VIDEO, "screencast", f"{a.name}（自動ズーム）"
        )
        rep: dict[str, Any] = {
            # index は外すときに unblur で指す番号。blurred は今回ぼかしたか
            "pii": [
                {
                    "index": i,
                    "kind": tr.kind,
                    "text": fin.mask(tr.text),
                    "start": round(max(0.0, tr.t0), 2),
                    "end": round(min(dur, tr.t1), 2),
                    "blurred": any(tr is x for x in fx.pii),
                }
                for i, tr in enumerate(fx.found)
            ],
            "fast": [[round(a, 2), round(b, 2)] for a, b, sp in fx.segs if sp > 1],
            "notes": notes,
        }
        # 一覧は動画の隣にも置く（文字は伏せ字だけ。元の文字はどこにも書かない）
        out.with_suffix(".finish.json").write_text(
            json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        res = AssetRead.model_validate(row)
        self.reports[res.id] = rep
        log.info(
            "screencast_zoom",
            asset=asset_id,
            shots=len(shots),
            clicks=len(click_f),
            pii=len(fx.pii),
            fast=len(rep["fast"]),
        )
        return res

    async def _purpose(self, project_id: str) -> str | None:
        async with self.db.session_factory() as s:
            p = await s.get(Project, project_id)
            return p.purpose if p is not None else None

    async def _finish(
        self,
        ff: str,
        src: Path,
        dur: float,
        has_audio: bool,
        motion: list[tuple[float, float]],
        cursor: list[tuple[float, float, float]] | None,
        keys: list[tuple[float, str]] | None,
        speedup: bool,
        blur_pii: bool,
        unblur: list[int] | None = None,
    ) -> tuple[Finish, list[str]]:
        notes: list[str] = []
        fx = Finish(cursor=sorted(cursor or []), keys=fin.visible_keys(keys or []))
        if keys:
            from video_automation.services.rendering.title_card import FONT_CANDIDATES

            fx.font = next((f for f in FONT_CANDIDATES if Path(f).is_file()), None)
        if speedup:
            sil = await asyncio.to_thread(silences, ff, src, dur, has_audio)
            fast = fin.fast_ranges(motion, sil, dur)
            fx.segs = fin.speed_segments(fast, dur)
            if fx.fast and fx.font is None:
                from video_automation.services.rendering.title_card import FONT_CANDIDATES

                fx.font = next((f for f in FONT_CANDIDATES if Path(f).is_file()), None)
        if blur_pii:
            if self.ocr is None:
                notes.append("文字の読み取り（OCR）が使えないため、個人情報のぼかしを省きました")
            else:
                try:
                    samples = await asyncio.to_thread(ocr_samples, ff, src, self.ocr)
                    fx.found = fin.track(samples)
                    # W3: 利用者が一覧で確かめて「ぼかさない」とした物（番号）を外す
                    skip = set(unblur or [])
                    fx.pii = [t for i, t in enumerate(fx.found) if i not in skip]
                    if skip & set(range(len(fx.found))):
                        n_ = len(skip & set(range(len(fx.found))))
                        notes.append(f"指定された {n_} 件の個人情報はぼかしませんでした")
                except (OcrUnavailable, subprocess.CalledProcessError) as exc:
                    notes.append(f"個人情報のぼかしを省きました（{type(exc).__name__}）")
        return fx, notes
