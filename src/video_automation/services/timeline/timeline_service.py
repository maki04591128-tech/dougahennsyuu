"""タイムライン・クリップ・編集命令 (P5-03〜P5-05)。

実行順（仕様 §7）: Command → Schema検証(Pydantic) → 権限（origin=llm は承認待ち）
→ プロジェクト状態検証（参照・範囲・重なり） → 承認 → 実行 → 履歴保存。
履歴は影響した行の before/after スナップショットで保持し、Undo/Redo はそれを復元する。
"""

import hashlib
from fractions import Fraction
from math import floor
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from video_automation.app.logging import get_logger
from video_automation.app.settings import Settings
from video_automation.database.database import Database
from video_automation.database.models import (
    Asset,
    AssetLicenseRow,
    AudioFile,
    Clip,
    EditCommand,
    MotionPresetRow,
    Project,
    Script,
    ScriptSegment,
    Track,
)
from video_automation.integrations.ffmpeg.runner import FFmpeg, FFmpegError
from video_automation.schemas.animation import ClipAnimation, MotionPreset, PresetKind
from video_automation.schemas.asset_source import AddBgmRequest
from video_automation.schemas.timeline import (
    AddClip,
    AddTrack,
    AssetImportRequest,
    AssetKind,
    AssetRead,
    Batch,
    ClipRead,
    ClipTransform,
    CommandOrigin,
    CommandRead,
    CommandRequest,
    CommandStatus,
    DeleteClip,
    MoveClip,
    ProjectUpdate,
    SetClipAnimation,
    SetClipProps,
    SplitClip,
    TimelineRead,
    TrackKind,
    TrackRead,
    TrimClip,
    ValidationIssue,
    ValidationResult,
)
from video_automation.services.animation.motion import (  # noqa: E402
    BUILTIN_BY_NAME,
    merge_keyframes,
    resolve_preset,
    split_animation,
)

log = get_logger("timeline")
Snapshot = dict[str, dict[str, dict[str, Any] | None]]
_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}


class TimelineError(Exception):
    code = "timeline_error"
    public_message = True  # API でも本文を返している（入力・状態の問題のみ）


class CommandError(TimelineError):
    code = "invalid_command"


class TimelineNotFoundError(TimelineError):
    code = "not_found"


class ForbiddenPathError(TimelineError):
    code = "forbidden_path"


def _row(obj: Track | Clip) -> dict[str, Any]:
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}


def asset_frames(asset: Asset, fps: float) -> int | None:
    if asset.kind == AssetKind.IMAGE.value or asset.duration_sec is None:
        return None
    return max(1, floor(asset.duration_sec * fps + 1e-6))


class TimelineService:
    def __init__(self, settings: Settings, db: Database, ffmpeg: FFmpeg) -> None:
        self.settings = settings
        self.db = db
        self.ffmpeg = ffmpeg

    # ---------- 取得 ----------
    async def _project(self, s: AsyncSession, project_id: str) -> Project:
        p = await s.get(Project, project_id)
        if p is None:
            raise TimelineNotFoundError(f"project {project_id}")
        return p

    async def get_timeline(self, project_id: str) -> TimelineRead:
        async with self.db.session_factory() as s:
            p = await self._project(s, project_id)
            tracks = (
                (
                    await s.execute(
                        select(Track).where(Track.project_id == project_id).order_by(Track.index)
                    )
                )
                .scalars()
                .all()
            )
            clips = (
                (
                    await s.execute(
                        select(Clip).where(Clip.project_id == project_id).order_by(Clip.start_frame)
                    )
                )
                .scalars()
                .all()
            )
            assets = (
                (
                    await s.execute(
                        select(Asset)
                        .where(Asset.project_id == project_id)
                        .order_by(Asset.created_at)
                    )
                )
                .scalars()
                .all()
            )
            return TimelineRead(
                project_id=p.id,
                width=p.width,
                height=p.height,
                fps_num=p.fps_num,
                fps_den=p.fps_den,
                duration_frames=max((c.end_frame for c in clips), default=0),
                tracks=[TrackRead.model_validate(t) for t in tracks],
                clips=[ClipRead.model_validate(c) for c in clips],
                assets=[AssetRead.model_validate(a) for a in assets],
            )

    async def update_project(self, project_id: str, body: ProjectUpdate) -> Project:
        async with self.db.session_factory() as s:
            p = await self._project(s, project_id)
            for k, v in body.model_dump(exclude_none=True).items():
                setattr(p, k, v)
            await s.commit()
            return p

    # ---------- 素材 ----------
    def _allowed(self, path: Path) -> bool:
        roots = [self.settings.data_dir, *self.settings.import_dirs]
        rp = path.resolve()
        for root in roots:
            try:
                rp.relative_to(root.resolve())
                return True
            except ValueError:
                continue
        return False

    async def import_asset(self, req: AssetImportRequest) -> AssetRead:
        src = Path(req.path)
        if not src.is_absolute() or not self._allowed(src):
            raise ForbiddenPathError("path is outside the allowed import directories")
        if not src.is_file():
            raise TimelineNotFoundError("file not found")
        try:
            info = await self.ffmpeg.probe(src)
        except FFmpegError as exc:
            raise CommandError("unsupported media file") from exc
        if src.suffix.lower() in _IMAGE_EXT or info.is_image:
            kind = AssetKind.IMAGE
        elif info.has_video:
            kind = AssetKind.VIDEO
        elif info.has_audio:
            kind = AssetKind.AUDIO
        else:
            raise CommandError("no audio/video stream")
        async with self.db.session_factory() as s:
            await self._project(s, req.project_id)
        aid = str(uuid4())
        dst_dir = self.settings.data_dir / "projects" / req.project_id / "assets"
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / f"{aid}{src.suffix.lower()}"
        h = hashlib.sha256()
        with src.open("rb") as fi, dst.open("wb") as fo:  # 取り込み時点の内容を固定（コピー）
            while chunk := fi.read(1 << 20):
                h.update(chunk)
                fo.write(chunk)
        row = Asset(
            id=aid,
            project_id=req.project_id,
            kind=kind.value,
            name=req.name or src.name,
            path=dst.relative_to(self.settings.data_dir).as_posix(),
            sha256=h.hexdigest(),
            duration_sec=None if kind == AssetKind.IMAGE else info.duration_sec,
            width=info.width,
            height=info.height,
            fps=info.fps,
            has_audio=info.has_audio and kind != AssetKind.IMAGE,
            source="import",
        )
        async with self.db.session_factory() as s:
            s.add(row)
            await s.commit()
            return AssetRead.model_validate(row)

    async def register_generated(
        self,
        project_id: str,
        path: Path,
        kind: AssetKind,
        source: str,
        name: str,
        audio_file_id: str | None = None,
    ) -> Asset:
        """data_dir 内で生成したファイル（音声・Blender出力等）を素材として登録する。"""
        info = await self.ffmpeg.probe(path)
        h = hashlib.sha256(path.read_bytes()).hexdigest()
        row = Asset(
            project_id=project_id,
            kind=kind.value,
            name=name,
            path=path.resolve().relative_to(self.settings.data_dir.resolve()).as_posix(),
            sha256=h,
            duration_sec=None if kind == AssetKind.IMAGE else info.duration_sec,
            width=info.width,
            height=info.height,
            fps=None if kind == AssetKind.IMAGE else info.fps,
            has_audio=info.has_audio and kind != AssetKind.IMAGE,
            source=source,
            audio_file_id=audio_file_id,
        )
        async with self.db.session_factory() as s:
            s.add(row)
            await s.commit()
        return row

    def asset_path(self, asset: Asset) -> Path:
        base = self.settings.data_dir.resolve()
        p = (base / asset.path).resolve()
        if base not in p.parents:
            raise ForbiddenPathError("asset path escapes data directory")
        return p

    # ---------- 命令の適用（スナップショット） ----------
    async def _snapshot(self, s: AsyncSession, tracks: set[str], clips: set[str]) -> Snapshot:
        snap: Snapshot = {"tracks": {}, "clips": {}}
        for tid in tracks:
            t = await s.get(Track, tid)
            snap["tracks"][tid] = _row(t) if t else None
        for cid in clips:
            c = await s.get(Clip, cid)
            snap["clips"][cid] = _row(c) if c else None
        return snap

    async def _restore(self, s: AsyncSession, snap: Snapshot) -> None:
        for cid, data in snap["clips"].items():
            if data is None and (c := await s.get(Clip, cid)) is not None:
                await s.delete(c)
        await s.flush()
        for tid, data in snap["tracks"].items():
            t = await s.get(Track, tid)
            if data is None:
                if t is not None:
                    await s.delete(t)
            elif t is None:
                s.add(Track(**data))
            else:
                for k, v in data.items():
                    setattr(t, k, v)
        await s.flush()
        for cid, data in snap["clips"].items():
            if data is None:
                continue
            c = await s.get(Clip, cid)
            if c is None:
                s.add(Clip(**data))
            else:
                for k, v in data.items():
                    setattr(c, k, v)
        await s.flush()

    @staticmethod
    def _touched(cmd: Any) -> tuple[set[str], set[str]]:
        """命令が変更しうる track / clip の ID。"""
        cmds = cmd.commands if isinstance(cmd, Batch) else [cmd]
        tracks: set[str] = set()
        clips: set[str] = set()
        for c in cmds:
            if isinstance(c, AddTrack):
                c.track_id = c.track_id or str(uuid4())
                tracks.add(c.track_id)
            elif isinstance(c, AddClip):
                c.clip_id = c.clip_id or str(uuid4())
                clips.add(c.clip_id)
            elif isinstance(c, SplitClip):
                c.new_clip_id = c.new_clip_id or str(uuid4())
                clips |= {c.clip_id, c.new_clip_id}
            else:
                clips.add(c.clip_id)
        return tracks, clips

    async def _check_overlap(self, s: AsyncSession, clip: Clip) -> None:
        q = select(Clip).where(
            Clip.track_id == clip.track_id,
            Clip.id != clip.id,
            Clip.start_frame < clip.end_frame,
            Clip.end_frame > clip.start_frame,
        )
        other = (await s.execute(q)).scalars().first()
        if other is not None:
            raise CommandError(
                f"clip overlaps {other.id} on the same track "
                f"[{other.start_frame},{other.end_frame})"
            )

    async def _clip(self, s: AsyncSession, project_id: str, clip_id: str) -> Clip:
        c = await s.get(Clip, clip_id)
        if c is None or c.project_id != project_id:
            raise CommandError(f"clip {clip_id} not found")
        return c

    async def _track(self, s: AsyncSession, project_id: str, track_id: str) -> Track:
        t = await s.get(Track, track_id)
        if t is None or t.project_id != project_id:
            raise CommandError(f"track {track_id} not found")
        return t

    async def _check_source(self, s: AsyncSession, clip: Clip, fps: float) -> None:
        asset = await s.get(Asset, clip.asset_id)
        assert asset is not None
        total = asset_frames(asset, fps)
        if clip.end_frame <= clip.start_frame:
            raise CommandError("clip must have positive length")
        if clip.source_out_frame - clip.source_in_frame != clip.end_frame - clip.start_frame:
            raise CommandError("source range length must equal timeline length")
        if clip.source_in_frame < 0 or (total is not None and clip.source_out_frame > total):
            raise CommandError(f"source range exceeds asset length ({total} frames)")

    async def _apply_one(self, s: AsyncSession, p: Project, c: Any) -> None:
        fps = p.fps_num / p.fps_den
        if isinstance(c, AddTrack):
            n = (
                await s.execute(
                    select(func.count()).select_from(Track).where(Track.project_id == p.id)
                )
            ).scalar_one()
            s.add(
                Track(
                    id=c.track_id,
                    project_id=p.id,
                    kind=c.kind.value,
                    name=c.name or f"{c.kind.value}{n + 1}",
                    index=n,
                )
            )
            await s.flush()
            return
        if isinstance(c, AddClip):
            track = await self._track(s, p.id, c.track_id)
            asset = await s.get(Asset, c.asset_id)
            if asset is None or asset.project_id != p.id:
                raise CommandError(f"asset {c.asset_id} not found")
            if (track.kind == TrackKind.AUDIO.value) != (asset.kind == AssetKind.AUDIO.value):
                raise CommandError(f"{asset.kind} asset cannot be placed on {track.kind} track")
            total = asset_frames(asset, fps)
            length = c.duration_frames
            if length is None:
                if total is None:
                    raise CommandError("duration_frames is required for images")
                length = total - c.source_in_frame
            clip = Clip(
                id=c.clip_id,
                project_id=p.id,
                track_id=track.id,
                asset_id=asset.id,
                start_frame=c.start_frame,
                end_frame=c.start_frame + length,
                source_in_frame=c.source_in_frame,
                source_out_frame=c.source_in_frame + length,
                transform=c.transform.model_dump(mode="json"),
                volume=c.volume,
            )
            s.add(clip)
        else:
            clip = await self._clip(s, p.id, c.clip_id)
            if isinstance(c, MoveClip):
                if c.track_id and c.track_id != clip.track_id:
                    old, new = (
                        await self._track(s, p.id, clip.track_id),
                        await self._track(s, p.id, c.track_id),
                    )
                    if old.kind != new.kind:
                        raise CommandError("cannot move clip to a different kind of track")
                    clip.track_id = new.id
                length = clip.end_frame - clip.start_frame
                clip.start_frame, clip.end_frame = c.start_frame, c.start_frame + length
            elif isinstance(c, TrimClip):
                if c.start_frame is not None:
                    delta = c.start_frame - clip.start_frame
                    clip.start_frame, clip.source_in_frame = (
                        c.start_frame,
                        clip.source_in_frame + delta,
                    )
                if c.end_frame is not None:
                    delta = c.end_frame - clip.end_frame
                    clip.end_frame, clip.source_out_frame = (
                        c.end_frame,
                        clip.source_out_frame + delta,
                    )
            elif isinstance(c, SplitClip):
                if not clip.start_frame < c.at_frame < clip.end_frame:
                    raise CommandError("split point must be inside the clip")
                offset = c.at_frame - clip.start_frame
                second = Clip(
                    **{
                        **_row(clip),
                        "id": c.new_clip_id,
                        "start_frame": c.at_frame,
                        "source_in_frame": clip.source_in_frame + offset,
                        "fade_in_frames": 0,
                    }
                )
                if clip.animation:
                    first, rest = split_animation(
                        ClipAnimation.model_validate(clip.animation), offset, Fraction(fps)
                    )
                    clip.animation = first.model_dump(mode="json") if first else None
                    second.animation = rest.model_dump(mode="json") if rest else None
                clip.end_frame, clip.source_out_frame = (
                    c.at_frame,
                    clip.source_in_frame + offset,
                )
                clip.fade_out_frames = 0
                await s.flush()
                s.add(second)
                await s.flush()
                await self._check_source(s, second, fps)
            elif isinstance(c, DeleteClip):
                await s.delete(clip)
                await s.flush()
                return
            elif isinstance(c, SetClipProps):
                for k in ("volume", "fade_in_frames", "fade_out_frames"):
                    if (v := getattr(c, k)) is not None:
                        setattr(clip, k, v)
                if c.transform is not None:
                    clip.transform = c.transform.model_dump(mode="json")
            elif isinstance(c, SetClipAnimation):
                await self._set_animation(s, p, clip, c)
        await s.flush()
        await self._check_source(s, clip, fps)
        length = clip.end_frame - clip.start_frame
        if clip.fade_in_frames + clip.fade_out_frames > length:
            raise CommandError("fades are longer than the clip")
        await self._check_overlap(s, clip)

    async def _set_animation(
        self, s: AsyncSession, p: Project, clip: Clip, c: SetClipAnimation
    ) -> None:
        fps = Fraction(p.fps_num, p.fps_den)
        length = clip.end_frame - clip.start_frame
        if c.clear:
            clip.animation = None
            return
        cur = ClipAnimation.model_validate(clip.animation) if clip.animation else ClipAnimation()
        if c.animation is not None:
            cur = c.animation
        if c.preset:
            preset = await self._preset(s, c.preset)
            if preset.kind == PresetKind.TEXT:
                raise CommandError("text presets are applied when creating a title card")
            if preset.kind == PresetKind.TRANSITION:
                assert preset.transition is not None
                tran = preset.transition
                if c.duration_sec:
                    frames = max(1, round(c.duration_sec * float(fps)))
                    tran = tran.model_copy(update={"duration_frames": frames})
                cur = cur.model_copy(update={"transition_in": tran})
            else:
                kfs = resolve_preset(
                    preset, length, fps, duration_sec=c.duration_sec, intensity=c.intensity
                )
                merged = merge_keyframes(cur.keyframes, kfs) if c.merge else kfs
                cur = ClipAnimation(
                    keyframes=merged, transition_in=cur.transition_in, preset=preset.name
                )
        if c.transition is not None:
            cur = cur.model_copy(update={"transition_in": c.transition})
        if c.clear_transition:
            cur = cur.model_copy(update={"transition_in": None})
        if cur.transition_in and cur.transition_in.duration_frames > length:
            raise CommandError("transition is longer than the clip")
        if any(k.frame >= length for k in cur.keyframes):
            raise CommandError(f"keyframes must be within the clip (0..{length - 1})")
        cur = ClipAnimation.model_validate(cur.model_dump())
        clip.animation = None if cur.is_empty() else cur.model_dump(mode="json")

    async def _preset(self, s: AsyncSession, name: str) -> MotionPreset:
        if name in BUILTIN_BY_NAME:
            return BUILTIN_BY_NAME[name]
        row = await s.get(MotionPresetRow, name)
        if row is None:
            raise CommandError(f"unknown motion preset: {name}")
        return MotionPreset.model_validate(row.definition)

    async def _apply(self, s: AsyncSession, p: Project, cmd: Any) -> tuple[Snapshot, Snapshot]:
        tracks, clips = self._touched(cmd)
        before = await self._snapshot(s, tracks, clips)
        for c in cmd.commands if isinstance(cmd, Batch) else [cmd]:
            await self._apply_one(s, p, c)
        # バッチ全体の整合（同一バッチ内の重なり等）を最終状態で再確認
        for cid in clips:
            if (row := await s.get(Clip, cid)) is not None:
                await self._check_overlap(s, row)
        return before, await self._snapshot(s, tracks, clips)

    # ---------- 公開API ----------
    async def validate_command(self, project_id: str, req: CommandRequest) -> ValidationResult:
        async with self.db.session_factory() as s:
            p = await self._project(s, project_id)
            try:
                await self._apply(s, p, req.command.model_copy(deep=True))
            except CommandError as exc:
                return ValidationResult(
                    valid=False, issues=[ValidationIssue(level="error", message=str(exc))]
                )
            finally:
                await s.rollback()
        return ValidationResult(valid=True, issues=[])

    async def _next_seq(self, s: AsyncSession, project_id: str) -> int:
        q = select(func.max(EditCommand.sequence)).where(EditCommand.project_id == project_id)
        return int((await s.execute(q)).scalar() or 0) + 1

    async def _discard_redo(self, s: AsyncSession, project_id: str) -> None:
        q = select(EditCommand).where(
            EditCommand.project_id == project_id,
            EditCommand.status == CommandStatus.UNDONE.value,
        )
        for row in (await s.execute(q)).scalars():
            row.status = CommandStatus.DISCARDED.value

    async def submit(self, project_id: str, req: CommandRequest) -> CommandRead:
        async with self.db.session_factory() as s:
            p = await self._project(s, project_id)
            if req.idempotency_key:  # 同じキーの再送は同じ結果を返す（二重適用しない）
                q = select(EditCommand).where(
                    EditCommand.project_id == project_id,
                    EditCommand.idempotency_key == req.idempotency_key,
                )
                if (dup := (await s.execute(q)).scalars().first()) is not None:
                    return CommandRead.model_validate(dup)
            cmd = req.command.model_copy(deep=True)
            self._touched(cmd)  # ID を確定させてから保存する
            row = EditCommand(
                project_id=project_id,
                sequence=0,
                idempotency_key=req.idempotency_key,
                origin=req.origin.value,
                description=req.description,
                command=cmd.model_dump(mode="json"),
                status=CommandStatus.PENDING_APPROVAL.value,
            )
            if req.origin == CommandOrigin.LLM:
                # LLM 由来はタイムライン変更の承認が必要（仕様 §10）。検証だけ先に行う
                try:
                    preview = await self._apply(s, p, cmd)
                finally:
                    await s.rollback()
                # 承認画面の差分表示用（承認時は現在の状態で取り直す）
                row.before, row.after = preview
                s.add(row)
            else:
                row.before, row.after = await self._apply(s, p, cmd)
                row.status = CommandStatus.APPLIED.value
                row.sequence = await self._next_seq(s, project_id)
                await self._discard_redo(s, project_id)
                s.add(row)
            await s.commit()
            log.info("command", project_id=project_id, status=row.status, type=cmd.type)
            return CommandRead.model_validate(row)

    async def decide(self, project_id: str, command_id: str, approve: bool) -> CommandRead:
        async with self.db.session_factory() as s:
            p = await self._project(s, project_id)
            row = await s.get(EditCommand, command_id)
            if row is None or row.project_id != project_id:
                raise TimelineNotFoundError(f"command {command_id}")
            if row.status != CommandStatus.PENDING_APPROVAL.value:
                raise CommandError(f"command is {row.status}")
            if approve:
                cmd = CommandRequest.model_validate({"command": row.command}).command
                row.before, row.after = await self._apply(s, p, cmd)  # 現在の状態で再検証
                row.status = CommandStatus.APPLIED.value
                row.sequence = await self._next_seq(s, project_id)
                await self._discard_redo(s, project_id)
            else:
                row.status = CommandStatus.REJECTED.value
            await s.commit()
            return CommandRead.model_validate(row)

    async def undo(self, project_id: str) -> CommandRead:
        async with self.db.session_factory() as s:
            await self._project(s, project_id)
            q = (
                select(EditCommand)
                .where(
                    EditCommand.project_id == project_id,
                    EditCommand.status == CommandStatus.APPLIED.value,
                )
                .order_by(EditCommand.sequence.desc())
            )
            row = (await s.execute(q)).scalars().first()
            if row is None or row.before is None:
                raise CommandError("nothing to undo")
            await self._restore(s, row.before)
            row.status = CommandStatus.UNDONE.value
            await s.commit()
            return CommandRead.model_validate(row)

    async def redo(self, project_id: str) -> CommandRead:
        async with self.db.session_factory() as s:
            await self._project(s, project_id)
            q = (
                select(EditCommand)
                .where(
                    EditCommand.project_id == project_id,
                    EditCommand.status == CommandStatus.UNDONE.value,
                )
                .order_by(EditCommand.sequence)
            )
            row = (await s.execute(q)).scalars().first()
            if row is None or row.after is None:
                raise CommandError("nothing to redo")
            await self._restore(s, row.after)
            row.status = CommandStatus.APPLIED.value
            await s.commit()
            return CommandRead.model_validate(row)

    async def list_commands(
        self, project_id: str, status: CommandStatus | None = None
    ) -> list[CommandRead]:
        async with self.db.session_factory() as s:
            q = select(EditCommand).where(EditCommand.project_id == project_id)
            if status is not None:
                q = q.where(EditCommand.status == status.value)
            rows = (await s.execute(q.order_by(EditCommand.created_at))).scalars()
            return [CommandRead.model_validate(r) for r in rows]

    # ---------- 台本 → タイムライン ----------
    async def from_script(self, project_id: str, script_id: str) -> CommandRead:
        async with self.db.session_factory() as s:
            script = await s.get(Script, script_id)
            if script is None or script.project_id != project_id:
                raise TimelineNotFoundError(f"script {script_id}")
            segs = (
                (
                    await s.execute(
                        select(ScriptSegment)
                        .where(ScriptSegment.script_id == script_id)
                        .order_by(ScriptSegment.sequence)
                    )
                )
                .scalars()
                .all()
            )
            if any(x.audio_file_id is None or x.start_frame is None for x in segs):
                raise CommandError("generate voices and synchronize subtitles first")
            audio = {
                a.id: a
                for a in (
                    await s.execute(
                        select(AudioFile).where(AudioFile.id.in_([x.audio_file_id for x in segs]))
                    )
                ).scalars()
            }
            existing = {
                a.audio_file_id: a
                for a in (
                    await s.execute(
                        select(Asset).where(
                            Asset.project_id == project_id, Asset.audio_file_id.is_not(None)
                        )
                    )
                ).scalars()
            }
        commands: list[Any] = [AddTrack(kind=TrackKind.AUDIO, name="ナレーション")]
        track_id = str(uuid4())
        commands[0].track_id = track_id
        for seg in segs:
            af = audio[seg.audio_file_id or ""]
            asset = existing.get(af.id) or await self.register_generated(
                project_id,
                self.settings.data_dir / af.path,
                AssetKind.AUDIO,
                "voice",
                f"{seg.speaker or 'voice'}_{seg.sequence + 1:03d}.wav",
                audio_file_id=af.id,
            )
            commands.append(
                AddClip(
                    track_id=track_id,
                    asset_id=asset.id,
                    start_frame=seg.start_frame or 0,
                    transform=ClipTransform(),
                )
            )
        return await self.submit(
            project_id,
            CommandRequest(
                command=Batch(commands=commands),
                description=f"台本 {script_id} の音声を配置",
            ),
        )

    async def add_bgm(
        self, project_id: str, req: AddBgmRequest, origin: CommandOrigin = CommandOrigin.USER
    ) -> CommandRead:
        """BGM トラックを追加し、曲が短ければ繰り返して指定区間を埋める（端はフェード）。"""
        tl = await self.get_timeline(project_id)
        asset = next((a for a in tl.assets if a.id == req.asset_id), None)
        if asset is None:
            raise TimelineNotFoundError(f"asset {req.asset_id}")
        if asset.kind != AssetKind.AUDIO.value:
            raise CommandError("BGM must be an audio asset")
        fps = tl.fps_num / tl.fps_den
        length = int((asset.duration_sec or 0) * fps)
        if length <= 0:
            raise CommandError("audio asset has no duration")
        end = req.end_frame or tl.duration_frames or (req.start_frame + length)
        if end <= req.start_frame:
            raise CommandError("end_frame must be after start_frame")
        track_id = str(uuid4())
        commands: list[Any] = [AddTrack(kind=TrackKind.AUDIO, name="BGM", track_id=track_id)]
        clip_ids: list[str] = []
        pos = req.start_frame
        while pos < end:
            dur = min(length, end - pos)
            cid = str(uuid4())
            clip_ids.append(cid)
            commands.append(
                AddClip(
                    track_id=track_id,
                    asset_id=asset.id,
                    start_frame=pos,
                    duration_frames=dur,
                    clip_id=cid,
                    volume=req.volume,
                )
            )
            pos += dur
            if not req.loop:
                break
        commands.append(SetClipProps(clip_id=clip_ids[0], fade_in_frames=req.fade_in_frames))
        commands.append(SetClipProps(clip_id=clip_ids[-1], fade_out_frames=req.fade_out_frames))
        return await self.submit(
            project_id,
            CommandRequest(
                command=Batch(commands=commands),
                origin=origin,
                description=f"BGM「{asset.name}」を配置（音量 {req.volume:g}）",
            ),
        )

    # ---------- 検証 ----------
    async def validate_project(self, project_id: str) -> ValidationResult:
        tl = await self.get_timeline(project_id)
        issues: list[ValidationIssue] = []
        assets = {a.id: a for a in tl.assets}
        if not tl.clips:
            issues.append(ValidationIssue(level="error", message="timeline is empty"))
        for c in tl.clips:
            asset = assets.get(c.asset_id)
            if asset is None or not (self.settings.data_dir / asset.path).is_file():
                issues.append(
                    ValidationIssue(level="error", message="asset file missing", clip_id=c.id)
                )
        if not any(t.kind == TrackKind.VIDEO for t in tl.tracks if not t.muted):
            issues.append(
                ValidationIssue(level="warning", message="no video track (background color only)")
            )
        by_track: dict[str, list[ClipRead]] = {}
        for c in tl.clips:
            by_track.setdefault(c.track_id, []).append(c)
        for clips in by_track.values():
            clips.sort(key=lambda c: c.start_frame)
            for prev, nxt in zip(clips, clips[1:], strict=False):
                if prev.end_frame > nxt.start_frame:
                    issues.append(ValidationIssue(level="error", message="overlap", clip_id=nxt.id))
        used = {c.asset_id for c in tl.clips}
        async with self.db.session_factory() as s:
            lic = (
                (await s.execute(select(AssetLicenseRow).where(AssetLicenseRow.asset_id.in_(used))))
                .scalars()
                .all()
            )
        for row in lic:
            if row.verdict == "allowed":
                continue
            name = assets[row.asset_id].name if row.asset_id in assets else row.asset_id
            if row.acknowledged:
                msg = f"license needs care ({row.license_code}): {name}"
                issues.append(ValidationIssue(level="warning", message=msg))
            else:
                msg = f"license not acknowledged ({row.license_code}): {name}"
                issues.append(ValidationIssue(level="error", message=msg))
        return ValidationResult(valid=not any(i.level == "error" for i in issues), issues=issues)
