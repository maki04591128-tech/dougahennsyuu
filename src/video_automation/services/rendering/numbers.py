"""数字が回る（統合版 T35）と年号が回る（T43）。Pillow で小さな画の列を作る。

- T35: 全部の桁が同時に回り始め、1 フレームに 1 つ数字が進み（くっきり）、左の桁が 12f 目、そこから
  右へ 1 桁ごとに 3f 遅れて目的の数字で止まる。カンマ・小数点・単位は回さない。跳ね返りなし
- T43: 台詞の「（1000〜2099）年」を、右上に画面の高さの 7%・型の text・縁は型の base で 4px で出す。
  その動画で 2 回目からの年は T35 の方法で回し、1 回目は不透明度 0→1 を 6f。文の終わりで 1→0 を 6f
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

YEAR = re.compile(r"(1[0-9]{3}|20[0-9]{2})年")
FIRST_STOP, STEP = 12, 3


def find_year(text: str) -> str | None:
    m = YEAR.search(text)
    return m.group(1) if m else None


def roll_text(target: str, f: int) -> str:
    """f フレーム目の表示（数字だけを回す。数字以外はそのまま）。"""
    out, k = [], 0
    for ch in target:
        if ch.isdigit():
            stop = FIRST_STOP + STEP * k
            out.append(ch if f >= stop else str((int(ch) - (stop - f)) % 10))
            k += 1
        else:
            out.append(ch)
    return "".join(out)


def roll_frames_count(target: str) -> int:
    n = sum(c.isdigit() for c in target)
    return FIRST_STOP + STEP * max(0, n - 1) + 1


def _font(path: str | None, size: int) -> Any:
    from PIL import ImageFont

    if path and Path(path).is_file():
        return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


def _rgb(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def year_frames(year: str, h: int, font: str | None, ink: str, base: str, roll: bool) -> list[Any]:
    """T43 の小さな画の列（回るときは止まるまで、回らないときは 1 枚）。幅は 6 字分。"""
    from PIL import Image, ImageDraw

    size = round(h * 0.07)
    f = _font(font, size)
    stroke = max(2, round(4 * h / 1080))
    bw, bh = round(size * 3.4), round(size * 1.4)
    text = f"{year}年"
    n = roll_frames_count(year) if roll else 1
    out = []
    for i in range(n):
        im = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        t = roll_text(year, i) + "年" if roll else text
        d.text((bw - stroke, bh / 2), t, font=f, fill=(*_rgb(ink), 255), anchor="rm",
               stroke_width=stroke, stroke_fill=(*_rgb(base), 255))  # fmt: skip
        out.append(im)
    return out
