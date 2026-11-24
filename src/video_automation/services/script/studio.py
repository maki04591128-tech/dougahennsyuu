"""対話型台本作成（仕様 §17、Phase 8）。

固定セリフ（ロック済み・ユーザー入力 USER・手修正 EDITED）は AI の生成・再生成で変更しない。
変更のたびに版（スナップショット）を残し、過去の版の確認・差分・復元ができる。
"""

import re
import time
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from video_automation.app.logging import get_logger
from video_automation.database.database import Database
from video_automation.database.models import (
    GenerationRun,
    Project,
    Script,
    ScriptCharacter,
    ScriptProposal,
    ScriptScene,
    ScriptSegment,
    ScriptVersionRow,
)
from video_automation.schemas.provider import ChatMessage, TaskType
from video_automation.schemas.script_studio import (
    CandidatesRead,
    CharacterIn,
    CharacterRead,
    DialogueInput,
    GenerationRunRead,
    GenerationSource,
    ProposalRead,
    RegenerateRequest,
    SceneRead,
    ScriptDetail,
    ScriptGenerateRequest,
    ScriptIssue,
    ScriptSettings,
    ScriptStudioCreate,
    ScriptUpdate,
    ScriptValidateRequest,
    ScriptValidation,
    TurnChange,
    TurnRead,
    TurnUpdate,
    VersionDiff,
    VersionRead,
    VoiceSettings,
)
from video_automation.schemas.subtitle import ScriptMode
from video_automation.services.llm_tasks.gateway import LLMError, LLMGateway
from video_automation.services.script import prompts as P

if TYPE_CHECKING:
    from video_automation.services.voice.characters import VoiceCharacterService

log = get_logger("script")
_SPACE = re.compile(r"[\s「」『』（）()、。・…！？!?,.\-]")


class ScriptStudioError(Exception):
    code = "script_error"
    public_message = True


class ScriptNotFoundError(ScriptStudioError):
    code = "script_not_found"


class ProposalStateError(ScriptStudioError):
    code = "proposal_state"


class FixedTurnError(ScriptStudioError):
    """固定セリフ（ロック・ユーザー入力・手修正）を AI で変えようとした。"""

    code = "turn_is_fixed"


def estimated_sec(text: str, chars_per_sec: float) -> float:
    n = len(_SPACE.sub("", text))
    return round(n / chars_per_sec + 0.3, 2)


def is_fixed(seg: ScriptSegment) -> bool:
    return seg.locked or seg.generation_source in (GenerationSource.USER, GenerationSource.EDITED)


def _norm(text: str) -> str:
    return _SPACE.sub("", text)


class ScriptStudio:
    def __init__(
        self, db: Database, llm: LLMGateway, voice_characters: "VoiceCharacterService | None" = None
    ) -> None:
        self.db = db
        self.llm = llm
        self.voice_characters = voice_characters

    async def _with_voice_style(self, chars: list[CharacterIn]) -> list[CharacterIn]:
        """声を選んだキャラクターの口調が空なら、その声の口調（登録済みのもの）で埋める。
        ユーザーが書いた口調は変えない。"""
        if self.voice_characters is None:
            return chars
        out = []
        for c in chars:
            if not c.speaking_style.strip() and c.voice.style_id is not None:
                _, style = await self.voice_characters.for_style(c.voice.style_id)
                if style is not None and style.text():
                    c = c.model_copy(update={"speaking_style": style.text()[:500]})
            out.append(c)
        return out

    # ---------- 読み出し ----------
    async def _script(self, s: AsyncSession, script_id: str) -> Script:
        sc = await s.get(Script, script_id)
        if sc is None:
            raise ScriptNotFoundError(f"script {script_id}")
        return sc

    @staticmethod
    async def _rows(
        s: AsyncSession, script_id: str
    ) -> tuple[list[ScriptCharacter], list[ScriptScene], list[ScriptSegment]]:
        chars = list(
            (
                await s.execute(
                    select(ScriptCharacter)
                    .where(ScriptCharacter.script_id == script_id)
                    .order_by(ScriptCharacter.sequence)
                )
            ).scalars()
        )
        scenes = list(
            (
                await s.execute(
                    select(ScriptScene)
                    .where(ScriptScene.script_id == script_id)
                    .order_by(ScriptScene.sequence)
                )
            ).scalars()
        )
        segs = list(
            (
                await s.execute(
                    select(ScriptSegment)
                    .where(ScriptSegment.script_id == script_id)
                    .order_by(ScriptSegment.sequence)
                )
            ).scalars()
        )
        return chars, scenes, segs

    @staticmethod
    def _settings(sc: Script) -> ScriptSettings:
        return ScriptSettings.model_validate(sc.settings or {})

    @staticmethod
    def _char_read(c: ScriptCharacter) -> CharacterRead:
        voice = VoiceSettings.model_validate(c.voice or {})
        voice.style_id = c.voice_style_id
        return CharacterRead(
            id=c.id,
            name=c.name,
            role=c.role,
            personality=c.personality,
            speaking_style=c.speaking_style,
            is_user=c.is_user,
            voice=voice,
            color=c.color,
        )

    async def detail(self, script_id: str, notes: list[str] | None = None) -> ScriptDetail:
        async with self.db.session_factory() as s:
            sc = await self._script(s, script_id)
            chars, scenes, segs = await self._rows(s, script_id)
        st = self._settings(sc)
        turns = [
            TurnRead.model_validate(x).model_copy(
                update={"estimated_sec": estimated_sec(x.text, st.chars_per_sec)}
            )
            for x in segs
        ]
        return ScriptDetail(
            id=sc.id,
            project_id=sc.project_id,
            title=sc.title,
            mode=sc.mode,
            status=sc.status,
            version=sc.version,
            settings=st,
            characters=[self._char_read(c) for c in chars],
            scenes=[SceneRead.model_validate(x) for x in scenes],
            segments=turns,
            estimated_total_sec=round(sum(t.estimated_sec for t in turns), 1),
            notes=notes or [],
        )

    # ---------- 版（履歴） ----------
    async def _snapshot(self, s: AsyncSession, sc: Script) -> dict[str, Any]:
        chars, scenes, segs = await self._rows(s, sc.id)
        return {
            "title": sc.title,
            "mode": sc.mode,
            "settings": sc.settings or {},
            "characters": [
                {
                    "id": c.id,
                    "sequence": c.sequence,
                    "name": c.name,
                    "role": c.role,
                    "personality": c.personality,
                    "speaking_style": c.speaking_style,
                    "is_user": c.is_user,
                    "voice_style_id": c.voice_style_id,
                    "voice": c.voice,
                    "color": c.color,
                }
                for c in chars
            ],
            "scenes": [
                {
                    "sequence": x.sequence,
                    "purpose": x.purpose,
                    "estimated_duration_sec": x.estimated_duration_sec,
                }
                for x in scenes
            ],
            "turns": [
                {
                    "id": x.id,
                    "scene_index": x.scene_index,
                    "sequence": x.sequence,
                    "speaker": x.speaker,
                    "text": x.text,
                    "generation_source": x.generation_source,
                    "reply_to_segment_id": x.reply_to_segment_id,
                    "locked": x.locked,
                    "voice_style_id": x.voice_style_id,
                    "original_text": x.original_text,
                }
                for x in segs
            ],
        }

    async def _commit(
        self, s: AsyncSession, sc: Script, reason: str, *, first: bool = False
    ) -> None:
        await s.flush()
        if not first:
            sc.version += 1
        s.add(
            ScriptVersionRow(
                script_id=sc.id,
                version=sc.version,
                reason=reason[:200],
                snapshot=await self._snapshot(s, sc),
            )
        )
        await s.commit()

    async def versions(self, script_id: str) -> list[VersionRead]:
        async with self.db.session_factory() as s:
            await self._script(s, script_id)
            rows = (
                await s.execute(
                    select(ScriptVersionRow)
                    .where(ScriptVersionRow.script_id == script_id)
                    .order_by(ScriptVersionRow.version)
                )
            ).scalars()
            return [
                VersionRead(
                    version=r.version,
                    reason=r.reason,
                    turn_count=len(r.snapshot.get("turns", [])),
                    created_at=r.created_at,
                )
                for r in rows
            ]

    async def _version(self, s: AsyncSession, script_id: str, version: int) -> dict[str, Any]:
        row = (
            await s.execute(
                select(ScriptVersionRow).where(
                    ScriptVersionRow.script_id == script_id, ScriptVersionRow.version == version
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise ScriptNotFoundError(f"version {version}")
        return row.snapshot

    async def diff(
        self, script_id: str, from_version: int, to_version: int | None = None
    ) -> VersionDiff:
        async with self.db.session_factory() as s:
            sc = await self._script(s, script_id)
            to_version = to_version or sc.version
            a = await self._version(s, script_id, from_version)
            b = await self._version(s, script_id, to_version)
        return diff_snapshots(a, b, from_version, to_version)

    async def _apply_snapshot(self, s: AsyncSession, sc: Script, snap: dict[str, Any]) -> None:
        script_id = sc.id
        _, _, segs = await self._rows(s, script_id)
        audio = {x.id: (x.text, x.speaker, x.audio_file_id) for x in segs}
        await s.execute(delete(ScriptSegment).where(ScriptSegment.script_id == script_id))
        await s.execute(delete(ScriptScene).where(ScriptScene.script_id == script_id))
        await s.execute(delete(ScriptCharacter).where(ScriptCharacter.script_id == script_id))
        sc.title, sc.mode, sc.settings = snap["title"], snap["mode"], snap["settings"]
        for c in snap["characters"]:
            s.add(ScriptCharacter(script_id=script_id, **c))
        for x in snap["scenes"]:
            s.add(ScriptScene(script_id=script_id, **x))
        for t in snap["turns"]:
            prev = audio.get(t["id"])
            keep = prev is not None and prev[0] == t["text"] and prev[1] == t["speaker"]
            s.add(
                ScriptSegment(
                    script_id=script_id, audio_file_id=prev[2] if keep and prev else None, **t
                )
            )

    async def restore(self, script_id: str, version: int) -> ScriptDetail:
        async with self.db.session_factory() as s:
            sc = await self._script(s, script_id)
            snap = await self._version(s, script_id, version)
            await self._apply_snapshot(s, sc, snap)
            await self._commit(s, sc, f"版{version}に戻す")
        return await self.detail(script_id)

    # ---------- 承認待ちの修正案（LLM 由来） ----------
    async def _finalize(
        self,
        s: AsyncSession,
        sc: Script,
        reason: str,
        propose: bool,
        notes: list[str] | None = None,
    ) -> str | None:
        """propose=True なら変更を保存せず修正案として残す（戻り値は修正案 ID）。"""
        if not propose:
            await self._commit(s, sc, reason)
            return None
        await s.flush()
        snap = await self._snapshot(s, sc)
        sid, pid, base = sc.id, sc.project_id, sc.version
        await s.rollback()
        row = ScriptProposal(
            script_id=sid,
            project_id=pid,
            base_version=base,
            reason=reason[:200],
            snapshot=snap,
            notes=notes or None,
        )
        s.add(row)
        await s.commit()
        return row.id

    async def proposal(self, proposal_id: str) -> ProposalRead:
        async with self.db.session_factory() as s:
            row = await s.get(ScriptProposal, proposal_id)
            if row is None:
                raise ScriptNotFoundError(f"proposal {proposal_id}")
            sc = await self._script(s, row.script_id)
            current = await self._snapshot(s, sc)
            out = ProposalRead.model_validate(row)
            out.diff = diff_snapshots(current, row.snapshot, sc.version, sc.version + 1)
            return out

    async def proposals(self, project_id: str, status: str | None = None) -> list[ProposalRead]:
        async with self.db.session_factory() as s:
            q = select(ScriptProposal).where(ScriptProposal.project_id == project_id)
            if status:
                q = q.where(ScriptProposal.status == status)
            ids = [r.id for r in (await s.execute(q.order_by(ScriptProposal.created_at))).scalars()]
        return [await self.proposal(i) for i in ids]

    async def decide_proposal(self, proposal_id: str, approve: bool) -> ProposalRead:
        async with self.db.session_factory() as s:
            row = await s.get(ScriptProposal, proposal_id)
            if row is None:
                raise ScriptNotFoundError(f"proposal {proposal_id}")
            if row.status != "PENDING_APPROVAL":
                raise ProposalStateError(f"proposal is {row.status}")
            if not approve:
                row.status = "REJECTED"
                await s.commit()
                return await self.proposal(proposal_id)
            sc = await self._script(s, row.script_id)
            if sc.version != row.base_version:  # 作成後に台本が変わった
                row.status = "STALE"
                await s.commit()
                raise ProposalStateError(
                    f"script changed since the proposal (v{row.base_version} -> v{sc.version})"
                )
            await self._apply_snapshot(s, sc, row.snapshot)
            row.status = "APPLIED"
            await self._commit(s, sc, f"承認: {row.reason}")
        return await self.proposal(proposal_id)

    async def runs(self, script_id: str) -> list[GenerationRunRead]:
        async with self.db.session_factory() as s:
            rows = (
                await s.execute(
                    select(GenerationRun)
                    .where(GenerationRun.script_id == script_id)
                    .order_by(GenerationRun.created_at)
                )
            ).scalars()
            return [GenerationRunRead.model_validate(r) for r in rows]

    # ---------- 作成・設定 ----------
    @staticmethod
    def _char_row(
        script_id: str, i: int, c: CharacterIn, cid: str | None = None
    ) -> ScriptCharacter:
        voice = c.voice.model_dump(exclude={"style_id"}, exclude_none=True)
        row = ScriptCharacter(
            script_id=script_id,
            sequence=i,
            name=c.name,
            role=c.role,
            personality=c.personality,
            speaking_style=c.speaking_style,
            is_user=c.is_user,
            voice_style_id=c.voice.style_id,
            voice=voice or None,
            color=c.color,
        )
        if cid is not None:
            row.id = cid
        return row

    async def create(self, req: ScriptStudioCreate) -> ScriptDetail:
        async with self.db.session_factory() as s:
            if await s.get(Project, req.project_id) is None:
                raise ScriptNotFoundError(f"project {req.project_id}")
            sc = Script(
                project_id=req.project_id,
                title=req.title,
                mode=req.mode.value,
                settings=req.settings.model_dump(mode="json"),
            )
            s.add(sc)
            await s.flush()
            for i, c in enumerate(await self._with_voice_style(req.characters)):
                s.add(self._char_row(sc.id, i, c))
            await self._commit(s, sc, "作成", first=True)
            sid = sc.id
        return await self.detail(sid)

    async def update(self, script_id: str, req: ScriptUpdate) -> ScriptDetail:
        async with self.db.session_factory() as s:
            sc = await self._script(s, script_id)
            changed = []
            if req.title is not None:
                sc.title = req.title
                changed.append("タイトル")
            if req.mode is not None and req.mode.value != sc.mode:
                sc.mode = req.mode.value
                changed.append(f"モード→{req.mode.value}")
            if req.settings is not None:
                sc.settings = req.settings.model_dump(mode="json")
                changed.append("設定")
            if req.characters is not None:
                names = [c.name for c in req.characters]
                if len(names) != len(set(names)):
                    raise ScriptStudioError("character names must be unique")
                mode = ScriptMode(sc.mode)
                if mode == ScriptMode.USER_ONE_SIDE and sum(c.is_user for c in req.characters) != 1:
                    raise ScriptStudioError("exactly one character must be is_user=true")
                old = {c.name: c.id for c in (await self._rows(s, script_id))[0]}
                await s.execute(
                    delete(ScriptCharacter).where(ScriptCharacter.script_id == script_id)
                )
                for i, c in enumerate(await self._with_voice_style(req.characters)):
                    s.add(self._char_row(script_id, i, c, old.get(c.name)))
                    # 音声設定をそのキャラクターのセリフへ反映（音声は作り直しが必要）
                    if c.voice.style_id is not None:
                        for seg in (await self._rows(s, script_id))[2]:
                            if seg.speaker == c.name and seg.voice_style_id != c.voice.style_id:
                                seg.voice_style_id = c.voice.style_id
                                seg.audio_file_id, seg.start_frame, seg.end_frame = None, None, None
                changed.append("キャラクター")
            if ScriptMode(sc.mode) == ScriptMode.USER_ONE_SIDE:
                chars = (await self._rows(s, script_id))[0] if req.characters is None else None
                if chars is not None and sum(c.is_user for c in chars) != 1:
                    raise ScriptStudioError("set exactly one user character for USER_ONE_SIDE")
            await self._commit(s, sc, "設定変更: " + "・".join(changed or ["なし"]))
        return await self.detail(script_id)

    # ---------- ユーザーのセリフ（モードB）・手修正 ----------
    async def add_dialogue(
        self, script_id: str, req: DialogueInput, propose: bool = False
    ) -> ScriptDetail | ProposalRead:
        async with self.db.session_factory() as s:
            sc = await self._script(s, script_id)
            chars, _, segs = await self._rows(s, script_id)
            by_name = {c.name: c for c in chars}
            user = next((c for c in chars if c.is_user), None)
            if req.replace:
                await s.execute(delete(ScriptSegment).where(ScriptSegment.script_id == script_id))
                segs = []
            seq = len(segs)
            scene = segs[-1].scene_index if segs else 0
            for line in req.lines:
                name = line.speaker or (user.name if user else None)
                if name is None:
                    raise ScriptStudioError("speaker is required (no user character set)")
                if name not in by_name:
                    raise ScriptStudioError(f"unknown speaker: {name}")
                scene = line.scene_index if line.scene_index is not None else scene
                s.add(
                    ScriptSegment(
                        script_id=script_id,
                        scene_index=scene,
                        sequence=seq,
                        speaker=name,
                        text=line.text,
                        original_text=line.text,
                        generation_source=GenerationSource.USER.value,
                        locked=True,
                        voice_style_id=by_name[name].voice_style_id,
                    )
                )
                seq += 1
            pid = await self._finalize(
                s,
                sc,
                f"セリフ登録 {len(req.lines)}件" + ("（置き換え）" if req.replace else ""),
                propose,
            )
        if pid:
            return await self.proposal(pid)
        return await self.detail(script_id)

    async def _segment(self, s: AsyncSession, script_id: str, turn_id: str) -> ScriptSegment:
        seg = await s.get(ScriptSegment, turn_id)
        if seg is None or seg.script_id != script_id:
            raise ScriptNotFoundError(f"turn {turn_id}")
        return seg

    async def update_turn(self, script_id: str, turn_id: str, req: TurnUpdate) -> ScriptDetail:
        """ユーザーによる手修正（ユーザー操作として版に記録する）。"""
        async with self.db.session_factory() as s:
            sc = await self._script(s, script_id)
            chars, _, segs = await self._rows(s, script_id)
            seg = await self._segment(s, script_id, turn_id)
            names = {c.name: c for c in chars}
            what = []
            if req.text is not None and req.text != seg.text:
                seg.text = req.text
                if seg.generation_source == GenerationSource.USER:
                    seg.original_text = req.text
                else:
                    seg.generation_source = GenerationSource.EDITED.value
                seg.audio_file_id, seg.start_frame, seg.end_frame = None, None, None
                what.append("本文")
            if req.speaker is not None and req.speaker != seg.speaker:
                if names and req.speaker not in names:
                    raise ScriptStudioError(f"unknown speaker: {req.speaker}")
                seg.speaker = req.speaker
                if req.speaker in names:
                    seg.voice_style_id = names[req.speaker].voice_style_id
                seg.audio_file_id, seg.start_frame, seg.end_frame = None, None, None
                what.append("話者")
            if req.scene_index is not None:
                seg.scene_index = req.scene_index
                what.append("シーン")
            if req.sequence is not None and req.sequence != seg.sequence:
                order = [x for x in segs if x.id != seg.id]
                order.insert(min(req.sequence, len(order)), seg)
                for i, x in enumerate(order):
                    x.sequence = i
                what.append("順序")
            await self._commit(s, sc, f"手修正（{'・'.join(what) or 'なし'}）: {seg.speaker}")
        return await self.detail(script_id)

    async def delete_turn(self, script_id: str, turn_id: str) -> ScriptDetail:
        async with self.db.session_factory() as s:
            sc = await self._script(s, script_id)
            seg = await self._segment(s, script_id, turn_id)
            await s.delete(seg)
            await s.flush()
            for i, x in enumerate((await self._rows(s, script_id))[2]):
                x.sequence = i
                if x.reply_to_segment_id == turn_id:
                    x.reply_to_segment_id = None
            await self._commit(s, sc, f"セリフ削除: {seg.speaker}「{seg.text[:20]}」")
        return await self.detail(script_id)

    async def lock(self, script_id: str, turn_id: str, locked: bool) -> ScriptDetail:
        async with self.db.session_factory() as s:
            sc = await self._script(s, script_id)
            seg = await self._segment(s, script_id, turn_id)
            seg.locked = locked
            await self._commit(
                s, sc, ("固定" if locked else "固定解除") + f": {seg.speaker}「{seg.text[:20]}」"
            )
        return await self.detail(script_id)

    # ---------- AI 生成 ----------
    async def _run(
        self, s: AsyncSession, script_id: str, kind: str, version: str, inp: dict[str, Any]
    ) -> GenerationRun:
        run = GenerationRun(
            script_id=script_id,
            kind=kind,
            prompt_version=version,
            input_snapshot=inp,
            status="RUNNING",
        )
        s.add(run)
        await s.commit()
        return run

    async def _finish(self, run_id: str, **values: Any) -> None:
        async with self.db.session_factory() as s:
            run = await s.get(GenerationRun, run_id)
            if run is not None:
                for k, v in values.items():
                    setattr(run, k, v)
                await s.commit()

    async def generate(
        self, script_id: str, req: ScriptGenerateRequest, propose: bool = False
    ) -> ScriptDetail | ProposalRead:
        """propose=True（LLM 由来）なら結果を承認待ちの修正案にする。"""
        async with self.db.session_factory() as s:
            mode = ScriptMode((await self._script(s, script_id)).mode)
        if mode == ScriptMode.FULL_AI:
            return await self._generate_full(script_id, req, propose)
        if mode == ScriptMode.USER_ONE_SIDE:
            return await self._generate_counterpart(script_id, req, propose)
        raise ScriptStudioError("MANUAL scripts are not generated (change the mode first)")

    async def _generate_full(
        self, script_id: str, req: ScriptGenerateRequest, propose: bool
    ) -> ScriptDetail | ProposalRead:
        async with self.db.session_factory() as s:
            sc = await self._script(s, script_id)
            chars, _, segs = await self._rows(s, script_id)
            st = self._settings(sc)
            if not chars:
                raise ScriptStudioError("add characters before generating")
            fixed = [x for x in segs if is_fixed(x)]
            cdata = [self._char_read(c).model_dump() for c in chars]
            inp: dict[str, Any] = {
                "settings": st.model_dump(mode="json"),
                "characters": cdata,
                "fixed": [{"speaker": x.speaker, "text": x.text} for x in fixed],
                "instruction": req.instruction,
            }
            run = await self._run(s, script_id, "full", P.FULL_VERSION, inp)
        total_chars = int((st.target_duration_sec or 90) * st.chars_per_sec)
        user = [
            P.settings_text(inp["settings"]) or "テーマ: （未指定。一般的な紹介動画）",
            "キャラクター:\n" + P.characters_text(cdata),
            f"台本全体のセリフの合計は約{total_chars}文字（目標尺に合わせる）。",
        ]
        if fixed:
            user.append(
                "次の固定セリフは一字一句変えず、この順序で台本に含めてください:\n"
                + "\n".join(f"- {x.speaker}: {x.text}" for x in fixed)
            )
        if req.instruction:
            user.append(f"追加の指示: {req.instruction}")
        user.append("speaker には上のキャラクター名だけを使い、指定のJSONで出力してください。")
        msgs = [
            ChatMessage(role="system", content=P.COMMON),
            ChatMessage(role="user", content="\n\n".join(user)),
        ]
        t0 = time.perf_counter()
        try:
            res = await self.llm.complete_json(
                TaskType.SCRIPT_GENERATION, msgs, P.FULL_SCHEMA, model_key=req.model_key
            )
        except LLMError as exc:
            await self._finish(
                run.id,
                status="FAILED",
                error=str(exc)[:1000],
                duration_ms=(time.perf_counter() - t0) * 1000,
            )
            raise
        names = {c["name"] for c in cdata}
        unknown = sorted({t["speaker"] for sc_ in res.data["scenes"] for t in sc_["turns"]} - names)
        notes = list(res.data.get("notes") or [])
        if unknown:
            notes.append(f"設定に無い話者を除外しました: {', '.join(unknown)}")
        async with self.db.session_factory() as s:
            sc = await self._script(s, script_id)
            chars, _, segs = await self._rows(s, script_id)
            voice = {c.name: c.voice_style_id for c in chars}
            fixed_rows = [x for x in segs if is_fixed(x)]
            for x in segs:
                if not is_fixed(x):
                    await s.delete(x)
            await s.execute(delete(ScriptScene).where(ScriptScene.script_id == script_id))
            await s.flush()
            order: list[ScriptSegment] = []
            remaining = list(fixed_rows)
            for i, scene in enumerate(res.data["scenes"]):
                s.add(
                    ScriptScene(
                        script_id=script_id,
                        sequence=i,
                        purpose=scene["purpose"][:500],
                        estimated_duration_sec=scene.get("estimated_duration_sec"),
                    )
                )
                for t in scene["turns"]:
                    if t["speaker"] not in names:
                        continue
                    match = next((f for f in remaining if _norm(f.text) == _norm(t["text"])), None)
                    if match is not None:  # 固定セリフはモデルの写しではなく元の行を使う
                        remaining.remove(match)
                        match.scene_index = i
                        order.append(match)
                        continue
                    seg = ScriptSegment(
                        script_id=script_id,
                        scene_index=i,
                        sequence=0,
                        speaker=t["speaker"],
                        text=t["text"][:2000],
                        generation_source=GenerationSource.AI.value,
                        voice_style_id=voice.get(t["speaker"]),
                        generation_run_id=run.id,
                    )
                    s.add(seg)
                    order.append(seg)
            if remaining:  # モデルが落とした固定セリフは元の相対位置に戻す
                notes.append(
                    f"固定セリフ {len(remaining)} 件をモデルが含めなかったため元の位置に戻しました"
                )
                for f in remaining:
                    pos = round(fixed_rows.index(f) / max(len(fixed_rows), 1) * len(order))
                    order.insert(min(pos, len(order)), f)
            for i, x in enumerate(order):
                x.sequence = i
            sc.status = "DRAFT"
            prop = await self._finalize(
                s, sc, f"AIで全台本を生成（{res.model_key}）", propose, notes
            )
        await self._finish(
            run.id,
            status="SUCCEEDED",
            model_key=res.model_key,
            output_snapshot={**res.data, "notes": notes},
            duration_ms=res.duration_ms,
        )
        if prop:
            return await self.proposal(prop)
        return await self.detail(script_id, notes)

    async def _generate_counterpart(
        self, script_id: str, req: ScriptGenerateRequest, propose: bool
    ) -> ScriptDetail | ProposalRead:
        async with self.db.session_factory() as s:
            sc = await self._script(s, script_id)
            chars, _, segs = await self._rows(s, script_id)
            st = self._settings(sc)
            user = next((c for c in chars if c.is_user), None)
            ai = [c for c in chars if not c.is_user]
            if user is None or not ai:
                raise ScriptStudioError("USER_ONE_SIDE needs a user character and an AI character")
            ai_char = ai[0]
            user_lines = [x for x in segs if x.speaker == user.name]
            if not user_lines:
                raise ScriptStudioError("register the user's lines first")
            # 既に固定の応答（手修正・ロック）がある入力セリフには新しく作らない
            answered = {
                x.reply_to_segment_id for x in segs if x.reply_to_segment_id and is_fixed(x)
            }
            ids = {f"L{i + 1}": x for i, x in enumerate(user_lines)}
            targets = [k for k, x in ids.items() if x.id not in answered]
            cdata = [self._char_read(c).model_dump() for c in chars]
            inp: dict[str, Any] = {
                "settings": st.model_dump(mode="json"),
                "characters": cdata,
                "lines": {k: x.text for k, x in ids.items()},
                "targets": targets,
                "instruction": req.instruction,
            }
            run = await self._run(s, script_id, "counterpart", P.COUNTERPART_VERSION, inp)
        convo = []
        for x in segs:
            if x.speaker == user.name:
                key = next(k for k, v in ids.items() if v.id == x.id)
                convo.append(f"[{key}] {x.speaker}: {x.text}")
            elif is_fixed(x):
                convo.append(f"（固定）{x.speaker}: {x.text}")
        user_total = sum(len(x.text) for x in user_lines)
        body = [
            P.settings_text(inp["settings"]),
            "キャラクター:\n" + P.characters_text(cdata),
            f"ユーザーが「{user.name}」を担当し、あなたは「{ai_char.name}」のセリフだけを作ります。"
            f"ユーザーのセリフは一字一句変えません。",
            f"応答方針: {st.response_policy.value}。{ai_char.name}の発話量の目安は合計約"
            f"{int(user_total * st.ai_ratio)}文字。"
            f"1つの入力セリフへの応答は最大{st.replies_per_input}発話。",
            "会話（[L番号] がユーザーの入力セリフ）:\n" + "\n".join(convo),
            "応答が必要な入力セリフ: "
            + ", ".join(targets)
            + "。応答は入力セリフを聞いた後の返事なので、after には返事をする相手の入力セリフの"
            "番号を書きます（L1 を聞いて返すなら after は L1。L1 より前の前置きだけ L0）。"
            "相づち・質問・説明・まとめを必要に応じて入れ、"
            "文脈が足りない点は推測で断定せず notes に書きます。",
        ]
        if req.instruction:
            body.append(f"追加の指示: {req.instruction}")
        msgs = [
            ChatMessage(role="system", content=P.COMMON),
            ChatMessage(role="user", content="\n\n".join(b for b in body if b)),
        ]
        t0 = time.perf_counter()
        try:
            res = await self.llm.complete_json(
                TaskType.SCRIPT_GENERATION, msgs, P.COUNTERPART_SCHEMA, model_key=req.model_key
            )
        except LLMError as exc:
            await self._finish(
                run.id,
                status="FAILED",
                error=str(exc)[:1000],
                duration_ms=(time.perf_counter() - t0) * 1000,
            )
            raise
        notes = list(res.data.get("notes") or [])
        replies: dict[str, list[str]] = {}
        for r in res.data["replies"]:
            key = r["after"].strip().upper()
            if key != "L0" and key not in targets:
                notes.append(f"対象外の位置 {r['after']} への応答を除外しました")
                continue
            replies.setdefault(key, []).extend(t["text"][:2000] for t in r["turns"])
        async with self.db.session_factory() as s:
            sc = await self._script(s, script_id)
            chars, _, segs = await self._rows(s, script_id)
            user = next(c for c in chars if c.is_user)
            ai_char = next(c for c in chars if not c.is_user)
            for x in segs:  # 固定でない AI の応答は作り直す
                if x.speaker != user.name and not is_fixed(x):
                    await s.delete(x)
            await s.flush()
            kept = [x for x in segs if x.speaker == user.name or is_fixed(x)]
            ids = {
                f"L{i + 1}": x for i, x in enumerate([x for x in kept if x.speaker == user.name])
            }
            order: list[ScriptSegment] = []

            def add(texts: list[str], after: ScriptSegment | None, scene: int) -> None:
                for t in texts[: st.replies_per_input * 3]:
                    seg = ScriptSegment(
                        script_id=script_id,
                        scene_index=scene,
                        sequence=0,
                        speaker=ai_char.name,
                        text=t,
                        generation_source=GenerationSource.AI.value,
                        reply_to_segment_id=after.id if after else None,
                        voice_style_id=ai_char.voice_style_id,
                        generation_run_id=run.id,
                    )
                    s.add(seg)
                    order.append(seg)

            add(replies.get("L0", []), None, kept[0].scene_index if kept else 0)
            for x in kept:
                order.append(x)
                lk = next((k for k, v in ids.items() if v.id == x.id), None)
                if lk:
                    add(replies.get(lk, []), x, x.scene_index)
            # 固定の応答をそれぞれの入力セリフの直後へ（並びを保つ）
            for i, x in enumerate(order):
                x.sequence = i
            prop = await self._finalize(
                s, sc, f"AIで{ai_char.name}のセリフを生成（{res.model_key}）", propose, notes
            )
        await self._finish(
            run.id,
            status="SUCCEEDED",
            model_key=res.model_key,
            output_snapshot={**res.data, "notes": notes},
            duration_ms=res.duration_ms,
        )
        if prop:
            return await self.proposal(prop)
        return await self.detail(script_id, notes)

    async def regenerate(
        self, script_id: str, turn_id: str, req: RegenerateRequest
    ) -> CandidatesRead | ScriptDetail:
        async with self.db.session_factory() as s:
            sc = await self._script(s, script_id)
            chars, _, segs = await self._rows(s, script_id)
            seg = await self._segment(s, script_id, turn_id)
            if is_fixed(seg):
                raise FixedTurnError(
                    "fixed turns (locked / user input / edited) are not regenerated"
                )
            st = self._settings(sc)
            idx = next(i for i, x in enumerate(segs) if x.id == seg.id)
            before = segs[max(0, idx - 6) : idx]
            after = segs[idx + 1 : idx + 4]
            reply_to = next((x for x in segs if x.id == seg.reply_to_segment_id), None)
            cdata = [self._char_read(c).model_dump() for c in chars]
            inp: dict[str, Any] = {
                "turn_id": turn_id,
                "current": seg.text,
                "instruction": req.instruction,
                "candidates": req.candidates,
            }
            run = await self._run(s, script_id, "regenerate", P.REGEN_VERSION, inp)
        body = [
            P.settings_text(st.model_dump(mode="json")),
            "キャラクター:\n" + P.characters_text(cdata),
            "直前の会話:\n" + "\n".join(f"{x.speaker}: {x.text}" for x in before),
            f"書き直す発話（{seg.speaker}）: {seg.text}",
            ("この発話が応答している入力: " + reply_to.text) if reply_to else "",
            "直後の会話:\n" + "\n".join(f"{x.speaker}: {x.text}" for x in after),
            f"{seg.speaker}の口調で、前後とつながる別の言い方を{req.candidates}案作ってください。"
            "前後の発話は変えません。",
            f"指示: {req.instruction}" if req.instruction else "",
        ]
        msgs = [
            ChatMessage(role="system", content=P.COMMON),
            ChatMessage(role="user", content="\n\n".join(b for b in body if b)),
        ]
        t0 = time.perf_counter()
        try:
            res = await self.llm.complete_json(
                TaskType.SCRIPT_GENERATION,
                msgs,
                P.regen_schema(req.candidates),
                model_key=req.model_key,
            )
        except LLMError as exc:
            await self._finish(
                run.id,
                status="FAILED",
                error=str(exc)[:1000],
                duration_ms=(time.perf_counter() - t0) * 1000,
            )
            raise
        cands = [c.strip()[:2000] for c in res.data["candidates"] if c.strip()][: req.candidates]
        await self._finish(
            run.id,
            status="SUCCEEDED",
            model_key=res.model_key,
            output_snapshot=res.data,
            duration_ms=res.duration_ms,
        )
        if req.candidates > 1:
            return CandidatesRead(turn_id=turn_id, candidates=cands, generation_run_id=run.id)
        return await self._apply_ai_text(
            script_id, turn_id, cands[0], run.id, f"再生成（{res.model_key}）"
        )

    async def apply_candidate(self, script_id: str, turn_id: str, text: str) -> ScriptDetail:
        return await self._apply_ai_text(script_id, turn_id, text, None, "候補を採用")

    async def _apply_ai_text(
        self, script_id: str, turn_id: str, text: str, run_id: str | None, reason: str
    ) -> ScriptDetail:
        async with self.db.session_factory() as s:
            sc = await self._script(s, script_id)
            seg = await self._segment(s, script_id, turn_id)
            if is_fixed(seg):
                raise FixedTurnError("fixed turns are not replaced by AI text")
            seg.text = text
            seg.generation_source = GenerationSource.AI.value
            if run_id is not None:
                seg.generation_run_id = run_id
            seg.audio_file_id, seg.start_frame, seg.end_frame = None, None, None
            await self._commit(s, sc, f"{reason}: {seg.speaker}")
        return await self.detail(script_id)

    # ---------- 検証 ----------
    async def validate(self, script_id: str, req: ScriptValidateRequest) -> ScriptValidation:
        d = await self.detail(script_id)
        issues: list[ScriptIssue] = []
        names = {c.name for c in d.characters}
        st = d.settings
        if not d.segments:
            issues.append(ScriptIssue(level="error", code="empty", message="セリフがありません"))
        seen: dict[str, str] = {}
        async with self.db.session_factory() as s:
            rows = {x.id: x for x in (await self._rows(s, script_id))[2]}
        for t in d.segments:
            if names and t.speaker not in names:
                issues.append(
                    ScriptIssue(
                        level="error",
                        code="unknown_speaker",
                        message=f"設定に無い話者: {t.speaker}",
                        turn_id=t.id,
                    )
                )
            if len(t.text) > 120:
                issues.append(
                    ScriptIssue(
                        level="warning",
                        code="long_line",
                        message=f"1発話が長い（{len(t.text)}文字）",
                        turn_id=t.id,
                    )
                )
            key = _norm(t.text)
            if key and key in seen:
                issues.append(
                    ScriptIssue(
                        level="warning",
                        code="duplicate",
                        message="同じセリフが重複しています",
                        turn_id=t.id,
                    )
                )
            seen.setdefault(key, t.id)
            row = rows[t.id]
            if (
                row.generation_source == GenerationSource.USER
                and row.original_text is not None
                and row.original_text != row.text
            ):
                issues.append(
                    ScriptIssue(
                        level="error",
                        code="user_line_changed",
                        message="ユーザー入力のセリフが原文と異なります",
                        turn_id=t.id,
                    )
                )
            if t.reply_to_segment_id and t.reply_to_segment_id not in rows:
                issues.append(
                    ScriptIssue(
                        level="warning",
                        code="broken_reply",
                        message="応答先のセリフがありません",
                        turn_id=t.id,
                    )
                )
            if t.voice_style_id is None:
                issues.append(
                    ScriptIssue(
                        level="info", code="no_voice", message="音声の話者が未設定", turn_id=t.id
                    )
                )
        for kw in st.must_keywords:
            if not any(kw in t.text for t in d.segments):
                issues.append(
                    ScriptIssue(
                        level="warning",
                        code="missing_keyword",
                        message=f"必須キーワードがありません: {kw}",
                    )
                )
        for ng in st.banned:
            for t in d.segments:
                if ng and ng in t.text:
                    issues.append(
                        ScriptIssue(
                            level="error",
                            code="banned_word",
                            message=f"禁止事項を含みます: {ng}",
                            turn_id=t.id,
                        )
                    )
        if st.target_duration_sec and d.segments:
            ratio = d.estimated_total_sec / st.target_duration_sec
            if not 0.8 <= ratio <= 1.2:
                issues.append(
                    ScriptIssue(
                        level="warning",
                        code="duration",
                        message=f"推定尺 {d.estimated_total_sec:.0f}秒が"
                        f"目標 {st.target_duration_sec}秒から"
                        f"{(ratio - 1) * 100:+.0f}%ずれています",
                    )
                )
        ratio_ai = None
        user_c = next((c for c in d.characters if c.is_user), None)
        if d.mode == ScriptMode.USER_ONE_SIDE and user_c:
            u = sum(len(t.text) for t in d.segments if t.speaker == user_c.name)
            a = sum(len(t.text) for t in d.segments if t.speaker != user_c.name)
            ratio_ai = round(a / u, 2) if u else None
            if ratio_ai is not None and not 0.5 * st.ai_ratio <= ratio_ai <= 2 * st.ai_ratio:
                issues.append(
                    ScriptIssue(
                        level="info",
                        code="ai_ratio",
                        message=f"AI側の分量 {ratio_ai} 倍（設定 {st.ai_ratio} 倍）",
                    )
                )
        if req.use_llm and d.segments:
            idmap = {f"T{i + 1}": t.id for i, t in enumerate(d.segments)}
            convo = "\n".join(f"[T{i + 1}] {t.speaker}: {t.text}" for i, t in enumerate(d.segments))
            msgs = [
                ChatMessage(
                    role="system", content=P.COMMON + " 台本の校閲者として問題点だけを指摘します。"
                ),
                ChatMessage(
                    role="user",
                    content=(
                        "キャラクター:\n"
                        + P.characters_text([c.model_dump() for c in d.characters])
                        + "\n\n台本:\n"
                        + convo
                        + "\n\n会話のつながり・口調の一貫性・事実の矛盾・重複を確認し、"
                        "問題があれば turn に [T番号] を付けて issues に書きます。無ければ空配列。"
                    ),
                ),
            ]
            res = await self.llm.complete_json(
                TaskType.PROOFREADING, msgs, P.CHECK_SCHEMA, model_key=req.model_key
            )
            for it in res.data["issues"][:30]:
                tid = idmap.get((it.get("turn") or "").strip("[] ").upper())
                issues.append(
                    ScriptIssue(
                        level=it["level"],
                        code="llm_review",
                        message=f"[{res.model_key}] {it['message'][:300]}",
                        turn_id=tid,
                    )
                )
        return ScriptValidation(
            valid=not any(i.level == "error" for i in issues),
            issues=issues,
            estimated_total_sec=d.estimated_total_sec,
            target_duration_sec=st.target_duration_sec,
            ratio_ai_to_user=ratio_ai,
        )


def diff_snapshots(a: dict[str, Any], b: dict[str, Any], va: int, vb: int) -> VersionDiff:
    ta = {t["id"]: t for t in a.get("turns", [])}
    tb = {t["id"]: t for t in b.get("turns", [])}
    changes: list[TurnChange] = []
    order_a = [t["id"] for t in a.get("turns", []) if t["id"] in tb]
    order_b = [t["id"] for t in b.get("turns", []) if t["id"] in ta]
    for t in b.get("turns", []):
        if t["id"] not in ta:
            changes.append(
                TurnChange(kind="added", turn_id=t["id"], speaker=t["speaker"], after=t["text"])
            )
        else:
            old = ta[t["id"]]
            if old["text"] != t["text"] or old["speaker"] != t["speaker"]:
                changes.append(
                    TurnChange(
                        kind="changed",
                        turn_id=t["id"],
                        speaker=t["speaker"],
                        before=old["text"],
                        after=t["text"],
                    )
                )
            elif order_a.index(t["id"]) != order_b.index(t["id"]):
                changes.append(
                    TurnChange(
                        kind="moved",
                        turn_id=t["id"],
                        speaker=t["speaker"],
                        before=str(order_a.index(t["id"])),
                        after=str(order_b.index(t["id"])),
                    )
                )
    for t in a.get("turns", []):
        if t["id"] not in tb:
            changes.append(
                TurnChange(kind="removed", turn_id=t["id"], speaker=t["speaker"], before=t["text"])
            )
    return VersionDiff(
        from_version=va,
        to_version=vb,
        changes=changes,
        settings_changed=a.get("settings") != b.get("settings") or a.get("mode") != b.get("mode"),
        characters_changed=a.get("characters") != b.get("characters"),
    )
