"""Blender で文字アニメーションのクリップ（透過付き）を作り、素材として登録する (P5-02)。

Blender の PNG 連番 → FFmpeg で透過付き MOV（PNG コーデック）に変換して取り込む。
複雑な演出は Phase 7 で拡張する。
"""

import asyncio
import tempfile
import time
from pathlib import Path
from uuid import uuid4

from video_automation.app.logging import get_logger
from video_automation.app.settings import Settings
from video_automation.database.database import Database
from video_automation.database.models import Project
from video_automation.integrations.blender.runner import Blender
from video_automation.integrations.ffmpeg.runner import FFmpeg, FFmpegError, ff_seconds
from video_automation.schemas.animation import CardRequest, InfographicRequest
from video_automation.schemas.timeline import AssetKind, AssetRead, TitleCardRequest
from video_automation.services.design.guard import font_key
from video_automation.services.rendering.card import card_filter, card_layout
from video_automation.services.timeline.timeline_service import (
    TimelineNotFoundError,
    TimelineService,
)

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\meiryob.ttc",
    r"C:\Windows\Fonts\meiryo.ttc",
    r"C:\Windows\Fonts\YuGothB.ttc",
    r"C:\Windows\Fonts\msgothic.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
]

# 書体のファミリー（font_key で揃えた名前）→ ファイル（太字を優先）
FONT_FILES = {
    "meiryo": [r"C:\Windows\Fonts\meiryob.ttc", r"C:\Windows\Fonts\meiryo.ttc"],
    "bizudgothic": [
        r"C:\Windows\Fonts\BIZ-UDGothicB.ttc",
        r"C:\Windows\Fonts\BIZ-UDGothicR.ttc",
    ],
    "yugothic": [r"C:\Windows\Fonts\YuGothB.ttc", r"C:\Windows\Fonts\YuGothM.ttc"],
    "notosansjp": ["/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"],
}

log = get_logger("title_card")


def _srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _rgba(hex_color: str) -> list[float]:
    """#RRGGBB（sRGB）→ Blender の色（線形）。Blender のマテリアルの色は線形なので、
    変換しないと暗い色が明るく浮く（明るい背景の型で題名が灰色に見えていた）。"""
    h = hex_color.lstrip("#")
    return [_srgb_to_linear(int(h[i : i + 2], 16) / 255) for i in (0, 2, 4)] + [1.0]


GLOSS_SEC = 25 / 30  # 統合版 L7: 820ms（25f）


def gloss_frames(fps: float) -> int:
    return max(4, round(GLOSS_SEC * fps))


class TitleCardService:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        blender: Blender,
        ffmpeg: FFmpeg,
        timeline: TimelineService,
    ) -> None:
        self.settings = settings
        self.db = db
        self.blender = blender
        self.ffmpeg = ffmpeg
        self.timeline = timeline

    def font_path(self, family: str | None = None) -> str | None:
        """書体のファイル。設定で指定があればそれを優先し、次に family（用途の型の書体）。"""
        if self.settings.title_font_path:
            return self.settings.title_font_path
        if family:
            key = font_key(family)
            for f in FONT_FILES.get(key, []):
                if Path(f).is_file():
                    return f
        return next((f for f in FONT_CANDIDATES if Path(f).is_file()), None)

    async def _project(self, project_id: str) -> tuple[int, int, int, int]:
        async with self.db.session_factory() as s:
            p = await s.get(Project, project_id)
            if p is None:
                raise TimelineNotFoundError(f"project {project_id}")
            return p.width, p.height, p.fps_num, p.fps_den

    @staticmethod
    def animation_seconds(req: TitleCardRequest) -> float:
        if req.animation_sec:
            return req.animation_sec
        n = len(req.text.replace("\n", ""))
        one_line = "\n" not in req.text and n > 1
        f = 1 / 30
        per_char = {  # 統合版 T16（30fps のフレーム数。title_card.py の entrance_frames と同じ）
            "decode": (2 * (n - 1) + 1) * f,
            "flip3d": (2 * (n - 1) + 10) * f,
            "mask_rise": (2 * (n - 1) + 10) * f,
            "stroke": 6 * n * f,
            "tracking": 18 * f,
            # 統合版 T6: カーソルが表示の終わりまで点滅するので、表示の全体を描く
            "typewriter": req.duration_sec,
        }
        if req.animation in per_char and one_line:
            return per_char[req.animation]
        return {
            # 統合版 B4: pop 12f、slide 16f（30fps）
            "pop": 0.4,
            "slide": 16 / 30,
            "zoom": 0.45,
            "slam": 16 * f,  # 統合版 T17: 4f＋着地 6f＋残像
            "typewriter": min(max(n * 0.07, 0.3), 3.0) + 0.1,
            "bounce": min(max(n * 0.05, 0.2), 2.0) + 0.45,
        }.get(req.animation, 0.0)

    async def _compose(
        self,
        tmp: Path,
        out: Path,
        *,
        size: tuple[int, int],
        fps: tuple[int, int],
        rendered: int,
        total: int,
        fade_in_sec: float,
        fade_out_sec: float,
        box: tuple[int, int, int, int] | None,
        box_rgba: str,
    ) -> None:
        """Blender の連番（動く部分だけ）→ 最終フレームで延長・フェード・帯 → 透過 MOV。"""
        w, h = size
        fps_s = f"{fps[0]}/{fps[1]}"
        dur = total * fps[1] / fps[0]
        rest = max(0, total - rendered) * fps[1] / fps[0]
        txt = ["setpts=PTS-STARTPTS", "format=rgba"]
        if rest > 0:
            txt.append(f"tpad=stop_mode=clone:stop_duration={ff_seconds(rest)}")
        graph = [f"[0:v]{','.join(txt)}[t]"]
        inputs = ["-framerate", fps_s, "-i", str(tmp / "frame_%04d.png")]
        src = "t"
        if box is not None:
            bx, by, bw, bh = box
            inputs += [
                "-f",
                "lavfi",
                "-i",
                f"color=c=black@0.0:s={w}x{h}:r={fps_s}:d={ff_seconds(dur)},format=rgba",
            ]
            graph.append(
                f"[1:v]format=rgba,drawbox=x={bx}:y={by}:w={bw}:h={bh}:color={box_rgba}:t=fill:replace=1,"
                f"fade=t=in:st=0:d=0.2:alpha=1[box]"
            )
            # 透明を保つため、帯の層は入力の時点で RGBA にしておく（-i 側で format=rgba）
            graph.append("[box][t]overlay=0:0:format=auto[bt]")
            src = "bt"
        fades = []
        if fade_in_sec > 0:
            fades.append(f"fade=t=in:st=0:d={ff_seconds(fade_in_sec)}:alpha=1")
        if fade_out_sec > 0:
            st = max(0.0, dur - fade_out_sec)
            fades.append(f"fade=t=out:st={ff_seconds(st)}:d={ff_seconds(fade_out_sec)}:alpha=1")
        graph.append(f"[{src}]{','.join([*fades, 'format=rgba'])}[out]")
        await self.ffmpeg.run(
            [
                *inputs,
                "-filter_complex",
                ";".join(graph),
                "-map",
                "[out]",
                "-frames:v",
                str(total),
                "-r",
                fps_s,
                "-c:v",
                "png",
                "-pix_fmt",
                "rgba",
                str(out),
            ]
        )

    @staticmethod
    def _box(text_box: list[int] | None, size: tuple[int, int]) -> tuple[int, int, int, int] | None:
        if not text_box:
            return None
        w, h = size
        x, y, bw, bh = text_box
        px, py = max(8, round(bh * 0.45)), max(4, round(bh * 0.22))
        x0, y0 = max(0, x - px), max(0, y - py)
        x1, y1 = min(w, x + bw + px), min(h, y + bh + py)
        return x0, y0, max(2, x1 - x0), max(2, y1 - y0)

    async def create(self, req: TitleCardRequest) -> AssetRead:
        w, h, fps_num, fps_den = await self._project(req.project_id)
        fps = round(fps_num / fps_den)
        total = max(1, round(req.duration_sec * fps_num / fps_den))
        anim_sec = self.animation_seconds(req)
        rendered = min(total, max(1, round(anim_sec * fps_num / fps_den)))
        gloss_at = 0
        if req.gloss:  # 統合版 L7: 入りの後に光の帯を 25f（30fps）
            gloss_at = rendered + 1
            rendered = min(total, gloss_at + gloss_frames(fps_num / fps_den))
            if rendered - gloss_at < 4:  # 表示が短すぎて通しきれないときは付けない
                gloss_at, rendered = 0, min(total, max(1, round(anim_sec * fps_num / fps_den)))
        out_dir = self.settings.data_dir / "projects" / req.project_id / "assets"
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"title_{uuid4().hex[:8]}.mov"
        t0 = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="va_title_") as tmp:
            result = await self.blender.run_script(
                "title_card",
                {
                    "text": req.text,
                    "width": w,
                    "height": h,
                    "fps": fps,
                    "frames": rendered,
                    "font_path": self.font_path(req.font),
                    "text_color": _rgba(req.text_color),
                    "outline": req.outline,
                    "outline_color": _rgba(req.outline_color),
                    "size": req.size,
                    "position": req.position,
                    "output_dir": tmp,
                    "animation": req.animation,
                    "gloss_at": gloss_at,
                    "sun_beams": req.sun_beams,
                    "solid3d": req.solid3d,
                    "gloss_frames": gloss_frames(fps_num / fps_den),
                },
            )
            if not result.get("frames"):
                raise RuntimeError("blender produced no frames")
            log.info(
                "title_card_rendered",
                engine=result.get("engine"),
                animation=req.animation,
                frames=result["frames"],
                total_frames=total,
                sec=round(time.perf_counter() - t0, 1),
            )
            await self._compose(
                Path(tmp),
                out,
                size=(w, h),
                fps=(fps_num, fps_den),
                rendered=int(result["frames"]),
                total=total,
                fade_in_sec=min(0.4, req.duration_sec / 2) if req.animation == "fade" else 0.0,
                fade_out_sec=min(req.fade_out_sec, req.duration_sec / 2),
                box=self._box(result.get("text_box"), (w, h)) if req.box else None,
                box_rgba=f"{req.box_color}@{req.box_opacity:g}",
            )
        asset = await self.timeline.register_generated(
            req.project_id, out, AssetKind.VIDEO, "blender", f"タイトル「{req.text[:20]}」"
        )
        return AssetRead.model_validate(asset)

    @staticmethod
    def _chart_colors(req: InfographicRequest) -> list[str]:
        if req.highlight is None or req.highlight >= len(req.values):
            return req.colors
        return [
            req.colors[0] if i == req.highlight else req.muted_color for i in range(len(req.values))
        ]

    async def infographic(self, req: InfographicRequest) -> AssetRead:
        w, h, fps_num, fps_den = await self._project(req.project_id)
        fps = round(fps_num / fps_den)
        total = max(1, round(req.duration_sec * fps_num / fps_den))
        rendered = min(total, max(2, round(req.animation_sec * fps_num / fps_den)))
        out_dir = self.settings.data_dir / "projects" / req.project_id / "assets"
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"chart_{uuid4().hex[:8]}.mov"
        with tempfile.TemporaryDirectory(prefix="va_chart_") as tmp:
            result = await self.blender.run_script(
                "infographic",
                {
                    "kind": req.kind,
                    "title": req.title,
                    "labels": req.labels,
                    "values": req.values,
                    "unit": req.unit,
                    "decimals": req.decimals,
                    "width": w,
                    "height": h,
                    "fps": fps,
                    "frames": rendered,
                    "font_path": self.font_path(req.font),
                    "text_color": _rgba(req.text_color),
                    "accent_colors": [_rgba(c) for c in self._chart_colors(req)],
                    "muted_color": _rgba(req.muted_color),
                    "output_dir": tmp,
                },
            )
            if not result.get("frames"):
                raise RuntimeError("blender produced no frames")
            m = round(min(w, h) * 0.04)
            await self._compose(
                Path(tmp),
                out,
                size=(w, h),
                fps=(fps_num, fps_den),
                rendered=int(result["frames"]),
                total=total,
                fade_in_sec=0.0,
                fade_out_sec=min(req.fade_out_sec, req.duration_sec / 2),
                box=(m, m, w - 2 * m, h - 2 * m) if req.box else None,
                box_rgba=f"{req.box_color}@{req.box_opacity:g}",
            )
        name = f"図表「{(req.title or req.kind)[:20]}」"
        asset = await self.timeline.register_generated(
            req.project_id, out, AssetKind.VIDEO, "blender", name
        )
        return AssetRead.model_validate(asset)

    # ---------- 動く背景（Blender の 3D を、グラデーションの上に薄く重ねる） ----------
    async def motion_background(
        self,
        project_id: str,
        *,
        style: str,
        base: str,
        base2: str,
        main: str,
        accent: str,
        loop_sec: float = 8.0,
        opacity: float = 0.55,
        name: str = "",
    ) -> AssetRead:
        """継ぎ目なく繰り返せる動く背景（MP4）。同じ設定なら作り直さない。"""
        import hashlib

        w, h, fps_num, fps_den = await self._project(project_id)
        fps = round(fps_num / fps_den)
        frames = max(2, round(loop_sec * fps_num / fps_den))
        spec = f"{style}|{w}x{h}|{fps_num}/{fps_den}|{frames}|{base}{base2}{main}{accent}|{opacity}"
        key = hashlib.sha1(spec.encode()).hexdigest()[:10]
        out_dir = self.settings.data_dir / "projects" / project_id / "assets"
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"bg_motion_{style}_{key}.mp4"
        if not out.is_file():
            with tempfile.TemporaryDirectory(prefix="va_bg_") as tmp:
                result = await self.blender.run_script(
                    "motion_bg",
                    {
                        "style": style,
                        "width": w,
                        "height": h,
                        "fps": fps,
                        "frames": frames,
                        "colors": {
                            "base": _rgba(base),
                            "base2": _rgba(base2),
                            "main": _rgba(main),
                            "accent": _rgba(accent),
                        },
                        "output_dir": tmp,
                    },
                    timeout=1800,
                )
                if not result.get("frames"):
                    raise RuntimeError("blender produced no frames")
                fps_s = f"{fps_num}/{fps_den}"
                c0, c1 = "0x" + base.lstrip("#"), "0x" + base2.lstrip("#")
                dur = ff_seconds(frames * fps_den / fps_num)
                # 3D の層は少しぼかし、薄くして重ねる（字幕・図より目立たせない）
                blur = max(1.0, w / 1100)
                graph = (
                    f"[1:v]format=rgba,scale={w}:{h}:flags=bicubic,gblur=sigma={blur:.2f},"
                    f"colorchannelmixer=aa={opacity:g}[fg];[0:v][fg]overlay=0:0:shortest=1,"
                    "format=yuv420p[out]"
                )
                grad = (
                    f"gradients=s={w}x{h}:c0={c0}:c1={c1}:x0=0:y0=0:x1={w}:y1={h}:"
                    f"r={fps_s}:d={dur}:speed=0.00001"
                )
                flat = f"color=c={c1}:s={w}x{h}:r={fps_s}:d={dur}"
                for src in (grad, flat):  # 古い FFmpeg には gradients が無い
                    try:
                        await self._bg_encode(src, tmp, graph, frames, fps_s, out)
                        break
                    except FFmpegError:
                        if src == flat:
                            raise
        asset = await self.timeline.register_generated(
            project_id, out, AssetKind.VIDEO, "blender", name or f"動く背景（{style}）"
        )
        return AssetRead.model_validate(asset)

    async def _bg_encode(
        self, src: str, tmp: str, graph: str, frames: int, fps_s: str, out: Path
    ) -> None:
        await self.ffmpeg.run(
            [
                "-f",
                "lavfi",
                "-i",
                src,
                "-framerate",
                fps_s,
                "-i",
                str(Path(tmp) / "frame_%04d.png"),
                "-filter_complex",
                graph,
                "-map",
                "[out]",
                "-frames:v",
                str(frames),
                "-r",
                fps_s,
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "20",
                "-an",
                str(out),
            ],
            timeout=600,
        )

    # ---------- 情報カード（FFmpeg で静止画を作る） ----------
    async def card(self, req: CardRequest) -> AssetRead:
        w, h, _, _ = await self._project(req.project_id)
        font = self.font_path(req.font)
        if font is None:
            raise RuntimeError("no Japanese font found (set title_font_path)")
        layout = card_layout(req, w, h)
        out_dir = self.settings.data_dir / "projects" / req.project_id / "assets"
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"card_{uuid4().hex[:8]}.png"
        with tempfile.TemporaryDirectory(prefix="va_card_") as tmp:
            vf = card_filter(req, layout, Path(font), Path(tmp))
            await self.ffmpeg.run(
                [
                    "-f",
                    "lavfi",
                    "-i",
                    f"color=c=black@0.0:s={layout.width}x{layout.height}:d=1,format=rgba",
                    "-vf",
                    vf,
                    "-frames:v",
                    "1",
                    "-update",
                    "1",
                    str(out),
                ],
                timeout=60,
            )
        if layout.icon is not None and req.icon and req.only_item is None:
            await asyncio.to_thread(_paste_icon, out, req.icon, layout.icon, req.accent_color)
        label = req.heading or (req.body[0] if req.body else "")
        asset = await self.timeline.register_generated(
            req.project_id, out, AssetKind.IMAGE, "ffmpeg", f"カード「{label[:20]}」"
        )
        return AssetRead.model_validate(asset)


def _paste_icon(path: Path, name: str, box: tuple[int, int, int], color: str) -> None:
    """カードの PNG に絵記号を重ねる（同じファイルに書き戻す）。"""
    from PIL import Image

    from video_automation.services.rendering.pictogram import render_icon

    x, y, size = box
    with Image.open(path) as im:
        card = im.convert("RGBA")
    card.alpha_composite(render_icon(name, size, color), (x, y))
    card.save(path)
