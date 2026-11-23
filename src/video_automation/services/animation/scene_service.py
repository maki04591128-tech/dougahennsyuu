"""シーン作成（mcp_visual の create_scene）。

背景＋タイトル＋カメラ演出＋入りの切替を1回の編集命令で配置する。
"""

from typing import Any
from uuid import uuid4

from sqlalchemy import select

from video_automation.app.settings import Settings
from video_automation.database.database import Database
from video_automation.database.models import Project, Track
from video_automation.integrations.ffmpeg.runner import FFmpeg
from video_automation.schemas.animation import SceneRequest
from video_automation.schemas.timeline import (
    AddClip,
    AddTrack,
    AssetKind,
    Batch,
    ClipTransform,
    CommandOrigin,
    CommandRequest,
    SetClipAnimation,
    TitleCardRequest,
    TrackKind,
)
from video_automation.services.rendering.title_card import TitleCardService
from video_automation.services.timeline.timeline_service import (
    CommandError,
    TimelineNotFoundError,
    TimelineService,
)

BG_TRACK, TITLE_TRACK = "シーン背景", "テロップ"


class SceneService:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        ffmpeg: FFmpeg,
        timeline: TimelineService,
        titles: TitleCardService,
    ) -> None:
        self.settings = settings
        self.db = db
        self.ffmpeg = ffmpeg
        self.timeline = timeline
        self.titles = titles

    async def _color_asset(self, project_id: str, color: str, w: int, h: int) -> str:
        out_dir = self.settings.data_dir / "projects" / project_id / "assets"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"bg_{color.lstrip('#').lower()}_{w}x{h}.png"
        existing = {a.path: a for a in (await self.timeline.get_timeline(project_id)).assets}
        rel = path.relative_to(self.settings.data_dir).as_posix()
        if rel in existing:
            return existing[rel].id
        await self.ffmpeg.run(
            [
                "-f",
                "lavfi",
                "-i",
                f"color=c=0x{color.lstrip('#')}:s={w}x{h}",
                "-frames:v",
                "1",
                str(path),
            ]
        )
        a = await self.timeline.register_generated(
            project_id, path, AssetKind.IMAGE, "generated", f"背景 {color}"
        )
        return a.id

    async def create(self, project_id: str, req: SceneRequest) -> dict[str, Any]:
        async with self.db.session_factory() as s:
            p = await s.get(Project, project_id)
            if p is None:
                raise TimelineNotFoundError(f"project {project_id}")
            tracks = list(
                (await s.execute(select(Track).where(Track.project_id == project_id))).scalars()
            )
        frames = max(1, round(req.duration_sec * p.fps_num / p.fps_den))
        tl = await self.timeline.get_timeline(project_id)
        bg_id = req.background_asset_id
        if bg_id:
            if not any(a.id == bg_id for a in tl.assets):
                raise TimelineNotFoundError(f"asset {bg_id}")
        else:
            bg_id = await self._color_asset(project_id, req.background_color, p.width, p.height)
        created = [bg_id]
        title_id = None
        if req.title:
            title = await self.titles.create(
                TitleCardRequest(
                    project_id=project_id,
                    text=req.title,
                    duration_sec=req.duration_sec,
                    animation=req.title_animation,
                    position=req.title_position,
                    box=req.title_position == "lower_third",
                )
            )
            title_id = title.id
            created.append(title_id)

        by_name = {t.name: t for t in tracks if t.kind == TrackKind.VIDEO.value}
        cmds: list[Any] = []

        def track(name: str) -> str:
            if name in by_name:
                return by_name[name].id
            tid = str(uuid4())
            cmds.append(AddTrack(kind=TrackKind.VIDEO, name=name, track_id=tid))
            by_name[name] = Track(id=tid, name=name, kind=TrackKind.VIDEO.value)
            return tid

        bg_track = track(BG_TRACK)
        bg_clip = str(uuid4())
        cmds.append(
            AddClip(
                track_id=bg_track,
                asset_id=bg_id,
                start_frame=req.start_frame,
                duration_frames=frames,
                clip_id=bg_clip,
                transform=ClipTransform(),
            )
        )
        if req.camera_preset:
            cmds.append(SetClipAnimation(clip_id=bg_clip, preset=req.camera_preset))
        if req.transition:
            cmds.append(SetClipAnimation(clip_id=bg_clip, transition=req.transition))
        if title_id:
            title_track = track(TITLE_TRACK)
            if title_track == bg_track:
                raise CommandError("title track must differ from background track")
            title_len = min(frames, max(1, round(req.duration_sec * p.fps_num / p.fps_den)))
            cmds.append(
                AddClip(
                    track_id=title_track,
                    asset_id=title_id,
                    start_frame=req.start_frame,
                    duration_frames=title_len,
                )
            )
        row = await self.timeline.submit(
            project_id,
            CommandRequest(
                command=Batch(commands=cmds),
                origin=CommandOrigin(req.origin),
                description=f"シーン「{req.title or req.background_color}」を配置",
            ),
        )
        return {"command": row.model_dump(mode="json"), "assets": created}
