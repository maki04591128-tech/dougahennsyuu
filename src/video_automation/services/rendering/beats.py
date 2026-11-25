"""曲の拍の検出（MV の画面を拍に合わせるため）。追加のライブラリを使わず numpy だけで行う。

1. 音声を 11025 Hz のモノラルにし、短い区間ごとの音の強さ（対数）の増え方を「立ち上がり」とする
2. 立ち上がりの自己相関から、テンポ（毎分 70〜180 拍）を選ぶ
3. 拍の間隔で並べた位置の立ち上がりの合計が最大になるずれ（位相）を選び、拍の時刻にする
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from video_automation.integrations.ffmpeg.runner import FFmpeg

SR = 11025
HOP = 256  # 約 23 ms
MIN_BPM, MAX_BPM = 70.0, 180.0


@dataclass
class Beats:
    bpm: float
    times: list[float]  # 拍の時刻（秒）
    confidence: float  # 0〜1（テンポの自己相関の強さ。低ければ拍を使わない）


def onset_strength(samples: np.ndarray) -> np.ndarray:
    n = len(samples) // HOP
    if n < 4:
        return np.zeros(0)
    frames = samples[: n * HOP].reshape(n, HOP).astype(np.float64)
    energy = np.log1p(1000.0 * np.sqrt((frames**2).mean(axis=1)))
    diff = np.diff(energy, prepend=energy[0])
    return np.maximum(diff, 0.0)


def detect(samples: np.ndarray, sr: int = SR) -> Beats:
    env = onset_strength(samples)
    rate = sr / HOP
    if len(env) < rate * 4 or env.max() <= 0:
        return Beats(0.0, [], 0.0)
    env = env - env.mean()
    ac = np.correlate(env, env, mode="full")[len(env) - 1 :]
    if ac[0] <= 0:
        return Beats(0.0, [], 0.0)
    lo, hi = int(rate * 60 / MAX_BPM), int(rate * 60 / MIN_BPM)
    lags = np.arange(lo, min(hi, len(ac) - 1) + 1)
    if len(lags) == 0:
        return Beats(0.0, [], 0.0)
    # 人が感じやすいテンポ（120 前後）を少し優先する
    weight = np.exp(-0.5 * (np.log2((rate * 60 / lags) / 120.0) / 0.9) ** 2)
    score = ac[lags] * weight
    lag = int(lags[int(score.argmax())])
    confidence = float(max(0.0, min(1.0, ac[lag] / ac[0])))
    # 自己相関の山を放物線で補間し、拍の間隔を小数で求める（整数のままだと長い曲でずれる）
    period = float(lag)
    if 1 <= lag < len(ac) - 1:
        a, b, c = ac[lag - 1], ac[lag], ac[lag + 1]
        den = a - 2 * b + c
        if den < 0:
            period = lag + 0.5 * (a - c) / den
    n = len(env)
    best_phase, best = 0.0, -1.0
    for ph in np.arange(0.0, period, 0.5):
        idx = np.round(np.arange(ph, n, period)).astype(int)
        idx = idx[idx < n]
        s = float(env[idx].sum())
        if s > best:
            best, best_phase = s, float(ph)
    times = [round(float(t) / rate, 3) for t in np.arange(best_phase, n, period)]
    return Beats(round(rate * 60 / period, 1), times, round(confidence, 3))


async def detect_file(ffmpeg: FFmpeg, path: Path) -> Beats:
    raw = await ffmpeg.run_capture(
        ["-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"],
        timeout=300,
    )
    samples = np.frombuffer(raw[: len(raw) // 2 * 2], dtype=np.int16).astype(np.float32) / 32768
    return detect(samples)
