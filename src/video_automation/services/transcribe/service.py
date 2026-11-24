"""文字起こし（⑤）: 取り込んだ音声・動画（歌・ナレーション・会話）から字幕や歌詞を作る。

- faster-whisper をローカルで使う。モデルは settings.whisper_model_dir から読むだけで、
  自動でダウンロードしない（モデルの入手はユーザーの承認のうえで別に行う）
- 結果は台本（Script）と字幕（Subtitle）として保存する。字幕の時刻は文字起こしの時刻。
  歌詞の文（lyrics）があれば、文は歌詞を使い時刻だけを文字起こしから借りる
- 用途の型が歌詞を使う（MV・リリック）なら、字幕は歌詞の見た目で出る
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import BaseModel, Field

from video_automation.app.logging import get_logger
from video_automation.app.settings import Settings
from video_automation.database.database import Database
from video_automation.database.models import Asset, Project, Script, ScriptSegment, Subtitle
from video_automation.integrations.ffmpeg.runner import FFmpeg
from video_automation.services.subtitle.text import default_max_chars, make_cues
from video_automation.services.transcribe.align import Line, Word, align_lyrics, lines_from_segments

if TYPE_CHECKING:
    from video_automation.services.design.service import DesignService

log = get_logger("transcribe")

ProgressFn = Callable[[float], Awaitable[None]]


class TranscribeError(Exception):
    code = "transcribe_error"


class TranscribeUnavailableError(TranscribeError):
    code = "transcribe_unavailable"


class TranscribeNotFoundError(TranscribeError):
    code = "not_found"


class TranscribeRequest(BaseModel):
    project_id: str
    asset_id: str  # 取り込んだ音声・動画（歌・ナレーション）
    language: str = Field(default="ja", pattern=r"^[a-z]{2}$")
    # 歌詞・台本の文（1 行 1 字幕）。あれば文はこれを使い、時刻だけを文字起こしから取る
    lyrics: str | None = Field(default=None, max_length=20_000)
    title: str = Field(default="", max_length=255)


class TranscribeLine(BaseModel):
    text: str
    start_sec: float
    end_sec: float
    matched: float = 1.0


class TranscribeResult(BaseModel):
    script_id: str
    language: str
    duration_sec: float
    lines: list[TranscribeLine]
    # 歌詞を合わせたとき、文字起こしとほとんど一致しなかった行（時刻が推定）
    weak_lines: list[int] = Field(default_factory=list)


@dataclass
class Recognized:
    words: list[Word]
    segments: list[tuple[str, float, float]]
    language: str
    duration: float


class Recognizer(Protocol):
    # audio: 16kHz モノラルの float32（numpy 配列）
    def __call__(self, audio: Any, language: str) -> Recognized: ...


class WhisperRecognizer:
    """faster-whisper（ローカルのモデルだけを使う）。モデルは最初に使うときに読み込む。"""

    def __init__(self, model_dir: Path, device: str = "auto") -> None:
        self.model_dir = model_dir
        self.device = device
        self._model: Any = None

    def _load(self) -> Any:
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as e:  # pragma: no cover - 任意の依存
                raise TranscribeUnavailableError(
                    "faster-whisper が入っていません（uv sync --extra transcribe）"
                ) from e
            if not (self.model_dir / "model.bin").is_file():
                raise TranscribeUnavailableError(f"モデルがありません: {self.model_dir}")
            errors = []
            devices = ["cuda", "cpu"] if self.device == "auto" else [self.device]
            for dev in devices:
                try:
                    self._model = WhisperModel(
                        str(self.model_dir),
                        device=dev,
                        compute_type="float16" if dev == "cuda" else "int8",
                        local_files_only=True,
                    )
                    log.info("whisper_loaded", device=dev)
                    break
                except Exception as e:  # noqa: BLE001 - CUDA の DLL が無い等は CPU に切り替える
                    errors.append(f"{dev}: {type(e).__name__}")
            if self._model is None:
                raise TranscribeUnavailableError("モデルを読み込めません: " + ", ".join(errors))
        return self._model

    def _run(self, audio: Any, language: str) -> Recognized:
        model = self._load()
        segs, info = model.transcribe(
            audio, language=language, word_timestamps=True, vad_filter=True, beam_size=5
        )
        words: list[Word] = []
        segments: list[tuple[str, float, float]] = []
        for sg in segs:  # 区切りは順に作られる（ここで実際に計算が走る）
            segments.append((sg.text, float(sg.start), float(sg.end)))
            for w in sg.words or []:
                words.append(Word(w.word, float(w.start), float(w.end)))
        return Recognized(words, segments, info.language, float(info.duration))

    def __call__(self, audio: Any, language: str) -> Recognized:
        try:
            return self._run(audio, language)
        except RuntimeError as e:
            # GPU のライブラリ（cuBLAS・cuDNN）が無いと計算の途中で失敗する。CPU でやり直す
            msg = str(e).lower()
            if self.device != "auto" or not any(k in msg for k in ("cuda", "cublas", "cudnn")):
                raise
            log.warning("whisper_cuda_failed", error=str(e)[:200])
            self.device, self._model = "cpu", None
            return self._run(audio, language)


class TranscriptionService:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        ffmpeg: FFmpeg,
        recognizer: Recognizer | None = None,
        design: DesignService | None = None,
    ) -> None:
        self.settings = settings
        self.db = db
        self.ffmpeg = ffmpeg
        self.design = design
        self._recognizer = recognizer

    async def _decode(self, path: Path) -> Any:
        """音声を 16kHz モノラルの float32 に（FFmpeg で。動画からも取り出す）。"""
        import numpy as np

        if not self.ffmpeg.ffmpeg:
            raise TranscribeUnavailableError("ffmpeg が見つかりません")
        proc = await asyncio.create_subprocess_exec(
            self.ffmpeg.ffmpeg,
            "-v",
            "error",
            "-nostdin",
            "-i",
            str(path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-f",
            "f32le",
            "-",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        if proc.returncode != 0 or not out:
            raise TranscribeError("音声を読み出せません: " + err.decode(errors="ignore")[-200:])
        return np.frombuffer(out, dtype=np.float32).copy()

    @property
    def recognizer(self) -> Recognizer:
        if self._recognizer is None:
            if self.settings.whisper_model_dir is None:
                raise TranscribeUnavailableError(
                    "文字起こしのモデルが未設定です（VA_WHISPER_MODEL_DIR）"
                )
            self._recognizer = WhisperRecognizer(
                self.settings.whisper_model_dir, self.settings.whisper_device
            )
        return self._recognizer

    def status(self) -> dict[str, Any]:
        d = self.settings.whisper_model_dir
        try:
            import importlib.util

            pkg = importlib.util.find_spec("faster_whisper") is not None
        except ImportError:  # pragma: no cover
            pkg = False
        return {
            "package": pkg,
            "model_dir": str(d) if d else None,
            "model": bool(d and (d / "model.bin").is_file()),
            "available": self._recognizer is not None
            or bool(pkg and d and (d / "model.bin").is_file()),
        }

    async def transcribe(
        self, req: TranscribeRequest, progress: ProgressFn | None = None
    ) -> TranscribeResult:
        async with self.db.session_factory() as s:
            p = await s.get(Project, req.project_id)
            a = await s.get(Asset, req.asset_id)
            if p is None or a is None or a.project_id != req.project_id:
                raise TranscribeNotFoundError("project or asset not found")
            if a.kind not in ("audio", "video") or (a.kind == "video" and not a.has_audio):
                raise TranscribeError("音声のある素材を選んでください")
            path = self.settings.data_dir / a.path
            fps = Fraction(p.fps_num, p.fps_den)
            name = a.name
            max_chars, max_lines = default_max_chars(p.width, p.height), 2
        if self.design is not None:
            prof = await self.design.profile_for(req.project_id)
            if prof is not None:  # 用途の型の 1 行の字数・行数（歌詞は 1 行など）
                max_chars = min(max_chars, prof.subtitle.max_chars_per_line)
                max_lines = prof.subtitle.max_lines
        if progress:
            await progress(0.05)
        recognizer = self.recognizer  # 未設定ならここで止める（音声の読み出しより先に）
        audio = await self._decode(path)
        if progress:
            await progress(0.15)
        rec = await asyncio.to_thread(recognizer, audio, req.language)
        if progress:
            await progress(0.8)
        if req.lyrics and req.lyrics.strip():
            lines = align_lyrics(req.lyrics.splitlines(), rec.words)
        else:
            lines = lines_from_segments(rec.segments)
        if not lines:
            raise TranscribeError("文字が見つかりませんでした")
        sid = await self._save(req, name, fps, lines, max_chars, max_lines)
        weak = [i for i, x in enumerate(lines) if req.lyrics and x.matched < 0.3]
        log.info("transcribed", lines=len(lines), weak=len(weak), language=rec.language)
        return TranscribeResult(
            script_id=sid,
            language=rec.language,
            duration_sec=rec.duration,
            lines=[
                TranscribeLine(
                    text=x.text,
                    start_sec=round(x.start, 3),
                    end_sec=round(x.end, 3),
                    matched=x.matched,
                )
                for x in lines
            ],
            weak_lines=weak,
        )

    async def _save(
        self,
        req: TranscribeRequest,
        name: str,
        fps: Fraction,
        lines: list[Line],
        max_chars: int,
        max_lines: int,
    ) -> str:
        def fr(sec: float) -> int:
            return max(0, round(sec * fps))

        async with self.db.session_factory() as s:
            sc = Script(
                project_id=req.project_id,
                title=req.title or f"文字起こし: {name}"[:255],
                mode="MANUAL",
            )
            s.add(sc)
            await s.flush()
            prev_end = 0
            idx = 0
            for i, ln in enumerate(lines):
                st = max(fr(ln.start), prev_end)
                en = max(st + 1, fr(ln.end))
                seg = ScriptSegment(
                    script_id=sc.id,
                    scene_index=0,
                    sequence=i,
                    speaker="",
                    text=ln.text[:2000],
                    generation_source="USER",
                    start_frame=st,
                    end_frame=en,
                )
                s.add(seg)
                await s.flush()
                # 長い行は型の字数で分け、時刻は字数の割合で分ける
                cues = make_cues(ln.text, max_chars, max_lines, strip_period=True) or [ln.text]
                sizes = [max(1, len(c.replace("\n", ""))) for c in cues]
                total, acc = sum(sizes), 0
                bounds = [st]
                for n in sizes:
                    acc += n
                    bounds.append(st + round((en - st) * acc / total))
                for k, cue in enumerate(cues):
                    a, b = bounds[k], max(bounds[k] + 1, bounds[k + 1])
                    s.add(
                        Subtitle(
                            script_id=sc.id,
                            script_segment_id=seg.id,
                            index=idx,
                            text=cue[:500],
                            speaker="",
                            start_frame=a,
                            end_frame=min(b, en) if k < len(cues) - 1 else en,
                        )
                    )
                    idx += 1
                prev_end = en
            await s.commit()
            return sc.id
