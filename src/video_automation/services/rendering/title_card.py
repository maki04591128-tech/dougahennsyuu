"""Blender で文字アニメーションのクリップ（透過付き）を作り、素材として登録する (P5-02)。

Blender の PNG 連番 → FFmpeg で透過付き MOV（PNG コーデック）に変換して取り込む。
複雑な演出は Phase 7 で拡張する。
"""

import tempfile
import time
from pathlib import Path
from uuid import uuid4

from video_automation.app.logging import get_logger
from video_automation.app.settings import Settings
from video_automation.database.database import Database
from video_automation.database.models import Project
from video_automation.integrations.blender.runner import Blender
from video_automation.integrations.ffmpeg.runner import FFmpeg
from video_automation.schemas.timeline import AssetKind, AssetRead, TitleCardRequest
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

log = get_logger("title_card")


def _rgba(hex_color: str) -> list[float]:
    h = hex_color.lstrip("#")
    return [int(h[i : i + 2], 16) / 255 for i in (0, 2, 4)] + [1.0]


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

    def font_path(self) -> str | None:
        if self.settings.title_font_path:
            return self.settings.title_font_path
        return next((f for f in FONT_CANDIDATES if Path(f).is_file()), None)

    async def create(self, req: TitleCardRequest) -> AssetRead:
        async with self.db.session_factory() as s:
            p = await s.get(Project, req.project_id)
            if p is None:
                raise TimelineNotFoundError(f"project {req.project_id}")
            w, h, fps_num, fps_den = p.width, p.height, p.fps_num, p.fps_den
        fps = round(fps_num / fps_den)
        frames = max(1, round(req.duration_sec * fps_num / fps_den))
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
                    "frames": frames,
                    "font_path": self.font_path(),
                    "text_color": _rgba(req.text_color),
                    "bg_color": [0, 0, 0, 0],
                    "output_dir": tmp,
                    "animation": req.animation,
                },
            )
            if not result.get("frames"):
                raise RuntimeError("blender produced no frames")
            log.info(
                "title_card_rendered",
                engine=result.get("engine"),
                samples=result.get("samples"),
                frames=result["frames"],
                sec=round(time.perf_counter() - t0, 1),
            )
            await self.ffmpeg.run(
                [
                    "-framerate",
                    f"{fps_num}/{fps_den}",
                    "-i",
                    str(Path(tmp) / "frame_%04d.png"),
                    "-c:v",
                    "png",
                    "-pix_fmt",
                    "rgba",
                    str(out),
                ]
            )
        asset = await self.timeline.register_generated(
            req.project_id, out, AssetKind.VIDEO, "blender", f"タイトル「{req.text[:20]}」"
        )
        return AssetRead.model_validate(asset)
