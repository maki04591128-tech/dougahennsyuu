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
from video_automation.integrations.blender.runner import Blender, BlenderError
from video_automation.integrations.ffmpeg.runner import FFmpeg, ff_seconds, filter_path
from video_automation.schemas.animation import ClipAnimation, TransitionType
from video_automation.schemas.design import DesignCheck
from video_automation.schemas.subtitle import SubtitleExportRequest, SubtitleFormat, SubtitleStyle
from video_automation.schemas.timeline import (
    RENDER_PROFILES,
    AssetKind,
    ClipTransform,
    CommandOrigin,
    CreditsMode,
    Encoder,
    FitMode,
    PreviewRequest,
    RenderJobRead,
    RenderRequest,
    RenderStatus,
    RichLevel,
    TrackKind,
)
from video_automation.services.animation.render_anim import (
    EXTENDS_PREVIOUS,
    ClipAnimFilters,
    clip_filters,
    defaults_only,
)
from video_automation.services.design.service import DesignService, DesignViolationError
from video_automation.services.rendering.flash_check import check_video
from video_automation.services.rendering.rich import RichPlan, max_level, plan_rich
from video_automation.services.subtitle.formats import ass_color, build_ass
from video_automation.services.subtitle.subtitle_service import SubtitleService
from video_automation.services.timeline.timeline_service import (
    CommandError,
    TimelineNotFoundError,
    TimelineService,
    asset_frames,
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


def video_codec_args(encoder: str, quality: int, fast: bool = False) -> list[str]:
    if encoder == Encoder.NVENC.value:
        return [
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
    return [
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast" if fast else "medium",
        "-crf",
        str(quality if not fast else 30),
    ]


def credit_card_ass(
    text: str, w: int, h: int, sec: float, fg: str, sub: str, font: str, head: str
) -> str:
    """クレジットの画面の ASS（中央にまとめる。画面では URL を省き、幅で折り返す。
    行が多いほど文字を小さくする）。URL を含む全文は .credits.txt（概要欄用）に残る。"""
    import re

    raw = []
    for ln in text.splitlines():
        ln = re.sub(r"\s*\(https?://.*?\)(?=,|\s*$)", "", ln)
        ln = re.sub(r"https?://\S+", "", ln).strip().lstrip("- ").strip()
        if ln:
            raw.append(ln)

    def em(ch: str) -> float:  # 文字の幅（全角 1・半角 0.55）
        return 1.0 if ord(ch) > 0x2E7F else 0.55

    def wrap(lines: list[str], size: float) -> list[str]:
        limit = w * 0.84 / size
        out: list[str] = []
        parts: list[str] = []
        for ln in lines:  # 「 / 」で区切った並び（声の一覧）は、入りきらなければ区切りで分ける
            if sum(em(ch) for ch in ln) > limit and " / " in ln:
                parts.extend(x.strip() for x in ln.split(" / "))
            else:
                parts.append(ln)
        for ln in parts:
            # 英数字は語（空白まで）で、全角は 1 文字ずつ折り返す
            tokens = re.findall(r"[!-~“”‘’]+\s*|\s+|.", ln)
            cur, cw = "", 0.0
            for tok in tokens:
                tw = sum(em(ch) for ch in tok)
                if cw + tw > limit and cur.strip():
                    out.append(cur.rstrip())
                    cur, cw = "", 0.0
                    tok = tok.lstrip()
                    tw = sum(em(ch) for ch in tok)
                cur += tok
                cw += tw
            if cur.strip():
                out.append(cur.rstrip())
        return out

    unit = min(w, h)  # 縦長でも横長と同じ見え方の大きさにする
    size = unit * 0.042
    lines = wrap(raw, size)
    for _ in range(6):  # 縦に収まるまで小さくする
        if len(lines) * size * 1.45 <= h * 0.62 or size <= unit * 0.02:
            break
        size = max(unit * 0.02, size * 0.85)
        lines = wrap(raw, size)
    big = round(unit * 0.042 * 1.35)

    def esc(t: str) -> str:
        return t.replace("\\", "＼").replace("{", "｛").replace("}", "｝")

    body = r"\N".join(esc(ln) for ln in lines)
    size = round(size)
    end = f"0:{int(sec // 60):02d}:{sec % 60:05.2f}"
    return (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {w}\nPlayResY: {h}\nWrapStyle: 2\n\n"  # 折り返しは自前で行う
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Head,{head},{big},{ass_color(sub)},&H00FFFFFF,&H00000000,&H00000000,"
        "1,0,0,0,100,100,2,0,1,0,0,8,40,40,0,1\n"
        f"Style: Body,{font},{size},{ass_color(fg)},&H00FFFFFF,&H00000000,&H00000000,"
        "0,0,0,0,100,100,0,0,1,0,0,5,80,80,0,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        f"Dialogue: 0,0:00:00.00,{end},Head,,0,0,0,,"
        f"{{\\fad(300,300)\\pos({w // 2},{round(h * 0.12)})}}クレジット\n"
        f"Dialogue: 0,0:00:00.00,{end},Body,,0,0,0,,"
        f"{{\\fad(300,300)\\pos({w // 2},{round(h * 0.55)})}}{body}\n"
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
        design: DesignService | None = None,
        blender: Blender | None = None,
        font_for: Callable[[str | None], str | None] | None = None,
    ) -> None:
        self.design = design
        self.blender = blender
        self.font_for = font_for  # 書体の名前 → ファイル（画面演出の文字に使う）
        # クレジットの文（使っている素材だけ）。素材のサービスが後から設定する
        self.credits_for: Callable[[str], Awaitable[str]] | None = None
        # 立ち絵（台本のキャラクターに立ち絵があれば、書き出しの前に自動で置く）
        self.characters: Any = None
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

    async def _clear_of_tachie(
        self, project_id: str, width: int, height: int, style: SubtitleStyle
    ) -> SubtitleStyle:
        """縦長で立ち絵を置いたときは、字幕を立ち絵の上に出す（下に並ぶキャラクターに重ねない）。

        横長の立ち絵は左右の端なので、字幕（中央）とは重ならない。"""
        if height <= width:
            return style
        from video_automation.services.characters.overlay import layout
        from video_automation.services.characters.service import TRACK

        tl = await self.timeline.get_timeline(project_id)
        track = next((t for t in tl.tracks if t.name == TRACK and not t.muted), None)
        if track is None or not any(c.track_id == track.id for c in tl.clips):
            return style
        need = round(layout(width, height)[1] + 0.03, 3)
        if style.margin_v_ratio >= need:
            return style
        return style.model_copy(update={"margin_v_ratio": need})

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
        style = await self.subtitles.project_style(p.id)
        style = await self._clear_of_tachie(p.id, width, height, style)
        if not fade:
            style = style.model_copy(update={"fade_in_ms": 0, "fade_out_ms": 0})
        content = build_ass(
            rows,
            Fraction(p.fps_num, p.fps_den),
            width,
            height,
            style,
            speaker_accents=await self.subtitles.speaker_accents(script_id),
            speaker_fonts=await self.subtitles.speaker_fonts(script_id),
            # 範囲を切り出しても、話者名を出すかどうかは台本全体で決める
            multi_speaker=len({c.speaker for c in cues if c.speaker}) >= 2,
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
        # ---- アニメーション（Phase 7） ----
        anims = {c.id: ClipAnimation.model_validate(c.animation) for c in visual if c.animation}
        # 入りのトランジションの間は前のクリップを延長して下に敷く
        # （素材の続きが無い分は最終フレームで止める）
        ext: dict[str, int] = {}
        dip_out: dict[str, int] = {}
        by_track_end = {(c.track_id, c.end_frame): c for c in visual}
        for c in visual:
            tran = anims[c.id].transition_in if c.id in anims else None
            prev = by_track_end.get((c.track_id, c.start_frame))
            if tran is None or prev is None:
                continue
            d = min(tran.duration_frames, c.end_frame - c.start_frame)
            if tran.type == TransitionType.DIP_TO_BLACK:
                dip_out[prev.id] = max(1, d // 2)
            elif tran.type in EXTENDS_PREVIOUS:
                ext[prev.id] = max(ext.get(prev.id, 0), d)

        used: dict[str, int] = {}
        span: dict[str, tuple[int, int, float, float]] = {}  # s0, s1, 実素材の秒, 静止で埋める秒
        for c in sorted(set(visual) | set(audible), key=lambda c: c.start_frame):
            s0, s1 = max(c.start_frame, r0), min(c.end_frame + ext.get(c.id, 0), r1)
            if s1 <= s0:
                continue
            a = assets[c.asset_id]
            src_path = str(self.timeline.asset_path(a))
            dur = sec(s1 - s0)
            src_in = sec(c.source_in_frame + (s0 - c.start_frame))
            if a.kind == AssetKind.IMAGE.value:
                media, pad = dur, 0.0
                inputs += ["-loop", "1", "-framerate", fps_s, "-t", ff_seconds(dur), "-i", src_path]
            else:
                total_src = asset_frames(a, float(fps))
                handle = (total_src - c.source_out_frame) if total_src is not None else 0
                media_end = min(s1, c.end_frame + max(0, handle))
                media = sec(max(media_end - s0, 1))
                pad = max(0.0, dur - media)
                if src_path.lower().endswith(".webm") and a.source == "characters":
                    # 透過つき VP9（立ち絵）。内蔵の vp9 復号器は透過を捨てるので libvpx で読む
                    inputs += ["-c:v", "libvpx-vp9"]
                inputs += ["-ss", ff_seconds(src_in), "-t", ff_seconds(media), "-i", src_path]
            used[c.id] = n
            span[c.id] = (s0, s1, media, pad)
            n += 1

        for c in visual:
            if c.id not in used:
                continue
            i = used[c.id]
            s0, s1, _, pad = span[c.id]
            a = assets[c.asset_id]
            off = sec(s0 - r0)
            dur = sec(s1 - s0)
            own_end = sec(c.end_frame - s0)  # 延長前のクリップの終わり（このクリップの時刻）
            t = ClipTransform.model_validate(c.transform or {})
            anim = anims.get(c.id)
            af = (
                clip_filters(
                    anim,
                    fps=fps,
                    k=sec(s0 - c.start_frame),
                    off=off,
                    clip_sec=sec(c.end_frame - c.start_frame),
                    src_size=(a.width, a.height),
                    canvas=(width, height),
                    is_image=a.kind == AssetKind.IMAGE.value,
                )
                if anim and not defaults_only(anim)
                else ClipAnimFilters()
            )
            fades = []
            if c.fade_in_frames and s0 == c.start_frame:
                fades.append(f"fade=t=in:st=0:d={ff_seconds(sec(c.fade_in_frames))}:alpha=1")
            out_frames = c.fade_out_frames or dip_out.get(c.id, 0)
            if out_frames and c.end_frame <= r1:
                st = max(0.0, own_end - sec(out_frames))
                fades.append(
                    f"fade=t=out:st={ff_seconds(st)}:d={ff_seconds(sec(out_frames))}:alpha=1"
                )
            if t.opacity < 1:
                fades.append(f"colorchannelmixer=aa={t.opacity}")
            raw = [f"fps={fps_s}", "setpts=PTS-STARTPTS"]
            if pad > 0:
                raw.append(f"tpad=stop_mode=clone:stop_duration={ff_seconds(pad)}")
            raw += af.pre
            filters.append(f"[{i}:v]{','.join(raw)}[raw{i}]")
            filters.append(_layout(t, width, height, f"raw{i}", f"lay{i}", i))
            chain = ",".join([*af.post, *fades, *af.resize, f"setpts=PTS+{ff_seconds(off)}/TB"])
            filters.append(f"[lay{i}]{chain}[v{i}]")
            dx = f"+({af.dx})" if af.dx else ""
            dy = f"+({af.dy})" if af.dy else ""
            x = f"'(W-w)/2+({t.x}{dx})*W'"
            y = f"'(H-h)/2+({t.y}{dy})*H'"
            filters.append(
                f"[{base}][v{i}]overlay=x={x}:y={y}:eof_action=pass:"
                f"enable='between(t,{ff_seconds(off)},{ff_seconds(off + dur)})'[b{i}]"
            )
            base = f"b{i}"

        for c in audible:
            if c.id not in used:
                continue
            i = used[c.id]
            s0, s1, media, _ = span[c.id]
            own = sec(min(c.end_frame, s1) - s0)
            dur = min(media, sec(s1 - s0))  # 延長分は素材の続きがある範囲だけ音を鳴らす
            ms = round(sec(s0 - r0) * 1000)
            parts = [
                "asetpts=PTS-STARTPTS",
                f"atrim=0:{ff_seconds(dur)}",
                "aresample=48000",
                "aformat=sample_fmts=fltp:channel_layouts=stereo",
                f"volume={c.volume}",
            ]
            anim = anims.get(c.id)
            tran = anim.transition_in if anim else None
            fade_in = sec(c.fade_in_frames) if c.fade_in_frames else 0.0
            if not fade_in and tran is not None:
                fade_in = sec(min(tran.duration_frames, c.end_frame - c.start_frame))
            if fade_in and s0 == c.start_frame:
                parts.append(f"afade=t=in:st=0:d={ff_seconds(fade_in)}")
            if dur > own + 1e-6:  # トランジションで延長した分は音をフェードアウト
                parts.append(f"afade=t=out:st={ff_seconds(own)}:d={ff_seconds(dur - own)}")
            elif c.fade_out_frames and c.end_frame <= r1:
                st = max(0.0, own - sec(c.fade_out_frames))
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
            venc = video_codec_args(encoder, quality, fast)
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
    async def design_check(self, req: RenderRequest) -> DesignCheck | None:
        """書き出す前のデザインの検査（用途の型に反する部品・色・字幕）。"""
        if self.design is None:
            return None
        style = await self.subtitles.project_style(req.project_id)
        colors: list[str] = []
        if req.script_id and req.burn_subtitles:
            colors = list((await self.subtitles.speaker_accents(req.script_id)).values())
        return await self.design.check(
            req.project_id,
            req.script_id,
            style,
            colors,
            burn_subtitles=req.burn_subtitles,
        )

    async def rich_plan(self, req: RenderRequest) -> RichPlan | None:
        """画面演出（rich_direction）で使う部品。使わないときは None。"""
        if req.rich_direction == RichLevel.OFF:
            return None
        if req.rich_direction == RichLevel.AUTO:
            # 既定: 用途の型で許される最も強い演出（用途が無い・Blender が無いときは付けない）
            if self.design is None or self.blender is None or not self.blender.available:
                return None
            aprof = await self.design.profile_for(req.project_id)
            if aprof is None:
                return None
            async with self.db.session_factory() as s:
                p = await s.get(Project, req.project_id)
                assert p is not None
                fps = p.fps_num / p.fps_den
            rp = plan_rich(max_level(aprof), aprof, fps)
            return rp if rp.active else None
        if self.design is None:
            raise CommandError("rich direction needs the design service")
        prof = await self.design.profile_for(req.project_id)
        if prof is None:
            raise CommandError("画面演出を付けるには、先に用途を設定してください")
        if self.blender is None or not self.blender.available:
            raise CommandError("画面演出を付けるには Blender が必要です")
        async with self.db.session_factory() as s:
            p = await s.get(Project, req.project_id)
            assert p is not None
            fps = p.fps_num / p.fps_den
        rp = plan_rich(req.rich_direction, prof, fps)
        return rp if rp.active else None

    async def request(self, req: RenderRequest) -> RenderJobRead:
        """origin=llm の場合は承認待ち（最終レンダリングは承認が必要：仕様 §10）。

        書き出す前にデザインを検査し、用途の型に反する部品があれば止める。例外にできるのは
        ユーザー自身の指示（allow_design_violations）だけで、LLM 経由では例外にしない。"""
        async with self.db.session_factory() as s:
            if await s.get(Project, req.project_id) is None:
                raise TimelineNotFoundError(f"project {req.project_id}")
        await self.rich_plan(req)  # 画面演出を使えるか（用途・Blender）を先に確かめる
        chk = await self.design_check(req)
        if chk is not None and not chk.ok:
            override = req.allow_design_violations and req.origin != CommandOrigin.LLM
            if not override:
                raise DesignViolationError(chk)
        async with self.db.session_factory() as s:
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

    async def _flash_guard(self, out: Path) -> None:
        """書き出した動画の点滅を調べ、安全基準（1 秒 3 回）を超えたら書き出しを止める。

        利用者の指示や用途の型でも外さない（光過敏性発作の安全基準）。超えた動画は消す。"""
        info = await self.ffmpeg.probe(out)
        if not info.has_video:
            return
        res = await check_video(self.ffmpeg, out, info.fps or 30.0)
        log.info("flash_check", ok=res.ok, max_per_sec=res.max_per_sec, frames=res.frames)
        if not res.ok:
            out.unlink(missing_ok=True)
            raise CommandError(res.message())

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

    async def _tachie(self, req: RenderRequest) -> None:
        """台本のキャラクターに立ち絵があれば、立ち絵の動画を作ってタイムラインに置く（既定）。"""
        if not req.script_id or self.characters is None:
            return
        if self.design is not None:
            from video_automation.schemas.design import Component

            prof = await self.design.profile_for(req.project_id)
            if prof is not None and not prof.allows(Component.CHARACTER):
                return
        await self.characters.apply(req.script_id)

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
        await self._tachie(req)
        rich = await self.rich_plan(req)
        if rich is not None:
            return await self._render_rich(render_id, req, rich, out, encoder, progress)
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
        await self._flash_guard(out)
        credits_path = await self._credits(req, out, encoder)
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
            "credits_path": credits_path,
        }

    async def _render_rich(
        self,
        render_id: str,
        req: RenderRequest,
        rich: RichPlan,
        out: Path,
        encoder: str,
        progress: ProgressFn,
    ) -> dict[str, Any]:
        """字幕なしで書き出す → Blender で画面演出 → 字幕を焼き込む（字幕は演出で動かない）。"""
        assert self.blender is not None and self.design is not None
        prof = RENDER_PROFILES[req.profile]
        r0 = req.start_frame or 0
        clean = out.with_suffix(".clean.mp4").resolve()
        rich_out = out.with_suffix(".rich.mp4").resolve()

        def part_progress(lo: float, hi: float) -> ProgressFn:
            async def f(x: float) -> None:
                await progress(lo + (hi - lo) * x)

            return f

        plan = await self.plan(
            req.project_id,
            prof.width,
            prof.height,
            clean,
            encoder=encoder,
            quality=max(10, req.quality - 4),  # 2 回目の圧縮で落ちる分だけ高めに
            start_frame=req.start_frame,
            end_frame=req.end_frame,
            loudnorm=req.loudnorm,
        )
        await self.ffmpeg.run(
            plan.args, total_sec=plan.duration_sec, progress=part_progress(0.0, 0.25)
        )
        async with self.db.session_factory() as s:
            p = await s.get(Project, req.project_id)
            assert p is not None
            fps_num, fps_den = p.fps_num, p.fps_den
        r1 = r0 + round(plan.duration_sec * fps_num / fps_den)
        cues: list[dict[str, Any]] = []
        if req.script_id:
            for c in await self.subtitles.cues(req.script_id):
                if c.start_frame is not None and r0 <= c.start_frame < r1:
                    cues.append(
                        {
                            "segment": c.script_segment_id,
                            "text": c.text,
                            "start_frame": c.start_frame - r0,
                            "end_frame": (c.end_frame or c.start_frame) - r0,
                        }
                    )
        outline = await self.design.outline(req.project_id)
        chapters = [
            [int(f) - r0, str(nm)] for f, nm in outline.get("chapters") or [] if r0 <= f < r1
        ]
        dprof = await self.design.profile_for(req.project_id)
        assert dprof is not None
        pal = dprof.palette
        if outline.get("title") and rich.parts.get("intro"):
            # 演出プランが題名カードを置いている。冒頭（コールドオープン）で同じ題名や
            # キャッチを繰り返すと題名が 2 回出るので、冒頭は付けない
            rich.parts["intro"] = False
            rich.params["intro_frames"] = 0
            rich.notes.append("題名カードがあるため、冒頭の題名は付けませんでした")
        await progress(0.3)
        timeout = max(1800.0, plan.duration_sec * 40)
        try:
            res = await self.blender.run_script(
                "rich_direction",
                {
                    "clean_path": str(clean),
                    "out_path": str(rich_out),
                    "font": (self.font_for(dprof.typography.heading_font) if self.font_for else "")
                    or "",
                    "width": prof.width,
                    "height": prof.height,
                    "fps_num": fps_num,
                    "fps_den": fps_den,
                    # 題名は演出プランが題名カードとして置いている。冒頭（コールドオープン）で
                    # 同じ題名を繰り返すと題名が 2 回出るので、冒頭には渡さない
                    "title": "",
                    "catch": "",
                    "chapters": chapters,
                    "cues": cues,
                    "colors": {
                        "base": pal.base,
                        "main": pal.main,
                        "accent": pal.accent,
                        "text": pal.text,
                    },
                    **rich.args(),
                },
                timeout=timeout,
            )
        except BlenderError as exc:
            raise CommandError(f"画面演出の書き出しに失敗しました: {exc}") from exc
        if not rich_out.is_file():
            raise CommandError("画面演出の書き出しに失敗しました（出力がありません）")
        failed = {k: v.get("error") for k, v in (res.get("parts") or {}).items() if not v["ok"]}
        intro = int(res.get("intro_frames") or 0)
        await progress(0.85)
        ass = None
        subs: list[str] = []
        # 字幕を動く文字で出せたときは、同じ文を字幕として焼き込まない（二重になる）
        kinetic = bool(rich.parts.get("lyrics")) and "lyrics" not in failed
        if kinetic:
            rich.notes.append("字幕は動く文字（キネティックタイポグラフィ）で出しました")
        if req.script_id:
            if req.burn_subtitles and not kinetic:
                # 冒頭の演出の分だけ字幕を後ろへずらす（offset が負＝後ろへ）
                ass = await self._ass_for(
                    req.script_id, prof.width, prof.height, out.with_suffix(".burn.ass"), r0 - intro
                )
            if req.external_subtitles:
                for fmt in (SubtitleFormat.SRT, SubtitleFormat.ASS):
                    r = await self.subtitles.export(
                        SubtitleExportRequest(script_id=req.script_id, format=fmt)
                    )
                    subs.append(r.path)
        info0 = await self.ffmpeg.probe(rich_out)
        args = ["-i", str(rich_out)]
        if ass is not None:
            args += ["-vf", f"ass='{filter_path(ass)}'", *video_codec_args(encoder, req.quality)]
        else:
            args += ["-c:v", "copy"]
        args += ["-c:a", "copy", "-movflags", "+faststart", str(out)]
        await self.ffmpeg.run(
            args, total_sec=info0.duration_sec, progress=part_progress(0.85, 0.99)
        )
        for f in (clean, rich_out):
            f.unlink(missing_ok=True)
        await self._flash_guard(out)
        credits_path = await self._credits(req, out, encoder)
        info = await self.ffmpeg.probe(out)
        rel = out.relative_to(self.settings.data_dir).as_posix()
        await self._set(
            render_id,
            status=RenderStatus.SUCCEEDED.value,
            output_path=rel,
            subtitle_paths=subs,
            finished_at=datetime.now(UTC),
        )
        log.info(
            "render_rich_done",
            render_id=render_id,
            level=rich.level.value,
            used=res.get("used"),
            failed=list(failed),
        )
        return {
            "output_path": rel,
            "duration_sec": info.duration_sec,
            "width": info.width,
            "height": info.height,
            "encoder": encoder,
            "subtitle_paths": subs,
            "credits_path": credits_path,
            "rich_direction": {
                "level": rich.level.value,
                "intro_frames": intro,
                "used": res.get("used") or {},
                "failed_parts": failed,
                "notes": rich.notes,
                "render_sec": res.get("render_sec"),
            },
        }

    async def _credits(self, req: RenderRequest, out: Path, encoder: str) -> str | None:
        """クレジットの文を書き出し、必要なら動画の終わりにクレジットの画面を足す。"""
        if req.credits == CreditsMode.NONE or self.credits_for is None:
            return None
        text = (await self.credits_for(req.project_id)).strip()
        if not text:
            return None
        path = out.with_suffix(".credits.txt")
        path.write_text(text + "\n", encoding="utf-8")
        if req.credits == CreditsMode.END_CARD:
            await self._append_credit_card(req, out, text, encoder)
        return path.relative_to(self.settings.data_dir).as_posix()

    async def _append_credit_card(
        self, req: RenderRequest, out: Path, text: str, encoder: str
    ) -> None:
        """動画の終わりに、用途の配色・書体でクレジットの画面（credits_sec 秒）を足す。"""
        prof = RENDER_PROFILES[req.profile]
        W, H = prof.width, prof.height
        async with self.db.session_factory() as s:
            p = await s.get(Project, req.project_id)
            assert p is not None
            fps = f"{p.fps_num}/{p.fps_den}"
        dprof = await self.design.profile_for(req.project_id) if self.design else None
        bg = dprof.palette.base if dprof else "#101418"
        fg = dprof.palette.text if dprof else "#F2F4F7"
        sub = dprof.palette.main if dprof else "#9AA4B2"
        font = dprof.typography.body_font if dprof else "Meiryo"
        head = dprof.typography.heading_font if dprof else font
        d = req.credits_sec
        ass = out.with_suffix(".credits.ass")
        ass.write_text(credit_card_ass(text, W, H, d, fg, sub, font, head), encoding="utf-8-sig")
        info = await self.ffmpeg.probe(out)
        main_sec = float(info.duration_sec or 0.0)
        tmp = out.with_suffix(".withcredits.mp4")
        main_a = "[0:a]" if info.has_audio else "[3:a]"
        fc = (
            f"[0:v]setsar=1,format=yuv420p[mv];"
            f"[1:v]ass='{filter_path(ass)}',setsar=1,format=yuv420p[cv];"
            f"[2:a]atrim=0:{d:.3f},asetpts=N/SR/TB[ca];"
            f"{main_a}aresample=48000,aformat=channel_layouts=stereo[ma];"
            f"[mv][ma][cv][ca]concat=n=2:v=1:a=1[v][a]"
        )
        args = [
            "-i",
            str(out),
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x{bg.lstrip('#')[-6:]}:s={W}x{H}:r={fps}:d={d:.3f}",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=stereo",
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=r=48000:cl=stereo:d={main_sec:.3f}",
            "-filter_complex",
            fc,
            "-map",
            "[v]",
            "-map",
            "[a]",
            *video_codec_args(encoder, req.quality),
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(tmp),
        ]
        await self.ffmpeg.run(args, total_sec=main_sec + d)
        tmp.replace(out)
        ass.unlink(missing_ok=True)

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
