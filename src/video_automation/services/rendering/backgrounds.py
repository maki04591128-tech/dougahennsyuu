"""素材が無い区間の背景（統合版 段階5d: R1〜R18）。Python（numpy・Pillow）で描いて動画にする。

- 継ぎ目なく繰り返すため、1 本の長さを LOOP_SEC 秒にし、動きの周期・流れる量はその長さで
  割り切れる値に丸める（丸めた値は各関数の説明に書く）
- 色は用途の型の配色（base・base2・surface・main・accent・text_muted・chart）
- R10 紙・R17 スタジオは動きが小さいので、R10 は静止画、R17 は光だけを動かす
"""

from __future__ import annotations

import math
import random
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

LOOP_SEC = 24.0

# 用途 → 背景（統合版 2.4 の「素材が無い区間の背景」。ここに無い用途は今の背景のまま）
PURPOSE_BACKGROUND: dict[str, str] = {
    "interview": "R6",
    "documentary": "R4",
    "story": "R2",
    "podcast": "R1",
    "explainer": "R5",
    "lecture": "R10",
    "tutorial": "R9",
    "recipe": "R13",
    "news": "R14",
    "corporate": "R3",
    "short": "R12",
    "game": "R11",
    "vlog": "R8",
    "ranking": "R15",
    "product": "R17",
    "promo": "R16",
    "event": "R18",
    "kids": "R7",
}
STATIC = {"R10"}  # 動かさない（静止画）
# 流れる量が 1 本の長さで割り切れない背景。最後の XFADE の間、1 周前の画へ重ねて戻す
NONPERIODIC = {"R2", "R3", "R6", "R8"}
XFADE = 0.15


def _rgb(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def _cycles(period: float) -> int:
    """LOOP_SEC の中に入る周期の数（最小 1）。周期は LOOP_SEC / この数 に丸める。"""
    return max(1, round(LOOP_SEC / period))


class Painter:
    """1 つの背景。frame(u) は u（0〜1、1 本の中の位置）の画を返す。"""

    def __init__(self, w: int, h: int, pal: dict[str, Any], seed: int = 7) -> None:
        self.w, self.h, self.pal = w, h, pal
        self.rng = random.Random(seed)
        self.setup()

    def setup(self) -> None:  # 静的な層を作る
        pass

    def frame(self, u: float) -> Any:  # pragma: no cover - 各背景で上書き
        raise NotImplementedError

    # 共通の道具
    def flat(self, c: str) -> Any:
        from PIL import Image

        return Image.new("RGB", (self.w, self.h), _rgb(c))

    def vgrad(self, top: str, bottom: str) -> Any:
        import numpy as np
        from PIL import Image

        a, b = np.array(_rgb(top), float), np.array(_rgb(bottom), float)
        t = np.linspace(0, 1, self.h)[:, None, None]
        img = (a * (1 - t) + b * t).repeat(self.w, axis=1)
        return Image.fromarray(img.astype(np.uint8), "RGB")

    def blob(self, d: int, c: str, alpha: float, blur: float) -> Any:
        """ぼけた円（透過）。"""
        from PIL import Image, ImageDraw, ImageFilter

        pad = int(blur * 3)
        im = Image.new("RGBA", (d + 2 * pad, d + 2 * pad), (*_rgb(c), 0))
        m = Image.new("L", im.size, 0)
        ImageDraw.Draw(m).ellipse([pad, pad, pad + d, pad + d], fill=round(255 * alpha))
        if blur > 0:
            m = m.filter(ImageFilter.GaussianBlur(blur))
        im.putalpha(m)
        return im


class Aurora(Painter):
    """R1: 主色・強調色のぼかした帯 3 本が上下に画面の高さの 6%（周期 8・12・12 秒。
    10 秒は 12 秒に丸めた）。"""

    def setup(self) -> None:
        from PIL import Image, ImageDraw, ImageFilter

        self.base = self.flat(self.pal["base"])
        self.bands = []
        cols = [self.pal["main"], self.pal["accent"], self.pal["main"]]
        for i, c in enumerate(cols):
            bh = int(self.h * 0.22)
            im = Image.new("RGBA", (self.w, bh * 3), (*_rgb(c), 0))
            m = Image.new("L", im.size, 0)
            ImageDraw.Draw(m).rectangle([0, bh, self.w, 2 * bh], fill=110 - 20 * i)
            im.putalpha(m.filter(ImageFilter.GaussianBlur(self.h * 0.12)))
            self.bands.append((im, (0.25 + 0.22 * i) * self.h - bh * 1.5, _cycles((8, 10, 12)[i])))

    def frame(self, u: float) -> Any:
        img = self.base.copy().convert("RGBA")
        for im, y0, k in self.bands:
            dy = math.sin(2 * math.pi * k * u) * self.h * 0.06
            img.alpha_composite(im, (0, int(y0 + dy)))
        return img.convert("RGB")


class Stars(Painter):
    """R2: 白の点 120 個（直径 2〜4px、不透明度 40〜90%）が周期 3〜6 秒で ±30% またたき、
    全体が左へ。

    左への流れは 1 本（24 秒）で画面の幅の 12%（0.5%／秒）。点は流れの方向に繰り返して置く。"""

    def setup(self) -> None:
        self.base = self.flat(self.pal["base"])
        r = self.rng
        self.pts = [
            (r.random(), r.random(), r.uniform(2, 4) * self.h / 1080, r.uniform(0.4, 0.9),
             _cycles(r.uniform(3, 6)), r.random())
            for _ in range(120)
        ]  # fmt: skip

    def frame(self, u: float) -> Any:
        from PIL import ImageDraw

        img = self.base.copy().convert("RGBA")
        lay = img.copy()
        lay.paste((0, 0, 0, 0), (0, 0, self.w, self.h))
        d = ImageDraw.Draw(lay)
        shift = 0.12 * u
        for x, y, dia, a, k, ph in self.pts:
            px = ((x - shift) % 1.0) * self.w
            al = max(0.0, min(1.0, a * (1 + 0.3 * math.sin(2 * math.pi * (k * u + ph)))))
            rr = dia / 2
            d.ellipse([px - rr, y * self.h - rr, px + rr, y * self.h + rr],
                      fill=(255, 255, 255, round(255 * al)))  # fmt: skip
        img.alpha_composite(lay)
        return img.convert("RGB")


class Network(Painter):
    """R3: 点 40 個が画面の幅の 1%／秒でまっすぐ動き、端で反対側から出る。
    近い点（幅の 14% 未満）を線で結ぶ。

    1 本（24 秒）で各点が画面の幅のちょうど 24% 動くよう、向きを 4 方向の斜めに限った。"""

    def setup(self) -> None:
        self.base = self.flat(self.pal["base"])
        r = self.rng
        self.pts = []
        for _ in range(40):
            ang = r.choice([1, 3, 5, 7]) * math.pi / 4
            self.pts.append((r.random(), r.random(), math.cos(ang), math.sin(ang)))
        self.col = _rgb(self.pal["main"])

    def frame(self, u: float) -> Any:
        from PIL import Image, ImageDraw

        lay = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        d = ImageDraw.Draw(lay)
        dist = 0.24 * u * self.w
        pts = [((x * self.w + cx * dist) % self.w, (y * self.h + cy * dist) % self.h)
               for x, y, cx, cy in self.pts]  # fmt: skip
        lim = 0.14 * self.w
        for i, (ax, ay) in enumerate(pts):
            for bx, by in pts[i + 1 :]:
                dd = math.hypot(ax - bx, ay - by)
                if dd < lim:
                    al = round(255 * 0.35 * (1 - dd / lim))
                    d.line([ax, ay, bx, by], fill=(*self.col, al), width=max(1, self.h // 1080))
        rr = 3 * self.h / 1080
        for x, y in pts:
            d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=(*self.col, 200))
        img = self.base.copy().convert("RGBA")
        img.alpha_composite(lay)
        return img.convert("RGB")


def _periodic_noise(gw: int, gh: int, rng: random.Random, octaves: int = 3) -> Any:
    """縦横に繰り返す滑らかな雑音（低い解像度の乱数を重ねて補間）。"""
    import numpy as np
    from PIL import Image

    acc = np.zeros((gh, gw))
    for o in range(octaves):
        n = 4 * 2**o
        g = np.array([[rng.random() for _ in range(n)] for _ in range(n)])
        g = np.concatenate([g, g[:1]], axis=0)
        g = np.concatenate([g, g[:, :1]], axis=1)
        im = Image.fromarray((g * 255).astype(np.uint8)).resize((gw, gh), Image.Resampling.BICUBIC)
        acc += np.asarray(im, float) / 255 / 2**o
    return acc / acc.max()


class Contours(Painter):
    """R4: 雑音（2 層を混ぜて時間で回す）の高さを 12 段に切った線（1px・text_muted・不透明度 30%）。

    雑音の 4 次元目の動き（1 秒に 0.02）は、2 つの雑音を 1 本（24 秒）
    で 1 周する重みで混ぜて代えた。"""

    def setup(self) -> None:
        self.base = self.flat(self.pal["base"])
        gw, gh = max(64, self.w // 6), max(36, self.h // 6)
        self.n1 = _periodic_noise(gw, gh, self.rng)
        self.n2 = _periodic_noise(gw, gh, self.rng)
        self.col = _rgb(self.pal["text_muted"])
        self.ink = self.flat(self.pal["text_muted"])

    def frame(self, u: float) -> Any:
        import numpy as np
        from PIL import Image

        a = 2 * math.pi * u
        f = self.n1 * (0.5 + 0.5 * math.cos(a)) + self.n2 * (0.5 + 0.5 * math.sin(a))
        f = (f - f.min()) / max(1e-9, f.max() - f.min())
        # 線は半分の解像度で求めてから広げる（1080p で 1 フレーム 1 秒かかったため）
        hw, hh = self.w // 2, self.h // 2
        im = Image.fromarray((f * 255).astype(np.uint8)).resize((hw, hh), Image.Resampling.BICUBIC)
        v = np.asarray(im, np.float32) / 255 * 12
        frac = np.abs(v - np.round(v))
        gy, gx = np.gradient(v)
        width = np.hypot(gx, gy) * 0.8 + 1e-6
        line = (np.clip(1 - frac / width, 0, 1) * 0.3 * 255).astype(np.uint8)
        m = Image.fromarray(line).resize((self.w, self.h), Image.Resampling.BILINEAR)
        return Image.composite(self.ink, self.base, m)


class DotRipple(Painter):
    """R5: 間隔は画面の高さの 4% の点の格子。中心からの波紋（波長は高さの 30%）
    の山の点だけ大きく主色に。

    波の速さは 1 本（24 秒）で波長のちょうど 12 個分（高さの 15%／秒）。"""

    def setup(self) -> None:
        self.base = self.flat(self.pal["base"])
        self.step = self.h * 0.04
        self.muted, self.main = _rgb(self.pal["text_muted"]), _rgb(self.pal["main"])

    def frame(self, u: float) -> Any:
        from PIL import Image, ImageDraw

        lay = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        d = ImageDraw.Draw(lay)
        cx, cy, lam = self.w / 2, self.h / 2, self.h * 0.30
        k = self.h / 1080
        y = self.step / 2
        while y < self.h:
            x = self.step / 2
            while x < self.w:
                r = math.hypot(x - cx, y - cy)
                crest = math.cos(2 * math.pi * (r / lam - 12 * u))
                if crest > 0.85:
                    rr = 3 * k
                    d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=(*self.main, 255))
                else:
                    rr = 1.5 * k
                    d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=(*self.muted, 90))
                x += self.step
            y += self.step
        img = self.base.copy().convert("RGBA")
        img.alpha_composite(lay)
        return img.convert("RGB")


class Bokeh(Painter):
    """R6: ぼけた円 24 個（直径は高さの 8〜20%、強調色と主色、不透明度 10〜25%）
    が上へ高さの 2%／秒。

    1 本（24 秒）で高さの 48% 上がる。上で消えた円は下から出る。"""

    def setup(self) -> None:
        self.base = self.vgrad(self.pal["base"], self.pal["base2"])
        r = self.rng
        self.items = []
        for i in range(24):
            d = int(self.h * r.uniform(0.08, 0.20))
            c = self.pal["accent"] if i % 2 else self.pal["main"]
            self.items.append(
                (r.random(), r.random(), self.blob(d, c, r.uniform(0.10, 0.25), d * 0.15))
            )

    def frame(self, u: float) -> Any:
        img = self.base.copy().convert("RGBA")
        span = self.h * 1.4
        for x, y, sp in self.items:
            py = (y * span - 0.48 * u * self.h) % span - self.h * 0.2
            img.alpha_composite(sp, (int(x * self.w - sp.size[0] / 2), int(py - sp.size[1] / 2)))
        return img.convert("RGB")


class Memphis(Painter):
    """R7: 丸・三角・ジグザグ・十字（辺は高さの 6%、図表の色）を間隔 12% の格子に置き、左下へ流す。

    流れは 1 本（24 秒）で格子のちょうど 4 マス分（高さの 2%／秒。並びの繰り返しに合わせた）。
    揺れは周期 4 秒・±8°。"""

    def setup(self) -> None:
        from PIL import Image, ImageDraw

        self.base = self.flat(self.pal["base"])
        s = int(self.h * 0.06)
        self.cell = self.h * 0.12
        cols = self.pal["chart"]
        self.sprites = []
        for k in range(4):
            im = Image.new("RGBA", (s * 2, s * 2), (0, 0, 0, 0))
            d = ImageDraw.Draw(im)
            c = (*_rgb(cols[k % len(cols)]), 255)
            o = s // 2
            lw = max(2, s // 8)
            if k == 0:
                d.ellipse([o, o, o + s, o + s], fill=c)
            elif k == 1:
                d.polygon([(o + s / 2, o), (o + s, o + s), (o, o + s)], fill=c)
            elif k == 2:
                pts = [(o + i * s / 4, o + (s / 3 if i % 2 else s * 2 / 3)) for i in range(5)]
                d.line(pts, fill=c, width=lw)
            else:
                d.line([(o + s / 2, o), (o + s / 2, o + s)], fill=c, width=lw)
                d.line([(o, o + s / 2), (o + s, o + s / 2)], fill=c, width=lw)
            self.sprites.append(im)

    def frame(self, u: float) -> Any:
        from PIL import Image

        img = self.base.copy().convert("RGBA")
        # 図形の並びは 4 マスで繰り返すので、流れる量と折り返す幅を 4 マスの倍数にする
        sh = 4 * self.cell * u
        nx = (int(self.w / self.cell) + 6) // 4 * 4
        ny = (int(self.h / self.cell) + 6) // 4 * 4
        ang = 8 * math.sin(2 * math.pi * _cycles(4) * u)
        rot = [
            sp.rotate(ang if i % 2 else -ang, resample=Image.Resampling.BICUBIC)
            for i, sp in enumerate(self.sprites)
        ]
        for j in range(-1, ny):
            for i in range(-1, nx):
                k = (i + 2 * j) % 4
                x = (i * self.cell - sh) % (nx * self.cell) - self.cell
                y = (j * self.cell + sh) % (ny * self.cell) - self.cell
                sp = rot[k]
                img.alpha_composite(sp, (int(x), int(y)))
        return img.convert("RGB")


class Sky(Painter):
    """R8: 上 #8EC5FF → 下 #DDF0FF の空に白の雲 5 つ。手前は右へ幅の 1.2%／秒、奥は 0.5%／秒。

    1 本（24 秒）で手前は幅の 28.8%、奥は 12% 動き、画面の外へ出た雲は左から戻る。"""

    def setup(self) -> None:
        from PIL import Image, ImageDraw, ImageFilter

        self.base = self.vgrad("#8EC5FF", "#DDF0FF")
        r = self.rng
        self.clouds = []
        for i in range(5):
            cw = int(self.w * r.uniform(0.16, 0.26))
            ch = int(cw * 0.45)
            im = Image.new("RGBA", (cw, ch), (255, 255, 255, 0))
            m = Image.new("L", im.size, 0)
            d = ImageDraw.Draw(m)
            d.ellipse([0, ch * 0.35, cw * 0.55, ch], fill=255)
            d.ellipse([cw * 0.25, 0, cw * 0.8, ch * 0.9], fill=255)
            d.ellipse([cw * 0.5, ch * 0.3, cw, ch], fill=255)
            im.putalpha(m.filter(ImageFilter.GaussianBlur(ch * 0.04)))
            near = i < 2
            self.clouds.append((r.random(), r.uniform(0.05, 0.55), im, 0.288 if near else 0.12))

    def frame(self, u: float) -> Any:
        img = self.base.copy().convert("RGBA")
        span = self.w * 1.3
        for x, y, im, v in self.clouds:
            px = (x * span + v * self.w * u) % span - self.w * 0.3
            img.alpha_composite(im, (int(px), int(y * self.h)))
        return img.convert("RGB")


class GridPaper(Painter):
    """R9: 間隔は高さの 5% の線（1px・主色・18%）と 5 本ごとの太い線（2px・30%）。左上へ流す。

    流れは 1 本（24 秒）で太い線の間隔のちょうど 1 つ分（高さの約 1%／秒）。"""

    def frame(self, u: float) -> Any:
        from PIL import Image, ImageDraw

        img = self.flat(self.pal["base"]).convert("RGBA")
        lay = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        d = ImageDraw.Draw(lay)
        st = self.h * 0.05
        off = 5 * st * u
        c = _rgb(self.pal["main"])
        k = max(1, round(self.h / 1080))
        i = 0
        x = -off % (5 * st) - 5 * st
        while x < self.w:
            bold = i % 5 == 0
            d.line(
                [(x, 0), (x, self.h)], fill=(*c, 77 if bold else 46), width=(2 if bold else 1) * k
            )
            x += st
            i += 1
        i = 0
        y = -off % (5 * st) - 5 * st
        while y < self.h:
            bold = i % 5 == 0
            d.line(
                [(0, y), (self.w, y)], fill=(*c, 77 if bold else 46), width=(2 if bold else 1) * k
            )
            y += st
            i += 1
        img.alpha_composite(lay)
        return img.convert("RGB")


class Paper(Painter):
    """R10: surface の面に紙の質感（細かい雑音を乗算・不透明度 10%）。動かさない。"""

    def frame(self, u: float) -> Any:
        import numpy as np
        from PIL import Image

        rng = np.random.default_rng(7)
        base = np.asarray(self.flat(self.pal["surface"]), float)
        n = rng.random((self.h // 2 + 1, self.w // 2 + 1))
        n = np.asarray(Image.fromarray((n * 255).astype(np.uint8)).resize((self.w, self.h)), float)
        mul = 1 - 0.10 * (1 - n / 255)
        return Image.fromarray((base * mul[..., None]).clip(0, 255).astype(np.uint8), "RGB")


class Outrun(Painter):
    """R11: 下半分に消失点が中央の地平線の格子（強調色・2px）。
    手前へ 1 秒に格子 1 マス（1 本で 24 マス）。

    上半分は base → base2 の縦のグラデーション。走査線は付けない（S1）。"""

    def setup(self) -> None:
        self.sky = self.vgrad(self.pal["base"], self.pal["base2"])
        self.col = _rgb(self.pal["accent"])

    def frame(self, u: float) -> Any:
        from PIL import ImageDraw

        img = self.sky.copy()
        d = ImageDraw.Draw(img)
        hy = self.h * 0.5
        d.rectangle([0, hy, self.w, self.h], fill=_rgb(self.pal["base"]))
        lw = max(2, round(2 * self.h / 1080))
        cx = self.w / 2
        for i in range(-12, 13):
            d.line([(cx, hy), (cx + i * self.w * 0.18, self.h)], fill=self.col, width=lw)
        ph = (24 * u) % 1.0
        for j in range(8):  # 奥ほど詰まる横線（z = 1, 2, 3 …。地平線に線が集まりすぎない数）
            z = j + 1 - ph
            if z <= 0.2:
                continue
            y = hy + (self.h - hy) / z
            if y <= self.h:
                d.line([(0, y), (self.w, y)], fill=self.col, width=lw)
        return img


class FocusLines(Painter):
    """R12: 中央（半径は高さの 35%）を空けて、外側に text_muted・不透明度 25% の楔形の線 60 本。

    2f ごとに線の長さを乱数で変える（Step 2）。"""

    def setup(self) -> None:
        self.base = self.flat(self.pal["base"])
        self.col = _rgb(self.pal["text_muted"])
        self.angles = [i * 2 * math.pi / 60 + self.rng.uniform(-0.03, 0.03) for i in range(60)]

    def frame(self, u: float) -> Any:
        from PIL import Image, ImageDraw

        step = (int(u * LOOP_SEC * 30) // 2) % int(LOOP_SEC * 15)  # 2f ごと（1 本で 1 周）
        r = random.Random(step)
        lay = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        d = ImageDraw.Draw(lay)
        cx, cy = self.w / 2, self.h / 2
        far = math.hypot(cx, cy) * 1.05
        for a in self.angles:
            inner = self.h * 0.35 + r.uniform(0, self.h * 0.12)
            wdt = 0.012
            p1 = (cx + math.cos(a) * inner, cy + math.sin(a) * inner)
            p2 = (cx + math.cos(a - wdt) * far, cy + math.sin(a - wdt) * far)
            p3 = (cx + math.cos(a + wdt) * far, cy + math.sin(a + wdt) * far)
            d.polygon([p1, p2, p3], fill=(*self.col, 64))
        img = self.base.copy().convert("RGBA")
        img.alpha_composite(lay)
        return img.convert("RGB")


class Gingham(Painter):
    """R13: 間隔は高さの 8% の縞（主色・不透明度 12% を縦横に重ねる）。右下へ流す。

    流れは 1 本（24 秒）で縞の 2 周期分（高さの約 1.3%／秒。1.5%／秒を割り切れる値に丸めた）。"""

    def frame(self, u: float) -> Any:
        import numpy as np
        from PIL import Image

        st = self.h * 0.08
        off = 2 * (2 * st) * u
        xs = (((np.arange(self.w) - off) // st) % 2).astype(np.intp)
        ys = (((np.arange(self.h) - off) // st) % 2).astype(np.intp)
        b, m = np.array(_rgb(self.pal["base"]), float), np.array(_rgb(self.pal["main"]), float)
        lut = np.array([b * (1 - a) + m * a for a in (0.0, 0.12, 0.24)], np.uint8)
        return Image.fromarray(lut[xs[None, :] + ys[:, None]], "RGB")


class RunningLines(Painter):
    """R14: 横の線（長さは幅の 10〜30%・1px・主色・30%）12 本が右へ流れる。

    速さは線ごとに、1 本（24 秒）で画面を 2〜5 周（幅の 8〜20%／秒 を周の数に丸めた）。"""

    def setup(self) -> None:
        self.base = self.flat(self.pal["base"])
        r = self.rng
        self.lines = [(r.random(), r.uniform(0.05, 0.95), r.uniform(0.1, 0.3), r.randint(2, 5))
                      for _ in range(12)]  # fmt: skip
        self.col = _rgb(self.pal["main"])

    def frame(self, u: float) -> Any:
        from PIL import Image, ImageDraw

        lay = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        d = ImageDraw.Draw(lay)
        k = max(1, round(self.h / 1080))
        for x, y, ln, laps in self.lines:
            span = 1 + ln
            px = ((x + laps * span * u) % span - ln) * self.w
            d.line(
                [(px, y * self.h), (px + ln * self.w, y * self.h)], fill=(*self.col, 77), width=k
            )
        img = self.base.copy().convert("RGBA")
        img.alpha_composite(lay)
        return img.convert("RGB")


class Diagonal(Painter):
    """R15: −30° の帯（幅は高さの 10%、base と base2 を交互）
    を帯に沿って流す（1 本で帯の 2 組分）。"""

    def setup(self) -> None:
        import numpy as np

        self.bw = self.h * 0.10
        a = math.radians(-30)
        yy, xx = np.mgrid[0 : self.h, 0 : self.w].astype(np.float32)
        # 帯に垂直な向きの座標（帯の向きに沿って流すと、帯は横へずれて見える）
        self.v0 = xx * math.sin(-a) + yy * math.cos(-a)
        self.c = np.array([_rgb(self.pal["base"]), _rgb(self.pal["base2"])], np.uint8)

    def frame(self, u: float) -> Any:
        import numpy as np
        from PIL import Image

        m = (np.floor((self.v0 - 4 * self.bw * u) / self.bw) % 2).astype(np.intp)
        return Image.fromarray(self.c[m], "RGB")


class Mesh(Painter):
    """R16: 主色・強調色・base2・base の 4 色のぼかしの中心が、画面の 20% の範囲を回る。

    周期は 12・24・24・12 秒（12〜20 秒を 1 本の長さで割り切れる値に丸めた）。"""

    def setup(self) -> None:
        self.base = self.flat(self.pal["base"])
        d = int(self.h * 0.9)
        self.blobs = [
            (self.blob(d, self.pal[c], 0.9, d * 0.25), cx, cy, k)
            for c, cx, cy, k in (
                ("main", 0.3, 0.35, 2), ("accent", 0.7, 0.4, 1), ("base2", 0.45, 0.7, 1),
                ("base", 0.75, 0.75, 2),
            )
        ]  # fmt: skip

    def frame(self, u: float) -> Any:
        img = self.base.copy().convert("RGBA")
        for sp, cx, cy, k in self.blobs:
            a = 2 * math.pi * k * u
            x = (cx + 0.1 * math.cos(a)) * self.w - sp.size[0] / 2
            y = (cy + 0.1 * math.sin(a)) * self.h - sp.size[1] / 2
            img.alpha_composite(sp, (int(x), int(y)))
        return img.convert("RGB")


class Studio(Painter):
    """R17: base の壁と床（境目は下から 30%、ぼかしでなじませる）
    と上からの楕円の光（周期 6 秒で ±5%）。"""

    def setup(self) -> None:
        import numpy as np
        from PIL import Image, ImageDraw, ImageFilter

        wall = np.array(_rgb(self.pal["base"]), float)
        floor = wall * 0.75
        t = np.clip((np.arange(self.h) - self.h * 0.7) / (self.h * 0.04) + 0.5, 0, 1)[:, None, None]
        img = (wall * (1 - t) + floor * t).repeat(self.w, axis=1)
        self.base = Image.fromarray(img.astype(np.uint8), "RGB")
        ell = Image.new("L", (self.w, self.h), 0)
        ImageDraw.Draw(ell).ellipse(
            [self.w * 0.2, -self.h * 0.3, self.w * 0.8, self.h * 0.75], fill=255
        )
        self.light = ell.filter(ImageFilter.GaussianBlur(self.h * 0.08))
        self.col = Image.new("RGB", (self.w, self.h), _rgb(self.pal["surface"]))

    def frame(self, u: float) -> Any:
        from PIL import Image

        a = 0.25 * (1 + 0.05 * math.sin(2 * math.pi * _cycles(6) * u))
        m = self.light.point(lambda v: round(v * a))
        return Image.composite(self.col, self.base, m)


class Confetti(Painter):
    """R18: 図表の色の長方形（8×16px）40 枚が下へ高さの 6%／秒、左右に周期 2 秒で ±2%、
    回りながら落ちる。

    継ぎ目なく繰り返すため、1 本（24 秒）で高さの 120%（5%／秒）落ちる。
    下で消えた紙は上から出る。"""

    def setup(self) -> None:
        self.base = self.flat(self.pal["base"])
        r = self.rng
        cols = self.pal["chart"]
        self.items = [(r.random(), r.random(), cols[i % len(cols)], r.random(), r.choice([1, 2, 3]))
                      for i in range(40)]  # fmt: skip

    def frame(self, u: float) -> Any:
        from PIL import Image, ImageDraw

        img = self.base.copy().convert("RGBA")
        k = self.h / 1080
        span = self.h * 1.2
        for x, y, c, ph, spins in self.items:
            py = (y * span + span * u) % span - self.h * 0.1
            px = (x + 0.02 * math.sin(2 * math.pi * (_cycles(2) * u + ph))) * self.w
            sp = Image.new("RGBA", (int(20 * k) + 2, int(20 * k) + 2), (0, 0, 0, 0))
            d = ImageDraw.Draw(sp)
            cx, cy = sp.size[0] / 2, sp.size[1] / 2
            d.rectangle([cx - 4 * k, cy - 8 * k, cx + 4 * k, cy + 8 * k], fill=(*_rgb(c), 255))
            sp = sp.rotate(360 * spins * u + 360 * ph, resample=Image.Resampling.BICUBIC)
            img.alpha_composite(sp, (int(px - cx), int(py - cy)))
        return img.convert("RGB")


STYLES: dict[str, Callable[..., Painter]] = {
    "R1": Aurora,
    "R2": Stars,
    "R3": Network,
    "R4": Contours,
    "R5": DotRipple,
    "R6": Bokeh,
    "R7": Memphis,
    "R8": Sky,
    "R9": GridPaper,
    "R10": Paper,
    "R11": Outrun,
    "R12": FocusLines,
    "R13": Gingham,
    "R14": RunningLines,
    "R15": Diagonal,
    "R16": Mesh,
    "R17": Studio,
    "R18": Confetti,
}


def render(
    ffmpeg: str,
    style: str,
    out: Path,
    size: tuple[int, int],
    fps: float,
    pal: dict[str, Any],
    seconds: float = LOOP_SEC,
    progress: Callable[[float], None] | None = None,
) -> Path:
    """背景を描く。静止の背景（R10）は PNG、
    それ以外は seconds 秒の MP4（最後の次が最初につながる）。"""
    w, h = size
    painter = STYLES[style](w, h, pal)

    def at(u: float) -> Any:
        img = painter.frame(u)
        if style in NONPERIODIC and u > 1 - XFADE:
            from PIL import Image

            k = (u - (1 - XFADE)) / XFADE
            k = k * k * (3 - 2 * k)
            img = Image.blend(img, painter.frame(u - 1), k)
        return img

    out.parent.mkdir(parents=True, exist_ok=True)
    if style in STATIC:
        painter.frame(0.0).save(out.with_suffix(".png"))
        return out.with_suffix(".png")
    n = max(2, round(seconds * fps))
    enc = subprocess.Popen(
        [ffmpeg, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
         "-r", f"{fps:.6f}", "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-pix_fmt", "yuv420p", str(out)],
        stdin=subprocess.PIPE,
    )  # fmt: skip
    assert enc.stdin is not None
    try:
        for f in range(n):
            enc.stdin.write(at(f / n).tobytes())
            if progress and f % 30 == 0:
                progress(f / n)
        enc.stdin.close()
        if enc.wait() != 0:
            raise RuntimeError("ffmpeg (background) failed")
    finally:
        if enc.poll() is None:
            enc.kill()
    return out
