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
