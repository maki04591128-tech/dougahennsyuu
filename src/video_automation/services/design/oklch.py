"""OKLCH による色の作り方（統合版 K2）。

キャラクターの公式の色から、色相を保って明るさ（L）と鮮やかさ（C の最大）だけを決めた
字幕の色を作る。色域の外になるときは C を下げる。"""

from __future__ import annotations

import math

# 統合版 3節: (L, C の最大)
TEXT = (0.95, 0.05)  # 文字
OUTLINE = (0.36, 0.11)  # 縁
ALT_OUTLINE = (0.24, 0.08)  # 代替の縁（同時に登場する話者と縁が近いとき）
FRAME = (0.65, 0.15)  # 枠・線


def _to_lin(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _to_srgb(c: float) -> float:
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def hex_to_oklch(color: str) -> tuple[float, float, float]:
    """#RRGGBB → (L, C, h°)。"""
    h = color.lstrip("#")[-6:]
    r, g, b = (_to_lin(int(h[i : i + 2], 16) / 255) for i in (0, 2, 4))
    l_ = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m_ = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s_ = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l_, m_, s_ = (math.copysign(abs(v) ** (1 / 3), v) for v in (l_, m_, s_))
    lum = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_
    a = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
    bb = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_
    return lum, math.hypot(a, bb), math.degrees(math.atan2(bb, a)) % 360


def _oklch_to_lin(lum: float, c: float, hue: float) -> tuple[float, float, float]:
    a, b = c * math.cos(math.radians(hue)), c * math.sin(math.radians(hue))
    l_ = (lum + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m_ = (lum - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s_ = (lum - 0.0894841775 * a - 1.2914855480 * b) ** 3
    return (
        4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_,
        -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_,
        -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_,
    )


def oklch_to_hex(lum: float, c: float, hue: float) -> str:
    """(L, C, h°) → #RRGGBB。色域の外なら色相と明るさを保って C を下げる。"""
    lo, hi = 0.0, c
    rgb = _oklch_to_lin(lum, c, hue)
    if not all(-1e-6 <= v <= 1 + 1e-6 for v in rgb):
        for _ in range(30):
            mid = (lo + hi) / 2
            if all(-1e-6 <= v <= 1 + 1e-6 for v in _oklch_to_lin(lum, mid, hue)):
                lo = mid
            else:
                hi = mid
        rgb = _oklch_to_lin(lum, lo, hue)
    r, g, b = (round(min(max(_to_srgb(min(max(v, 0.0), 1.0)), 0.0), 1.0) * 255) for v in rgb)
    return f"#{r:02X}{g:02X}{b:02X}"


def derive(color: str, role: tuple[float, float]) -> str:
    """公式の色から、役割（TEXT・OUTLINE・ALT_OUTLINE・FRAME）の色を作る。"""
    _, c, hue = hex_to_oklch(color)
    lum, cmax = role
    return oklch_to_hex(lum, min(c, cmax), hue)
