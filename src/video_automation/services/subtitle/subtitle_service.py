"""字幕サービス (P4-01〜P4-07)。

台本作成 → セグメント単位の音声生成 → 字幕生成（改行） → 音声時間で同期 → ASS/SRT 出力。
台本の表記（漢字かな）と音声のモーラ（読み）は1対1に対応しないため、各字幕行の読みを
エンジンの accent_phrases で取得してモーラ数で音声のモーラ時刻表に整列させる (P4-03)。
"""

from collections.abc import Awaitable, Callable
from fractions import Fraction
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from video_automation.app.logging import get_logger
from video_automation.app.settings import Settings
from video_automation.database.database import Database
from video_automation.database.models import (
    AudioFile,
    Project,
    Script,
    ScriptCharacter,
    ScriptSegment,
    Subtitle,
)
from video_automation.schemas.subtitle import (
    ScriptCreate,
    ScriptRead,
    ScriptSegmentRead,
    SubtitleCue,
    SubtitleExportRequest,
    SubtitleExportResult,
    SubtitleFormat,
    SubtitleGenerateRequest,
    SubtitleStyle,
    SubtitleSyncRequest,
)
from video_automation.schemas.voice import (
    AccentPhrasesRequest,
    AudioQuery,
    Prosody,
    VoiceGenerateItem,
    VoiceGenerateRequest,
)
from video_automation.services.subtitle.formats import build_ass, build_srt
from video_automation.services.subtitle.text import default_max_chars, make_cues, split_script
from video_automation.services.voice.reading import emphasis_terms, normalize_for_speech
from video_automation.services.voice.timing import mora_times
from video_automation.services.voice.voice_service import (
    EngineUnavailableError,
    InvalidVoiceRequestError,
    NotFoundError,
    VoiceService,
)

# 話者の既定の色（明るめ。縁取りには暗くして使う）
SPEAKER_PALETTE = ["#4FC3F7", "#FFB74D", "#81C784", "#F06292", "#BA68C8", "#FFD54F"]

log = get_logger("subtitle")
if TYPE_CHECKING:
    from video_automation.schemas.design import DesignProfile
    from video_automation.services.design.service import DesignService
    from video_automation.services.voice.reading_service import ReadingService

ProgressFn = Callable[[float], Awaitable[None]]


class SubtitleError(Exception):
    code = "subtitle_error"


class SubtitleStateError(SubtitleError):
    code = "subtitle_state_error"


def fps_of(project: Project) -> Fraction:
    return Fraction(project.fps_num, project.fps_den)


def sec_to_frame(sec: float, fps: Fraction) -> int:
    return round(Fraction(sec).limit_denominator(1_000_000) * fps)


def style_from_profile(prof: "DesignProfile") -> SubtitleStyle:
    """用途の型から字幕の見た目を作る（書体・大きさ・帯の形・帯の色）。"""
    from video_automation.schemas.design import Component
    from video_automation.services.design.guard import is_dark

    spec = prof.subtitle
    if not prof.allows(Component.SUBTITLE) and prof.allows(Component.LYRIC_TEXT):
        # 字幕を使わず歌詞を使う型（MV・リリック動画）: 字幕の行を歌詞として見せる
        lo, hi = spec.size_range
        return SubtitleStyle(
            font_name=prof.typography.heading_font,
            font_size_ratio=min(hi * 1.2, spec.size_ratio * 1.5),
            band=False,
            outline=1.0,
            shadow=0.0,
            primary_color=prof.palette.text,
            outline_color=prof.palette.base,
            margin_v_ratio=max(0.12, prof.safe_area.bottom + 0.06),
            fade_in_ms=250,
            fade_out_ms=300,
            speaker_label=False,
            speaker_outline=False,
            mode="lyric",
            glow_color=prof.palette.main,
        )
    band = spec.band != "none"
    surface = prof.palette.surface.lstrip("#")
    dark_text = is_dark(prof.palette.text)
    return SubtitleStyle(
        font_name=prof.typography.body_font,
        font_size_ratio=spec.size_ratio,
        band=band,
        band_mode="fitted" if spec.band == "fitted" else "full",
        band_color=f"#B8{surface}",  # 面の色を 72% の不透明度で
        # 帯の無い型は縁取りを太くして背景から分離する
        outline=(1.5 if band else 3.0) if dark_text else (2.0 if band else 4.0),
        shadow=0.0 if (band or dark_text) else 1.5,
        primary_color=prof.palette.text,
        # 暗い文字（明るい背景の型）は明るい縁取りにする。暗い縁取りだと字が潰れる
        outline_color=prof.palette.base if dark_text else "#000000",
        speaker_outline=not dark_text,
        speaker_label=prof.allows(Component.SPEAKER_LABEL),
    )


class SubtitleService:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        voice: VoiceService,
        readings: "ReadingService | None" = None,
        design: "DesignService | None" = None,
    ) -> None:
        self.settings = settings
        self.db = db
        self.voice = voice
        self.readings = readings
        self.design = design

    async def _speech(self, project_id: str | None, text: str) -> str:
        """読み上げ用の文（読みのルール・略語・単位・括弧を整えたもの）。"""
        if self.readings is not None:
            return await self.readings.speech_text(project_id, text)
        return normalize_for_speech(text)

    # ---------- 台本 (P4-01) ----------
    async def create_script(self, req: ScriptCreate) -> ScriptRead:
        drafts = split_script(req.text)
        if not drafts:
            raise SubtitleStateError("script has no text")
        async with self.db.session_factory() as s:
            if await s.get(Project, req.project_id) is None:
                raise NotFoundError(f"project {req.project_id}")
            script = Script(project_id=req.project_id, title=req.title, mode=req.mode.value)
            s.add(script)
            await s.flush()
            for i, d in enumerate(drafts):
                style = req.speaker_styles.get(d.speaker, req.default_style_id)
                s.add(
                    ScriptSegment(
                        script_id=script.id,
                        scene_index=d.scene_index,
                        sequence=i,
                        speaker=d.speaker,
                        text=d.text,
                        generation_source="USER",
                        voice_style_id=style,
                    )
                )
            await s.commit()
            sid = script.id
        return await self.get_script(sid)

    async def get_script(self, script_id: str) -> ScriptRead:
        async with self.db.session_factory() as s:
            script = await s.get(Script, script_id)
            if script is None:
                raise NotFoundError(f"script {script_id}")
            segs = await self._segments(s, script_id)
            r = ScriptRead.model_validate(script)
            r.segments = [ScriptSegmentRead.model_validate(x) for x in segs]
            return r

    async def list_scripts(self, project_id: str) -> list[ScriptRead]:
        async with self.db.session_factory() as s:
            q = select(Script).where(Script.project_id == project_id).order_by(Script.created_at)
            ids = [x.id for x in (await s.execute(q)).scalars()]
        return [await self.get_script(i) for i in ids]

    @staticmethod
    async def _segments(s: AsyncSession, script_id: str) -> list[ScriptSegment]:
        q = select(ScriptSegment).where(ScriptSegment.script_id == script_id)
        return list((await s.execute(q.order_by(ScriptSegment.sequence))).scalars())

    async def _project_of(self, s: AsyncSession, script_id: str) -> tuple[Script, Project]:
        script = await s.get(Script, script_id)
        if script is None:
            raise NotFoundError(f"script {script_id}")
        project = await s.get(Project, script.project_id)
        assert project is not None
        return script, project

    # ---------- 音声生成（Phase 3 連携） ----------
    async def generate_voices(
        self, script_id: str, regenerate: bool = False, progress: ProgressFn | None = None
    ) -> ScriptRead:
        async with self.db.session_factory() as s:
            script, _ = await self._project_of(s, script_id)
            segs = await self._segments(s, script_id)
            todo = [x for x in segs if regenerate or x.audio_file_id is None]
            q = select(ScriptCharacter).where(ScriptCharacter.script_id == script_id)
            chars = {c.name: c for c in (await s.execute(q)).scalars()}
            missing = [x.sequence for x in todo if x.voice_style_id is None]
            if missing:
                raise SubtitleStateError(f"voice style not set for segments {missing[:10]}")
            # 台本スタジオで設定したキャラクターごとの抑揚（話速・高さ等）と抑揚づけの強さ
            prosody = {n: Prosody.model_validate(c.voice or {}) for n, c in chars.items()}
            expressive = {
                n: (c.voice or {}).get("expressiveness", self.settings.voice_expressiveness)
                for n, c in chars.items()
            }
            for i, seg in enumerate(todo):
                audio = (
                    await self.voice.generate(
                        VoiceGenerateRequest(
                            project_id=script.project_id,
                            items=[
                                VoiceGenerateItem(
                                    text=seg.text,
                                    reading_text=await self._speech(script.project_id, seg.text),
                                    style_id=seg.voice_style_id or 0,
                                    prosody=prosody.get(seg.speaker) or Prosody(),
                                    expressiveness=expressive.get(
                                        seg.speaker, self.settings.voice_expressiveness
                                    ),
                                    emphasis=emphasis_terms(seg.text),
                                    script_segment_id=seg.id,
                                )
                            ],
                        )
                    )
                )[0]
                seg.audio_file_id = audio.id
                seg.start_frame = seg.end_frame = None  # 音声が変わったら再同期が必要
                await s.commit()
                if progress:
                    await progress((i + 1) / max(1, len(todo)))
        return await self.get_script(script_id)

    # ---------- 字幕生成 (P4-02) ----------
    async def generate(self, req: SubtitleGenerateRequest) -> list[SubtitleCue]:
        async with self.db.session_factory() as s:
            script, project = await self._project_of(s, req.script_id)
            max_chars = req.max_chars_per_line or default_max_chars(project.width, project.height)
            max_lines = req.max_lines or 2
            if self.design and project.purpose:
                prof = await self.design.profile_for(project.id)
                if prof and req.max_chars_per_line is None:
                    # 用途の型の 1 行の文字数（読みやすさの上限）を超えない
                    max_chars = min(max_chars, prof.subtitle.max_chars_per_line)
                if prof and req.max_lines is None:
                    max_lines = prof.subtitle.max_lines  # 歌詞は 1 行など
            await s.execute(delete(Subtitle).where(Subtitle.script_id == script.id))
            idx = 0
            for seg in await self._segments(s, script.id):
                for text in make_cues(seg.text, max_chars, max_lines, req.strip_period):
                    s.add(
                        Subtitle(
                            script_id=script.id,
                            script_segment_id=seg.id,
                            index=idx,
                            text=text,
                            speaker=seg.speaker,
                        )
                    )
                    idx += 1
            await s.commit()
        return await self.cues(req.script_id)

    async def cues(self, script_id: str) -> list[SubtitleCue]:
        async with self.db.session_factory() as s:
            q = select(Subtitle).where(Subtitle.script_id == script_id).order_by(Subtitle.index)
            return [SubtitleCue.model_validate(r) for r in (await s.execute(q)).scalars()]

    # ---------- 音声時間取得・同期 (P4-03 / P4-04) ----------
    async def _reading_moras(
        self, text: str, style_id: int, engine_id: str, project_id: str | None = None
    ) -> int:
        # 音声と同じ読み（正規化後の文）でモーラ数を数える
        clean = await self._speech(project_id, text.replace("\n", ""))
        try:
            aps = await self.voice.accent_phrases(
                AccentPhrasesRequest(text=clean, style_id=style_id, engine_id=engine_id)
            )
            return max(1, sum(len(a.moras) for a in aps))
        except (InvalidVoiceRequestError, EngineUnavailableError):
            # 読みが取れない場合は文字数で近似
            return max(1, len(clean))

    async def _align(
        self, cues: list[Subtitle], audio: AudioFile, style_id: int
    ) -> list[tuple[float, float]]:
        """セグメント内の各キューの (開始, 終了) 秒（音声先頭基準）。"""
        times = mora_times(AudioQuery.model_validate(audio.query))
        if len(cues) == 1 or not times:
            first = times[0][0] if times else 0.0
            last = times[-1][1] if times else audio.duration_sec
            return [(first, last)] if len(cues) == 1 else [(first, last)] * len(cues)
        counts = [
            await self._reading_moras(c.text, style_id, audio.engine_id, audio.project_id)
            for c in cues
        ]
        total, n = sum(counts), len(times)
        out, cum = [], 0
        for k in counts:
            a = min(n - 1, round(cum / total * n))
            cum += k
            b = max(a, min(n - 1, round(cum / total * n) - 1))
            out.append((times[a][0], times[b][1]))
        return out

    async def synchronize(self, req: SubtitleSyncRequest) -> list[SubtitleCue]:
        async with self.db.session_factory() as s:
            script, project = await self._project_of(s, req.script_id)
            fps = fps_of(project)
            segs = await self._segments(s, script.id)
            q = select(Subtitle).where(Subtitle.script_id == script.id).order_by(Subtitle.index)
            all_cues = list((await s.execute(q)).scalars())
            if not all_cues:
                raise SubtitleStateError("generate subtitles first")
            no_audio = [x.sequence for x in segs if x.audio_file_id is None]
            if no_audio:
                raise SubtitleStateError(f"audio not generated for segments {no_audio[:10]}")
            by_seg: dict[str, list[Subtitle]] = {}
            for c in all_cues:
                by_seg.setdefault(c.script_segment_id, []).append(c)

            cursor = 0  # フレーム
            gap = sec_to_frame(req.gap_sec, fps)
            for seg in segs:
                audio = await s.get(AudioFile, seg.audio_file_id)
                if audio is None:
                    raise SubtitleStateError(f"audio file missing for segment {seg.sequence}")
                length = max(1, sec_to_frame(audio.duration_sec, fps))
                seg.start_frame, seg.end_frame = cursor, cursor + length
                spans = await self._align(by_seg.get(seg.id, []), audio, seg.voice_style_id or 0)
                for cue, (a, b) in zip(by_seg.get(seg.id, []), spans, strict=True):
                    cue.start_frame = cursor + sec_to_frame(a, fps)
                    cue.end_frame = max(cue.start_frame + 1, cursor + sec_to_frame(b, fps))
                cursor += length + gap

            # 最短表示時間：次のキュー開始までの範囲で延長。重なりは除去（半開区間）
            min_len = sec_to_frame(req.min_duration_sec, fps)
            hold = sec_to_frame(req.hold_sec, fps)
            for i, cue in enumerate(all_cues):
                assert cue.start_frame is not None and cue.end_frame is not None
                limit = all_cues[i + 1].start_frame if i + 1 < len(all_cues) else None
                if cue.end_frame - cue.start_frame < min_len:
                    cue.end_frame = cue.start_frame + min_len
                if limit is not None:  # 次の字幕までの間（行の間の無音）に残す
                    cue.end_frame = max(cue.end_frame, min(cue.end_frame + hold, limit))
                if limit is not None and cue.end_frame > limit:
                    cue.end_frame = max(cue.start_frame + 1, limit)
            await s.commit()
        log.info("subtitles_synchronized", script_id=req.script_id, cues=len(all_cues))
        return await self.cues(req.script_id)

    # ---------- 見た目（P4-07 の拡張） ----------
    async def project_style(self, project_id: str) -> SubtitleStyle:
        """保存した字幕の見た目。保存が無く用途（デザインの型）があれば、その型から作る。"""
        async with self.db.session_factory() as s:
            p = await s.get(Project, project_id)
            if p is None:
                raise NotFoundError(f"project {project_id}")
            saved = p.subtitle_style
        if saved:
            return SubtitleStyle.model_validate(saved)
        prof = await self.design.profile_for(project_id) if self.design else None
        return style_from_profile(prof) if prof else SubtitleStyle()

    async def set_project_style(self, project_id: str, style: SubtitleStyle) -> SubtitleStyle:
        async with self.db.session_factory() as s:
            p = await s.get(Project, project_id)
            if p is None:
                raise NotFoundError(f"project {project_id}")
            p.subtitle_style = style.model_dump(mode="json")
            await s.commit()
        return style

    async def speaker_accents(self, script_id: str) -> dict[str, str]:
        """話者の色。キャラクターに色があればそれを、無ければ登場順に既定の色を割り当てる。"""
        async with self.db.session_factory() as s:
            _, proj = await self._project_of(s, script_id)
            project_id = proj.id
            chars = list(
                (
                    await s.execute(
                        select(ScriptCharacter)
                        .where(ScriptCharacter.script_id == script_id)
                        .order_by(ScriptCharacter.sequence)
                    )
                ).scalars()
            )
            speakers = list(
                dict.fromkeys(
                    (
                        await s.execute(
                            select(ScriptSegment.speaker)
                            .where(ScriptSegment.script_id == script_id)
                            .order_by(ScriptSegment.sequence)
                        )
                    ).scalars()
                )
            )
        out: dict[str, str] = {c.name: c.color for c in chars if c.color}
        order = [c.name for c in chars] + [x for x in speakers if x]
        palette = SPEAKER_PALETTE
        if self.design:  # 用途の型があれば、その配色（メイン・アクセントの明暗違い）を使う
            prof = await self.design.profile_for(project_id)
            if prof:
                palette = prof.palette.speakers
        i = 0
        for name in dict.fromkeys(order):
            if name in out:
                continue
            out[name] = palette[i % len(palette)]
            i += 1
        return out

    # ---------- 出力 (P4-05 / P4-06 / P4-07) ----------
    async def export(self, req: SubtitleExportRequest) -> SubtitleExportResult:
        async with self.db.session_factory() as s:
            script, project = await self._project_of(s, req.script_id)
            fps = fps_of(project)
        cues = await self.cues(req.script_id)
        if not cues:
            raise SubtitleStateError("generate subtitles first")
        if any(c.start_frame is None or c.end_frame is None for c in cues):
            raise SubtitleStateError("synchronize subtitles first")
        rows = [(c.start_frame or 0, c.end_frame or 0, c.speaker, c.text) for c in cues]
        if req.format == SubtitleFormat.ASS:
            content = build_ass(
                rows,
                fps,
                project.width,
                project.height,
                req.style or await self.project_style(project.id),
                req.speaker_colors,
                title=script.title or "VideoAutomation",
                speaker_accents=await self.speaker_accents(req.script_id),
            )
        else:
            content = build_srt(rows, fps)
        out_dir = self.settings.data_dir / "projects" / project.id / "subtitles"
        out_dir.mkdir(parents=True, exist_ok=True)
        path: Path = out_dir / f"{script.id}.{req.format.value}"
        # UTF-8 (BOM付き)。SRT/ASS とも Windows の再生ソフト・libass で読める
        path.write_text(content, encoding="utf-8-sig", newline="")
        return SubtitleExportResult(
            format=req.format,
            path=path.relative_to(self.settings.data_dir).as_posix(),
            content=content,
            cue_count=len(cues),
        )
