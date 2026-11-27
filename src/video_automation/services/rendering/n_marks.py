"""手描きの丸と虫めがね（統合版 段階5d: N12・N13）と線のふるえ（F3）。Pillow で描く。

- N12: 語の外接矩形を 1.25 倍に広げた楕円を、太さ 5px（1080p の出力で）・型の accent の線で、
  12f（easeInOutSine）で描く。線は F3 でふるえる（3 コマごとに ±1.5px の乱数でずらす）
- N13: 語の高さが画面の高さの 3.5% 未満（T40 を下回る）なら、N12 の代わりに、語の中心を 2.5 倍に
  拡大した円（直径は画面の高さの 32%・白い縁 6px・影）を語の右上へ出す（pop_in_soft 14f）

画は元のスライドと同じ縦横比の透過の画（小さくしてよい）。スライドのクリップと同じ置き方・
カメラのキーで重ねると、寄ったときも語の位置に合う。
"""

from __future__ import annotations

import math
import random
from collections.abc import Iterator
from typing import Any

N12_PAD = 1.25
N12_DRAW = 12
N13_MIN = 0.035  # 語の高さ（画面の高さ比）がこれ未満なら虫めがね
N13_ZOOM = 2.5
N13_DIAM = 0.32
JITTER_STEP = 3  # F3: 3 コマごと
JITTER_PX = 1.5


def _rgb(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def _sine_io(u: float) -> float:
    u = min(max(u, 0.0), 1.0)
    return -(math.cos(math.pi * u) - 1) / 2


def _cubic_out(u: float) -> float:
    u = min(max(u, 0.0), 1.0)
    return 1 - (1 - u) ** 3


def jitter(points: list[tuple[float, float]], frame: int, px: float, seed: int = 0
           ) -> list[tuple[float, float]]:  # fmt: skip
    """F3: 線の点を 3 コマごとに ±px の乱数でずらす（同じ 3 コマの間は同じずれ）。"""
    rnd = random.Random(seed * 100003 + frame // JITTER_STEP)
    return [(x + rnd.uniform(-px, px), y + rnd.uniform(-px, px)) for x, y in points]


def circle_frames(
    size: tuple[int, int],
    box: tuple[float, float, float, float],
    n: int,
    accent: str,
    px_per_out: float,
    seed: int = 0,
) -> Iterator[Any]:
    """N12: size は画の大きさ、box は画の画素での語の範囲。px_per_out は出力 1px あたりの
    画の px。"""
    from PIL import Image, ImageDraw

    w, h = size
    bx, by, bw, bh = box
    cx, cy = bx + bw / 2, by + bh / 2
    rx, ry = bw / 2 * N12_PAD + 4 * px_per_out, bh / 2 * N12_PAD + 4 * px_per_out
    seg = 72
    pts = [
        (
            cx + rx * math.cos(-math.pi / 2 + 2 * math.pi * k / seg * 1.05),
            cy + ry * math.sin(-math.pi / 2 + 2 * math.pi * k / seg * 1.05),
        )
        for k in range(seg + 1)
    ]  # fmt: skip  (少し行き過ぎて閉じる手描きの丸)
    width = max(1, round(5 * px_per_out))
    for i in range(n):
        im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        k = max(2, round(len(pts) * _sine_io((i + 1) / N12_DRAW)))
        p = jitter(pts[:k], i, JITTER_PX * px_per_out, seed)
        ImageDraw.Draw(im).line(p, fill=(*_rgb(accent), 255), width=width, joint="curve")
        yield im


def magnifier(slide: Any, box: tuple[float, float, float, float], diam: float, px_per_out: float
              ) -> Any:  # fmt: skip
    """N13: 語の中心を 2.5 倍に拡大した円の画（直径 diam px・白い縁 6px・影）。"""
    from PIL import Image, ImageDraw, ImageFilter

    bx, by, bw, bh = box
    cx, cy = bx + bw / 2, by + bh / 2
    r = diam / 2
    src_r = r / N13_ZOOM
    crop = slide.convert("RGBA").crop(
        (round(cx - src_r), round(cy - src_r), round(cx + src_r), round(cy + src_r))
    )
    d = max(4, round(diam))
    lens = crop.resize((d, d), Image.Resampling.BICUBIC)
    mask = Image.new("L", (d, d), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, d - 1, d - 1), fill=255)
    edge = max(1, round(6 * px_per_out))
    pad = round(d * 0.08)
    out = Image.new("RGBA", (d + 2 * pad, d + 2 * pad), (0, 0, 0, 0))
    shadow = Image.new("L", out.size, 0)
    ImageDraw.Draw(shadow).ellipse((pad + 4, pad + 8, pad + d + 4, pad + d + 8), fill=110)
    shadow = shadow.filter(ImageFilter.GaussianBlur(max(1, pad // 2)))
    out.paste((0, 0, 0, 255), (0, 0), shadow)
    out.paste(lens, (pad, pad), mask)
    ImageDraw.Draw(out).ellipse((pad, pad, pad + d - 1, pad + d - 1), outline=(255, 255, 255, 255),
                                width=edge)  # fmt: skip
    return out


def magnifier_frames(
    size: tuple[int, int],
    lens: Any,
    box: tuple[float, float, float, float],
    n: int,
) -> Iterator[Any]:
    """N13: 虫めがねを語の右上へ出す（scale 0.92→1・不透明度 0→1、14f、easeOutCubic）。"""
    from PIL import Image

    w, h = size
    bx, by, bw, bh = box
    lw, lh = lens.size
    # 語の右上（画面からはみ出さない）
    x = min(max(0, bx + bw), w - lw)
    y = min(max(0, by - lh), h - lh)
    for i in range(n):
        u = _cubic_out((i + 1) / 14)
        s = 0.92 + 0.08 * u
        im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        sw, sh = max(1, round(lw * s)), max(1, round(lh * s))
        part = lens.resize((sw, sh), Image.Resampling.BICUBIC)
        if u < 1:
            part.putalpha(part.getchannel("A").point(lambda v, u=u: round(v * u)))
        im.alpha_composite(part, (round(x + (lw - sw) / 2), round(y + (lh - sh) / 2)))
        yield im


# ---------- カードの項目の印（統合版 I1 線のアイコン・N15 チェックリスト） ----------

DONE_WORDS = ("できました", "完了", "終わり")  # N15: 文の終わりの語
I1_DRAW, N15_DRAW = 12, 8
N15_DIM = 0.45  # 項目の文字を不透明度 55% に見せるため、面の色を 45% で重ねる


def is_done(text: str) -> bool:
    """N15: 文が「できました」「完了」「終わり」で終わるか（句点・感嘆符は除く）。"""
    t = text.rstrip("。．！!？?　 ～〜ー")
    return t.endswith(DONE_WORDS) or t.endswith(("完了です", "終わりです"))


def item_box(layout: Any, item: int) -> tuple[int, int, int, int] | None:
    """カードの本文の item 番目の文字の範囲（カードの画素。x, y, 幅, 高さ）と、行の大きさ。"""
    from video_automation.services.rendering.card import text_width

    ls = [ln for ln in layout.lines if ln.item == item]
    if not ls:
        return None
    x0 = min(ln.x for ln in ls)
    y0 = min(ln.y for ln in ls)
    x1 = max(ln.x + text_width(ln.text, ln.size) for ln in ls)
    y1 = max(ln.y + ln.size for ln in ls)
    return round(x0), round(y0), round(x1 - x0), round(y1 - y0)


def item_head(layout: Any, item: int) -> tuple[int, int, int] | None:
    """項目の頭（行頭記号があればその位置・一辺、無ければ 1 行目の左）。カードの画素。"""
    for (bx, by, side), it in zip(layout.bullets, layout.bullet_items, strict=False):
        if it == item:
            return bx, by, side
    ls = [ln for ln in layout.lines if ln.item == item]
    if not ls:
        return None
    return ls[0].x - ls[0].size, ls[0].y, ls[0].size


def icon_draw_frames(name: str, size: int, color: str) -> list[Any]:
    """I1: 線の絵記号を 12f（easeInOutSine）で描く（中心から時計回りに現す）。"""
    from PIL import Image, ImageDraw

    from video_automation.services.rendering.pictogram import render_icon

    icon = render_icon(name, max(4, size), color)
    out = []
    for i in range(I1_DRAW):
        u = _sine_io((i + 1) / I1_DRAW)
        m = Image.new("L", icon.size, 0)
        ImageDraw.Draw(m).pieslice((-size, -size, 2 * size, 2 * size), -90, -90 + 360 * u,
                                   fill=255)  # fmt: skip
        fr = icon.copy()
        from PIL import ImageChops

        fr.putalpha(ImageChops.multiply(icon.getchannel("A"), m))
        out.append(fr)
    return out


def check_frames(size: int, color: str) -> list[Any]:
    """N15: チェックを 8f で描く（線を伸ばす）。"""
    from PIL import Image, ImageDraw

    pts = [(0.18, 0.55), (0.42, 0.78), (0.84, 0.26)]
    seg = [(pts[0], pts[1]), (pts[1], pts[2])]
    lens = [math.dist(a, b) for a, b in seg]
    total = sum(lens)
    width = max(2, round(size / 7))
    out = []
    for i in range(N15_DRAW):
        u = _cubic_out((i + 1) / N15_DRAW) * total
        im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        acc = 0.0
        for (a, b), ln in zip(seg, lens, strict=True):
            if u <= acc:
                break
            t = min(1.0, (u - acc) / ln)
            p = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
            d.line([(a[0] * size, a[1] * size), (p[0] * size, p[1] * size)],
                   fill=(*_rgb(color), 255), width=width, joint="curve")  # fmt: skip
            acc += ln
        out.append(im)
    return out


# ---------- P9 特長の引き出し線（商品紹介） ----------

P9_LINE, P9_TEXT = 8, 10  # 線を伸ばす 8f、語を T10 で出す 10f


def _expo_out(u: float) -> float:
    u = min(max(u, 0.0), 1.0)
    return 1.0 if u >= 1 else 1 - 2 ** (-10 * u)


def callout_anchors(rect: tuple[float, float, float, float], w: int) -> list[tuple[Any, ...]]:
    """P9: 画像の左上・右上・右下の順に、画像の縁から外へ伸ばす線（始点・終点・語の揃え）。"""
    x0, y0, x1, y1 = rect
    ln = 0.08 * w
    d = ln / math.sqrt(2)
    return [
        ((x0, y0), (x0 - d, y0 - d), "rs"),  # 左上: 語は線の先の左
        ((x1, y0), (x1 + d, y0 - d), "ls"),
        ((x1, y1), (x1 + d, y1 + d), "lt"),
    ]


def callout_layer(
    size: tuple[int, int],
    rect: tuple[float, float, float, float],
    words: list[str],
    shown: list[int | None],
    color: str,
    ink: str,
    font: str | None,
) -> Any:
    """P9 の 1 枚。shown[k] は k 番目の特長の、出てからのフレーム数（None はまだ）。
    線は 2px・型の main、語は画面の高さの 4.5%（T10 で下からせり上がる）。"""
    from PIL import Image, ImageDraw, ImageFont

    w, h = size
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    px = max(10, round(0.045 * h))
    f = ImageFont.truetype(font, px) if font else ImageFont.load_default(px)
    lw = max(1, round(2 * h / 1080))
    for k, (word, (p0, p1, anchor)) in enumerate(
        zip(words, callout_anchors(rect, w), strict=False)
    ):
        t = shown[k] if k < len(shown) else None
        if t is None:
            continue
        u = _cubic_out((t + 1) / P9_LINE)
        end = (p0[0] + (p1[0] - p0[0]) * u, p0[1] + (p1[1] - p0[1]) * u)
        d.line([p0, end], fill=(*_rgb(color), 255), width=lw)
        tt = t - P9_LINE
        if tt < 0:
            continue
        e = _expo_out((tt + 1) / P9_TEXT)
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(layer).text(
            (p1[0], p1[1] + (1 - e) * px * 1.2), word, font=f, fill=(*_rgb(ink), 255),
            anchor=anchor, stroke_width=max(1, px // 14), stroke_fill=(0, 0, 0, 160),
        )  # fmt: skip
        # 行の高さで切り抜く（マスクの下からせり上がる）
        top = p1[1] - px * 1.3 if anchor.endswith("s") else p1[1]
        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).rectangle((0, top, w, top + px * 1.3), fill=255)
        from PIL import ImageChops

        layer.putalpha(ImageChops.multiply(layer.getchannel("A"), mask))
        im.alpha_composite(layer)
    return im
