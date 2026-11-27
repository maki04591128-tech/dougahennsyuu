"""花字（リアクションのテロップ。統合版 T42）。Pillow で描く。

5 層（下から）: ①押し出し（縁の色、右下へ文字の大きさ × 0.06）②外の縁（枠・線の色、幅 × 0.10）
③白の縁（幅 × 0.06）④面（上「薄い色」→ 下「文字」の色の縦のグラデーション）⑤光沢（白・不透明度
35%、文字の上半分）。

入り: 1 文字ずつ 2f ずらし、各文字は scale 0→1.15（happy・excited）または 0→1.25（surprise）を
6f（easeOutCubic）→ 1.00 を 4f（easeInOutSine）。同時に y −文字の大きさ × 0.15→0、回転 −8°→0°
（surprise は回転なし）。待機: 行全体を回転 ±1.8°（1.15Hz の sin）・上下 ±3px（1080p）。
出: scale 1→0 を 5f（easeInCubic）。
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

EMOTIONS = ("happy", "surprise", "excited")
STAGGER, POP, SETTLE, OUT = 2, 6, 4, 5


@dataclass
class FlowerColors:
    edge: str  # 3 節の「縁」（押し出し）
    frame: str  # 「枠・線」（外の縁）
    light: str  # 「薄い色」（面の上）
    text: str  # 「文字」（面の下）


def _rgb(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def _font(path: str | None, size: int) -> Any:
    from PIL import ImageFont

    if path and Path(path).is_file():
        return ImageFont.truetype(path, max(1, size))
    return ImageFont.load_default(max(1, size))


def _cubic_out(u: float) -> float:
    u = min(max(u, 0.0), 1.0)
    return 1 - (1 - u) ** 3


def _sine_io(u: float) -> float:
    u = min(max(u, 0.0), 1.0)
    return -(math.cos(math.pi * u) - 1) / 2


def _cubic_in(u: float) -> float:
    u = min(max(u, 0.0), 1.0)
    return u**3


def glyph(ch: str, font: str | None, size: int, col: FlowerColors) -> Any:
    """1 文字の 5 層を重ねた画（文字の大きさの 1.5 倍の正方形。中心が文字の中心）。"""
    from PIL import Image, ImageChops, ImageDraw, ImageFilter

    side = round(size * 1.5)
    f = _font(font, size)
    mask = Image.new("L", (side, side), 0)
    ImageDraw.Draw(mask).text((side / 2, side / 2), ch, font=f, fill=255, anchor="mm")

    def grow(r: float) -> Any:
        k = max(1, round(r)) * 2 + 1
        return mask.filter(ImageFilter.MaxFilter(k))

    outer, white = grow(size * 0.10), grow(size * 0.06)
    im = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    push = Image.new("RGBA", (side, side), (*_rgb(col.edge), 0))
    push.putalpha(outer)
    off = round(size * 0.06)
    im.alpha_composite(push, (off, off))
    for m, c in ((outer, col.frame), (white, "#FFFFFF")):
        lay = Image.new("RGBA", (side, side), (*_rgb(c), 0))
        lay.putalpha(m)
        im.alpha_composite(lay)
    # 面: 上「薄い色」→ 下「文字」の色
    top, bot = _rgb(col.light), _rgb(col.text)
    box = mask.getbbox() or (0, 0, side, side)
    grad = Image.new("RGBA", (side, side))
    gd = ImageDraw.Draw(grad)
    for y in range(side):
        t = min(1.0, max(0.0, (y - box[1]) / max(1, box[3] - box[1])))
        rgb = tuple(round(a + (b - a) * t) for a, b in zip(top, bot, strict=True))
        gd.line([(0, y), (side, y)], fill=(*rgb, 255))
    grad.putalpha(mask)
    im.alpha_composite(grad)
    # 光沢: 白・不透明度 35%、文字の上半分
    half = Image.new("L", (side, side), 0)
    mid = (box[1] + box[3]) // 2
    ImageDraw.Draw(half).rectangle((0, 0, side, mid), fill=round(255 * 0.35))
    gloss = Image.new("RGBA", (side, side), (255, 255, 255, 0))
    gloss.putalpha(ImageChops.multiply(mask, half))
    im.alpha_composite(gloss)
    return im


def layout(text: str, font: str | None, size: int) -> tuple[list[float], int, int]:
    """各文字の中心の x と、画の大きさ（拡大・回転の余白を含む）。"""
    f = _font(font, size)
    xs, x = [], 0.0
    for ch in text:
        adv = f.getlength(ch) or size * 0.5
        xs.append(x + adv / 2)
        x += adv
    pad = round(size * 0.9)
    return [v + pad for v in xs], round(x + 2 * pad), round(size * 1.5 + 2 * pad)


def frames(text: str, font: str | None, size: int, col: FlowerColors, emotion: str, n: int,
           fps: float, h_out: int) -> tuple[tuple[int, int], Iterator[Any]]:  # fmt: skip
    """花字の画の大きさと、n フレームの画の列（生成器）。"""
    from PIL import Image

    xs, cw, ch = layout(text, font, size)
    glyphs = [glyph(c, font, size, col) for c in text]
    peak = 1.25 if emotion == "surprise" else 1.15
    tilt = 0.0 if emotion == "surprise" else -8.0
    wob = 3 * h_out / 1080

    def gen() -> Iterator[Any]:
        for f in range(n):
            line = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
            for i, g in enumerate(glyphs):
                t = f - STAGGER * i
                if t < 0:
                    continue
                if t < POP:
                    e = _cubic_out((t + 1) / POP)
                    s = peak * e
                    dy = -size * 0.15 * (1 - e)
                    rot = tilt * (1 - e)
                elif t < POP + SETTLE:
                    s = peak + (1.0 - peak) * _sine_io((t - POP + 1) / SETTLE)
                    dy, rot = 0.0, 0.0
                else:
                    s, dy, rot = 1.0, 0.0, 0.0
                if s <= 0.01:
                    continue
                gs = max(1, round(g.size[0] * s))
                pic = g.resize((gs, gs), Image.Resampling.BICUBIC)
                if rot:
                    pic = pic.rotate(rot, resample=Image.Resampling.BICUBIC)
                line.alpha_composite(pic, (round(xs[i] - gs / 2), round(ch / 2 + dy - gs / 2)))
            # 待機: 行全体の回転と上下の揺れ。出: 最後の 5f で縮む
            sec = f / fps
            ang = 1.8 * math.sin(2 * math.pi * 1.15 * sec)
            yy = wob * math.sin(2 * math.pi * 1.15 * sec + math.pi / 2)
            k = 1.0 - _cubic_in((f - (n - OUT) + 1) / OUT) if f >= n - OUT else 1.0
            out = line.rotate(ang, resample=Image.Resampling.BICUBIC)
            if k < 1:
                sw, sh = max(1, round(cw * k)), max(1, round(ch * k))
                small = out.resize((sw, sh), Image.Resampling.BICUBIC)
                out = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
                out.alpha_composite(small, ((cw - sw) // 2, (ch - sh) // 2))
            dy_px = round(yy)
            if dy_px:
                moved = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
                if dy_px > 0:
                    moved.alpha_composite(out.crop((0, 0, cw, ch - dy_px)), (0, dy_px))
                else:
                    moved.alpha_composite(out.crop((0, -dy_px, cw, ch)), (0, 0))
                out = moved
            yield out

    return (cw, ch), gen()
