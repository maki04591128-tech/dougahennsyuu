"""統合版 T28: 語が図に変わる（解説・講義・子ども向けの強調テロップ）。

強調テロップの文節が「語と図の辞書」（pictogram.KEYWORDS。仕様の assets/icon_words.json の
代わり）の語を含むとき、文節が入り終わってから 12f 静止 → 文字の輪郭と図の輪郭を T23 の方法
（64 点・12f・easeInOutCubic）で変える。図の高さは文字の高さの 1.6 倍、色は型の main。
行の表示の終わりまで出す。

輪郭は、文字（字の間をつないだ塊）と図の外周を 64 点に揃えて点を直線補間する。変形の間は、
文字を前半で薄くし、図を後半で濃くし、補間した輪郭の線（3px・main）を重ねる。
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np

from video_automation.services.rendering import line_title as lt
from video_automation.services.rendering.pictogram import KEYWORDS, render_icon

HOLD = 12
MORPH = 12
POINTS = 64
ICON_RATIO = 1.6


def icon_for(text: str) -> str | None:
    """文節に含まれる辞書の語の図（上から順に照合）。無ければ None。"""
    for name, words in KEYWORDS:
        if any(w in text for w in words):
            return name
    return None


def _rgb(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def resample(pts: list[tuple[float, float]], n: int = POINTS) -> list[tuple[float, float]]:
    """閉じた折れ線を、周の長さで等間隔の n 点にする（始点は最も上の点）。"""
    if not pts:
        return []
    ring = [*pts, pts[0]]
    seg = [math.dist(a, b) for a, b in zip(ring, ring[1:], strict=False)]
    total = sum(seg) or 1.0
    out = []
    j, acc = 0, 0.0
    for k in range(n):
        d = total * k / n
        while j < len(seg) - 1 and acc + seg[j] < d:
            acc += seg[j]
            j += 1
        u = (d - acc) / seg[j] if seg[j] else 0.0
        a, b = ring[j], ring[j + 1]
        out.append((a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u))
    return out


def silhouette(mask: Any, merge: int) -> list[tuple[float, float]]:
    """画（L）の塊の外周（字の間は merge px 広げてつなぐ）を 64 点で。"""
    from PIL import ImageFilter

    m = mask.point(lambda v: 255 if v >= 96 else 0)
    if merge > 0:
        m = m.filter(ImageFilter.MaxFilter(2 * (merge // 2) + 1))
    comp = lt._largest_component(np.asarray(m) > 0)
    if comp is None:
        return []
    return resample([(float(x), float(y)) for x, y in lt.outer_contour(comp)])


def _cubic_io(u: float) -> float:
    u = min(max(u, 0.0), 1.0)
    return 4 * u**3 if u < 0.5 else 1 - (-2 * u + 2) ** 3 / 2


def morph_frames(
    text: str, icon: str, n: int, size: int, font: str | None, ink: str, main: str
) -> Iterator[Any]:
    """T28（n フレーム、透過）。0〜12f 文字、12〜24f 変形、以後は図。画の大きさは一定。"""
    from PIL import Image, ImageDraw, ImageFont

    f = (ImageFont.truetype(font, size) if font and Path(font).is_file()
         else ImageFont.load_default(size))  # fmt: skip
    tw = round(f.getlength(text)) or size
    ih = round(size * ICON_RATIO)
    cw, ch = max(tw, ih) + 16, max(round(size * 1.4), ih) + 16
    word = Image.new("L", (cw, ch), 0)
    ImageDraw.Draw(word).text((cw / 2, ch / 2), text, font=f, fill=255, anchor="mm")
    pic = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    pic.alpha_composite(render_icon(icon, ih, main), ((cw - ih) // 2, (ch - ih) // 2))
    a_pts = silhouette(word, max(2, size // 4))
    b_pts = silhouette(pic.getchannel("A"), max(2, ih // 10))
    if a_pts and b_pts:  # 始点を近い所にそろえる（ねじれを減らす）
        k = min(range(POINTS), key=lambda i: math.dist(a_pts[0], b_pts[i]))
        b_pts = b_pts[k:] + b_pts[:k]
    ink_l = Image.new("RGBA", (cw, ch), (*_rgb(ink), 255))
    lw = max(2, round(size / 24))
    for t in range(n):
        im = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        u = (t - HOLD) / MORPH
        if u <= 0:
            layer = ink_l.copy()
            layer.putalpha(word)
            im.alpha_composite(layer)
        elif u >= 1:
            im.alpha_composite(pic)
        else:
            e = _cubic_io(u)
            ta = max(0.0, 1 - 2 * e)
            if ta > 0:
                layer = ink_l.copy()
                layer.putalpha(word.point(lambda v, a_=ta: round(v * a_)))
                im.alpha_composite(layer)
            pa = max(0.0, 2 * e - 1)
            if pa > 0:
                p2 = pic.copy()
                p2.putalpha(pic.getchannel("A").point(lambda v, a_=pa: round(v * a_)))
                im.alpha_composite(p2)
            if a_pts and b_pts:
                pts = [(x0 + (x1 - x0) * e, y0 + (y1 - y0) * e)
                       for (x0, y0), (x1, y1) in zip(a_pts, b_pts, strict=True)]  # fmt: skip
                ImageDraw.Draw(im).line([*pts, pts[0]], fill=(*_rgb(main), 255), width=lw,
                                        joint="curve")  # fmt: skip
        yield im


def card_morph_frames(
    card: Any,
    box: tuple[int, int, int, int],
    text: str,
    icon: str,
    n: int,
    start: int,
    size: int,
    font: str | None,
    main: str,
) -> Iterator[Any]:
    """強調テロップのカード（card: RGBA）の文字の範囲 box で T28 を行う画の列（n フレーム）。
    start フレーム目までは元のカード、そこから 12f 静止して変形する。文字は元のカードの文字を
    薄くして消し（面の色で塗った画と混ぜる）、図は文字の範囲の中心に置く。図がカードより高い
    ときは、上下に余白を足す（中心は同じ）。"""
    from PIL import Image, ImageDraw

    x0, y0, x1, y1 = box
    blank = card.copy()
    sample = card.getpixel((min(card.width - 1, x1 + 2), min(card.height - 1, max(0, y0 - 2))))
    ImageDraw.Draw(blank).rectangle((x0, y0, x1, y1), fill=sample)
    ih = round(size * ICON_RATIO)
    pad = max(0, (ih + 8 - card.height + 1) // 2)
    cw, chh = card.width, card.height + 2 * pad
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2 + pad
    mf = list(morph_frames(text, icon, HOLD + MORPH + 1, size, font, "#FFFFFF", main))
    mw, mh = mf[0].size
    ox, oy = round(cx - mw / 2), round(cy - mh / 2)

    def put(base: Any) -> Any:
        im = Image.new("RGBA", (cw, chh), (0, 0, 0, 0))
        im.alpha_composite(base, (0, pad))
        return im

    full, empty = put(card), put(blank)
    for t in range(n):
        k = t - start
        if k < HOLD:
            yield full
            continue
        u = (k - HOLD) / MORPH
        if u >= 1:
            im = empty.copy()
            im.alpha_composite(mf[-1], (ox, oy))
            yield im
            continue
        e = _cubic_io(u)
        im = Image.blend(full, empty, min(1.0, 2 * e))
        m = mf[min(len(mf) - 1, k)]
        # morph_frames の文字（白）は使わず、線と図だけを重ねる
        a = m.getchannel("A")
        rgb = m.convert("RGB")
        white = rgb.point(lambda v: 255 if v > 240 else 0).convert("L")
        keep = Image.composite(Image.new("L", a.size, 0), a, white)
        m2 = m.copy()
        m2.putalpha(keep)
        im.alpha_composite(m2, (ox, oy))
        yield im
