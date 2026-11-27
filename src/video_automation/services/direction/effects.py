"""効果と粒（統合版 段階5d: X1〜X3・X5・X6）。透過の画の列を作り、呼び出し側が動画にして重ねる。

- X1 紙吹雪: 図表の色の長方形（10×20px）60 枚を、下の左右の角から中央の上へ打ち上げて落とす（45f）
- X2 きらめき: 白の 4 つの角の星（文字の高さの 60%）を scale 0→1→0・回転 0→90° で 10f
- X3 集中線: R12 の線（中央の半径 30% を空ける）を白・不透明度 60% で 6f
- X5 煙: 灰色 #8890A0 のぼけた雲（不透明度 20%）6 つが上へ高さの 3%／秒、左右に周期 5 秒で ±2%
- X6 残り火: #FF8A3D の点 30 個（直径 3〜6px・光）が上へ高さの 10〜20%／秒、上へ行くほど薄く
"""

from __future__ import annotations

import math
import random
from typing import Any

FRAMES = {"X1": 45, "X2": 10, "X3": 6}
LOOP = {"X5": 150, "X6": 150}  # 繰り返して敷く粒（5 秒で継ぎ目なく繰り返す）


def _rgb(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def confetti(w: int, h: int, colors: list[str], seed: int = 1) -> list[Any]:
    """X1（30fps・45f）。初速は高さの 120%／秒、向きは ±20°、重力で落ちて 45f で消える。"""
    from PIL import Image, ImageDraw

    rnd = random.Random(seed)
    k = h / 1080
    g = 1.0 * h  # 重力（画面の高さ／秒²。統合版に値が無いので、画面の上の 3 割まで届く値にした）
    parts = []
    for i in range(60):
        left = i % 2 == 0
        x0 = 0.0 if left else float(w)
        ang = math.radians(rnd.uniform(-20, 20)) + math.atan2(-h * 0.9, (w / 2 - x0))
        v = 1.2 * h * rnd.uniform(0.8, 1.2)
        parts.append(
            (x0, float(h), v * math.cos(ang), v * math.sin(ang), colors[i % len(colors)],
             rnd.uniform(0, 360), rnd.uniform(-720, 720))
        )  # fmt: skip
    out = []
    for f in range(FRAMES["X1"]):
        t = f / 30
        im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        fade = 1.0 if f < 35 else max(0.0, (44 - f) / 9)
        for x0, y0, vx, vy, c, r0, spin in parts:
            x, y = x0 + vx * t, y0 + vy * t + g * t * t / 2
            sp = Image.new("RGBA", (int(24 * k) + 2,) * 2, (0, 0, 0, 0))
            cx = sp.size[0] / 2
            ImageDraw.Draw(sp).rectangle(
                [cx - 5 * k, cx - 10 * k, cx + 5 * k, cx + 10 * k],
                fill=(*_rgb(c), round(255 * fade)),
            )
            sp = sp.rotate(r0 + spin * t, resample=Image.Resampling.BICUBIC)
            if -50 < x < w + 50 and y < h + 50:
                im.alpha_composite(sp, (int(x - cx), int(y - cx)))
        out.append(im)
    return out


def sparkle(size: int) -> list[Any]:
    """X2（10f）。size は星の大きさ（文字の高さの 60%）。"""
    from PIL import Image, ImageDraw

    out = []
    for f in range(FRAMES["X2"]):
        u = f / (FRAMES["X2"] - 1)
        s = math.sin(math.pi * u)  # 0→1→0
        im = Image.new("RGBA", (size * 2, size * 2), (0, 0, 0, 0))
        if s > 0.01:
            d = ImageDraw.Draw(im)
            c = size
            r, q = size / 2 * s, size / 2 * s * 0.22
            pts = []
            for i in range(8):
                a = math.radians(90 * u) + i * math.pi / 4
                rr = r if i % 2 == 0 else q
                pts.append((c + rr * math.cos(a), c + rr * math.sin(a)))
            d.polygon(pts, fill=(255, 255, 255, 255))
        out.append(im)
    return out


def focus_lines(w: int, h: int, seed: int = 1) -> list[Any]:
    """X3（6f）。2f ごとに線の長さを変える。"""
    from PIL import Image, ImageDraw

    rnd0 = random.Random(seed)
    angles = [i * 2 * math.pi / 60 + rnd0.uniform(-0.03, 0.03) for i in range(60)]
    cx, cy = w / 2, h / 2
    far = math.hypot(cx, cy) * 1.05
    out = []
    for f in range(FRAMES["X3"]):
        rnd = random.Random(seed * 100 + f // 2)
        im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        for a in angles:
            inner = h * 0.30 + rnd.uniform(0, h * 0.12)
            wd = 0.012
            d.polygon(
                [(cx + math.cos(a) * inner, cy + math.sin(a) * inner),
                 (cx + math.cos(a - wd) * far, cy + math.sin(a - wd) * far),
                 (cx + math.cos(a + wd) * far, cy + math.sin(a + wd) * far)],
                fill=(255, 255, 255, 153),
            )  # fmt: skip
        out.append(im)
    return out


def smoke(w: int, h: int, seed: int = 1) -> list[Any]:
    """X5（150f で繰り返す）。雲は上へ 3%／秒（150f で 15%）流れ、外へ出たら下から戻る。"""
    from PIL import Image, ImageDraw, ImageFilter

    rnd = random.Random(seed)
    clouds = []
    for _ in range(6):
        d = int(h * rnd.uniform(0.25, 0.4))
        m = Image.new("L", (d * 2, d), 0)
        ImageDraw.Draw(m).ellipse([d * 0.2, d * 0.2, d * 1.8, d * 0.8], fill=255)
        m = m.filter(ImageFilter.GaussianBlur(d * 0.15)).point(lambda v: round(v * 0.2))
        sp = Image.new("RGBA", m.size, (0x88, 0x90, 0xA0, 0))
        sp.putalpha(m)
        clouds.append((rnd.random(), rnd.random(), sp))
    n = LOOP["X5"]

    def draw(u: float) -> Any:
        im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        for x, y, sp in clouds:
            span = h + sp.size[1]
            py = ((y - 0.15 * u) % 1.0) * span - sp.size[1]
            px = (x + 0.02 * math.sin(2 * math.pi * u)) * w - sp.size[0] / 2
            im.alpha_composite(sp, (int(px), int(py)))
        return im

    out = []
    for f in range(n):
        u = f / n
        im = draw(u)
        if u > 0.8:  # 流れた量は 1 周に満たないので、最後の 1 秒で 1 周前の画へ重ねて戻す
            im = Image.blend(im, draw(u - 1), (u - 0.8) / 0.2)
        out.append(im)
    return out


def embers(w: int, h: int, seed: int = 1) -> list[Any]:
    """X6（150f で 1 周）。各点は 5 秒で画面の高さの 1 周上がる（10〜20%／秒を 20% に丸めた）。"""
    from PIL import Image, ImageDraw, ImageFilter

    rnd = random.Random(seed)
    pts = [
        (rnd.random(), rnd.random(), rnd.uniform(3, 6) * h / 1080, rnd.random()) for _ in range(30)
    ]
    n = LOOP["X6"]
    out = []
    for f in range(n):
        u = f / n
        im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        for x, y, r, ph in pts:
            py = ((y - u) % 1.0) * h  # 5 秒で 1 周 = 高さの 20%／秒
            px = (x + 0.03 * math.sin(2 * math.pi * (u + ph))) * w
            a = round(255 * (py / h))  # 上へ行くほど薄く
            d.ellipse([px - r, py - r, px + r, py + r], fill=(0xFF, 0x8A, 0x3D, a))
        glow = im.filter(ImageFilter.GaussianBlur(8 * h / 1080))
        glow.alpha_composite(im)
        out.append(glow)
    return out


def loading_done(h: int, accent: str, fps: float = 30) -> list[Any]:
    """I2: 直径は画面の高さの 10% の輪。円弧が 1 周 18f で 2 周してから、輪を accent で塗り、
    白のチェックを 8f で描く。1.5 秒で消す（最後の 6f で薄く）。"""
    from PIL import Image, ImageDraw

    d_ = round(h * 0.10)
    pad = max(4, d_ // 10)
    size = d_ + 2 * pad
    lw = max(3, d_ // 12)
    total = round(1.5 * fps)
    col = _rgb(accent)
    out = []
    for f in range(total):
        im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        box = [pad, pad, pad + d_, pad + d_]
        fade = 1.0 if f < total - 6 else (total - 1 - f) / 6
        a = round(255 * fade)
        if f < 36:  # 2 周
            start = (f % 18) / 18 * 360 - 90
            d.arc(box, start, start + 270, fill=(*col, a), width=lw)
        else:
            d.ellipse(box, fill=(*col, a))
            u = min(1.0, (f - 36) / 8)
            p1, p2, p3 = (0.28, 0.52), (0.44, 0.68), (0.74, 0.36)
            seg = u * 2
            path = [p1]
            if seg <= 1:
                path.append((p1[0] + (p2[0] - p1[0]) * seg, p1[1] + (p2[1] - p1[1]) * seg))
            else:
                v = seg - 1
                path += [p2, (p2[0] + (p3[0] - p2[0]) * v, p2[1] + (p3[1] - p2[1]) * v)]
            xy = [(pad + x * d_, pad + y * d_) for x, y in path]
            if len(xy) >= 2:
                d.line(xy, fill=(255, 255, 255, a), width=lw, joint="curve")
        out.append(im)
    return out
