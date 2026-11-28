"""統合版 L7 のロゴ: シリーズのロゴ（Q4）にも、光沢のスイープを 1 回通す。

題名と同じ値: 斜めの白い帯（不透明度 40%）が 25f（easeInOutCubic）でロゴの面を左から右へ
1 回通る。帯はロゴの透明でない所だけに描き、ロゴと同じ大きさの透過の画の列にして、ロゴの
すぐ上に同じ置き方で重ねる。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

FRAMES = 25
OPACITY = 0.40
BAND = 0.22  # 帯の幅（ロゴの幅に対する比）
SLANT = 0.35  # 帯の傾き（高さ 1 あたりの横のずれ。ロゴの幅に対する比）
START_SEC = 0.5  # ロゴが出てから帯が通り始めるまで


def _ease(u: float) -> float:
    u = min(max(u, 0.0), 1.0)
    return 4 * u**3 if u < 0.5 else 1 - (-2 * u + 2) ** 3 / 2


def frames(logo: Path, max_side: int = 640) -> Iterator[Any]:
    """ロゴの画像 → 帯の画の列（FRAMES 枚。ロゴと同じ縦横比、長い辺は最大 max_side）。"""
    import numpy as np
    from PIL import Image

    src = Image.open(logo).convert("RGBA")
    k = min(1.0, max_side / max(src.size))
    if k < 1.0:
        src = src.resize((max(1, round(src.width * k)), max(1, round(src.height * k))))
    w, h = src.size
    alpha = np.asarray(src.getchannel("A"), np.float32) / 255.0
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    # 斜めの座標（上ほど右へ寄る）。0〜1+SLANT の範囲を帯の中心が通る
    s = xx / w + (1 - yy / max(1, h - 1)) * SLANT * h / w
    span = 1 + SLANT * h / w + 2 * BAND
    for i in range(FRAMES):
        c = -BAND + span * _ease((i + 1) / FRAMES)
        band = np.clip(1 - np.abs(s - c) / (BAND / 2), 0, 1)
        a = (band * alpha * OPACITY * 255).astype(np.uint8)
        rgba = np.zeros((h, w, 4), np.uint8)
        rgba[..., :3] = 255
        rgba[..., 3] = a
        yield Image.fromarray(rgba, "RGBA")
