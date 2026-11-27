"""統合版 T41: 一本の線で題名を描く（会社紹介の最後の 3 秒）。

画面の左端の中央から、題名の各文字の最も外側の輪郭（各文字の最も大きい塊の外周）を前から順に
つなぎ（文字と文字の間は直線）、1 本の線にする。太さ 3px（1080p）・型の main。線の長さの
0→100% を 36f（easeInOutSine）で描き、描き終えたら文字の面を不透明度 0→1 に 12f。

Blender の Trim Curve の代わりに、文字を画にして外周をたどった折れ線を Pillow で描く。
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np

DRAW = 36
FILL = 12
WIDTH_1080 = 3
SIZE_RATIO = 0.10  # 文字の大きさ（画面の高さに対する比）
MAX_W = 0.80  # 題名の幅の最大（画面の幅に対する比）

# Moore 近傍（右から時計回り。画像の座標は y が下向き）
_NB = [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)]


def _rgb(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def _largest_component(mask: np.ndarray) -> np.ndarray | None:
    """8 近傍でつながる塊のうち、画素の最も多いもの。"""
    h, w = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    best: list[tuple[int, int]] = []
    ys, xs = np.nonzero(mask)
    for y0, x0 in zip(ys.tolist(), xs.tolist(), strict=True):
        if seen[y0, x0]:
            continue
        comp = []
        q = deque([(y0, x0)])
        seen[y0, x0] = True
        while q:
            y, x = q.popleft()
            comp.append((y, x))
            for dx, dy in _NB:
                yy, xx = y + dy, x + dx
                if 0 <= yy < h and 0 <= xx < w and mask[yy, xx] and not seen[yy, xx]:
                    seen[yy, xx] = True
                    q.append((yy, xx))
        if len(comp) > len(best):
            best = comp
    if not best:
        return None
    out = np.zeros_like(mask, dtype=bool)
    yy, xx = zip(*best, strict=True)
    out[list(yy), list(xx)] = True
    return out


def outer_contour(mask: np.ndarray) -> list[tuple[int, int]]:
    """塊の外周を Moore 近傍の追跡でたどる（x, y の列。始点は最も上の行の最も左の画素）。"""
    m = np.pad(mask, 1)
    ys, xs = np.nonzero(m)
    if len(ys) == 0:
        return []
    i = int(np.lexsort((xs, ys))[0])
    start = (int(xs[i]), int(ys[i]))
    pts = [start]
    cur, back = start, 4  # 左から入ってきたとみなす
    for _ in range(m.size * 2):
        found = False
        for k in range(8):
            d = (back + 1 + k) % 8
            nx, ny = cur[0] + _NB[d][0], cur[1] + _NB[d][1]
            if m[ny, nx]:
                back = (d + 4) % 8
                cur = (nx, ny)
                found = True
                break
        if not found or cur == start:
            break
        pts.append(cur)
    return [(x - 1, y - 1) for x, y in pts]


def title_path(
    text: str, w: int, h: int, font: str | None
) -> tuple[list[tuple[float, float]], Any, int]:
    """題名の 1 本の線（画面の座標の折れ線）、文字の面の画（L）、文字の大きさ。"""
    from PIL import Image, ImageDraw, ImageFont

    size = round(h * SIZE_RATIO)

    def load(s: int) -> Any:
        if font and Path(font).is_file():
            return ImageFont.truetype(font, s)
        return ImageFont.load_default(s)

    f = load(size)
    tw = f.getlength(text)
    if tw > w * MAX_W:
        size = max(8, int(size * w * MAX_W / tw))
        f = load(size)
        tw = f.getlength(text)
    face = Image.new("L", (w, h), 0)
    ImageDraw.Draw(face).text((w / 2, h / 2), text, font=f, fill=255, anchor="mm")
    x = w / 2 - tw / 2
    path: list[tuple[float, float]] = [(0.0, h / 2)]
    step = max(1, size // 40)
    for ch in text:
        adv = f.getlength(ch)
        if not ch.isspace():
            g = Image.new("L", (int(adv) + size, size * 2), 0)
            ImageDraw.Draw(g).text((size // 2, size), ch, font=f, fill=255, anchor="lm")
            comp = _largest_component(np.asarray(g) >= 128)
            if comp is not None:
                c = outer_contour(comp)[::step]
                if c:
                    j = min(range(len(c)), key=lambda k: c[k][0])  # 左端から回り始める
                    c = c[j:] + c[:j] + [c[j]]
                    ox, oy = x - size // 2, h / 2 - size
                    path.extend((px + ox, py + oy) for px, py in c)
        x += adv
    return path, face, size


def _ease_sine(t: float) -> float:
    return -(math.cos(math.pi * t) - 1) / 2


def _prefix(path: list[tuple[float, float]], frac: float) -> list[tuple[float, float]]:
    """折れ線の前から長さ frac の部分。"""
    seg = [math.dist(a, b) for a, b in zip(path, path[1:], strict=False)]
    total = sum(seg)
    if total <= 0 or frac <= 0:
        return path[:1]
    left = total * min(1.0, frac)
    out = [path[0]]
    for (a, b), s in zip(zip(path, path[1:], strict=False), seg, strict=True):
        if left >= s:
            out.append(b)
            left -= s
            continue
        if s > 0:
            k = left / s
            out.append((a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k))
        break
    return out


def line_title_frames(
    text: str, n: int, w: int, h: int, font: str | None, line_color: str, fill_color: str
) -> Iterator[Any]:
    """T41（n フレーム、透過）。0〜36f で線を描き、36〜48f で文字の面を出し、最後まで静止。"""
    from PIL import Image, ImageDraw

    path, face, _size = title_path(text, w, h, font)
    lw = max(1, round(WIDTH_1080 * h / 1080))
    lc = (*_rgb(line_color), 255)
    fc = _rgb(fill_color)
    for t in range(n):
        im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        k = min(1.0, (t + 1) / DRAW)
        pts = _prefix(path, _ease_sine(k))
        if len(pts) >= 2:
            ImageDraw.Draw(im).line(pts, fill=lc, width=lw, joint="curve")
        if t >= DRAW:
            a = min(1.0, (t - DRAW + 1) / FILL)
            layer = Image.new("RGBA", (w, h), (*fc, 255))
            layer.putalpha(face.point(lambda v, a_=a: round(v * a_)))
            im.alpha_composite(layer)
        yield im
