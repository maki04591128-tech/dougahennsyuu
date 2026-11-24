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
from typing import Any, Protocol

from pydantic import BaseModel, Field

from video_automation.app.logging import get_logger
from video_automation.app.settings import Settings
from video_automation.database.database import Database
from video_automation.database.models import Asset, Project, Script, ScriptSegment, Subtitle
from video_automation.services.transcribe.align import Line, Word, align_lyrics, lines_from_segments

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
    def __call__(self, path: Path, language: str) -> Recognized: ...


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

    def __call__(self, path: Path, language: str) -> Recognized:
        model = self._load()
        segs, info = model.transcribe(
            str(path), language=language, word_timestamps=True, vad_filter=True, beam_size=5
        )
        words: list[Word] = []
        segments: list[tuple[str, float, float]] = []
        for sg in segs:
            segments.append((sg.text, float(sg.start), float(sg.end)))
            for w in sg.words or []:
                words.append(Word(w.word, float(w.start), float(w.end)))
        return Recognized(words, segments, info.language, float(info.duration))


class TranscriptionService:
    def __init__(
        self, settings: Settings, db: Database, recognizer: Recognizer | None = None
    ) -> None:
        self.settings = settings
        self.db = db
        self._recognizer = recognizer

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
        if progress:
            await progress(0.05)
        rec = await asyncio.to_thread(self.recognizer, path, req.language)
        if progress:
            await progress(0.8)
        if req.lyrics and req.lyrics.strip():
            lines = align_lyrics(req.lyrics.splitlines(), rec.words)
        else:
            lines = lines_from_segments(rec.segments)
        if not lines:
            raise TranscribeError("文字が見つかりませんでした")
        sid = await self._save(req, name, fps, lines)
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
        self, req: TranscribeRequest, name: str, fps: Fraction, lines: list[Line]
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
                s.add(
                    Subtitle(
                        script_id=sc.id,
                        script_segment_id=seg.id,
                        index=i,
                        text=ln.text[:500],
                        speaker="",
                        start_frame=st,
                        end_frame=en,
                    )
                )
                prev_end = en
            await s.commit()
            return sc.id
