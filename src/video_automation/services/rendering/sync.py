"""音ハメ（統合版 M1）と周波数帯ごとの音反応（M2）。numpy だけで、曲の音声から時刻と値を作る。

M1 音ハメの 3 種
- 拍頭: 1 小節に 1 回（最初の拍から 4 拍ごと。4/4 拍子とみなす）
- ピーク: 1 曲に最大 8 回（立ち上がりの強い順。互いに 2 秒以上離す）
- 子音: 1 曲に最大 3 か所。単語の開始時刻の前後 60ms で、2000〜8000Hz の立ち上がりが最大のフレーム

M2 周波数帯ごとの音反応（各値は曲全体の 95 パーセンタイルで割って最大 1、下がるときは 150ms で戻す）
- キック 40〜100Hz（FFT 2048）→ 画面全体の拡大 1.00→1.02
- スネア 150〜250Hz（FFT 1024）→ 装飾の図形の拡大 1.00→1.08
- ハイハット 3000〜15000Hz（FFT 1024）→ 粒子の不透明度 0.3→0.8
主役の文字は M1 だけで動かす（M2 では動かさない）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from video_automation.integrations.ffmpeg.runner import FFmpeg

SR = 32000  # ハイハット（〜15000Hz）が入る標本化周波数
BEATS_PER_BAR = 4
PEAK_MAX, PEAK_GAP_SEC = 8, 2.0
CONSONANT_MAX, CONSONANT_WINDOW_SEC = 3, 0.06
CONSONANT_BAND = (2000.0, 8000.0)
RELEASE_SEC = 0.15
BANDS: dict[str, tuple[float, float, int, float, float]] = {
    # 名前: (低, 高, FFT の大きさ, 値の始め, 値の終わり)
    "kick": (40.0, 100.0, 2048, 1.00, 1.02),
    "snare": (150.0, 250.0, 1024, 1.00, 1.08),
    "hihat": (3000.0, 15000.0, 1024, 0.3, 0.8),
}


@dataclass
class Hits:
    bars: list[int] = field(default_factory=list)  # 拍頭（フレーム）
    peaks: list[int] = field(default_factory=list)
    consonants: list[int] = field(default_factory=list)


def band_energy(
    samples: np.ndarray, sr: int, fps: float, lo: float, hi: float, n_fft: int
) -> np.ndarray:
    """動画の 1 フレームごとの、帯域 lo〜hi の強さ（窓は Hann、中心はそのフレームの時刻）。"""
    n_frames = int(len(samples) / sr * fps)
    if n_frames <= 0:
        return np.zeros(0)
    win = np.hanning(n_fft)
    freqs = np.fft.rfftfreq(n_fft, 1 / sr)
    sel = (freqs >= lo) & (freqs <= hi)
    pad = np.concatenate([np.zeros(n_fft // 2), samples, np.zeros(n_fft)])
    starts = (np.arange(n_frames) / fps * sr).astype(int)
    out = np.empty(n_frames)
    for k in range(0, n_frames, 600):  # 長い曲でもメモリを使いすぎないように分けて計算する
        idx = starts[k : k + 600, None] + np.arange(n_fft)[None, :]
        spec = np.abs(np.fft.rfft(pad[idx] * win, axis=1))
        out[k : k + 600] = np.sqrt((spec[:, sel] ** 2).sum(axis=1))
    return out


def normalize(env: np.ndarray, fps: float) -> np.ndarray:
    """95 パーセンタイルで割って最大 1。下がるときは 150ms で戻す（上がるときはすぐ）。"""
    if env.size == 0:
        return env
    p95 = float(np.percentile(env, 95))
    v = np.clip(env / p95, 0.0, 1.0) if p95 > 0 else np.zeros_like(env)
    step = 1.0 / max(1.0, RELEASE_SEC * fps)
    out = np.empty_like(v)
    cur = 0.0
    for i, x in enumerate(v):
        cur = x if x >= cur else max(x, cur - step)
        out[i] = cur
    return out


def band_curves(
    samples: np.ndarray, sr: int, fps: float, names: tuple[str, ...] = tuple(BANDS)
) -> dict[str, list[float]]:
    """M2: フレームごとの値（キックは拡大率、スネアは拡大率、ハイハットは不透明度）。"""
    out = {}
    for name in names:
        lo, hi, n_fft, a, b = BANDS[name]
        v = normalize(band_energy(samples, sr, fps, lo, hi, n_fft), fps)
        out[name] = [round(a + (b - a) * float(x), 4) for x in v]
    return out


def _onset(env: np.ndarray) -> np.ndarray:
    e = np.log1p(1000.0 * env / (env.max() or 1.0))
    return np.maximum(np.diff(e, prepend=e[:1]), 0.0)


def hits(
    samples: np.ndarray,
    sr: int,
    fps: float,
    beats: list[float],
    words: list[float] | None = None,
) -> Hits:
    """M1: 拍頭・ピーク・子音のフレーム。beats は拍の時刻（秒）、words は単語の開始時刻（秒）。"""
    h = Hits()
    h.bars = sorted({round(t * fps) for t in beats[::BEATS_PER_BAR]})
    full = band_energy(samples, sr, fps, 20.0, sr / 2, 1024)
    if full.size == 0:
        return h
    on = _onset(full)
    gap = PEAK_GAP_SEC * fps
    for i in np.argsort(-on):
        if len(h.peaks) >= PEAK_MAX or on[i] <= 0:
            break
        if all(abs(int(i) - p) >= gap for p in h.peaks):
            h.peaks.append(int(i))
    h.peaks.sort()
    if words:
        hi = _onset(band_energy(samples, sr, fps, *CONSONANT_BAND, 1024))
        w = max(1, round(CONSONANT_WINDOW_SEC * fps))
        cand = []
        for t in words:
            c = round(t * fps)
            lo_i, hi_i = max(0, c - w), min(len(hi), c + w + 1)
            if lo_i >= hi_i:
                continue
            j = lo_i + int(np.argmax(hi[lo_i:hi_i]))
            cand.append((float(hi[j]), j))
        cand.sort(reverse=True)
        h.consonants = sorted(j for v, j in cand[:CONSONANT_MAX] if v > 0)
    return h


async def load(ffmpeg: FFmpeg, path: Path) -> np.ndarray:
    raw = await ffmpeg.run_capture(
        ["-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"],
        timeout=300,
    )
    return np.frombuffer(raw[: len(raw) // 2 * 2], dtype=np.int16).astype(np.float32) / 32768
