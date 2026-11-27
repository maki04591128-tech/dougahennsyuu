"""題名・帯・締めのカード（統合版 段階5d: H3・H4・H6）。Pillow で描く。

- H3 ニュースの帯とティッカー: 下から 12% の見出しの帯（高さ 9%・型の main・白の文字）と、
  その下の高さ 5% のティッカー（#111111）。ティッカーは横に長い 1 枚の絵にして呼び出し側が流す
- H4 クレジットロール: 中央揃え・文字は画面の高さの 4%。1 行が画面の高さを 6 秒で通る速さで
  下から上へ流し、最後の行が中央に来たら 2 秒止める（縦に長い 1 枚の絵。呼び出し側が流す）
- H6 中間字幕のカード: 黒 #111111 の全面に白の二重の枠（外 4px・内 1px、間 8px）と文（明朝・
  画面の高さの 6%・中央）。表示は 文字数 ÷ 6 ＋ 1 秒
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

H6_STARTS = ("その頃", "一方", "数日後", "翌朝", "やがて")
CREDIT_SEC_PER_SCREEN = 6.0
CREDIT_HOLD_SEC = 2.0
TICKER_SPEED = 0.12  # 画面の幅／秒


def _font(path: str | None, size: int) -> Any:
    from PIL import ImageFont

    if path and Path(path).is_file():
        return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


def _rgb(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def is_intertitle(text: str) -> bool:
    return text.lstrip().startswith(H6_STARTS)


def intertitle_sec(text: str) -> float:
    n = len(re.sub(r"\s", "", text))
    return n / 6 + 1.0


def intertitle(w: int, h: int, text: str, font: str | None) -> Any:
    """H6 の全面のカード。"""
    from PIL import Image, ImageDraw

    im = Image.new("RGB", (w, h), (0x11, 0x11, 0x11))
    d = ImageDraw.Draw(im)
    k = h / 1080
    m = round(h * 0.06)
    o, i_, gap = max(2, round(4 * k)), max(1, round(1 * k)), round(8 * k)
    d.rectangle([m, m, w - m, h - m], outline=(255, 255, 255), width=o)
    d.rectangle([m + o + gap, m + o + gap, w - m - o - gap, h - m - o - gap],
                outline=(255, 255, 255), width=i_)  # fmt: skip
    size = round(h * 0.06)
    f = _font(font, size)
    while d.textlength(text, font=f) > w * 0.8 and size > 12:
        size -= 2
        f = _font(font, size)
    d.text((w / 2, h / 2), text, font=f, fill=(255, 255, 255), anchor="mm")
    return im


def credits(w: int, h: int, lines: list[str], font: str | None, color: str) -> Any:
    """H4 の縦に長い絵（透過）。上下に画面の高さぶんの余白は付けない（呼び出し側が流す）。"""
    from PIL import Image, ImageDraw

    size = round(h * 0.04)
    f = _font(font, size)
    step = round(size * 1.8)
    im = Image.new("RGBA", (w, step * len(lines) + size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for i, ln in enumerate(lines):
        d.text((w / 2, step * i + size * 0.6), ln, font=f, fill=(*_rgb(color), 255), anchor="mm",
               stroke_width=max(1, size // 12), stroke_fill=(0, 0, 0, 200))  # fmt: skip
    return im


def credit_frames(h: int, strip_h: int, fps: float) -> tuple[int, int]:
    """(流れる長さ, 止める長さ) のフレーム数。最後の行（絵の下端）が中央に来るまで流す。"""
    speed = h / CREDIT_SEC_PER_SCREEN  # 画素／秒
    dist = h / 2 + strip_h  # 絵の上端が画面の下端から出て、下端が中央に来るまで
    return round(dist / speed * fps), round(CREDIT_HOLD_SEC * fps)


def news_band(w: int, h: int, headline: str, font: str | None, main: str) -> Any:
    """H3 の見出しの帯（画面の幅 × 高さ 9%）。"""
    from PIL import Image, ImageDraw

    bh = round(h * 0.09)
    im = Image.new("RGBA", (w, bh), (*_rgb(main), 255))
    d = ImageDraw.Draw(im)
    size = round(bh * 0.5)
    f = _font(font, size)
    while d.textlength(headline, font=f) > w * 0.9 and size > 10:
        size -= 2
        f = _font(font, size)
    d.text((round(w * 0.04), bh / 2), headline, font=f, fill=(255, 255, 255, 255), anchor="lm")
    return im


def ticker_strip(w: int, h: int, items: list[str], font: str | None) -> tuple[Any, int]:
    """H3 のティッカー（高さ 5%・#111111）。中身を 2 周分並べた横に長い絵と、1 周の幅（画素）。"""
    from PIL import Image, ImageDraw

    th = round(h * 0.05)
    size = round(th * 0.55)
    f = _font(font, size)
    text = "　／　".join(items) + "　／　"
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    unit = max(1, round(probe.textlength(text, font=f)))
    reps = max(1, -(-w // unit))  # 1 周の幅を画面の幅以上にする
    period = unit * reps
    im = Image.new("RGBA", (period * 2, th), (0x11, 0x11, 0x11, 255))
    d = ImageDraw.Draw(im)
    for r in range(reps * 2):
        d.text((r * unit, th / 2), text, font=f, fill=(255, 255, 255, 255), anchor="lm")
    return im, period


def chapter_card(w: int, h: int, number: int, title: str, font: str | None, accent: str,
                 ink: str) -> Any:  # fmt: skip
    """H1: 章の番号（画面の高さの 22%・型の accent・不透明度 35%）の上に章の題（高さの 8%）。"""
    num, ttl, _mask = chapter_parts(w, h, number, title, font, accent, ink)
    num.alpha_composite(ttl)
    return num


def chapter_parts(w: int, h: int, number: int, title: str, font: str | None, accent: str,
                  ink: str) -> tuple[Any, Any, Any]:  # fmt: skip
    """H1 の番号の層・題の層・題の文字の形（L）。統合版 T48（縁から塗りへ）で題だけを動かす。"""
    from PIL import Image, ImageDraw

    num = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(num)
    fn = _font(font, round(h * 0.22))
    d.text((w / 2, h / 2), f"{number:02d}", font=fn, fill=(*_rgb(accent), round(255 * 0.35)),
           anchor="mm")  # fmt: skip
    size = round(h * 0.08)
    ft = _font(font, size)
    while d.textlength(title, font=ft) > w * 0.8 and size > 12:
        size -= 2
        ft = _font(font, size)
    ttl = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(ttl).text((w / 2, h / 2), title, font=ft, fill=(*_rgb(ink), 255),
                             anchor="mm", stroke_width=max(1, size // 14),
                             stroke_fill=(0, 0, 0, 160))  # fmt: skip
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).text((w / 2, h / 2), title, font=ft, fill=255, anchor="mm")
    return num, ttl, mask
