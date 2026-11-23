"""FFmpeg による測定（P9-02 / P9-04）: ラウドネス・トゥルーピーク・無音・黒画面・静止。

ffmpeg のフィルタ（ebur128, silencedetect, blackdetect, freezedetect）を 1 回の実行で通し、
標準エラーのログを解析する。シェルを経由せず引数リストで実行する。
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

from video_automation.integrations.ffmpeg.runner import FFmpeg, FFmpegError

_NUM = r"(-?\d+(?:\.\d+)?|-?inf)"


@dataclass
class Span:
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class Measurement:
    duration_sec: float | None = None
    integrated_lufs: float | None = None
    loudness_range: float | None = None
    true_peak_dbfs: float | None = None
    silences: list[Span] = field(default_factory=list)
    blacks: list[Span] = field(default_factory=list)
    freezes: list[Span] = field(default_factory=list)


def _f(v: str) -> float | None:
    try:
        x = float(v)
    except ValueError:
        return None
    return None if x in (float("inf"), float("-inf")) else x


def parse_log(log: str, duration: float | None) -> Measurement:
    m = Measurement(duration_sec=duration)
    summary = log.rsplit("Summary:", 1)[1] if "Summary:" in log else ""
    if mm := re.search(rf"I:\s+{_NUM} LUFS", summary):
        m.integrated_lufs = _f(mm.group(1))
    if mm := re.search(rf"LRA:\s+{_NUM} LU", summary):
        m.loudness_range = _f(mm.group(1))
    if mm := re.search(rf"Peak:\s+{_NUM} dBFS", summary):
        m.true_peak_dbfs = _f(mm.group(1))
    end = duration or 0.0
    start: float | None = None
    for line in log.splitlines():
        if mm := re.search(rf"silence_start: {_NUM}", line):
            start = _f(mm.group(1)) or 0.0
        elif (mm := re.search(rf"silence_end: {_NUM}", line)) and start is not None:
            m.silences.append(Span(start, _f(mm.group(1)) or start))
            start = None
    if start is not None:  # 最後まで無音
        m.silences.append(Span(start, max(start, end)))
    for mm in re.finditer(rf"black_start:{_NUM} black_end:{_NUM}", log):
        m.blacks.append(Span(_f(mm.group(1)) or 0.0, _f(mm.group(2)) or 0.0))
    fstart: float | None = None
    for line in log.splitlines():
        if mm := re.search(rf"freeze_start: {_NUM}", line):
            fstart = _f(mm.group(1)) or 0.0
        elif (mm := re.search(rf"freeze_end: {_NUM}", line)) and fstart is not None:
            m.freezes.append(Span(fstart, _f(mm.group(1)) or fstart))
            fstart = None
    if fstart is not None:
        m.freezes.append(Span(fstart, max(fstart, end)))
    return m


async def measure(
    ffmpeg: FFmpeg,
    path: Path,
    *,
    audio: bool = True,
    video: bool = False,
    silence_db: float = -50.0,
    silence_min_sec: float = 0.5,
    timeout: float = 1800,
) -> Measurement:
    """音声（ラウドネス・ピーク・無音）と映像（黒画面・静止）を測る。"""
    ff, _ = ffmpeg._require()
    info = await ffmpeg.probe(path)
    graph: list[str] = []
    maps: list[str] = []
    if audio and info.has_audio:
        graph.append(
            "[0:a]ebur128=peak=true:framelog=verbose,"
            f"silencedetect=n={silence_db}dB:d={silence_min_sec}[a]"
        )
        maps += ["-map", "[a]"]
    if video and info.has_video and not info.is_image:
        graph.append("[0:v]blackdetect=d=0.5:pix_th=0.10,freezedetect=n=-60dB:d=2[v]")
        maps += ["-map", "[v]"]
    if not graph:
        return Measurement(duration_sec=info.duration_sec)
    rc, _, err = await ffmpeg._capture(
        ff,
        "-hide_banner",
        "-nostats",
        "-i",
        str(path),
        "-filter_complex",
        ";".join(graph),
        *maps,
        "-f",
        "null",
        "-",
        timeout=timeout,
    )
    if rc != 0:
        raise FFmpegError(f"analysis failed: {err.strip()[-300:]}")
    return parse_log(err, info.duration_sec)
