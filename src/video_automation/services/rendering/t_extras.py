"""文字の追加（統合版 段階5d: T44・T45・T47・T48・T49・T50）。Pillow で描く。

- T44 奥へ流れる文字: 予告編・朗読・物語の最初の台詞を、X 軸に 55° 倒した面で下から奥へ流す
- T45 回る語の見出し: PR・CM の題名に「／」があるとき、語を同じ位置で入れ替える
- T47 流れる帯: ランキング・イベントの題名の後ろに、−8° と ＋8° の帯 2 本
- T48 縁から塗りへ: ゲーム実況の章の題（H1）を縁だけで出してから、左から塗る
- T49 段々の残響: 縦型ショートの題名の上下に、縁だけの同じ文字を 2 段ずつ
- T50 円周を回る文字: イベントの題名の後ろ・ポッドキャストのカバーの周りに、題名を円周に並べる

値はすべて 30fps・1080p の初期値（統合版の表の値）。
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from pathlib import Path
from typing import Any

BAND_ANGLE = 8.0  # T47
BAND_SPEED = 0.10  # T47: 画面の幅の 10%／秒
RING_SEC = 20.0  # T50: 1 周の秒
CRAWL_TILT = 55.0  # T44
CRAWL_SPEED = 0.08  # T44: 画面の高さの 8%／秒
SLASH = "／"


def _font(path: str | None, size: int) -> Any:
    from PIL import ImageFont

    if path and Path(path).is_file():
        return ImageFont.truetype(path, max(1, size))
    return ImageFont.load_default(max(1, size))


def _rgb(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def _ease_out(t: float) -> float:
    t = min(max(t, 0.0), 1.0)
    return 1 - (1 - t) ** 3


def _ease_in_out(t: float) -> float:
    t = min(max(t, 0.0), 1.0)
    return 4 * t**3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def title_font_px(text: str, font: str | None, w: int, h: int, size: float) -> int:
    """Blender の題名（幅の 80%・高さの 25% に収め、size 倍）と同じ大きさの文字の px。"""
    lines = text.split("\n")
    probe = 100
    f = _font(font, probe)
    tw = max(f.getlength(ln) for ln in lines) or 1.0
    th = probe * 1.2 * len(lines)
    return max(8, round(probe * min(0.8 * w / tw, 0.25 * h / th) * size))


# ---------- T45 回る語の見出し ----------


def rotating_words(title: str) -> tuple[str, list[str]] | None:
    """「速い／軽い／安い」→ ("", [速い, 軽い, 安い])。
    「新しい 速い／軽い」→ ("新しい", [速い, 軽い])。
    「／」が無い、語が 2 つ未満、改行があるときは None。"""
    if SLASH not in title or "\n" in title:
        return None
    parts = [p.strip() for p in title.split(SLASH)]
    head, _, first = parts[0].rpartition(" ")
    if not first:
        head, first = "", parts[0]
    words = [first, *parts[1:]]
    if len(words) < 2 or any(not x for x in words):
        return None
    return head.strip(), words


def rotating_frames(
    w: int, h: int, head: str, words: list[str], n: int, font: str | None, ink: str,
    accent: str, size: float = 1.0,
) -> Iterator[Any]:  # fmt: skip
    """T45: 1 語 24f、入れ替えは前の語が上へ・次の語が下から 8f（easeInOutCubic）。最後の語で止め、
    型の accent にする。n フレーム（題名の長さ）を出す。"""
    from PIL import Image, ImageDraw

    widest = max(words, key=len)
    px = title_font_px(f"{head} {widest}".strip(), font, w, h, size)
    f = _font(font, px)
    head_w = f.getlength(head + " ") if head else 0.0
    word_w = max(f.getlength(x) for x in words)
    x0 = (w - head_w - word_w) / 2
    line_h = round(px * 1.25)
    y0 = (h - line_h) // 2
    hold, swap = 24, 8
    timeline: list[tuple[int, int, float]] = []  # (語, 次の語, 入れ替えの進み)
    for k in range(len(words)):
        timeline += [(k, k, 0.0)] * hold
        if k + 1 < len(words):
            timeline += [(k, k + 1, (j + 1) / swap) for j in range(swap)]
    last = len(words) - 1
    for i in range(n):
        a, b, t = timeline[i] if i < len(timeline) else (last, last, 0.0)
        im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        if head:
            d.text((x0, y0 + line_h / 2), head, font=f, fill=_rgb(ink), anchor="lm")
        # 語は行の高さの帯の中だけに見せる（帯の外は切る）
        band = Image.new("RGBA", (round(word_w) + 4, line_h), (0, 0, 0, 0))
        bd = ImageDraw.Draw(band)
        e = _ease_in_out(t)
        col_a = accent if a == last else ink
        col_b = accent if b == last else ink
        mid = line_h / 2
        bd.text((0, mid - e * line_h), words[a], font=f, fill=_rgb(col_a), anchor="lm")
        if b != a:
            bd.text((0, mid + (1 - e) * line_h), words[b], font=f, fill=_rgb(col_b), anchor="lm")
        im.alpha_composite(band, (round(x0 + head_w), y0))
        yield im


# ---------- T47 流れる帯 ----------


def title_band(w: int, h: int, text: str, font: str | None, face: str, ink: str,
               angle: float) -> Any:  # fmt: skip
    """T47: 高さは画面の 9%・幅は画面の 1.8 倍の帯に、題名を画面の高さの 5% で繰り返して
    並べ、傾ける。"""
    from PIL import Image, ImageDraw

    bw, bh = round(w * 1.8), round(h * 0.09)
    im = Image.new("RGBA", (bw, bh), (*_rgb(face), 255))
    d = ImageDraw.Draw(im)
    f = _font(font, round(h * 0.05))
    unit = text.replace("\n", " ") + "　・　"
    step = max(1.0, f.getlength(unit))
    x = 0.0
    while x < bw:
        d.text((x, bh / 2), unit, font=f, fill=_rgb(ink), anchor="lm")
        x += step
    return im.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True)


# ---------- T49 段々の残響 ----------


def echo_outline(w: int, h: int, text: str, font: str | None, ink: str, size: float) -> Any:
    """T49: 題名と同じ大きさの、縁だけ（2px・text・不透明度 35%）の文字（行の高さの画）。"""
    from PIL import Image, ImageDraw

    px = title_font_px(text, font, w, h, size)
    f = _font(font, px)
    lines = text.split("\n")
    tw = max(round(f.getlength(ln)) for ln in lines) + 8
    th = round(px * 1.2 * len(lines)) + 8
    im = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    sw = max(1, round(2 * h / 1080))
    d.multiline_text((tw / 2, th / 2), text, font=f, fill=(0, 0, 0, 0), anchor="mm",
                     align="center", stroke_width=sw, stroke_fill=(*_rgb(ink), 89))  # fmt: skip
    return im


def echo_pair(w: int, h: int, text: str, font: str | None, ink: str, size: float, level: int,
              dy: float = 0.0) -> Any:  # fmt: skip
    """T49 の 1 段（題名の上と下に 1 つずつ。level 1 が題名に近い段）を画面の大きさの画に。"""
    from PIL import Image

    one = echo_outline(w, h, text, font, ink, size)
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    step = one.size[1] * 0.9
    cy = h * (0.5 + dy)
    for sign in (-1, 1):
        y = round(cy + sign * level * step - one.size[1] / 2)
        if -one.size[1] < y < h:
            im.alpha_composite(one, (round((w - one.size[0]) / 2), y))
    return im


# ---------- T50 円周を回る文字 ----------


def ring_text(radius: int, text: str, font: str | None, color: str, h: int) -> Any:
    """T50: 半径 radius の円周に、題名（画面の高さの 4%・text_muted）を繰り返して並べた画。
    回すのはクリップの回転のキーフレーム（20 秒で 1 周）。"""
    from PIL import Image, ImageDraw

    px = max(8, round(h * 0.04))
    f = _font(font, px)
    unit = text.replace("\n", " ") + " ・ "
    side = 2 * (radius + px) + 4
    im = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    c = side / 2
    circ = 2 * math.pi * radius
    chars: list[str] = []
    while sum(f.getlength(ch) for ch in chars) < circ - px:
        chars += list(unit)
    total = sum(f.getlength(ch) for ch in chars)
    scale = circ / max(total, 1.0)  # 円周にちょうど収める
    ang = -math.pi / 2
    for ch in chars:
        adv = f.getlength(ch) * scale
        mid = ang + adv / 2 / radius
        g = Image.new("RGBA", (px * 2, px * 2), (0, 0, 0, 0))
        ImageDraw.Draw(g).text((px, px), ch, font=f, fill=_rgb(color), anchor="mm")
        g = g.rotate(-math.degrees(mid) - 90, resample=Image.Resampling.BICUBIC)
        x = c + radius * math.cos(mid) - px
        y = c + radius * math.sin(mid) - px
        im.alpha_composite(g, (round(x), round(y)))
        ang += adv / radius
    return im


# ---------- T48 縁から塗りへ ----------


def outline_to_fill_frames(card: Any, text_mask: Any, accent: str, n: int, h: int
                           ) -> Iterator[Any]:  # fmt: skip
    """T48: 章の題の画（card）を、縁だけ（2px・accent）で T10（下から 10f、easeOutExpo）出し、
    8f 後に面を左から右へ 10f（linear）で塗る。text_mask は題の文字の形（L）。"""
    from PIL import Image, ImageChops, ImageFilter

    cw, ch = card.size
    sw = max(1, round(2 * h / 1080))
    grown = text_mask.filter(ImageFilter.MaxFilter(2 * sw + 1))
    edge = ImageChops.subtract(grown, text_mask)
    outline = Image.new("RGBA", (cw, ch), (*_rgb(accent), 0))
    outline.putalpha(edge)
    box = text_mask.getbbox() or (0, 0, cw, ch)
    rise = box[3] - box[1]
    for i in range(n):
        im = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        t = min(1.0, (i + 1) / 10)
        e = 1 - 2 ** (-10 * t) if t < 1 else 1.0
        dy = round((1 - e) * rise)
        # 行の高さで切り抜いて下からせり上がる
        line = outline.crop((0, box[1], cw, box[3]))
        shifted = Image.new("RGBA", line.size, (0, 0, 0, 0))
        if dy < line.size[1]:
            shifted.alpha_composite(line, (0, dy))
        im.alpha_composite(shifted, (0, box[1]))
        fill_t = (i - 18 + 1) / 10
        if fill_t > 0:
            x1 = box[0] + round((box[2] - box[0]) * min(1.0, fill_t))
            m = Image.new("L", (cw, ch), 0)
            m.paste(255, (0, 0, x1, ch))
            filled = card.copy()
            filled.putalpha(ImageChops.multiply(card.getchannel("A"), m))
            im.alpha_composite(filled)
            im.alpha_composite(outline)
        yield im


def chapter_outline_fill(num: Any, title: Any, mask: Any, accent: str, n: int, h: int
                         ) -> Iterator[Any]:  # fmt: skip
    """T48 を H1 の章の題に: 番号の層は最初の 10f で不透明度 0→1、題は縁から塗りへ。"""
    from PIL import Image

    box = mask.getbbox() or (0, 0, *mask.size)
    pad = max(4, round(h * 0.01))
    crop = (max(0, box[0] - pad), max(0, box[1] - pad), min(mask.size[0], box[2] + pad),
            min(mask.size[1], box[3] + pad))  # fmt: skip
    t_card, t_mask = title.crop(crop), mask.crop(crop)
    for i, fr in enumerate(outline_to_fill_frames(t_card, t_mask, accent, n, h)):
        im = Image.new("RGBA", num.size, (0, 0, 0, 0))
        k = min(1.0, (i + 1) / 10)
        if k < 1:
            a = num.getchannel("A").point(lambda v, k=k: round(v * k))
            nn = num.copy()
            nn.putalpha(a)
            im.alpha_composite(nn)
        else:
            im.alpha_composite(num)
        im.alpha_composite(fr, (crop[0], crop[1]))
        yield im


# ---------- T44 奥へ流れる文字 ----------


def _perspective_coeffs(src: list[tuple[float, float]], dst: list[tuple[float, float]]
                        ) -> list[float]:  # fmt: skip
    """dst の点 → src の点 へ写す PIL の PERSPECTIVE の係数。"""
    import numpy as np

    rows = []
    rhs = []
    for (x, y), (u, v) in zip(dst, src, strict=True):
        rows.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        rows.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        rhs += [u, v]
    sol = np.linalg.solve(np.array(rows, dtype=float), np.array(rhs, dtype=float))
    return [float(x) for x in sol]


def crawl_frames(w: int, h: int, text: str, font: str | None, ink: str, n: int, fps: float
                 ) -> Iterator[Any]:  # fmt: skip
    """T44: 文を画面の幅の 60% で中央揃えに組み、X 軸に 55° 倒して下から奥へ流す。
    速さは画面の高さの 8%／秒。奥の 30% は不透明度 1→0。"""
    from PIL import Image, ImageChops, ImageDraw

    px = max(12, round(h * 0.06))
    f = _font(font, px)
    col_w = w * 0.6
    # 1 行に入る字数で折り返す
    out_lines: list[str] = []
    cur = ""
    for ch in text.strip():
        if f.getlength(cur + ch) > col_w and cur:
            out_lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        out_lines.append(cur)
    line_h = round(px * 1.5)
    strip_h = h + line_h * len(out_lines) + h
    strip = Image.new("RGBA", (w, strip_h), (0, 0, 0, 0))
    d = ImageDraw.Draw(strip)
    for k, ln in enumerate(out_lines):
        d.text((w / 2, h + k * line_h), ln, font=f, fill=_rgb(ink), anchor="mt")
    # 倒した面: 手前（画面の下端）は幅 100%、奥（画面の上から 15%）は幅 cos(55°) 程度に狭める
    near_y, far_y = float(h), h * 0.15
    k_ = math.cos(math.radians(CRAWL_TILT))
    far_half = w * 0.5 * max(0.18, k_ * 0.6)
    dst = [(w / 2 - far_half, far_y), (w / 2 + far_half, far_y), (w, near_y), (0.0, near_y)]
    fade = Image.linear_gradient("L").resize((w, h))  # 上が 0（黒）→ 下が 255
    cut = round(h * 0.15 + (near_y - far_y) * 0.3)
    alpha_ramp = fade.point(lambda v: 255 if v * h / 255 >= cut else round(255 * v * h / 255 / cut))
    view_h = h  # 面の上で見える範囲（画面 1 枚分）
    for i in range(n):
        off = round(i / fps * CRAWL_SPEED * h)
        top = min(off, strip_h - view_h)
        src_box = [(0.0, float(top)), (float(w), float(top)), (float(w), float(top + view_h)),
                   (0.0, float(top + view_h))]  # fmt: skip
        coeffs = _perspective_coeffs(src_box, dst)
        im = strip.transform((w, h), Image.Transform.PERSPECTIVE, coeffs, Image.Resampling.BICUBIC)
        im.putalpha(ImageChops.multiply(im.getchannel("A"), alpha_ramp))
        yield im


# ---------- T36 取り消し線で消す・T46 打ち直すタイプライター ----------

NOT_WORDS = ("ではなく", "じゃなく")
STRIKE_RED = "#E03A3A"
_CUT = "、。，．,.!！?？をがはにでとへものや "


def not_phrases(text: str) -> tuple[str, str] | None:
    """「A ではなく B」の A（直前の 1 字を含む文節）と B（続く文節）。無ければ None。"""
    idx = min((i for i in (text.find(w) for w in NOT_WORDS) if i > 0), default=-1)
    if idx <= 0:
        return None
    word = next(w for w in NOT_WORDS if text.find(w) == idx)
    before = text[:idx]
    cut = max(before.rfind(ch) for ch in _CUT)
    a = before[cut + 1 :].strip()
    rest = text[idx + len(word) :].lstrip("、, ")
    stop = min((i for i in (rest.find(ch) for ch in "、。！？!?") if i >= 0), default=len(rest))
    b = rest[:stop]
    for tail in ("です", "でした", "だ", "にします", "を使います"):
        if b.endswith(tail) and len(b) > len(tail):
            b = b[: -len(tail)]
            break
    if not a or not b:
        return None
    return a[-10:], b[:10]


def _card(w: int, h: int, size: int) -> tuple[Any, Any]:
    from PIL import Image, ImageDraw

    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    return im, ImageDraw.Draw(im)


def strike_frames(a: str, b: str, font: str | None, size: int, ink: str, panel: str, n: int
                  ) -> tuple[tuple[int, int], Iterator[Any]]:  # fmt: skip
    """T36: 「A B」を出し、A の文字の高さの中央に太さ 文字の大きさ × 0.08・#E03A3A の線を左から
    右へ 3f（linear）で引き、6f 静止の後、A を scale y 1→0 に 6f（easeInCubic）で消して、B を
    左へ詰める（6f、easeOutCubic）。入りは不透明度 0→1 を 6f。"""
    from PIL import Image, ImageDraw

    f = _font(font, size)
    gap = size * 0.4
    wa, wb = f.getlength(a), f.getlength(b)
    pad = round(size * 0.35)
    cw, ch = round(wa + gap + wb + 2 * pad), round(size * 1.6)
    t_in, t_line, t_hold, t_out = 6, 3, 6, 6

    def gen() -> Iterator[Any]:
        for i in range(n):
            im = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
            d = ImageDraw.Draw(im)
            d.rounded_rectangle((0, 0, cw - 1, ch - 1), radius=round(size * 0.25),
                                fill=(*_rgb(panel), 225))  # fmt: skip
            t = i - t_in
            sy = 1.0
            shift = 0.0
            if t >= t_line + t_hold:
                u = min(1.0, (t - t_line - t_hold + 1) / t_out)
                sy = 1 - u**3
                shift = (wa + gap) * _ease_out(u)
            cy = ch / 2
            if sy > 0.02:
                layer = Image.new("RGBA", (round(wa) + 4, ch), (0, 0, 0, 0))
                ld = ImageDraw.Draw(layer)
                ld.text((0, cy), a, font=f, fill=(*_rgb(ink), 255), anchor="lm")
                if t >= 0:
                    x1 = wa * min(1.0, (t + 1) / t_line)
                    lw = max(2, round(size * 0.08))
                    ld.line([(0, cy), (x1, cy)], fill=(*_rgb(STRIKE_RED), 255), width=lw)
                if sy < 1:
                    lh = max(1, round(ch * sy))
                    layer = layer.resize((layer.size[0], lh), Image.Resampling.BICUBIC)
                im.alpha_composite(layer, (pad, round((ch - layer.size[1]) / 2)))
            d.text((pad + wa + gap - shift, cy), b, font=f, fill=(*_rgb(ink), 255), anchor="lm")
            if i < t_in:
                im.putalpha(im.getchannel("A").point(lambda v, k=(i + 1) / t_in: round(v * k)))
            yield im

    return (cw, ch), gen()


def retype_frames(a: str, b: str, font: str | None, size: int, ink: str, panel: str, n: int
                  ) -> tuple[tuple[int, int], Iterator[Any]]:  # fmt: skip
    """T46: A を後ろから 1 字 1f で消し（カーソル「▍」を付ける）、続く B を 1 字 2f で打つ。"""
    from PIL import Image, ImageDraw

    f = _font(font, size)
    pad = round(size * 0.35)
    cur = "▍"
    cw = round(max(f.getlength(a), f.getlength(b)) + f.getlength(cur) + 2 * pad)
    ch = round(size * 1.6)
    hold = 12  # A を読ませる間

    def text_at(i: int) -> str:
        if i < hold:
            return a
        k = i - hold
        if k < len(a):
            return a[: len(a) - k - 1]
        k -= len(a)
        return b[: min(len(b), k // 2 + 1)]

    def gen() -> Iterator[Any]:
        for i in range(n):
            im = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
            d = ImageDraw.Draw(im)
            d.rounded_rectangle((0, 0, cw - 1, ch - 1), radius=round(size * 0.25),
                                fill=(*_rgb(panel), 225))  # fmt: skip
            s = text_at(i)
            d.text((pad, ch / 2), s, font=f, fill=(*_rgb(ink), 255), anchor="lm")
            if (i // 8) % 2 == 0:  # カーソルは 15f 周期で点滅（T6 と同じ）
                d.text((pad + f.getlength(s), ch / 2), cur, font=f, fill=(*_rgb(ink), 255),
                       anchor="lm")  # fmt: skip
            yield im

    return (cw, ch), gen()
