"""音声合成サービス (P3-01〜P3-08)。

- P3-01 Engine Capability: engine_manifest.supported_features / supported_devices / user_dict で判定
- P3-02 Speaker Manager: /speakers（talk / streaming_talk スタイルのみ合成対象）
- P3-03 Audio Generator: audio_query → 抑揚適用 → synthesis → WAV保存
- P3-04 Pronunciation Manager: accent_phrases 取得・アクセント変更後の mora 再計算
- P3-05 Prosody Controller: 全体の話速・音高・抑揚・音量・無音長（未対応機能は拒否）
- P3-06 Dictionary Manager: エンジンのユーザー辞書と DB を同期
- P3-07 Audio Preview: 保存せずに WAV を返す
- P3-08 Audio Repository: audio_files にパス・ハッシュ・長さ・クエリ・区間情報を保存
"""

import hashlib
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from sqlalchemy import select

from video_automation.app.logging import get_logger
from video_automation.app.settings import Settings
from video_automation.database.database import Database
from video_automation.database.models import AudioFile, Project, PronunciationEntry
from video_automation.integrations.voicevox.client import (
    VoicevoxClient,
    VoicevoxError,
    VoicevoxUnavailableError,
)
from video_automation.schemas.voice import (
    PROSODY_FEATURES,
    AccentPhrase,
    AccentPhrasesRequest,
    AudioFileRead,
    AudioQuery,
    DictionaryWordRequest,
    EngineFeature,
    EngineStatus,
    MoraEditRequest,
    PhraseTiming,
    Prosody,
    ProsodyRequest,
    VoiceGenerateItem,
    VoiceGenerateRequest,
    VoicePreviewRequest,
    VoiceSpeaker,
    VoiceStyle,
)
from video_automation.services.voice.expressive import find_spans, join_split_words, kana_of, shape
from video_automation.services.voice.reading import emphasis_terms, normalize_for_speech
from video_automation.services.voice.timing import (
    DEFAULT_FRAME_RATE,
    level_speech,
    phrase_timings,
    wav_info,
)

_KATA_WORD = re.compile(r"[ァ-ヴー]{3,}")

log = get_logger("voice")
ProgressFn = Callable[[float], Awaitable[None]]
_SMALL_KANA = set("ァィゥェォャュョヮ")


class VoiceError(Exception):
    code = "voice_error"


class EngineUnavailableError(VoiceError):
    code = "voice_engine_unavailable"


class UnsupportedFeatureError(VoiceError):
    code = "voice_feature_unsupported"


class NotFoundError(VoiceError):
    code = "not_found"


class InvalidVoiceRequestError(VoiceError):
    code = "invalid_voice_request"


def mora_count(katakana: str) -> int:
    return sum(1 for ch in katakana if ch not in _SMALL_KANA)


@dataclass
class _EngineInfo:
    status: EngineStatus
    frame_rate: float
    credit: str


class VoiceService:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.db = db
        self.clients: dict[str, VoicevoxClient] = {
            eid: VoicevoxClient(url, transport=transport)
            for eid, url in settings.voice_engine_urls().items()
        }
        self._info: dict[str, _EngineInfo] = {}
        self._style_ids: dict[str, set[int]] = {}  # エンジンごとの声（style_id）の一覧

    # ---------- P3-01 Engine Capability ----------
    async def _probe(self, engine_id: str) -> _EngineInfo:
        c = self.clients[engine_id]
        st = EngineStatus(engine_id=engine_id, base_url=c.base_url, available=False)
        try:
            st.version = await c.version()
        except VoicevoxError as exc:
            st.error = str(exc) or type(exc).__name__
            return _EngineInfo(st, DEFAULT_FRAME_RATE, "")
        frame_rate = DEFAULT_FRAME_RATE
        try:
            manifest = await c.engine_manifest()
            st.name = manifest.get("name")
            st.default_sampling_rate = manifest.get("default_sampling_rate")
            frame_rate = float(manifest.get("frame_rate") or DEFAULT_FRAME_RATE)
            for k, v in (manifest.get("supported_features") or {}).items():
                st.features[k] = bool(v.get("value")) if isinstance(v, dict) else bool(v)
        except VoicevoxError:
            st.error = "engine_manifest unavailable"  # 機能は全て未確認として扱う
        try:
            st.devices = await c.supported_devices()
        except VoicevoxError:
            pass
        try:
            await c.user_dict()
            st.features[EngineFeature.USER_DICT.value] = True
        except VoicevoxError:
            st.features[EngineFeature.USER_DICT.value] = False
        st.available = True
        name = st.name or ""
        credit = "VOICEVOX Nemo" if "nemo" in name.lower() else "VOICEVOX:{speaker}"
        if "nemo" in name.lower():
            st.terms_note = "生成音声の利用時は「VOICEVOX Nemo」のクレジット表記が必要"
        else:
            st.terms_note = "各キャラクターの利用規約に従う（クレジット VOICEVOX:キャラ名）"
        return _EngineInfo(st, frame_rate, credit)

    async def engines(self, refresh: bool = True) -> list[EngineStatus]:
        out = []
        for eid in self.clients:
            if refresh or eid not in self._info:
                self._info[eid] = await self._probe(eid)
            out.append(self._info[eid].status)
        return out

    async def _cached_or_probe(self, eid: str) -> _EngineInfo:
        """使えると分かっているエンジンは覚えた情報を使い、使えなかったエンジンは確かめ直す
        （アプリより後に VOICEVOX を起動したときに、ずっと「使えない」のままにしない）。"""
        info = self._info.get(eid)
        if info is None or not info.status.available:
            info = await self._probe(eid)
            self._info[eid] = info
        return info

    async def _engine(self, engine_id: str | None) -> tuple[str, VoicevoxClient, _EngineInfo]:
        if engine_id is None:
            for eid in self.clients:
                info = await self._cached_or_probe(eid)
                if info.status.available:
                    return eid, self.clients[eid], info
            raise EngineUnavailableError("no voice engine available")
        if engine_id not in self.clients:
            raise NotFoundError(f"engine {engine_id}")
        cached = self._info.get(engine_id)
        if cached is None or not cached.status.available:
            cached = await self._probe(engine_id)
            self._info[engine_id] = cached
        info = cached
        if not info.status.available:
            raise EngineUnavailableError(engine_id)
        return engine_id, self.clients[engine_id], info

    async def engine_for_style(
        self, engine_id: str | None, style_id: int
    ) -> tuple[str, VoicevoxClient, _EngineInfo]:
        """話者の声（style_id）を持つエンジン。engine_id の指定があればそれを使う。
        複数のエンジン（Nemo と製品版など）で声を混ぜられるよう、声ごとに選ぶ。"""
        if engine_id is not None:
            return await self._engine(engine_id)
        first: tuple[str, VoicevoxClient, _EngineInfo] | None = None
        for eid in self.clients:
            info = await self._cached_or_probe(eid)
            if not info.status.available:
                continue
            first = first or (eid, self.clients[eid], info)
            ids = self._style_ids.get(eid)
            if ids is None:
                try:
                    speakers = await self.speakers(eid)
                except VoiceError:
                    continue
                ids = {st.id for sp in speakers for st in sp.styles}
                self._style_ids[eid] = ids
            if style_id in ids:
                return eid, self.clients[eid], info
        if first is None:
            raise EngineUnavailableError("no voice engine available")
        return first

    async def speaker_of(self, style_id: int) -> VoiceSpeaker | None:
        """声（style_id）の話者。使えるエンジンを順に探す。"""
        for st in await self.engines(refresh=False):
            if not st.available:
                continue
            try:
                for sp in await self.speakers(st.engine_id):
                    if any(x.id == style_id for x in sp.styles):
                        return sp
            except VoiceError:
                continue
        return None

    @staticmethod
    def _require(info: _EngineInfo, feature: EngineFeature) -> None:
        # manifest に無い機能は「未確認」→利用不可（仕様 §8：未対応機能は利用不可にする）
        if not info.status.features.get(feature.value, False):
            raise UnsupportedFeatureError(feature.value)

    async def _call(self, coro: Awaitable[Any]) -> Any:
        try:
            return await coro
        except VoicevoxUnavailableError as exc:
            raise EngineUnavailableError(str(exc)) from exc
        except VoicevoxError as exc:
            if exc.status in (400, 422):
                raise InvalidVoiceRequestError(str(exc)) from exc
            raise VoiceError(str(exc)) from exc

    # ---------- P3-02 Speaker Manager ----------
    async def speakers(self, engine_id: str | None = None) -> list[VoiceSpeaker]:
        eid, c, _ = await self._engine(engine_id)
        raw = await self._call(c.speakers())
        return [
            VoiceSpeaker(
                engine_id=eid,
                name=s["name"],
                speaker_uuid=s["speaker_uuid"],
                version=s.get("version"),
                styles=[
                    VoiceStyle(id=st["id"], name=st["name"], type=st.get("type", "talk"))
                    for st in s.get("styles", [])
                ],
            )
            for s in raw
        ]

    async def _style(self, engine_id: str, style_id: int) -> tuple[VoiceSpeaker, VoiceStyle]:
        for sp in await self.speakers(engine_id):
            for st in sp.styles:
                if st.id == style_id:
                    if not st.is_talk:
                        raise InvalidVoiceRequestError(f"style {style_id} is not a talk style")
                    return sp, st
        raise NotFoundError(f"style {style_id}")

    # ---------- P3-04 Pronunciation ----------
    async def accent_phrases(self, req: AccentPhrasesRequest) -> list[AccentPhrase]:
        eid, c, _ = await self.engine_for_style(req.engine_id, req.style_id)
        await self._style(eid, req.style_id)
        raw = await self._call(c.accent_phrases(req.text, req.style_id))
        return [AccentPhrase.model_validate(a) for a in raw]

    async def recompute(self, req: MoraEditRequest) -> list[AccentPhrase]:
        eid, c, info = await self.engine_for_style(req.engine_id, req.style_id)
        if req.recompute in ("mora_data", "mora_pitch"):
            self._require(info, EngineFeature.ADJUST_MORA_PITCH)
        if req.recompute in ("mora_data", "mora_length"):
            self._require(info, EngineFeature.ADJUST_PHONEME_LENGTH)
        for ap in req.accent_phrases:
            if not 1 <= ap.accent <= len(ap.moras):
                raise InvalidVoiceRequestError("accent must be within 1..len(moras)")
        payload = [a.model_dump(exclude_none=True) for a in req.accent_phrases]
        raw = await self._call(c.recompute_moras(req.recompute, payload, req.style_id))
        return [AccentPhrase.model_validate(a) for a in raw]

    # ---------- P3-05 Prosody ----------
    def apply_prosody(
        self,
        query: AudioQuery,
        prosody: Prosody,
        info: _EngineInfo,
        default_intonation: float | None = None,
    ) -> AudioQuery:
        q = query.model_copy(deep=True)
        values = prosody.model_dump(exclude_none=True)
        if (
            default_intonation is not None
            and "intonation_scale" not in values
            and info.status.features.get(EngineFeature.ADJUST_INTONATION_SCALE.value, False)
        ):
            values["intonation_scale"] = default_intonation
        for field, value in values.items():
            attr, feature = PROSODY_FEATURES[field]
            if feature is not None:
                self._require(info, feature)
            setattr(q, attr, value)
        return q

    # ---------- P3-03 / P3-07 合成 ----------
    async def _synthesize(
        self,
        eid: str,
        c: VoicevoxClient,
        info: _EngineInfo,
        item: VoiceGenerateItem,
        base_query: AudioQuery | None = None,
    ) -> tuple[bytes, AudioQuery]:
        await self._style(eid, item.style_id)
        speech = item.reading_text or item.text
        if base_query is None:
            base_query = AudioQuery.model_validate(
                await self._call(c.audio_query(speech, item.style_id))
            )
        if item.accent_phrases is not None:
            base_query.accent_phrases = item.accent_phrases
            base_query.kana = None
        else:
            joined = await self._join_words(c, info, item, base_query.accent_phrases)
            if joined is not None:
                base_query.accent_phrases = joined
                base_query.kana = None
        if item.accent_phrases is None and item.expressiveness:
            base_query.accent_phrases = await self._expressive(c, item, base_query.accent_phrases)
            base_query.kana = None
        if item.expressiveness:  # 台本の読み上げ: 前後の無音を短く（間はタイムラインで作る）
            edge = self.settings.voice_edge_silence
            base_query.prePhonemeLength = min(base_query.prePhonemeLength, edge)
            base_query.postPhonemeLength = min(base_query.postPhonemeLength, edge)
        query = self.apply_prosody(
            base_query,
            item.prosody,
            info,
            self.settings.voice_default_intonation if item.expressiveness else None,
        )
        wav = await self._call(
            c.synthesis(query.model_dump(mode="json", exclude_none=False), item.style_id)
        )
        if wav[:4] != b"RIFF":
            raise VoiceError("engine did not return WAV")
        return wav, query

    async def _join_words(
        self,
        c: VoicevoxClient,
        info: _EngineInfo,
        item: VoiceGenerateItem,
        phrases: list[AccentPhrase],
    ) -> list[AccentPhrase] | None:
        """カタカナ語が句の途中で切れていたらつなぎ、音高を再計算する。"""
        speech = item.reading_text or item.text
        feats = info.status.features
        if not (
            feats.get(EngineFeature.ADJUST_MORA_PITCH.value, False)
            and feats.get(EngineFeature.ADJUST_PHONEME_LENGTH.value, False)
        ):
            return None
        # 読み替えで入れた語も、元からあるカタカナ語（ギガパスカル など）も対象にする
        words = list(dict.fromkeys(_KATA_WORD.findall(speech)))
        if not words:
            return None
        kana: list[str] = []
        for w in words[:8]:
            try:
                raw = await self._call(c.accent_phrases(w, item.style_id))
            except VoiceError:
                continue
            kana.append(kana_of([AccentPhrase.model_validate(a) for a in raw]))
        joined, changed = join_split_words(phrases, kana)
        if not changed:
            return None
        try:
            raw = await self._call(
                c.recompute_moras(
                    "mora_data", [a.model_dump(exclude_none=True) for a in joined], item.style_id
                )
            )
        except VoiceError:
            return None
        return [AccentPhrase.model_validate(a) for a in raw]

    async def _expressive(
        self, c: VoicevoxClient, item: VoiceGenerateItem, phrases: list[AccentPhrase]
    ) -> list[AccentPhrase]:
        """強調語の読みを調べてから抑揚を整える（エンジンの推定を出発点にする）。"""
        kana_terms: list[str] = []
        for term in item.emphasis or emphasis_terms(item.text):
            try:
                raw = await self._call(c.accent_phrases(normalize_for_speech(term), item.style_id))
            except VoiceError:
                continue
            kana_terms.append(kana_of([AccentPhrase.model_validate(a) for a in raw]))
        return shape(
            phrases,
            strength=item.expressiveness or 0.0,
            emphasis=find_spans(phrases, kana_terms),
            exclaim="！" in item.text or "!" in item.text,
        )

    async def preview(self, req: VoicePreviewRequest) -> bytes:
        eid, c, info = await self.engine_for_style(req.engine_id, req.style_id)
        wav, _ = await self._synthesize(eid, c, info, req)
        return wav

    def _audio_dir(self, project_id: str | None) -> Path:
        d = self.settings.data_dir / "projects" / (project_id or "_shared") / "audio"
        d.mkdir(parents=True, exist_ok=True)
        return d

    async def _save(
        self,
        eid: str,
        info: _EngineInfo,
        project_id: str | None,
        item: VoiceGenerateItem,
        wav: bytes,
        query: AudioQuery,
    ) -> AudioFile:
        wav = level_speech(wav)  # セリフ間の音量差をそろえる
        rate, duration, _ = wav_info(wav)
        aid = str(uuid4())
        path = self._audio_dir(project_id) / f"{aid}.wav"
        path.write_bytes(wav)
        speaker, _ = await self._style(eid, item.style_id)
        row = AudioFile(
            id=aid,
            project_id=project_id,
            script_segment_id=item.script_segment_id,
            engine_id=eid,
            engine_version=info.status.version,
            style_id=item.style_id,
            text=item.text,
            path=path.relative_to(self.settings.data_dir).as_posix(),
            sha256=hashlib.sha256(wav).hexdigest(),
            sample_rate=rate,
            duration_sec=duration,
            query=query.model_dump(mode="json"),
            prosody=item.prosody.model_dump(exclude_none=True),
            timings=[t.model_dump() for t in phrase_timings(query, info.frame_rate)],
            credit=info.credit.format(speaker=speaker.name),
        )
        async with self.db.session_factory() as s:
            s.add(row)
            await s.commit()
        return row

    async def generate(
        self, req: VoiceGenerateRequest, progress: ProgressFn | None = None
    ) -> list[AudioFileRead]:
        if req.project_id is not None:
            async with self.db.session_factory() as s:
                if await s.get(Project, req.project_id) is None:
                    raise NotFoundError(f"project {req.project_id}")
        out: list[AudioFileRead] = []
        eid = ""
        for i, item in enumerate(req.items):
            eid, c, info = await self.engine_for_style(req.engine_id, item.style_id)
            wav, query = await self._synthesize(eid, c, info, item)
            row = await self._save(eid, info, req.project_id, item, wav, query)
            out.append(self.to_read(row))
            if progress:
                await progress((i + 1) / len(req.items))
        log.info("voice_generated", engine=eid, count=len(out))
        return out

    async def regenerate_with_prosody(self, req: ProsodyRequest) -> AudioFileRead:
        """既存音声のクエリ（読み・アクセント修正を含む）を引き継いで抑揚だけ変えて作り直す。
        元のファイルは履歴として残す（Undo 用）。"""
        async with self.db.session_factory() as s:
            old = await s.get(AudioFile, req.audio_id)
        if old is None:
            raise NotFoundError(f"audio {req.audio_id}")
        eid, c, info = await self._engine(old.engine_id)
        prev = Prosody.model_validate(old.prosody or {})
        merged = Prosody.model_validate(
            {**prev.model_dump(exclude_none=True), **req.prosody.model_dump(exclude_none=True)}
        )
        item = VoiceGenerateItem(
            text=old.text,
            style_id=old.style_id,
            prosody=merged,
            accent_phrases=req.accent_phrases,
            script_segment_id=old.script_segment_id,
        )
        wav, query = await self._synthesize(
            eid, c, info, item, base_query=AudioQuery.model_validate(old.query)
        )
        return self.to_read(await self._save(eid, info, old.project_id, item, wav, query))

    # ---------- P3-08 Repository ----------
    @staticmethod
    def to_read(row: AudioFile) -> AudioFileRead:
        r = AudioFileRead.model_validate(row)
        r.timings = [PhraseTiming.model_validate(t) for t in row.timings or []]
        r.prosody = dict(row.prosody or {})
        return r

    async def list_audio(self, project_id: str | None = None) -> list[AudioFileRead]:
        q = select(AudioFile).order_by(AudioFile.created_at)
        if project_id is not None:
            q = q.where(AudioFile.project_id == project_id)
        async with self.db.session_factory() as s:
            return [self.to_read(r) for r in (await s.execute(q)).scalars()]

    async def audio_path(self, audio_id: str) -> Path:
        async with self.db.session_factory() as s:
            row = await s.get(AudioFile, audio_id)
        if row is None:
            raise NotFoundError(f"audio {audio_id}")
        base = self.settings.data_dir.resolve()
        path = (base / row.path).resolve()
        if base not in path.parents or not path.is_file():  # 許可ディレクトリ外は返さない
            raise NotFoundError(f"audio file {audio_id}")
        return path

    # ---------- P3-06 Dictionary ----------
    async def list_words(self) -> list[PronunciationEntry]:
        async with self.db.session_factory() as s:
            res = await s.execute(select(PronunciationEntry).order_by(PronunciationEntry.surface))
            return list(res.scalars())

    @staticmethod
    def _word_params(req: DictionaryWordRequest) -> dict[str, Any]:
        if req.accent_type > mora_count(req.pronunciation):
            raise InvalidVoiceRequestError("accent_type exceeds mora count")
        return {
            "surface": req.surface,
            "pronunciation": req.pronunciation,
            "accent_type": req.accent_type,
            "word_type": req.word_type.value,
            "priority": req.priority,
        }

    async def add_word(self, req: DictionaryWordRequest) -> PronunciationEntry:
        eid, c, info = await self._engine(req.engine_id)
        self._require(info, EngineFeature.USER_DICT)
        params = self._word_params(req)
        word_uuid = await self._call(c.add_word(**params))
        row = PronunciationEntry(
            engine_id=eid,
            engine_word_uuid=word_uuid,
            surface=req.surface,
            pronunciation=req.pronunciation,
            accent_type=req.accent_type,
            word_type=req.word_type.value,
            priority=req.priority,
        )
        async with self.db.session_factory() as s:
            s.add(row)
            await s.commit()
        return row

    async def update_word(self, word_id: str, req: DictionaryWordRequest) -> PronunciationEntry:
        async with self.db.session_factory() as s:
            row = await s.get(PronunciationEntry, word_id)
            if row is None:
                raise NotFoundError(f"word {word_id}")
            _, c, info = await self._engine(row.engine_id)
            self._require(info, EngineFeature.USER_DICT)
            params = self._word_params(req)
            if row.engine_word_uuid:
                await self._call(c.update_word(row.engine_word_uuid, **params))
            row.surface, row.pronunciation = req.surface, req.pronunciation
            row.accent_type, row.word_type, row.priority = (
                req.accent_type,
                req.word_type.value,
                req.priority,
            )
            await s.commit()
            return row

    async def delete_word(self, word_id: str) -> None:
        async with self.db.session_factory() as s:
            row = await s.get(PronunciationEntry, word_id)
            if row is None:
                raise NotFoundError(f"word {word_id}")
            _, c, _ = await self._engine(row.engine_id)
            if row.engine_word_uuid:
                try:
                    await c.delete_word(row.engine_word_uuid)
                except VoicevoxError as exc:
                    if exc.status != 404 and exc.status != 422:  # エンジン側に既に無い場合は続行
                        raise VoiceError(str(exc)) from exc
            await s.delete(row)
            await s.commit()

    async def restore_dictionary(self, engine_id: str | None = None) -> int:
        """DBにあってエンジン辞書に無い単語を再登録する（エンジン再インストール後の復元）。"""
        eid, c, info = await self._engine(engine_id)
        self._require(info, EngineFeature.USER_DICT)
        present = set((await self._call(c.user_dict())).keys())
        restored = 0
        async with self.db.session_factory() as s:
            rows = (
                await s.execute(
                    select(PronunciationEntry).where(PronunciationEntry.engine_id == eid)
                )
            ).scalars()
            for row in rows:
                if row.engine_word_uuid in present:
                    continue
                row.engine_word_uuid = await self._call(
                    c.add_word(
                        surface=row.surface,
                        pronunciation=row.pronunciation,
                        accent_type=row.accent_type,
                        word_type=row.word_type,
                        priority=row.priority,
                    )
                )
                restored += 1
            await s.commit()
        return restored

    async def aclose(self) -> None:
        for c in self.clients.values():
            await c.aclose()
