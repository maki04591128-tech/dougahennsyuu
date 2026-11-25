# ruff: noqa: E501
"""品質検査（P9-02〜05）。

タイムライン・音声・字幕・書き出した動画・ライセンスを検査し、項目ごとに
error（直さないと公開できない）/ warning（確認が必要）/ info（参考）でまとめる。
結果は qa_reports に保存する。基準値は日本語の解説動画を想定した目安。
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import select

from video_automation.app.logging import get_logger
from video_automation.app.settings import Settings
from video_automation.core.jobs.job_manager import JobManager
from video_automation.database.database import Database
from video_automation.database.models import (
    Asset,
    AssetLicenseRow,
    AudioFile,
    Project,
    QAReportRow,
    RenderJob,
    Script,
    ScriptSegment,
)
from video_automation.integrations.ffmpeg.runner import FFmpeg, FFmpegError
from video_automation.schemas.qa import (
    SECTION_LABEL,
    QACheck,
    QALevel,
    QAReportRead,
    QAReportSummary,
    QARequest,
    QASection,
    QASectionName,
)
from video_automation.schemas.timeline import RENDER_PROFILES, RenderProfileName, RenderStatus
from video_automation.services.qa.measure import Measurement, measure
from video_automation.services.subtitle.subtitle_service import SubtitleService
from video_automation.services.subtitle.text import default_max_chars
from video_automation.services.timeline.timeline_service import (
    TimelineNotFoundError,
    TimelineService,
)

log = get_logger("qa")
ProgressFn = Callable[[float], Awaitable[None]]

# ---- 基準値（目安） ----
PEAK_CLIP_DBFS = -0.1  # これを超えるとクリッピングの恐れ
PEAK_WARN_DBFS = -1.0  # 配信の推奨トゥルーピーク
VOICE_MIN_LUFS = -35.0  # これより小さいセリフは聞き取りにくい
VOICE_SPREAD_LU = 6.0  # セリフ間の音量差の許容
LEAD_SILENCE_SEC = 0.6
TAIL_SILENCE_SEC = 1.0
MID_SILENCE_SEC = 1.5
SPEECH_CPS_MAX = 12.0  # 読み上げ速度（文字/秒）の上限
SPEECH_CPS_MIN = 2.0
CUE_MIN_SEC = 0.6
CUE_MAX_SEC = 8.0
CUE_CPS_MAX = 10.0  # 字幕の読み取り速度（文字/秒）
TARGET_LUFS = -14.0  # 書き出しのラウドネス目標（loudnorm）
LUFS_TOLERANCE = 2.0
BLACK_WARN_SEC = 0.5
FREEZE_INFO_SEC = 5.0


def _status(checks: list[QACheck]) -> str:
    levels = {c.level for c in checks}
    if QALevel.ERROR in levels:
        return "failed"
    if QALevel.WARNING in levels:
        return "warnings"
    return "passed"


def _chars(text: str) -> int:
    # 読む文字数には、句読点・かぎかっこ・三点リーダーなどの記号を数えない
    return len(
        "".join(
            ch for ch in text if not ch.isspace() and ch not in "、。・！？!?,.「」『』（）()…〜"
        )
    )


class QAService:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        ffmpeg: FFmpeg,
        timeline: TimelineService,
        subtitles: SubtitleService,
        jobs: JobManager,
    ) -> None:
        self.settings = settings
        self.db = db
        self.ffmpeg = ffmpeg
        self.timeline = timeline
        self.subtitles = subtitles
        self.jobs = jobs

    # ---------- 公開 API ----------
    def submit(self, project_id: str, req: QARequest) -> Any:
        async def run(progress: ProgressFn) -> dict[str, Any]:
            r = await self.run(project_id, req, progress)
            return {"report_id": r.id, "status": r.status}

        return self.jobs.submit("qa", run)

    async def get(self, report_id: str) -> QAReportRead:
        async with self.db.session_factory() as s:
            row = await s.get(QAReportRow, report_id)
            if row is None:
                raise TimelineNotFoundError(f"qa report {report_id}")
            return self._read(row)

    async def list_reports(self, project_id: str) -> list[QAReportSummary]:
        async with self.db.session_factory() as s:
            rows = (
                await s.execute(
                    select(QAReportRow)
                    .where(QAReportRow.project_id == project_id)
                    .order_by(QAReportRow.created_at.desc())
                )
            ).scalars()
            out = []
            for r in rows:
                counts = {lv.value: 0 for lv in QALevel}
                for sec in r.report.get("sections", []):
                    for c in sec.get("checks", []):
                        counts[c["level"]] = counts.get(c["level"], 0) + 1
                out.append(
                    QAReportSummary(
                        id=r.id,
                        status=r.status,
                        render_id=r.render_id,
                        counts=counts,
                        created_at=r.created_at,
                    )
                )
            return out

    @staticmethod
    def _read(row: QAReportRow) -> QAReportRead:
        return QAReportRead(
            id=row.id,
            project_id=row.project_id,
            render_id=row.render_id,
            script_id=row.script_id,
            status=row.status,
            sections=[QASection.model_validate(x) for x in row.report.get("sections", [])],
            duration_ms=row.duration_ms,
            created_at=row.created_at,
        )

    async def run(
        self, project_id: str, req: QARequest, progress: ProgressFn | None = None
    ) -> QAReportRead:
        t0 = time.perf_counter()
        async with self.db.session_factory() as s:
            p = await s.get(Project, project_id)
            if p is None:
                raise TimelineNotFoundError(f"project {project_id}")
            render = await self._render(s, project_id, req.render_id)
            script_id = req.script_id or (render.request.get("script_id") if render else None)
            if script_id is None:
                latest = (
                    (
                        await s.execute(
                            select(Script)
                            .where(Script.project_id == project_id)
                            .order_by(Script.created_at.desc())
                        )
                    )
                    .scalars()
                    .first()
                )
                script_id = latest.id if latest else None
            fps = p.fps_num / p.fps_den
            width, height = p.width, p.height
        wanted = req.sections or list(QASectionName)
        steps: list[tuple[QASectionName, Callable[[], Awaitable[QASection]]]] = [
            (QASectionName.PROJECT, lambda: self._project(project_id)),
            (QASectionName.AUDIO, lambda: self._audio(script_id)),
            (
                QASectionName.SUBTITLE,
                lambda: self._subtitle(project_id, script_id, fps, width, height),
            ),
            (QASectionName.VIDEO, lambda: self._video(project_id, render, fps)),
            (QASectionName.LICENSE, lambda: self._license(project_id)),
        ]
        sections: list[QASection] = []
        todo = [(n, f) for n, f in steps if n in wanted]
        for i, (name, fn) in enumerate(todo):
            try:
                sec = await fn()
            except FFmpegError as exc:  # 測定できなかったことを結果として残す
                sec = self._section(
                    name,
                    [QACheck(level=QALevel.ERROR, code="measure_failed", message=str(exc)[:300])],
                )
            sections.append(sec)
            if progress:
                await progress((i + 1) / len(todo))
        considered = [x for x in sections if x.status != "skipped"]
        status = _status([c for x in considered for c in x.checks])
        ms = (time.perf_counter() - t0) * 1000
        async with self.db.session_factory() as s:
            row = QAReportRow(
                project_id=project_id,
                render_id=render.id if render else None,
                script_id=script_id,
                status=status,
                report={"sections": [x.model_dump(mode="json") for x in sections]},
                duration_ms=ms,
            )
            s.add(row)
            await s.commit()
            log.info("qa_done", project_id=project_id, status=status, ms=round(ms))
            return self._read(row)

    @staticmethod
    async def _render(s: Any, project_id: str, render_id: str | None) -> RenderJob | None:
        if render_id:
            r = await s.get(RenderJob, render_id)
            if r is None or r.project_id != project_id:
                raise TimelineNotFoundError(f"render {render_id}")
            return r
        return (
            (
                await s.execute(
                    select(RenderJob)
                    .where(
                        RenderJob.project_id == project_id,
                        RenderJob.status == RenderStatus.SUCCEEDED.value,
                    )
                    .order_by(RenderJob.created_at.desc())
                )
            )
            .scalars()
            .first()
        )

    @staticmethod
    def _section(
        name: QASectionName,
        checks: list[QACheck],
        metrics: dict[str, Any] | None = None,
        skipped: bool = False,
    ) -> QASection:
        return QASection(
            name=name,
            label=SECTION_LABEL[name],
            status="skipped" if skipped else _status(checks),
            checks=checks,
            metrics=metrics or {},
        )

    # ---------- タイムライン ----------
    async def _project(self, project_id: str) -> QASection:
        v = await self.timeline.validate_project(project_id)
        checks = [
            QACheck(
                level=QALevel(i.level),
                code="timeline",
                message=i.message,
                target=i.clip_id,
            )
            for i in v.issues
            if not i.message.startswith("license")  # ライセンスは専用の項目で詳しく見る
        ]
        tl = await self.timeline.get_timeline(project_id)
        return self._section(
            QASectionName.PROJECT,
            checks,
            {
                "tracks": len(tl.tracks),
                "clips": len(tl.clips),
                "duration_sec": round(tl.duration_frames * tl.fps_den / tl.fps_num, 2),
            },
        )

    # ---------- P9-02 音声品質 ----------
    async def _audio(self, script_id: str | None) -> QASection:
        name = QASectionName.AUDIO
        if script_id is None:
            return self._section(
                name,
                [QACheck(level=QALevel.INFO, code="no_script", message="台本がありません")],
                skipped=True,
            )
        async with self.db.session_factory() as s:
            segs = list(
                (
                    await s.execute(
                        select(ScriptSegment)
                        .where(ScriptSegment.script_id == script_id)
                        .order_by(ScriptSegment.sequence)
                    )
                ).scalars()
            )
            ids = [x.audio_file_id for x in segs if x.audio_file_id]
            files = {
                a.id: a
                for a in (await s.execute(select(AudioFile).where(AudioFile.id.in_(ids)))).scalars()
            }
        checks: list[QACheck] = []
        missing = [x for x in segs if not x.audio_file_id]
        if missing:
            checks.append(
                QACheck(
                    level=QALevel.WARNING,
                    code="voice_missing",
                    message=f"音声が未生成のセリフ {len(missing)} 件（例: {missing[0].text[:20]}）",
                    target=missing[0].id,
                )
            )
        sem = asyncio.Semaphore(4)

        async def one(seg: ScriptSegment) -> tuple[ScriptSegment, AudioFile, Measurement | None]:
            af = files[seg.audio_file_id or ""]
            path = self.settings.data_dir / af.path
            if not path.is_file():
                return seg, af, None
            async with sem:
                return (
                    seg,
                    af,
                    await measure(self.ffmpeg, path, silence_db=-45, silence_min_sec=0.3),
                )

        results = await asyncio.gather(
            *[one(x) for x in segs if x.audio_file_id and x.audio_file_id in files]
        )
        loud: list[float] = []
        rates = set()
        for seg, af, m in results:
            label = f"{seg.speaker}「{seg.text[:16]}」"
            if m is None:
                checks.append(
                    QACheck(
                        level=QALevel.ERROR,
                        code="voice_file_missing",
                        message=f"音声ファイルがありません: {label}",
                        target=seg.id,
                    )
                )
                continue
            rates.add(af.sample_rate)
            dur = m.duration_sec or af.duration_sec
            if m.true_peak_dbfs is not None:
                if m.true_peak_dbfs > PEAK_CLIP_DBFS:
                    checks.append(
                        QACheck(
                            level=QALevel.ERROR,
                            code="clipping",
                            message=f"音割れの恐れ（ピーク {m.true_peak_dbfs:.1f} dBFS）: {label}",
                            target=seg.id,
                        )
                    )
                elif m.true_peak_dbfs > PEAK_WARN_DBFS:
                    checks.append(
                        QACheck(
                            level=QALevel.WARNING,
                            code="peak_high",
                            message=f"ピークが高い（{m.true_peak_dbfs:.1f} dBFS）: {label}",
                            target=seg.id,
                        )
                    )
            if m.integrated_lufs is not None:
                loud.append(m.integrated_lufs)
                if m.integrated_lufs < VOICE_MIN_LUFS:
                    checks.append(
                        QACheck(
                            level=QALevel.WARNING,
                            code="too_quiet",
                            message=f"音量が小さい（{m.integrated_lufs:.1f} LUFS）: {label}",
                            target=seg.id,
                        )
                    )
            for sp in m.silences:
                if sp.start <= 0.01 and sp.duration > LEAD_SILENCE_SEC:
                    msg, code = f"冒頭の無音が長い（{sp.duration:.1f}秒）", "lead_silence"
                elif dur and sp.end >= dur - 0.05 and sp.duration > TAIL_SILENCE_SEC:
                    msg, code = f"末尾の無音が長い（{sp.duration:.1f}秒）", "tail_silence"
                elif (
                    0.01 < sp.start
                    and (not dur or sp.end < dur - 0.05)
                    and sp.duration > MID_SILENCE_SEC
                ):
                    msg, code = (
                        f"途中に長い無音（{sp.start:.1f}秒から {sp.duration:.1f}秒）",
                        "mid_silence",
                    )
                else:
                    continue
                checks.append(
                    QACheck(
                        level=QALevel.WARNING, code=code, message=f"{msg}: {label}", target=seg.id
                    )
                )
            n = _chars(seg.text)
            if dur and n >= 4:
                cps = n / dur
                if cps > SPEECH_CPS_MAX:
                    checks.append(
                        QACheck(
                            level=QALevel.WARNING,
                            code="speech_fast",
                            message=f"読み上げが速い（{cps:.1f} 文字/秒）: {label}",
                            target=seg.id,
                        )
                    )
                elif cps < SPEECH_CPS_MIN:
                    checks.append(
                        QACheck(
                            level=QALevel.WARNING,
                            code="speech_slow",
                            message=f"読み上げが遅い・無音が多い（{cps:.1f} 文字/秒）: {label}",
                            target=seg.id,
                        )
                    )
        if len(loud) >= 2 and max(loud) - min(loud) > VOICE_SPREAD_LU:
            checks.append(
                QACheck(
                    level=QALevel.WARNING,
                    code="loudness_spread",
                    message=f"セリフ間の音量差が大きい（{min(loud):.1f}〜{max(loud):.1f} LUFS）",
                )
            )
        if len(rates) > 1:
            checks.append(
                QACheck(
                    level=QALevel.INFO,
                    code="sample_rates",
                    message=f"サンプルレートが混在: {sorted(rates)}（書き出し時に揃えます）",
                )
            )
        return self._section(
            name,
            checks,
            {
                "segments": len(segs),
                "voiced": len(results),
                "loudness_min": round(min(loud), 1) if loud else None,
                "loudness_max": round(max(loud), 1) if loud else None,
            },
        )

    # ---------- P9-03 字幕同期 ----------
    async def _subtitle(
        self, project_id: str, script_id: str | None, fps: float, width: int, height: int
    ) -> QASection:
        name = QASectionName.SUBTITLE
        if script_id is None:
            return self._section(
                name,
                [QACheck(level=QALevel.INFO, code="no_script", message="台本がありません")],
                skipped=True,
            )
        cues = await self.subtitles.cues(script_id)
        if not cues:
            return self._section(
                name,
                [
                    QACheck(
                        level=QALevel.WARNING, code="no_cues", message="字幕が生成されていません"
                    )
                ],
            )
        async with self.db.session_factory() as s:
            segs = {
                x.id: x
                for x in (
                    await s.execute(
                        select(ScriptSegment).where(ScriptSegment.script_id == script_id)
                    )
                ).scalars()
            }
        # 字幕は行の終わりの無音（次の行の音声が始まるまで）には残ってよい
        timed_segs = sorted(
            (x for x in segs.values() if x.start_frame is not None),
            key=lambda x: x.start_frame or 0,
        )
        first_cue: dict[str, int] = {}
        for q in cues:
            if q.start_frame is not None:
                first_cue[q.script_segment_id] = min(
                    first_cue.get(q.script_segment_id, q.start_frame), q.start_frame
                )
        next_start = {
            x.id: max(y.start_frame or 0, first_cue.get(y.id, 0))
            for x, y in zip(timed_segs, timed_segs[1:], strict=False)
        }
        tl = await self.timeline.get_timeline(project_id)
        max_chars = default_max_chars(width, height)
        checks: list[QACheck] = []

        def at(frame: int | None) -> float | None:
            return None if frame is None else round(frame / fps, 2)

        unsynced = [c for c in cues if c.start_frame is None or c.end_frame is None]
        if unsynced:
            checks.append(
                QACheck(
                    level=QALevel.ERROR,
                    code="unsynced",
                    message=f"時刻が未設定の字幕 {len(unsynced)} 件（字幕の同期を実行してください）",
                    target=unsynced[0].id,
                )
            )
        timed = sorted(
            [c for c in cues if c.start_frame is not None and c.end_frame is not None],
            key=lambda c: (c.start_frame or 0, c.index),
        )
        for c in timed:
            assert c.start_frame is not None and c.end_frame is not None
            label = f"#{c.index + 1}「{c.text.replace(chr(10), ' ')[:16]}」"
            dur = (c.end_frame - c.start_frame) / fps
            if c.end_frame <= c.start_frame:
                checks.append(
                    QACheck(
                        level=QALevel.ERROR,
                        code="empty_span",
                        message=f"表示時間が 0: {label}",
                        target=c.id,
                        at_sec=at(c.start_frame),
                    )
                )
                continue
            if dur < CUE_MIN_SEC:
                checks.append(
                    QACheck(
                        level=QALevel.WARNING,
                        code="cue_short",
                        message=f"表示が短い（{dur:.2f}秒）: {label}",
                        target=c.id,
                        at_sec=at(c.start_frame),
                    )
                )
            elif dur > CUE_MAX_SEC:
                checks.append(
                    QACheck(
                        level=QALevel.WARNING,
                        code="cue_long",
                        message=f"表示が長い（{dur:.1f}秒）: {label}",
                        target=c.id,
                        at_sec=at(c.start_frame),
                    )
                )
            cps = _chars(c.text) / dur
            if cps > CUE_CPS_MAX:
                checks.append(
                    QACheck(
                        level=QALevel.WARNING,
                        code="cue_fast",
                        message=f"読み切れない速さ（{cps:.1f} 文字/秒）: {label}",
                        target=c.id,
                        at_sec=at(c.start_frame),
                    )
                )
            lines = c.text.split("\n")
            if len(lines) > 2:
                checks.append(
                    QACheck(
                        level=QALevel.WARNING,
                        code="too_many_lines",
                        message=f"{len(lines)} 行あります（2 行まで推奨）: {label}",
                        target=c.id,
                    )
                )
            longest = max(len(x) for x in lines)
            if longest > max_chars:
                checks.append(
                    QACheck(
                        level=QALevel.WARNING,
                        code="line_long",
                        message=f"1 行 {longest} 文字（目安 {max_chars} 文字）: {label}",
                        target=c.id,
                    )
                )
            seg = segs.get(c.script_segment_id)
            if seg and seg.start_frame is not None and seg.end_frame is not None:
                end_ok = max(seg.end_frame, next_start.get(seg.id) or seg.end_frame) + 1
                if c.start_frame < seg.start_frame - 1 or c.end_frame > end_ok:
                    checks.append(
                        QACheck(
                            level=QALevel.ERROR,
                            code="out_of_voice",
                            message=f"音声の区間からはみ出しています: {label}",
                            target=c.id,
                            at_sec=at(c.start_frame),
                        )
                    )
            if tl.duration_frames and c.start_frame >= tl.duration_frames:
                checks.append(
                    QACheck(
                        level=QALevel.WARNING,
                        code="after_end",
                        message=f"動画の終わりより後の字幕: {label}",
                        target=c.id,
                        at_sec=at(c.start_frame),
                    )
                )
        for a, b in zip(timed, timed[1:], strict=False):
            assert a.end_frame is not None and b.start_frame is not None
            gap = b.start_frame - a.end_frame
            if gap < 0:
                checks.append(
                    QACheck(
                        level=QALevel.ERROR,
                        code="overlap",
                        message=f"字幕 #{a.index + 1} と #{b.index + 1} が重なっています",
                        target=b.id,
                        at_sec=at(b.start_frame),
                    )
                )
            elif 0 < gap < 2:
                checks.append(
                    QACheck(
                        level=QALevel.INFO,
                        code="flicker",
                        message=f"字幕 #{a.index + 1} と #{b.index + 1} の間が {gap} フレーム（ちらつき）",
                        target=b.id,
                        at_sec=at(b.start_frame),
                    )
                )
        return self._section(name, checks, {"cues": len(cues), "max_chars_per_line": max_chars})

    # ---------- P9-04 映像品質 ----------
    async def _video(self, project_id: str, render: RenderJob | None, fps: float) -> QASection:
        name = QASectionName.VIDEO
        if render is None or not render.output_path:
            return self._section(
                name,
                [
                    QACheck(
                        level=QALevel.INFO, code="no_render", message="書き出した動画がありません"
                    )
                ],
                skipped=True,
            )
        path = self.settings.data_dir / render.output_path
        if not path.is_file():
            return self._section(
                name,
                [
                    QACheck(
                        level=QALevel.ERROR,
                        code="render_missing",
                        message=f"書き出したファイルがありません: {render.output_path}",
                        target=render.id,
                    )
                ],
            )
        checks: list[QACheck] = []
        info = await self.ffmpeg.probe(path)
        prof = RENDER_PROFILES[RenderProfileName(render.profile)]
        if (info.width, info.height) != (prof.width, prof.height):
            checks.append(
                QACheck(
                    level=QALevel.ERROR,
                    code="resolution",
                    message=f"解像度 {info.width}x{info.height}（期待 {prof.width}x{prof.height}）",
                    target=render.id,
                )
            )
        if info.fps is None or abs(info.fps - fps) > 0.01:
            checks.append(
                QACheck(
                    level=QALevel.ERROR,
                    code="fps",
                    message=f"フレームレート {info.fps}（期待 {fps:.3f}）",
                    target=render.id,
                )
            )
        if info.codec not in ("h264", "hevc"):
            checks.append(
                QACheck(
                    level=QALevel.WARNING,
                    code="codec",
                    message=f"映像コーデック {info.codec}（配信向けは H.264 推奨）",
                    target=render.id,
                )
            )
        tl = await self.timeline.get_timeline(project_id)
        req = render.request or {}
        start = req.get("start_frame") or 0
        end = req.get("end_frame") or tl.duration_frames
        expected = max(0, end - start) / fps
        if info.duration_sec is not None and abs(info.duration_sec - expected) > max(2 / fps, 0.1):
            checks.append(
                QACheck(
                    level=QALevel.WARNING,
                    code="duration",
                    message=f"長さ {info.duration_sec:.2f}秒（タイムライン {expected:.2f}秒）",
                    target=render.id,
                )
            )
        needs_audio = any(t.kind == "audio" and not t.muted for t in tl.tracks) or any(
            a.has_audio for a in tl.assets if a.id in {c.asset_id for c in tl.clips}
        )
        if needs_audio and not info.has_audio:
            checks.append(
                QACheck(
                    level=QALevel.ERROR,
                    code="no_audio",
                    message="音声トラックがありません",
                    target=render.id,
                )
            )
        m = await measure(self.ffmpeg, path, audio=True, video=True)
        if m.integrated_lufs is not None and req.get("loudnorm", True):
            if abs(m.integrated_lufs - TARGET_LUFS) > LUFS_TOLERANCE:
                checks.append(
                    QACheck(
                        level=QALevel.WARNING,
                        code="loudness",
                        message=f"ラウドネス {m.integrated_lufs:.1f} LUFS（目標 {TARGET_LUFS:.0f}）",
                        target=render.id,
                    )
                )
        if m.true_peak_dbfs is not None and m.true_peak_dbfs > PEAK_WARN_DBFS:
            level = QALevel.ERROR if m.true_peak_dbfs > PEAK_CLIP_DBFS else QALevel.WARNING
            checks.append(
                QACheck(
                    level=level,
                    code="peak",
                    message=f"トゥルーピーク {m.true_peak_dbfs:.1f} dBFS（-1 以下推奨）",
                    target=render.id,
                )
            )
        for b in m.blacks:
            if b.duration >= BLACK_WARN_SEC:
                checks.append(
                    QACheck(
                        level=QALevel.WARNING,
                        code="black",
                        message=f"黒画面 {b.start:.2f}〜{b.end:.2f}秒（{b.duration:.1f}秒）",
                        target=render.id,
                        at_sec=round(b.start, 2),
                    )
                )
        for f in m.freezes:
            if f.duration >= FREEZE_INFO_SEC:
                checks.append(
                    QACheck(
                        level=QALevel.INFO,
                        code="freeze",
                        message=f"画面が {f.duration:.1f}秒 止まっています（{f.start:.1f}秒から。静止画なら問題なし）",
                        target=render.id,
                        at_sec=round(f.start, 2),
                    )
                )
        if m.duration_sec and info.has_audio:
            for sp in m.silences:
                if sp.duration >= 3.0:
                    checks.append(
                        QACheck(
                            level=QALevel.INFO,
                            code="silence",
                            message=f"無音 {sp.start:.1f}〜{sp.end:.1f}秒",
                            target=render.id,
                            at_sec=round(sp.start, 2),
                        )
                    )
        for sub in render.subtitle_paths or []:
            if not (self.settings.data_dir / sub).is_file():
                checks.append(
                    QACheck(
                        level=QALevel.ERROR,
                        code="subtitle_file_missing",
                        message=f"字幕ファイルがありません: {sub}",
                        target=render.id,
                    )
                )
        return self._section(
            name,
            checks,
            {
                "file": render.output_path,
                "width": info.width,
                "height": info.height,
                "fps": info.fps,
                "codec": info.codec,
                "duration_sec": info.duration_sec,
                "size_mb": round(path.stat().st_size / 1e6, 2),
                "loudness_lufs": m.integrated_lufs,
                "true_peak_dbfs": m.true_peak_dbfs,
                "encoder": render.encoder,
            },
        )

    # ---------- P9-05 ライセンス ----------
    async def _license(self, project_id: str) -> QASection:
        tl = await self.timeline.get_timeline(project_id)
        used = {c.asset_id for c in tl.clips}
        async with self.db.session_factory() as s:
            assets = {
                a.id: a
                for a in (await s.execute(select(Asset).where(Asset.id.in_(used)))).scalars()
            }
            lic = {
                r.asset_id: r
                for r in (
                    await s.execute(
                        select(AssetLicenseRow).where(AssetLicenseRow.asset_id.in_(used))
                    )
                ).scalars()
            }
            voice_ids = [a.audio_file_id for a in assets.values() if a.audio_file_id]
            voice = {
                v.id: v
                for v in (
                    await s.execute(select(AudioFile).where(AudioFile.id.in_(voice_ids)))
                ).scalars()
            }
        clips_by_asset: dict[str, list[Any]] = {}
        for c in tl.clips:
            clips_by_asset.setdefault(c.asset_id, []).append(c)
        checks: list[QACheck] = []
        attribution: list[str] = []
        local = 0
        for aid in sorted(used):
            a = assets.get(aid)
            if a is None:
                continue
            row = lic.get(aid)
            if row is None:
                if a.audio_file_id and a.audio_file_id in voice:
                    credit = voice[a.audio_file_id].credit
                    if credit and credit not in attribution:
                        attribution.append(credit)
                elif a.source == "import":  # アプリ内で作った素材（タイトル・図表等）は対象外
                    local += 1
                continue
            info = row.license or {}
            name = f"「{a.name}」（{row.source} / {row.license_code}）"
            if row.verdict != "allowed":
                if row.acknowledged:
                    checks.append(
                        QACheck(
                            level=QALevel.WARNING,
                            code="acknowledged",
                            message=f"条件付きで使用（確認済み）: {name}",
                            target=aid,
                        )
                    )
                else:
                    checks.append(
                        QACheck(
                            level=QALevel.ERROR,
                            code="not_acknowledged",
                            message=f"ライセンス未確認: {name}",
                            target=aid,
                        )
                    )
            if info.get("commercial_use") is False:
                checks.append(
                    QACheck(
                        level=QALevel.WARNING,
                        code="non_commercial",
                        message=f"商用利用不可（収益化する動画では使えません）: {name}",
                        target=aid,
                    )
                )
            elif info.get("commercial_use") is None and row.verdict == "allowed":
                checks.append(
                    QACheck(
                        level=QALevel.INFO,
                        code="commercial_unknown",
                        message=f"商用利用の可否が不明: {name}",
                        target=aid,
                    )
                )
            if info.get("modification") is False:
                edited = any(
                    c.animation is not None
                    or c.source_in_frame > 0
                    or (
                        a.duration_sec
                        and (c.source_out_frame - c.source_in_frame) / (tl.fps_num / tl.fps_den)
                        < a.duration_sec - 0.1
                    )
                    for c in clips_by_asset.get(aid, [])
                )
                if edited:
                    checks.append(
                        QACheck(
                            level=QALevel.WARNING,
                            code="no_derivatives",
                            message=f"改変禁止の素材を切り出し・加工しています: {name}",
                            target=aid,
                        )
                    )
            if info.get("share_alike"):
                checks.append(
                    QACheck(
                        level=QALevel.INFO,
                        code="share_alike",
                        message=f"継承条件あり（動画も同じライセンスで公開する必要）: {name}",
                        target=aid,
                    )
                )
            if info.get("attribution_required") or row.attribution:
                text = row.attribution or row.title
                if text and text not in attribution:
                    attribution.append(text)
        if attribution:
            checks.append(
                QACheck(
                    level=QALevel.INFO,
                    code="credits",
                    message=f"クレジット表記が必要: {len(attribution)} 件（概要欄に記載してください）",
                )
            )
        if local:
            checks.append(
                QACheck(
                    level=QALevel.INFO,
                    code="local_assets",
                    message=f"取り込んだ素材 {local} 件は取得元の記録がありません（権利を確認してください）",
                )
            )
        return self._section(
            QASectionName.LICENSE,
            checks,
            {"used_assets": len(used), "licensed": len(lic), "credits": attribution},
        )
