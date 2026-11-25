"""曲の拍の検出（numpy だけ）。"""

import numpy as np

from video_automation.services.rendering.beats import SR, detect


def _clicks(bpm: float, sec: float, offset: float = 0.0) -> np.ndarray:
    x = np.random.default_rng(1).normal(0, 0.01, int(SR * sec)).astype(np.float32)
    step = 60.0 / bpm
    t = offset
    while t < sec - 0.1:
        i = int(t * SR)
        n = int(0.05 * SR)
        x[i : i + n] += (
            0.8 * np.exp(-np.arange(n) / (0.01 * SR)) * np.sin(2 * np.pi * 120 * np.arange(n) / SR)
        )
        t += step
    return x


def test_detects_tempo_and_phase_of_clicks():
    b = detect(_clicks(120, 20, offset=0.25))
    assert abs(b.bpm - 120) < 3 and b.confidence > 0.3
    # 拍の時刻は実際のクリックに近い（23ms 単位のずれは許す）
    assert all(abs(((t - 0.25) / 0.5) - round((t - 0.25) / 0.5)) * 0.5 < 0.06 for t in b.times)
    b = detect(_clicks(90, 20))
    assert abs(b.bpm - 90) < 3


def test_silence_has_no_beats():
    b = detect(np.zeros(SR * 10, dtype=np.float32))
    assert b.times == [] and b.confidence == 0.0
