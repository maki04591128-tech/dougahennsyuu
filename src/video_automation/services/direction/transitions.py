"""場面の切り替えの型（統合版 段階5d: E6〜E10）。

図・カードのショットが替わる所（前のショットの終わり = 次のショットの始まり）に、用途の型の
切り替えを付ける。回数は 2.1 の切り替えの上限（多くの用途は 1 分に 1 か所）まで。

- E6 形のワイプ・E9 光漏れ・E10 グリッチは、切り替えの時刻に重ねる透過の動画（下の画は替えない）
- E7 ウィップパン・E8 ズームパンチは、次のショットの入り（Transition の whip・zoom_punch）
- E11 ページめくり・E12 キューブ・E13 マッチカットは未対応（今のまま）
"""

from __future__ import annotations

import math
import random
from typing import Any

# 用途 → 切り替えの型（統合版 2.4。ディゾルブの用途はここに無い）
PURPOSE_TRANSITION: dict[str, str] = {
    "explainer": "E6",
    "ranking": "E6",
    "kids": "E6",
    "vlog": "E7",
    "short": "E8",
    "event": "E9",
    "game": "E10",
}
OVERLAY = {"E6": 16, "E9": 12, "E10": 6}  # 重ねる動画の長さ（30fps のフレーム）
CUT_AT = {"E6": 9, "E9": 6, "E10": 3}  # 重ねる動画の何フレーム目で下の画が替わるか
ENTRANCE = {"E7": ("whip", 6), "E8": ("zoom_punch", 5)}  # 次のショットの入り（種類, フレーム）
PER_MINUTE = {"short": 99, "event": 1}  # 2.1 の上限。無い用途は 1 分に 1 か所


def pick_cuts(bounds: list[int], fps: float, total: int, purpose: str) -> list[int]:
    """切り替えを付ける境目（各 1 分の区間に最大 PER_MINUTE 個。前の方から）。"""
    per = PER_MINUTE.get(purpose, 1)
    minute = round(60 * fps)
    used: dict[int, int] = {}
    out = []
    for b in sorted(set(bounds)):
        if b <= 0 or b >= total:
            continue
        k = b // minute
        if used.get(k, 0) >= per:
            continue
        used[k] = used.get(k, 0) + 1
        out.append(b)
    return out


def _rgb(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def _cubic_io(u: float) -> float:
    u = min(max(u, 0.0), 1.0)
    return 4 * u**3 if u < 0.5 else 1 - (-2 * u + 2) ** 3 / 2


def shape_wipe(w: int, h: int, colors: list[str]) -> list[Any]:
    """E6: 3 色の面が右下から左上へずらして画面を覆い、同じ順で抜ける。全体 16f。

    統合版の「3f ずらし・各 8f」では 16f に入りと出が収まらないので、2f ずらし・各 5f にした。
    覆いきった 9f 目に下の画が替わる（CUT_AT）。"""
    from PIL import Image, ImageDraw

    n = 16
    diag = math.hypot(w, h)
    out = []
    for f in range(n):
        im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        for i, c in enumerate(colors[:3]):
            # 入り: 2f ずらしで各 5f、出: 同じ順で各 5f（16f に収めるため値を詰めた）
            cover = _cubic_io((f - 2 * i) / 5)
            leave = _cubic_io((f - 6 - 2 * i) / 5)
            if cover <= 0 or leave >= 1:
                continue
            # 右下の角から左上へ進む帯。先頭 = cover、後ろの端 = leave（対角線の長さで）
            head, tail = cover * diag * 1.05, leave * diag * 1.05

            def pt(s: float, t: float) -> tuple[float, float]:
                # 対角線に沿った位置 s、それに垂直な位置 t（画面の右下が原点）
                ux, uy = w / diag, h / diag
                return (w - s * ux - t * uy, h - s * uy + t * ux)

            big = diag
            poly = [pt(tail, -big), pt(head, -big), pt(head, big), pt(tail, big)]
            d.polygon(poly, fill=(*_rgb(c), 255))
        out.append(im)
    return out


def light_leak(w: int, h: int) -> list[Any]:
    """E9: 暖色 #FFB46A のぼけた光（加算のつもりで不透明度は最大 60%）が左から右へ 12f で通る。

    光の幅は画面の幅の 40%（S1 の輝度の変化を抑えるため）。"""
    import numpy as np
    from PIL import Image

    n = 12
    xs = np.arange(w, dtype=np.float32)
    ys = np.arange(h, dtype=np.float32)[:, None]
    out = []
    for f in range(n):
        u = (f + 0.5) / n
        cx = -0.2 * w + u * 1.4 * w
        sx = 0.2 * w  # 光の幅（半値の幅で画面の約 33%。最大は 40%）
        a = np.exp(-(((xs - cx) / sx) ** 2))[None, :] * (0.75 + 0.25 * np.sin(ys / h * math.pi))
        a = (0.6 * math.sin(math.pi * u) ** 0.5 * a * 255).astype(np.uint8)
        rgba = np.zeros((h, w, 4), np.uint8)
        rgba[..., :3] = (0xFF, 0xB4, 0x6A)
        rgba[..., 3] = a
        out.append(Image.fromarray(rgba, "RGBA"))
    return out


def glitch(w: int, h: int, colors: list[str], seed: int) -> list[Any]:
    """E10: 高さ 8% の横の帯ごとに x ±画面の幅の 3%・R と B を ±6px ずらした色の帯（6f）。

    下の画を切って動かすことはできないので、色の帯（R・B の 2 色・不透明度 55%）を重ねて代える。"""
    from PIL import Image, ImageDraw

    rnd = random.Random(seed)
    bh = max(2, round(h * 0.08))
    rows = list(range(0, h, bh))
    shifts = [rnd.uniform(-0.03, 0.03) * w for _ in rows]
    out = []
    for f in range(6):
        k = 1 - abs(f - 2.5) / 3  # 真ん中で最も強く
        im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        for y, s in zip(rows, shifts, strict=True):
            if rnd.random() < 0.45:
                continue
            dx = s * k
            px = 6 * h / 1080 * k
            d.rectangle([w * 0.1 + dx - px, y, w * 0.9 + dx - px, y + bh], fill=(255, 40, 60, 70))
            d.rectangle([w * 0.1 + dx + px, y, w * 0.9 + dx + px, y + bh], fill=(40, 120, 255, 70))
        out.append(im)
    return out


def overlay_frames(kind: str, w: int, h: int, colors: list[str], seed: int) -> list[Any]:
    if kind == "E6":
        return shape_wipe(w, h, colors)
    if kind == "E9":
        return light_leak(w, h)
    return glitch(w, h, colors, seed)
