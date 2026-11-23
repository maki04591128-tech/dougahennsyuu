"""レンダリング (P5-06〜P5-10)。タイムラインから FFmpeg の filter_complex を組み立てる。

- P5-06 16:9 / 9:16: クリップごとに fit / fill（焦点位置でクロップ）/ blur（ぼかし背景）
- P5-07 FHD / 4K: 出力プロファイル。NVENC が使えれば利用（auto）
- P5-08 字幕焼き込み: Phase 4 の字幕データから出力解像度の ASS を作り libass で描画
- P5-09 音声ミックス: 配置・音量・フェード・ラウドネス正規化（-14 LUFS）
- P5-10 MP4 出力: Job で実行、進捗通知、キャンセル時は FFmpeg を終了
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path
from typing import Any

from sqlalchemy import select

from video_automation.app.logging import get_logger
from video_automation.app.settings import Settings
from video_automation.core.jobs.job_manager import JobManager
from video_automation.database.database import Database
from video_automation.database.models import Asset, Clip, Project, RenderJob, Script, Track
from video_automation.integrations.ffmpeg.runner import FFmpeg, ff_seconds, filter_path
from video_automation.schemas.subtitle import SubtitleExportRequest, SubtitleFormat, SubtitleStyle
from video_automation.schemas.timeline import (
    RENDER_PROFILES,
    AssetKind,
    ClipTransform,
    CommandOrigin,
    Encoder,
    FitMode,
    PreviewRequest,
    RenderJobRead,
    RenderRequest,
    RenderStatus,
    TrackKind,
)
from video_automation.services.subtitle.formats import build_ass
from video_automation.services.subtitle.subtitle_service import SubtitleService
from video_automation.services.timeline.timeline_service import (
    CommandError,
    TimelineNotFoundError,
    TimelineService,
)

log = get_logger("render")
ProgressFn = Callable[[float], Awaitable[None]]
FILTER_SCRIPT_THRESHOLD = 6000  # これ以上長いグラフはファイル経由（Windows のコマンド長制限対策）


@dataclass
class RenderPlan:
    args: list[str]
    duration_sec: float
    width: int
    height: int


def _layout(t: ClipTransform, cw: int, ch: int, label_in: str, label_out: str, idx: int) -> str:
    """素材をキャンバス (cw x ch) に合わせる。出力は cw x ch の RGBA。"""
    tw, th = max(2, round(cw * t.scale)), max(2, round(ch * t.scale))
    if t.fit == FitMode.FILL:
        return (
            f"[{label_in}]scale={tw}:{th}:force_original_aspect_ratio=increase,"
            f"crop={tw}:{th}:(iw-{tw})*{t.focus_x}:(ih-{th})*{t.focus_y},"
            f"format=rgba[{label_out}]"
        )
    fg = f"scale={tw}:{th}:force_original_aspect_ratio=decrease,format=rgba"
    if t.fit == FitMode.FIT:
        return f"[{label_in}]{fg}[{label_out}]"
    # BLUR: 背景＝同じ映像を画面いっぱいに拡大してぼかし、前景＝全体を収める
    return (
        f"[{label_in}]split[bgs{idx}][fgs{idx}];"
        f"[bgs{idx}]scale={cw}:{ch}:force_original_aspect_ratio=increase,crop={cw}:{ch},"
        f"boxblur=20:2,format=rgba[bg{idx}];"
        f"[fgs{idx}]{fg}[fg{idx}];"
        f"[bg{idx}][fg{idx}]overlay=(W-w)/2:(H-h)/2,format=rgba[{label_out}]"
    )


class RenderService:
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
        self._nvenc: bool | None = None

    async def _encoder(self, choice: Encoder) -> str:
        if choice == Encoder.AUTO:
            if self._nvenc is None:
                self._nvenc = await self.ffmpeg.nvenc_usable()
            return Encoder.NVENC.value if self._nvenc else Encoder.LIBX264.value
        return choice.value

    async def _ass_for(
        self,
        script_id: str,
        width: int,
        height: int,
        out: Path,
        offset_frames: int = 0,
        fade: bool = True,
    ) -> Path:
        """出力解像度の PlayRes で ASS を作る（同じ字幕データから焼き込み用を生成）。

        範囲指定の出力では出力の先頭＝タイムラインの offset_frames なので、その分ずらす。"""
        cues = await self.subtitles.cues(script_id)
        if not cues or any(c.start_frame is None for c in cues):
            raise CommandError("subtitles are not synchronized")
        async with self.db.session_factory() as s:
            script = await s.get(Script, script_id)
            assert script is not None
            p = await s.get(Project, script.project_id)
            assert p is not None
        rows = [
            (
                max(0, (c.start_frame or 0) - offset_frames),
                (c.end_frame or 0) - offset_frames,
                c.speaker,
                c.text,
            )
            for c in cues
            if (c.end_frame or 0) > offset_frames
        ]
        content = build_ass(
            rows,
            Fraction(p.fps_num, p.fps_den),
            width,
            height,
            SubtitleStyle() if fade else SubtitleStyle(fade_in_ms=0, fade_out_ms=0),
        )
        out.write_text(content, encoding="utf-8-sig", newline="")
        return out

    async def plan(
        self,
        project_id: str,
        width: int,
        height: int,
        output: Path,
        *,
        encoder: str = "libx264",
        quality: int = 20,
        start_frame: int | None = None,
        end_frame: int | None = None,
        ass_path: Path | None = None,
        loudnorm: bool = True,
        fast: bool = False,
        single_frame: int | None = None,
    ) -> RenderPlan:
        async with self.db.session_factory() as s:
            p = await s.get(Project, project_id)
            if p is None:
                raise TimelineNotFoundError(f"project {project_id}")
            fps = Fraction(p.fps_num, p.fps_den)
            tracks = {
                t.id: t
                for t in (
                    await s.execute(select(Track).where(Track.project_id == project_id))
                ).scalars()
            }
            clips = list(
                (await s.execute(select(Clip).where(Clip.project_id == project_id))).scalars()
            )
            assets = {
                a.id: a
                for a in (
                    await s.execute(select(Asset).where(Asset.project_id == project_id))
                ).scalars()
            }
        clips = [c for c in clips if not tracks[c.track_id].muted]
        timeline_end = max((c.end_frame for c in clips), default=0)
        if timeline_end == 0:
            raise CommandError("timeline is empty")
        r0 = start_frame or 0
        r1 = min(end_frame or timeline_end, timeline_end)
        if single_frame is not None:
            r0, r1 = single_frame, single_frame + 1
        if r1 <= r0:
            raise CommandError("render range is empty")

        def sec(frames: int) -> float:
            return float(Fraction(frames) / fps)

        total = sec(r1 - r0)
        fps_s = f"{fps.numerator}/{fps.denominator}"
        inputs: list[str] = [
            "-f",
            "lavfi",
            "-i",
            f"color=c=black:s={width}x{height}:r={fps_s}:d={ff_seconds(total)}",
        ]
        filters: list[str] = []
        base = "0:v"
        audio_labels: list[str] = []
        n = 1
        # 下のトラック（index 小）から順に重ねる
        visual = sorted(
            (c for c in clips if tracks[c.track_id].kind == TrackKind.VIDEO.value),
            key=lambda c: (tracks[c.track_id].index, c.start_frame),
        )
        audible = [
            c
            for c in clips
            if (
                tracks[c.track_id].kind == TrackKind.AUDIO.value
                or (
                    assets[c.asset_id].kind == AssetKind.VIDEO.value
                    and assets[c.asset_id].has_audio
                )
            )
            and c.volume > 0
            and single_frame is None  # 1フレームのプレビューでは音声を扱わない
        ]
        used: dict[str, int] = {}
        for c in sorted(set(visual) | set(audible), key=lambda c: c.start_frame):
            s0, s1 = max(c.start_frame, r0), min(c.end_frame, r1)
            if s1 <= s0:
                continue
            a = assets[c.asset_id]
            src_path = str(self.timeline.asset_path(a))
            dur = sec(s1 - s0)
            src_in = sec(c.source_in_frame + (s0 - c.start_frame))
            if a.kind == AssetKind.IMAGE.value:
                inputs += ["-loop", "1", "-framerate", fps_s, "-t", ff_seconds(dur), "-i", src_path]
            else:
                inputs += ["-ss", ff_seconds(src_in), "-t", ff_seconds(dur), "-i", src_path]
            used[c.id] = n
            n += 1

        for c in visual:
            if c.id not in used:
                continue
            i = used[c.id]
            s0 = max(c.start_frame, r0)
            off = sec(s0 - r0)
            dur = sec(min(c.end_frame, r1) - s0)
            t = ClipTransform.model_validate(c.transform or {})
            fades = []
            if c.fade_in_frames and s0 == c.start_frame:
                fades.append(f"fade=t=in:st=0:d={ff_seconds(sec(c.fade_in_frames))}:alpha=1")
            if c.fade_out_frames and min(c.end_frame, r1) == c.end_frame:
                st = max(0.0, dur - sec(c.fade_out_frames))
                fades.append(
                    f"fade=t=out:st={ff_seconds(st)}:d={ff_seconds(sec(c.fade_out_frames))}:alpha=1"
                )
            if t.opacity < 1:
                fades.append(f"colorchannelmixer=aa={t.opacity}")
            filters.append(f"[{i}:v]fps={fps_s},setpts=PTS-STARTPTS[raw{i}]")
            filters.append(_layout(t, width, height, f"raw{i}", f"lay{i}", i))
            chain = ",".join([*fades, f"setpts=PTS+{ff_seconds(off)}/TB"])
            filters.append(f"[lay{i}]{chain}[v{i}]")
            x = f"(W-w)/2+{t.x}*W"
            y = f"(H-h)/2+{t.y}*H"
            filters.append(
                f"[{base}][v{i}]overlay=x={x}:y={y}:eof_action=pass:"
                f"enable='between(t,{ff_seconds(off)},{ff_seconds(off + dur)})'[b{i}]"
            )
            base = f"b{i}"

        for c in audible:
            if c.id not in used:
                continue
            i = used[c.id]
            s0 = max(c.start_frame, r0)
            dur = sec(min(c.end_frame, r1) - s0)
            ms = round(sec(s0 - r0) * 1000)
            parts = [
                "asetpts=PTS-STARTPTS",
                "aresample=48000",
                "aformat=sample_fmts=fltp:channel_layouts=stereo",
                f"volume={c.volume}",
            ]
            if c.fade_in_frames and s0 == c.start_frame:
                parts.append(f"afade=t=in:st=0:d={ff_seconds(sec(c.fade_in_frames))}")
            if c.fade_out_frames and min(c.end_frame, r1) == c.end_frame:
                st = max(0.0, dur - sec(c.fade_out_frames))
                parts.append(
                    f"afade=t=out:st={ff_seconds(st)}:d={ff_seconds(sec(c.fade_out_frames))}"
                )
            parts.append(f"adelay=delays={ms}:all=1")
            filters.append(f"[{i}:a]{','.join(parts)}[a{i}]")
            audio_labels.append(f"[a{i}]")

        vout = base
        if ass_path is not None:
            filters.append(f"[{vout}]ass='{filter_path(ass_path)}'[vsub]")
            vout = "vsub"
        filters.append(f"[{vout}]format=yuv420p[vout]")
        if single_frame is not None:
            pass  # 音声出力なし
        elif audio_labels:
            mix = (
                f"{''.join(audio_labels)}amix=inputs={len(audio_labels)}:normalize=0:"
                f"duration=longest"
            )
            tail = f",apad,atrim=0:{ff_seconds(total)}"
            if loudnorm and not fast and single_frame is None:
                tail += ",loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000"
            filters.append(f"{mix}{tail}[aout]")
        else:
            filters.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{ff_seconds(total)}[aout]")

        graph = ";".join(filters)
        graph_args: list[str]
        if len(graph) > FILTER_SCRIPT_THRESHOLD:
            gfile = output.with_suffix(".filtergraph.txt")
            gfile.write_text(graph, encoding="utf-8")
            major = int((await self.ffmpeg.version()).split(".")[0].lstrip("n") or 0)
            graph_args = (
                ["-/filter_complex", str(gfile)]
                if major >= 7
                else ["-filter_complex_script", str(gfile)]
            )
        else:
            graph_args = ["-filter_complex", graph]

        if single_frame is not None:
            out_args = ["-map", "[vout]", "-frames:v", "1", "-update", "1", str(output)]
        else:
            if encoder == Encoder.NVENC.value:
                venc = [
                    "-c:v",
                    "h264_nvenc",
                    "-preset",
                    "p1" if fast else "p5",
                    "-rc",
                    "vbr",
                    "-cq",
                    str(quality),
                    "-b:v",
                    "0",
                ]
            else:
                venc = [
                    "-c:v",
                    "libx264",
                    "-preset",
                    "ultrafast" if fast else "medium",
                    "-crf",
                    str(quality if not fast else 30),
                ]
            out_args = [
                "-map",
                "[vout]",
                "-map",
                "[aout]",
                *venc,
                "-r",
                fps_s,
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-ar",
                "48000",
                "-movflags",
                "+faststart",
                "-t",
                ff_seconds(total),
                str(output),
            ]
        return RenderPlan(
            args=[*inputs, *graph_args, *out_args], duration_sec=total, width=width, height=height
        )

    # ---------- プレビュー ----------
    async def preview_frame(self, project_id: str, req: PreviewRequest) -> bytes:
        pw, ph = await self._preview_size(project_id, req)
        out_dir = self.settings.data_dir / "cache" / "preview"
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"{project_id}_frame.png"
        ass = None
        if req.script_id:
            ass = await self._ass_for(
                req.script_id, pw, ph, out_dir / f"{project_id}_p.ass", req.frame or 0, fade=False
            )
        plan = await self.plan(
            project_id, pw, ph, out, single_frame=req.frame or 0, ass_path=ass, fast=True
        )
        await self.ffmpeg.run(plan.args, timeout=300)
        return out.read_bytes()

    async def _preview_size(self, project_id: str, req: PreviewRequest) -> tuple[int, int]:
        async with self.db.session_factory() as s:
            p = await s.get(Project, project_id)
            if p is None:
                raise TimelineNotFoundError(f"project {project_id}")
            w, h = p.width, p.height
        if req.profile is not None:
            prof = RENDER_PROFILES[req.profile]
            w, h = prof.width, prof.height
        pw = req.width - req.width % 2
        ph = max(2, round(h * pw / w))
        return pw, ph - ph % 2

    async def preview_video(
        self, project_id: str, req: PreviewRequest, progress: ProgressFn | None = None
    ) -> str:
        pw, ph = await self._preview_size(project_id, req)
        out_dir = self.settings.data_dir / "projects" / project_id / "previews"
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / "preview.mp4"
        ass = None
        if req.script_id:
            ass = await self._ass_for(
                req.script_id, pw, ph, out_dir / "preview.ass", req.start_frame or 0
            )
        plan = await self.plan(
            project_id,
            pw,
            ph,
            out,
            start_frame=req.start_frame,
            end_frame=req.end_frame,
            ass_path=ass,
            fast=True,
        )
        await self.ffmpeg.run(plan.args, total_sec=plan.duration_sec, progress=progress)
        return out.relative_to(self.settings.data_dir).as_posix()

    # ---------- 本番レンダリング ----------
    async def request(self, req: RenderRequest) -> RenderJobRead:
        """origin=llm の場合は承認待ち（最終レンダリングは承認が必要：仕様 §10）。"""
        async with self.db.session_factory() as s:
            if await s.get(Project, req.project_id) is None:
                raise TimelineNotFoundError(f"project {req.project_id}")
            row = RenderJob(
                project_id=req.project_id,
                profile=req.profile.value,
                status=RenderStatus.PENDING_APPROVAL.value
                if req.origin == CommandOrigin.LLM
                else RenderStatus.QUEUED.value,
                request=req.model_dump(mode="json"),
            )
            s.add(row)
            await s.commit()
            rid = row.id
        if req.origin != CommandOrigin.LLM:
            await self._start(rid)
        return await self.get(rid)

    async def decide(self, render_id: str, approve: bool) -> RenderJobRead:
        async with self.db.session_factory() as s:
            row = await s.get(RenderJob, render_id)
            if row is None:
                raise TimelineNotFoundError(f"render {render_id}")
            if row.status != RenderStatus.PENDING_APPROVAL.value:
                raise CommandError(f"render is {row.status}")
            row.status = (RenderStatus.QUEUED if approve else RenderStatus.REJECTED).value
            await s.commit()
        if approve:
            await self._start(render_id)
        return await self.get(render_id)

    async def _set(self, render_id: str, **values: Any) -> None:
        async with self.db.session_factory() as s:
            row = await s.get(RenderJob, render_id)
            assert row is not None
            for k, v in values.items():
                setattr(row, k, v)
            await s.commit()

    async def _start(self, render_id: str) -> None:
        async with self.db.session_factory() as s:
            row = await s.get(RenderJob, render_id)
            assert row is not None
            req = RenderRequest.model_validate(row.request)

        async def run(progress: ProgressFn) -> dict[str, Any]:
            try:
                return await self._render(render_id, req, progress)
            except BaseException as exc:
                cancelled = not isinstance(exc, Exception)
                await self._set(
                    render_id,
                    status=(RenderStatus.CANCELLED if cancelled else RenderStatus.FAILED).value,
                    error=None if cancelled else str(exc)[:1000],
                    finished_at=datetime.now(UTC),
                )
                raise

        job = self.jobs.submit("render", run)
        await self._set(render_id, job_id=job.id)

    async def _render(
        self, render_id: str, req: RenderRequest, progress: ProgressFn
    ) -> dict[str, Any]:
        prof = RENDER_PROFILES[req.profile]
        encoder = await self._encoder(req.encoder)
        out_dir = self.settings.data_dir / "projects" / req.project_id / "renders"
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = out_dir / f"{stamp}_{prof.name.value}.mp4"
        await self._set(render_id, status=RenderStatus.RENDERING.value, encoder=encoder)
        ass = None
        subs: list[str] = []
        if req.script_id:
            if req.burn_subtitles:
                ass = await self._ass_for(
                    req.script_id,
                    prof.width,
                    prof.height,
                    out.with_suffix(".burn.ass"),
                    req.start_frame or 0,
                )
            if req.external_subtitles:
                for fmt in (SubtitleFormat.SRT, SubtitleFormat.ASS):
                    r = await self.subtitles.export(
                        SubtitleExportRequest(script_id=req.script_id, format=fmt)
                    )
                    subs.append(r.path)
        plan = await self.plan(
            req.project_id,
            prof.width,
            prof.height,
            out,
            encoder=encoder,
            quality=req.quality,
            start_frame=req.start_frame,
            end_frame=req.end_frame,
            ass_path=ass,
            loudnorm=req.loudnorm,
        )
        await self.ffmpeg.run(plan.args, total_sec=plan.duration_sec, progress=progress)
        info = await self.ffmpeg.probe(out)
        rel = out.relative_to(self.settings.data_dir).as_posix()
        await self._set(
            render_id,
            status=RenderStatus.SUCCEEDED.value,
            output_path=rel,
            subtitle_paths=subs,
            finished_at=datetime.now(UTC),
        )
        log.info("render_done", render_id=render_id, encoder=encoder, path=rel)
        return {
            "output_path": rel,
            "duration_sec": info.duration_sec,
            "width": info.width,
            "height": info.height,
            "encoder": encoder,
            "subtitle_paths": subs,
        }

    async def get(self, render_id: str) -> RenderJobRead:
        async with self.db.session_factory() as s:
            row = await s.get(RenderJob, render_id)
            if row is None:
                raise TimelineNotFoundError(f"render {render_id}")
            return RenderJobRead.model_validate(row)

    async def list(self, project_id: str) -> list[RenderJobRead]:
        async with self.db.session_factory() as s:
            q = select(RenderJob).where(RenderJob.project_id == project_id)
            rows = (await s.execute(q.order_by(RenderJob.created_at))).scalars()
            return [RenderJobRead.model_validate(r) for r in rows]
