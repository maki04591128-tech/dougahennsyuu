"""立ち絵のサービス: パックの取り込み・一覧と、台本の立ち絵をタイムラインに置く。

- 台本のキャラクターに立ち絵のパック（tachie_pack）があれば、書き出しのときに自動で
  立ち絵の動画を作り、「立ち絵」トラックに置く（既定の演出）。台本・音声・パックが
  変わっていなければ作り直さない。
- 2 人までを左右に置き、どちらも画面の中央を向くよう、元の絵の向きに合わせて左右反転する。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import wave
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from video_automation.app.logging import get_logger
from video_automation.app.settings import Settings
from video_automation.database.database import Database
from video_automation.database.models import (
    AudioFile,
    Project,
    Script,
    ScriptCharacter,
    ScriptSegment,
)
from video_automation.integrations.ffmpeg.runner import FFmpeg
from video_automation.schemas.timeline import (
    AddClip,
    AddTrack,
    AssetKind,
    Batch,
    ClipTransform,
    CommandOrigin,
    CommandRequest,
    DeleteClip,
    FitMode,
    MoveTrack,
    TrackKind,
)
from video_automation.services.characters import motion
from video_automation.services.characters.overlay import Cast, layout, render_overlay
from video_automation.services.characters.pack import (
    PackError,
    build_pack,
    load_pack,
    psd_from,
    read_meta,
)
from video_automation.services.timeline.timeline_service import (
    CommandError,
    TimelineService,
    asset_frames,
)

log = get_logger("characters")
TRACK = "立ち絵"
TEXT_TRACK = "演出・文字"  # 演出の文字トラック（direction の TRACKS と同じ名前）
OVERLAY_VERSION = 3  # 統合版 F1・Q8（まばたき・うなずき）、F2・F4（登場・視線）
ProgressFn = Callable[[float], Awaitable[None]]


class CharacterService:
    def __init__(
        self, settings: Settings, db: Database, timeline: TimelineService, ffmpeg: FFmpeg
    ) -> None:
        self.settings, self.db, self.timeline, self.ffmpeg = settings, db, timeline, ffmpeg

    @property
    def packs_dir(self) -> Path:
        return self.settings.data_dir / "characters"

    # ---------- パック ----------
    def packs(self) -> list[dict[str, Any]]:
        out = []
        if self.packs_dir.is_dir():
            for d in sorted(self.packs_dir.iterdir()):
                if (d / "pack.json").is_file():
                    m = read_meta(d)
                    out.append(
                        {
                            "id": d.name,
                            "name": m.get("name", d.name),
                            "author": m.get("author", ""),
                            "credit": m.get("credit", ""),
                            "faces": m.get("faces", "front"),
                            "expressions": sorted(m.get("expressions", {})),
                            "poses": sorted(m.get("poses", {})),
                        }
                    )
        return out

    async def import_pack(self, path: Path, pack_id: str | None = None) -> dict[str, Any]:
        """PSD（または PSD を含む zip）から立ち絵パックを作る。"""
        if not path.is_absolute() or not self.timeline._allowed(path):
            raise CommandError("path is outside the allowed import directories")
        if not path.is_file():
            raise CommandError("file not found")
        work = self.packs_dir / f"_work_{uuid4().hex[:8]}"
        try:
            psd = await asyncio.to_thread(psd_from, path, work)
            tmp = self.packs_dir / f"_build_{uuid4().hex[:8]}"
            meta = await asyncio.to_thread(build_pack, psd, tmp)
            if (work / "readme.txt").is_file():
                shutil.copy(work / "readme.txt", tmp / "readme.txt")
            pid = pack_id or _slug(meta["name"]) or tmp.name
            dst = self.packs_dir / pid
            if dst.exists():
                shutil.rmtree(dst)
            tmp.rename(dst)
        except PackError as exc:
            raise CommandError(str(exc)) from exc
        finally:
            shutil.rmtree(work, ignore_errors=True)
        log.info("tachie_pack_imported", pack=pid, name=meta["name"])
        return next(p for p in self.packs() if p["id"] == pid)

    def credits(self, pack_ids: list[str]) -> list[str]:
        out = []
        for pid in pack_ids:
            d = self.packs_dir / pid
            if (d / "pack.json").is_file():
                c = read_meta(d).get("credit")
                if c and c not in out:
                    out.append(c)
        return out

    async def project_credits(self, project_id: str) -> list[str]:
        """プロジェクトの台本で使っている立ち絵のクレジット（「立ち絵：作者」）。"""
        async with self.db.session_factory() as s:
            packs = (
                await s.execute(
                    select(ScriptCharacter.tachie_pack)
                    .join(Script, Script.id == ScriptCharacter.script_id)
                    .where(
                        Script.project_id == project_id, ScriptCharacter.tachie_pack.is_not(None)
                    )
                )
            ).scalars()
            ids = list(dict.fromkeys(x for x in packs if x))
        return self.credits(ids)

    # ---------- 台本の立ち絵 ----------
    async def _cast(self, script_id: str) -> tuple[Project, list[ScriptCharacter], list[Any]]:
        async with self.db.session_factory() as s:
            sc = await s.get(Script, script_id)
            if sc is None:
                raise CommandError(f"script {script_id} not found")
            p = await s.get(Project, sc.project_id)
            assert p is not None
            chars = list(
                (
                    await s.execute(
                        select(ScriptCharacter)
                        .where(ScriptCharacter.script_id == script_id)
                        .order_by(ScriptCharacter.sequence)
                    )
                ).scalars()
            )
            segs = list(
                (
                    await s.execute(
                        select(ScriptSegment, AudioFile)
                        .outerjoin(AudioFile, AudioFile.id == ScriptSegment.audio_file_id)
                        .where(ScriptSegment.script_id == script_id)
                        .order_by(ScriptSegment.sequence)
                    )
                ).all()
            )
        cast = [c for c in chars if c.tachie_pack and (self.packs_dir / c.tachie_pack).is_dir()]
        return p, cast[:2], segs

    async def apply(
        self,
        script_id: str,
        progress: ProgressFn | None = None,
        origin: CommandOrigin = CommandOrigin.USER,
    ) -> dict[str, Any]:
        """台本の立ち絵の動画を作り（変わっていなければそのまま）、「立ち絵」トラックに置く。"""
        p, cast, segs = await self._cast(script_id)
        tl = await self.timeline.get_timeline(p.id)
        track = next((t for t in tl.tracks if t.name == TRACK and t.kind == TrackKind.VIDEO), None)
        old = [c for c in tl.clips if track and c.track_id == track.id]
        if not cast:
            if old:
                await self.timeline.submit(
                    p.id,
                    CommandRequest(
                        command=Batch(commands=[DeleteClip(clip_id=c.id) for c in old]),
                        origin=origin,
                        description="立ち絵を外す",
                    ),
                )
            return {"applied": False, "reason": "no character has a tachie pack"}
        lines = [
            (seg, af)
            for seg, af in segs
            if seg.start_frame is not None and seg.end_frame is not None and seg.speaker
        ]
        if not lines:
            raise CommandError("generate voices and synchronize subtitles first")
        fps = p.fps_num / p.fps_den
        total = max(tl.duration_frames, max(seg.end_frame for seg, _ in lines) + round(fps))
        # 終了画面（統合版 S9）の間は立ち絵を出さない（動画の枠・登録の円に重なる）
        es = (((p.design_manifest or {}).get("direction") or {}).get("outline") or {}).get(
            "end_screen"
        )
        if es:
            total = max(max(seg.end_frame for seg, _ in lines), round(float(es) * fps))
        sides = ["left", "right"] if len(cast) > 1 else ["right"]
        key_src = {
            "v": OVERLAY_VERSION,
            "size": [p.width, p.height, p.fps_num, p.fps_den],
            "total": total,
            "cast": [
                [
                    c.name,
                    c.tachie_pack,
                    sides[i],
                    read_meta(self.packs_dir / str(c.tachie_pack)).get("source"),
                ]
                for i, c in enumerate(cast)
            ],
            "lines": [
                [seg.speaker, seg.text, seg.start_frame, seg.end_frame, af.sha256 if af else None]
                for seg, af in lines
            ],
        }
        key = hashlib.sha256(json.dumps(key_src, ensure_ascii=False).encode()).hexdigest()[:16]
        name = f"立ち絵 {key}"
        assets = {a.id: a for a in tl.assets}
        if old and all(assets.get(c.asset_id) and assets[c.asset_id].name == name for c in old):
            return {"applied": True, "cached": True, "clip_id": old[0].id}
        out_dir = self.settings.data_dir / "projects" / p.id / "characters"
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"tachie_{key}.webm"
        ml = [
            motion.Line(
                seg.speaker,
                seg.text,
                seg.start_frame,
                seg.end_frame,
                await asyncio.to_thread(self._levels, af, fps, seg.end_frame - seg.start_frame),
                self._accent(af, fps),
            )
            for seg, af in lines
        ]
        h = round(p.height * layout(p.width, p.height)[1])
        casts = []
        for i, c in enumerate(cast):
            d = self.packs_dir / str(c.tachie_pack)
            faces = read_meta(d).get("faces", "front")
            mirror = (sides[i] == "left" and faces == "left") or (
                sides[i] == "right" and faces == "right"
            )
            pack = await asyncio.to_thread(load_pack, d, h, mirror)
            casts.append(Cast(pack, sides[i], motion.plan(c.name, ml, fps, i), i))
        loop = asyncio.get_running_loop()

        async def report(x: float) -> None:
            if progress:
                await progress(x)

        def prog(x: float) -> None:
            asyncio.run_coroutine_threadsafe(report(min(0.95, x)), loop)

        ff = self.ffmpeg.ffmpeg
        if not ff:
            raise CommandError("ffmpeg not found")
        bw, bh = await asyncio.to_thread(
            render_overlay, casts, total, fps, (p.width, p.height), out, ff, prog
        )
        asset = await self.timeline.register_generated(
            p.id, out, AssetKind.VIDEO, "characters", name
        )
        cmds: list[Any] = [DeleteClip(clip_id=c.id) for c in old]
        tid = track.id if track else str(uuid4())
        if track is None:
            cmds.append(AddTrack(kind=TrackKind.VIDEO, name=TRACK, track_id=tid))
            # 演出の文字（タイトル・テロップ）は立ち絵より上に重ねる
            txt = next((t for t in tl.tracks if t.name == TEXT_TRACK), None)
            if txt is not None:
                cmds.append(MoveTrack(track_id=txt.id, index=1000))
        cid = str(uuid4())
        cmds.append(
            AddClip(
                track_id=tid,
                asset_id=asset.id,
                start_frame=0,
                # 書き出した長さ（WebM の時刻の丸めで 1 フレーム短いことがある）
                duration_frames=min(total, asset_frames(asset, fps) or total),
                clip_id=cid,
                # 帯を画面の下に寄せる（帯の幅 = 画面の幅）
                transform=ClipTransform(
                    fit=FitMode.FIT, x=0.0, y=round((1 - bh / p.height) / 2, 6)
                ),
            )
        )
        await self.timeline.submit(
            p.id, CommandRequest(command=Batch(commands=cmds), origin=origin, description="立ち絵")
        )
        log.info("tachie_applied", project=p.id, frames=total, cast=[c.name for c in cast])
        return {"applied": True, "cached": False, "clip_id": cid, "asset_id": asset.id}

    @staticmethod
    def _accent(af: AudioFile | None, fps: float) -> int | None:
        """統合版 Q8: アクセントの最も強い句の頭（発話の始まりからのフレーム）。"""
        if af is None or not af.query:
            return None
        try:
            from video_automation.schemas.voice import AudioQuery
            from video_automation.services.voice.timing import strongest_phrase_sec

            sec = strongest_phrase_sec(AudioQuery.model_validate(af.query))
        except Exception:  # noqa: BLE001 - 読めない音声はうなずかない
            return None
        return None if sec is None else round(sec * fps)

    def _levels(self, af: AudioFile | None, fps: float, frames: int) -> list[int] | None:
        if af is None:
            return None
        if af.query:  # 統合版 S2: 音素（モーラ）から口の形を決める
            try:
                from video_automation.schemas.voice import AudioQuery

                return motion.viseme_levels(AudioQuery.model_validate(af.query), fps, frames)
            except ValueError:
                pass  # クエリが読めないときは音量から決める
        path = self.settings.data_dir / af.path
        try:
            import numpy as np

            with wave.open(str(path), "rb") as w:
                rate, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
                raw = w.readframes(w.getnframes())
            if width != 2:
                return None
            x = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
            if ch > 1:
                x = x.reshape(-1, ch).mean(axis=1)
            return motion.mouth_levels(x, rate, fps, frames)
        except (OSError, wave.Error, ValueError, ImportError):
            return None


def _slug(name: str) -> str:
    table = {"ずんだもん": "zundamon", "四国めたん": "shikoku_metan"}
    if name in table:
        return table[name]
    s = "".join(ch if ch.isascii() and (ch.isalnum() or ch in "-_") else "_" for ch in name)
    s = s.strip("_")
    return s or hashlib.sha1(name.encode()).hexdigest()[:10]
