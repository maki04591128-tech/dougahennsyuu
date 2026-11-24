"""FFmpeg Adapter (P5-01)。

- 実行ファイル検出（設定 → PATH）、バージョン・エンコーダ（NVENC 有無）
- ffprobe によるメディア情報取得
- -progress による進捗通知と、キャンセル時のプロセス終了
コマンドは引数リストで組み立て、シェルを経由しない（コマンドインジェクション防止）。
"""

import asyncio
import json
import re
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

ProgressFn = Callable[[float], Awaitable[None]]


class FFmpegError(Exception):
    pass


class FFmpegNotFoundError(FFmpegError):
    pass


@dataclass
class MediaInfo:
    path: str
    duration_sec: float | None
    has_video: bool
    has_audio: bool
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    codec: str | None = None
    is_image: bool = False
    raw: dict = field(default_factory=dict)


def _fps(rate: str | None) -> float | None:
    if not rate or rate in ("0/0", "0"):
        return None
    if "/" in rate:
        n, d = rate.split("/")
        return float(n) / float(d) if float(d) else None
    return float(rate)


class FFmpeg:
    def __init__(self, ffmpeg: str | None = None, ffprobe: str | None = None) -> None:
        self.ffmpeg = ffmpeg or shutil.which("ffmpeg")
        self.ffprobe = ffprobe or (
            str(Path(self.ffmpeg).with_name(Path(self.ffmpeg).name.replace("ffmpeg", "ffprobe")))
            if self.ffmpeg
            else None
        )
        if self.ffprobe and not Path(self.ffprobe).exists():
            self.ffprobe = shutil.which("ffprobe")
        self._encoders: set[str] | None = None
        self._version: str | None = None

    @property
    def available(self) -> bool:
        return bool(self.ffmpeg and self.ffprobe)

    def _require(self) -> tuple[str, str]:
        if not self.ffmpeg or not self.ffprobe:
            raise FFmpegNotFoundError("ffmpeg/ffprobe not found")
        return self.ffmpeg, self.ffprobe

    async def _capture(self, *args: str, timeout: float = 60) -> tuple[int, str, str]:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise FFmpegError("timeout") from None
        return (
            proc.returncode or 0,
            out.decode("utf-8", "replace"),
            err.decode("utf-8", "replace"),
        )

    async def run_capture(self, args: list[str], *, timeout: float = 600) -> bytes:
        """ffmpeg を実行して標準出力（生のバイト列）を返す。"""
        ff, _ = self._require()
        proc = await asyncio.create_subprocess_exec(
            ff,
            "-hide_banner",
            "-nostdin",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout)
        except (asyncio.CancelledError, TimeoutError):
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
            raise
        if proc.returncode != 0:
            msg = err.decode("utf-8", "replace").strip()[-500:]
            raise FFmpegError(f"ffmpeg exited {proc.returncode}: {msg}")
        return out

    async def version(self) -> str:
        if self._version is None:
            ff, _ = self._require()
            _, out, _ = await self._capture(ff, "-hide_banner", "-version")
            m = re.search(r"ffmpeg version (\S+)", out)
            self._version = m.group(1) if m else "unknown"
        return self._version

    async def encoders(self) -> set[str]:
        if self._encoders is None:
            ff, _ = self._require()
            _, out, _ = await self._capture(ff, "-hide_banner", "-encoders")
            self._encoders = {
                parts[1] for line in out.splitlines() if len(parts := line.split()) >= 2
            }
        return self._encoders

    async def nvenc_usable(self) -> bool:
        """エンコーダ一覧にあっても GPU が無いと失敗するため、実際に1フレーム符号化して確認。"""
        if "h264_nvenc" not in await self.encoders():
            return False
        ff, _ = self._require()
        rc, _, _ = await self._capture(
            ff,
            "-hide_banner",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=256x256:d=0.1",
            "-frames:v",
            "1",
            "-c:v",
            "h264_nvenc",
            "-f",
            "null",
            "-",
            timeout=30,
        )
        return rc == 0

    async def probe(self, path: Path) -> MediaInfo:
        _, fp = self._require()
        rc, out, err = await self._capture(
            fp,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        )
        if rc != 0:
            raise FFmpegError(f"ffprobe failed: {err.strip()[:200]}")
        data = json.loads(out)
        streams = data.get("streams", [])
        v = next((s for s in streams if s.get("codec_type") == "video"), None)
        a = next((s for s in streams if s.get("codec_type") == "audio"), None)
        fmt = data.get("format", {})
        dur = fmt.get("duration") or (v or a or {}).get("duration")
        image_codecs = {"png", "mjpeg", "bmp", "webp", "tiff", "gif"}
        is_image = bool(
            v
            and v.get("codec_name") in image_codecs
            and (
                fmt.get("format_name", "").endswith("_pipe")
                or fmt.get("format_name") == "image2"  # 拡張子付きの静止画（jpg 等）
                or dur in (None, "N/A")
            )
        )
        return MediaInfo(
            path=str(path),
            duration_sec=None if is_image or dur in (None, "N/A") else float(dur),
            has_video=v is not None,
            has_audio=a is not None,
            width=v.get("width") if v else None,
            height=v.get("height") if v else None,
            fps=_fps(v.get("avg_frame_rate") or v.get("r_frame_rate")) if v else None,
            codec=(v or a or {}).get("codec_name"),
            is_image=is_image,
            raw={"format": fmt.get("format_name")},
        )

    async def run(
        self,
        args: list[str],
        *,
        total_sec: float | None = None,
        progress: ProgressFn | None = None,
        timeout: float = 3600,
    ) -> str:
        """ffmpeg を実行。キャンセル（CancelledError）時はプロセスを終了させる。"""
        ff, _ = self._require()
        cmd = [ff, "-hide_banner", "-nostats", "-progress", "pipe:1", "-y", *args]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        err_chunks: list[bytes] = []

        async def read_err() -> None:
            assert proc.stderr is not None
            while chunk := await proc.stderr.read(4096):
                err_chunks.append(chunk)
                if len(err_chunks) > 64:
                    del err_chunks[:32]

        async def read_progress() -> None:
            assert proc.stdout is not None
            while line := await proc.stdout.readline():
                text = line.decode("utf-8", "replace").strip()
                if text.startswith("out_time_us=") and total_sec and progress:
                    try:
                        sec = int(text.split("=", 1)[1]) / 1e6
                    except ValueError:
                        continue
                    await progress(max(0.0, min(0.99, sec / total_sec)))

        try:
            await asyncio.wait_for(
                asyncio.gather(read_err(), read_progress(), proc.wait()), timeout
            )
        except (asyncio.CancelledError, TimeoutError):
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
            raise
        stderr = b"".join(err_chunks).decode("utf-8", "replace")
        if proc.returncode != 0:
            raise FFmpegError(f"ffmpeg exited {proc.returncode}: {stderr.strip()[-500:]}")
        return stderr


def ff_seconds(value: float) -> str:
    return f"{value:.6f}"


def filter_path(path: Path) -> str:
    """フィルタ引数用のパス（Windows のドライブ区切り ':' と '\\' をエスケープ）。"""
    p = path.resolve().as_posix()
    return p.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
