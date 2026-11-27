"""音の視覚化（統合版 段階5d: M10・M11。MV・リリック）。透過の画の列を作る。

- M11 拍の輪: イントロ（最初の歌詞まで）の拍ごとに、中央から輪（太さ 3px・V12 の文字の色）が
  半径 0 → 画面の高さの 45% へ広がり、不透明度 60% → 0。1 つの輪は 20f
- M10 スペクトラム: 2 回目以降の間奏に、画面の下から 10% の位置に横幅 60% で棒 32 本。棒の高さは
  40〜8000Hz を対数で 32 に分けた各帯域の強さ（曲全体の 95 パーセンタイルで割る。最大は画面の
  高さの 20%）。下がるときは 150ms で戻す
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

RING_FRAMES = 20
BARS = 32
LOW, HIGH = 40.0, 8000.0
RELEASE_SEC = 0.15


def _rgb(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def interludes(spans: list[tuple[float, float, str]]) -> list[tuple[float, float]]:
    """歌詞の区間の間で、歌詞の無い区間（line_spans で間奏として残った隙間）。"""
    out = []
    for (_a, b, _s), (a2, _b2, _s2) in zip(spans, spans[1:], strict=False):
        if a2 - b > 0.5:
            out.append((b, a2))
    return out


def ring_frames(
    beats: list[float], end: float, fps: float, w: int, h: int, color: str
) -> list[Any]:
    """M11（0 秒〜end 秒の画の列）。"""
    from PIL import Image, ImageDraw

    n = max(1, round(end * fps))
    starts = [round(b * fps) for b in beats if b < end]
    col = _rgb(color)
    lw = max(2, round(3 * h / 1080))
    out = []
    for f in range(n):
        im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        for s in starts:
            if 0 <= f - s < RING_FRAMES:
                u = (f - s) / RING_FRAMES
                r = 0.45 * h * (1 - (1 - u) ** 3)
                a = round(255 * 0.6 * (1 - u))
                if r > lw:
                    d.ellipse(
                        [w / 2 - r, h / 2 - r, w / 2 + r, h / 2 + r], outline=(*col, a), width=lw
                    )
        out.append(im)
    return out


def band_levels(wave: np.ndarray, sr: int, fps: float) -> np.ndarray:
    """フレームごとの 32 帯域の強さ（全体の 95 パーセンタイルで割り最大 1。下がるときは 150ms）。"""
    hop = sr / fps
    win = 2048
    nfr = max(1, int(len(wave) / hop))
    freqs = np.fft.rfftfreq(win, 1 / sr)
    edges = np.geomspace(LOW, HIGH, BARS + 1)
    idx = [np.where((freqs >= a) & (freqs < b))[0] for a, b in zip(edges, edges[1:], strict=False)]
    window = np.hanning(win).astype(np.float32)
    lev = np.zeros((nfr, BARS), np.float32)
    for f in range(nfr):
        c = int(f * hop)
        seg = wave[max(0, c - win // 2) : c + win // 2]
        if len(seg) < win:
            seg = np.pad(seg, (0, win - len(seg)))
        mag = np.abs(np.fft.rfft(seg * window))
        for k, ix in enumerate(idx):
            if len(ix):
                lev[f, k] = float(mag[ix].mean())
    ref = np.percentile(lev, 95, axis=0)
    lev = np.minimum(1.0, lev / np.maximum(ref, 1e-9))
    rel = math.exp(-1 / max(1e-6, RELEASE_SEC * fps))  # 下がるときは 150ms でなめらかに
    for f in range(1, nfr):
        lev[f] = np.maximum(lev[f], lev[f - 1] * rel)
    return lev


def spectrum_frames(levels: np.ndarray, a: float, b: float, fps: float, w: int, h: int,
                    color: str) -> list[Any]:  # fmt: skip
    """M10（a〜b 秒の画の列）。"""
    from PIL import Image, ImageDraw

    col = _rgb(color)
    f0, f1 = round(a * fps), round(b * fps)
    x0, span = w * 0.2, w * 0.6
    bw = span / BARS
    base = h * 0.9
    out = []
    for f in range(f0, f1):
        im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        lv = levels[min(f, len(levels) - 1)] if len(levels) else np.zeros(BARS)
        for k in range(BARS):
            hh = float(lv[k]) * h * 0.2
            if hh >= 1:
                x = x0 + k * bw
                d.rectangle([x + bw * 0.15, base - hh, x + bw * 0.85, base], fill=(*col, 255))
        out.append(im)
    return out
