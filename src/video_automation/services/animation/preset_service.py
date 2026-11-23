"""モーションプリセット管理（P7-06）。組み込みはコード、ユーザー定義は DB（motion_presets）。"""

from fractions import Fraction

from sqlalchemy import select

from video_automation.database.database import Database
from video_automation.database.models import Clip, MotionPresetRow, Project
from video_automation.schemas.animation import (
    ClipAnimation,
    MotionPreset,
    MotionPresetCreate,
    PresetKind,
    SaveClipAsPreset,
)
from video_automation.services.animation.motion import BUILTIN_BY_NAME, keyframes_to_preset
from video_automation.services.timeline.timeline_service import (
    CommandError,
    TimelineNotFoundError,
)


class PresetService:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def list(self, kind: PresetKind | None = None) -> list[MotionPreset]:
        async with self.db.session_factory() as s:
            rows = (
                await s.execute(select(MotionPresetRow).order_by(MotionPresetRow.name))
            ).scalars()
            user = [MotionPreset.model_validate(r.definition) for r in rows]
        out = list(BUILTIN_BY_NAME.values()) + user
        return [p for p in out if kind is None or p.kind == kind]

    async def create(self, req: MotionPresetCreate) -> MotionPreset:
        if req.name in BUILTIN_BY_NAME:
            raise CommandError(f"{req.name} is a built-in preset name")
        preset = MotionPreset(
            name=req.name,
            kind=PresetKind(req.kind),
            label=req.label,
            description=req.description,
            keyframes=req.keyframes,
        )
        async with self.db.session_factory() as s:
            row = await s.get(MotionPresetRow, req.name)
            data = preset.model_dump(mode="json")
            if row is None:
                s.add(
                    MotionPresetRow(
                        name=req.name,
                        kind=req.kind,
                        label=req.label,
                        description=req.description,
                        definition=data,
                    )
                )
            else:
                row.kind, row.label, row.description, row.definition = (
                    req.kind,
                    req.label,
                    req.description,
                    data,
                )
            await s.commit()
        return preset

    async def from_clip(self, req: SaveClipAsPreset) -> MotionPreset:
        async with self.db.session_factory() as s:
            clip = await s.get(Clip, req.clip_id)
            if clip is None:
                raise TimelineNotFoundError(f"clip {req.clip_id}")
            p = await s.get(Project, clip.project_id)
            assert p is not None
        anim = ClipAnimation.model_validate(clip.animation) if clip.animation else None
        if not anim or not anim.keyframes:
            raise CommandError("the clip has no keyframes")
        kfs = keyframes_to_preset(anim.keyframes, Fraction(p.fps_num, p.fps_den))
        return await self.create(
            MotionPresetCreate(name=req.name, kind=req.kind, label=req.label, keyframes=kfs)
        )

    async def delete(self, name: str) -> None:
        if name in BUILTIN_BY_NAME:
            raise CommandError("built-in presets cannot be deleted")
        async with self.db.session_factory() as s:
            row = await s.get(MotionPresetRow, name)
            if row is None:
                raise TimelineNotFoundError(f"preset {name}")
            await s.delete(row)
            await s.commit()
