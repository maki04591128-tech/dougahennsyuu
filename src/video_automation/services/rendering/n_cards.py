"""台本の語だけで出せる図解（統合版 段階5d: N1・N6・N7・N8・N11）。Pillow で描く。

- N1 手順の流れ: 続く文の頭に「まず」「次に」「最後に」（「1つ目」…）
  が 2 つ以上 → 各文の最初の文節を
  箱（角の丸み 12px・枠は型の main 3px）に入れて左から等間隔に並べ、箱の間を線でつなぐ
- N6 目次と章の進み: 章が 3 つ以上の動画の 2 章目からの頭に、全章の題を縦に並べて 2.0 秒
- N7 用語の定義カード: 文が「（語）とは」で始まる
- N8 正誤カード: 文に「実は」「誤解」「と思われがちですが」 → 前の文を「よくある誤解」、
  その文を「実際は」
- N11 引用カード: 文に「」で括られた 20 字以上の引用
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

STEP_WORDS = (
    ("まず", "次に", "最後に"),
    ("1つ目", "2つ目", "3つ目"),
    ("一つ目", "二つ目", "三つ目"),
)
MYTH_WORDS = ("実は", "誤解", "と思われがちですが")
MYTH_RED = "#E03A3A"
_DEF = re.compile(r"^\s*([^、。！？「」]{1,12}?)とは[、,]?(.+)$")
_QUOTE = re.compile(r"「([^「」]{20,})」")
_PARTICLE = re.compile(r"^(.+?[をがはにでとへもの、])")


def _font(path: str | None, size: int) -> Any:
    from PIL import ImageFont

    if path and Path(path).is_file():
        return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


def _rgb(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def first_phrase(text: str, limit: int = 10) -> str:
    """最初の文節（の近似: 最初の助詞・読点まで。長ければ limit 字で切る）。"""
    t = text.strip()
    for words in STEP_WORDS:
        for w in words:
            if t.startswith(w):
                t = t[len(w) :].lstrip("、, ")
    m = _PARTICLE.match(t)
    s = (m.group(1) if m else t).rstrip("、")
    return s[:limit]


def step_runs(texts: list[str]) -> list[list[int]]:
    """N1: 「まず」「次に」「最後に」で始まる文が続く区間（文の番号の列。2 つ以上）。"""
    runs: list[list[int]] = []
    cur: list[int] = []
    for i, t in enumerate(texts):
        hit = any(t.lstrip().startswith(w) for ws in STEP_WORDS for w in ws)
        if hit and (not cur or i - cur[-1] <= 2):
            cur.append(i)
        elif hit:
            if len(cur) >= 2:
                runs.append(cur)
            cur = [i]
        if cur and i - cur[-1] > 2:
            if len(cur) >= 2:
                runs.append(cur)
            cur = []
    if len(cur) >= 2:
        runs.append(cur)
    return runs


def definition(text: str) -> tuple[str, str] | None:
    m = _DEF.match(text)
    return (m.group(1).strip(), m.group(2).strip()) if m else None


def is_myth(text: str) -> bool:
    return any(w in text for w in MYTH_WORDS)


def quote(text: str) -> str | None:
    m = _QUOTE.search(text)
    return m.group(1) if m else None


def _wrap(d: Any, text: str, f: Any, width: float) -> list[str]:
    out, cur = [], ""
    for ch in text:
        if d.textlength(cur + ch, font=f) > width and cur:
            out.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        out.append(cur)
    return out


def _canvas(w: int, h: int) -> Any:
    from PIL import Image

    return Image.new("RGBA", (w, h), (0, 0, 0, 0))


def steps(w: int, h: int, labels: list[str], shown: int, font: str | None, main: str,
          accent: str, ink: str, surface: str) -> Any:  # fmt: skip
    """N1: shown 個目までの箱と線。"""
    from PIL import ImageDraw

    im = _canvas(w, h)
    d = ImageDraw.Draw(im)
    n = len(labels)
    k = h / 1080
    bw, bh = w * 0.8 / n * 0.78, h * 0.16
    gap = (w * 0.8 - bw * n) / max(1, n - 1)
    y0 = h * 0.42
    f = _font(font, round(h * 0.045))
    for i in range(min(shown, n)):
        x0 = w * 0.1 + i * (bw + gap)
        if i > 0:  # 前の箱からの線と、型の accent の点
            px = x0 - gap
            d.line(
                [(px, y0 + bh / 2), (x0, y0 + bh / 2)],
                fill=(*_rgb(main), 255),
                width=max(2, round(3 * k)),
            )
            r = 6 * k
            d.ellipse([x0 - r * 2, y0 + bh / 2 - r, x0, y0 + bh / 2 + r], fill=(*_rgb(accent), 255))
        d.rounded_rectangle([x0, y0, x0 + bw, y0 + bh], radius=round(12 * k),
                            fill=(*_rgb(surface), 235), outline=(*_rgb(main), 255),
                            width=max(2, round(3 * k)))  # fmt: skip
        t = labels[i]
        size = round(h * 0.045)
        while d.textlength(t, font=f) > bw * 0.88 and size > 10:
            size -= 2
            f = _font(font, size)
        d.text((x0 + bw / 2, y0 + bh / 2), t, font=f, fill=(*_rgb(ink), 255), anchor="mm")
        f = _font(font, round(h * 0.045))
    return im


def toc(w: int, h: int, titles: list[str], current: int, font: str | None, accent: str,
        ink: str, surface: str) -> Any:  # fmt: skip
    """N6: current（0 始まり）が次の章。済んだ章は不透明度 45% とチェック、
    次の章は accent・×1.15。"""
    from PIL import ImageDraw

    im = _canvas(w, h)
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([w * 0.25, h * 0.12, w * 0.75, h * 0.88], radius=round(h * 0.02),
                        fill=(*_rgb(surface), 235))  # fmt: skip
    n = len(titles)
    step = h * 0.68 / max(1, n)
    base = min(round(h * 0.05), round(step * 0.6))
    for i, t in enumerate(titles):
        y = h * 0.16 + step * (i + 0.5)
        if i < current:
            col, a, size = ink, round(255 * 0.45), base
        elif i == current:
            col, a, size = accent, 255, round(base * 1.15)
        else:
            col, a, size = ink, 255, base
        f = _font(font, size)
        x = w * 0.32
        if i < current:
            r = size * 0.35
            d.line([(x - r * 2.4, y), (x - r * 1.6, y + r * 0.8), (x - r * 0.2, y - r * 0.9)],
                   fill=(*_rgb(accent), a), width=max(2, size // 10))  # fmt: skip
        d.text((x, y), t[:24], font=f, fill=(*_rgb(col), a), anchor="lm")
    return im


def definition_card(w: int, h: int, word: str, rest: str, font: str | None, accent: str,
                    ink: str, surface: str) -> Any:  # fmt: skip
    """N7: 左に語（画面の高さの 9%）、右に文の残り。面は surface、左の辺に accent の線 8px。"""
    from PIL import ImageDraw

    im = _canvas(w, h)
    d = ImageDraw.Draw(im)
    x0, y0, x1, y1 = w * 0.08, h * 0.30, w * 0.92, h * 0.62
    k = h / 1080
    d.rectangle([x0, y0, x1, y1], fill=(*_rgb(surface), 240))
    d.rectangle([x0, y0, x0 + 8 * k, y1], fill=(*_rgb(accent), 255))
    fw = _font(font, round(h * 0.09))
    size = round(h * 0.09)
    while d.textlength(word, font=fw) > (x1 - x0) * 0.4 and size > 12:
        size -= 2
        fw = _font(font, size)
    d.text((x0 + 40 * k, (y0 + y1) / 2), word, font=fw, fill=(*_rgb(accent), 255), anchor="lm")
    wx = x0 + 40 * k + d.textlength(word, font=fw) + 40 * k
    fr = _font(font, round(h * 0.054))
    rows = _wrap(d, rest, fr, x1 - wx - 30 * k)[:3]
    lh = h * 0.054 * 1.4
    top = (y0 + y1) / 2 - lh * len(rows) / 2
    for i, r in enumerate(rows):
        d.text((wx, top + lh * (i + 0.5)), r, font=fr, fill=(*_rgb(ink), 255), anchor="lm")
    return im


def myth_card(w: int, h: int, myth: str, fact: str, font: str | None, accent: str,
              show_fact: bool) -> Any:  # fmt: skip
    """N8: 左に「よくある誤解」（#E03A3A の札・取り消し線・不透明度 45%）、
    右に「実際は」（accent の札）。"""
    from PIL import ImageDraw

    im = _canvas(w, h)
    k = h / 1080

    def tag(label: str, body: str, col: str, x0: float, struck: bool) -> Any:
        lay = _canvas(w, h)
        d = ImageDraw.Draw(lay)
        bw, top = w * 0.40, h * 0.28
        fl, fb = _font(font, round(h * 0.04)), _font(font, round(h * 0.045))
        rows = _wrap(d, body, fb, bw - 40 * k)[:4]
        bh = h * 0.08 + len(rows) * h * 0.045 * 1.45 + 30 * k
        d.rounded_rectangle(
            [x0, top, x0 + bw, top + bh], radius=round(16 * k), fill=(*_rgb(col), 255)
        )
        d.text(
            (x0 + 20 * k, top + h * 0.04), label, font=fl, fill=(255, 255, 255, 255), anchor="lm"
        )
        for i, r in enumerate(rows):
            y = top + h * 0.08 + h * 0.045 * 1.45 * (i + 0.5)
            d.text((x0 + 20 * k, y), r, font=fb, fill=(255, 255, 255, 255), anchor="lm")
            if struck:  # T36 の線（太さは文字の 8%・#E03A3A。白の札の上なので白い縁を付けない）
                lw = max(2, round(h * 0.045 * 0.08))
                d.line([(x0 + 20 * k, y), (x0 + 20 * k + d.textlength(r, font=fb), y)],
                       fill=(40, 0, 0, 255), width=lw)  # fmt: skip
        if struck:
            a = lay.getchannel("A").point(lambda v: round(v * 0.45))
            lay.putalpha(a)
        return lay

    im.alpha_composite(tag("よくある誤解", myth, MYTH_RED, w * 0.07, True))
    if show_fact:
        im.alpha_composite(tag("実際は", fact, accent, w * 0.53, False))
    return im


def quote_card(w: int, h: int, text: str, speaker: str, font: str | None, accent: str,
               ink: str) -> Any:  # fmt: skip
    """N11: 引用を画面の幅の 70% に組み、左上に「“」（高さの 20%・accent・60%）、下に発言者。"""
    from PIL import ImageDraw

    im = _canvas(w, h)
    d = ImageDraw.Draw(im)
    fq = _font(font, round(h * 0.2))
    d.text((w * 0.12, h * 0.2), "“", font=fq, fill=(*_rgb(accent), round(255 * 0.6)), anchor="lm")
    size = round(h * 0.06)
    f = _font(font, size)
    rows = _wrap(d, text, f, w * 0.7)
    while len(rows) > 4 and size > 16:
        size -= 2
        f = _font(font, size)
        rows = _wrap(d, text, f, w * 0.7)
    lh = size * 1.5
    top = h * 0.5 - lh * len(rows) / 2
    for i, r in enumerate(rows):
        d.text((w * 0.15, top + lh * (i + 0.5)), r, font=f, fill=(*_rgb(ink), 255), anchor="lm",
               stroke_width=max(1, size // 16), stroke_fill=(0, 0, 0, 180))  # fmt: skip
    if speaker:
        fs = _font(font, round(h * 0.035))
        d.text((w * 0.85, top + lh * len(rows) + h * 0.04), f"— {speaker}", font=fs,
               fill=(*_rgb(ink), 230), anchor="rm")  # fmt: skip
    return im
