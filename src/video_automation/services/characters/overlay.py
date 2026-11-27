"""立ち絵を描いて、透過つきの動画（VP9 の WebM）にする。

画面の下の帯（高さ band）だけを描き、タイムラインでは下に寄せて重ねる（ファイルを小さく保つ）。
"""

from __future__ import annotations

import math
import subprocess
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from video_automation.services.characters.motion import Animator, Schedule, State
from video_automation.services.characters.pack import Pack, Part

BAND_RATIO = 0.78  # 描く帯の高さ（画面の高さに対する比）
HEIGHT_RATIO = 0.64  # キャラクターの高さ（画面の高さに対する比）
SHOW_RATIO = 0.86  # キャラクターのうち画面に入る割合（足元は画面の下で切れる）
# 透過つき VP9（WebM）。読むときは libvpx-vp9 で復号する（ffmpeg 内蔵の vp9 は透過を捨てる）
ENCODE = [
    "-c:v",
    "libvpx-vp9",
    "-pix_fmt",
    "yuva420p",
    "-deadline",
    "realtime",
    "-cpu-used",
    "8",
    "-row-mt",
    "1",
    "-crf",
    "26",
    "-b:v",
    "0",
    "-auto-alt-ref",
    "0",
]


def layout(w: int, h: int) -> tuple[float, float, float]:
    """(帯の高さの比, キャラクターの高さの比, 左のキャラクターの足元の x の比)。

    横長は左右の端に大きく、縦長（ショート）は下の左右に小さく置く（字幕・中央の絵に重ねない）。"""
    if h > w:
        return 0.42, 0.34, 0.2
    return BAND_RATIO, HEIGHT_RATIO, 0.115


# 動きで外側へはみ出す分（キャラクターの高さに対する比）。外への移動（dx 最大 0.035+0.004）・
# 外への傾き（最大約 3.5 度 → 上端で約 0.061）・腕の振りの分を見込む
MOTION_MARGIN = 0.1
EDGE_PAD = 0.008  # 画面の端との余白（幅に対する比）
MAX_FOOT_X = 0.3  # 内側へ寄せる上限（これ以上は中央の絵に重なるので寄せない）


def safe_foot_x(w: int, char_w: float, char_h: float, side_x: float) -> float:
    """左のキャラクターの足元の x（画素）。動いても画面の端で切れない位置まで内側へ寄せる。

    char_w はパックの全部品を合わせた範囲の幅（Pack.size[0]）。伸び縮みの分も少し見込む。"""
    need = char_w / 2 * 1.03 + char_h * MOTION_MARGIN + w * EDGE_PAD
    return min(max(w * side_x, need), w * MAX_FOOT_X)


@dataclass
class Cast:
    pack: Pack
    side: str  # left / right
    schedule: Schedule
    seed: int = 0


def _arm_pivot(p: Part, size: tuple[int, int]) -> tuple[float, float]:
    """腕の部品の肩の位置（体の中心に近い側の、上端。上げた腕は下端）。"""
    cx = size[0] / 2
    inner = p.x + p.w if abs(p.x + p.w - cx) < abs(p.x - cx) else p.x
    raised = p.y < size[1] * 0.3
    return inner, (p.y + p.h if raised else p.y + p.h * 0.05)


class Composer:
    """表情・ポーズ・腕の角度ごとのキャラクター画像（キャッシュつき）。"""

    def __init__(self, pack: Pack, toward: int) -> None:
        self.pack = pack
        self.toward = toward  # 中央の向き（+1=右, -1=左）
        self.cache: OrderedDict[tuple[Any, ...], Any] = OrderedDict()

    def _choice(self, st: State) -> dict[str, str]:
        exp = self.pack.expression(st.expression)
        pose = self.pack.pose(st.pose)
        mouths = exp.get("mouth") or []
        mouth = mouths[min(st.mouth, len(mouths) - 1)] if mouths else ""
        eyes = exp.get("eyes", "open")
        eye_parts = self.pack.parts.get("eyes", {})
        if st.blink_phase >= 0 and exp.get("blink", True) and self.pack.blink in eye_parts:
            # 統合版 F1: 開 → 半目 1f → 閉 2f → 半目 1f → 開。半目の部品が無ければ閉じた目 3f
            if self.pack.blink_half and self.pack.blink_half in eye_parts:
                eyes = self.pack.blink_half if st.blink_phase in (0, 3) else self.pack.blink
            elif st.blink_phase < 3:
                eyes = self.pack.blink
        return {
            **pose,
            "eyes": eyes,
            "brows": exp.get("brows", ""),
            "cheek": exp.get("cheek", ""),
            "mouth": mouth,
            "symbol": exp.get("symbol", "なし"),
        }

    def image(self, st: State) -> Any:
        from PIL import Image

        ch = self._choice(st)
        moving = self._moving_arm(st)
        ang = round(st.arm) if moving else 0
        sc = round(st.arm_s, 2) if moving else 1.0
        key = (tuple(sorted(ch.items())), moving, ang, sc)
        im = self.cache.get(key)
        if im is not None:
            self.cache.move_to_end(key)
            return im
        w, h = self.pack.size
        im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        for item in self.pack.stack:
            if isinstance(item, Part):
                im.alpha_composite(item.image, (round(item.x), round(item.y)))
                continue
            part = self.pack.parts.get(item, {}).get(ch.get(item, ""))
            if part is None:
                continue
            if item == moving and (ang or sc != 1.0):
                px, py = _arm_pivot(part, self.pack.size)
                layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                layer.alpha_composite(part.image, (round(part.x), round(part.y)))
                # 肩を支点に回して伸び縮み（反転したキャラクターは向きも反転）
                a = math.radians(-ang * self.toward)
                ca, sa = math.cos(a) / sc, math.sin(a) / sc
                layer = layer.transform(
                    (w, h),
                    Image.Transform.AFFINE,
                    (ca, -sa, px - ca * px + sa * py, sa, ca, py - sa * px - ca * py),
                    resample=Image.Resampling.BICUBIC,
                )
                im.alpha_composite(layer)
            else:
                im.alpha_composite(part.image, (round(part.x), round(part.y)))
        self.cache[key] = im
        if len(self.cache) > 400:
            self.cache.popitem(last=False)
        return im

    def _moving_arm(self, st: State) -> str | None:
        if st.pop_arms:
            return st.pop_arms[0]
        if abs(st.arm) > 0.4:
            return self.pack.gesture_arm.get(st.pose) or self.pack.gesture_arm.get("wave")
        return None


def _place(
    canvas: Any,
    im: Any,
    st: State,
    foot: tuple[float, float],
    toward: int,
    char_h: float,
    bright: float,
) -> None:
    """足元 foot を支点に、伸び縮み・傾き・移動をして canvas に重ねる。"""
    from PIL import Image

    w, h = im.size
    sx, sy = max(0.5, st.sx), max(0.5, st.sy)
    rot = math.radians(st.rot * toward)  # 正 = 中央へ傾く
    dx = st.dx * char_h * toward
    dy = st.dy * char_h
    # 出力の座標 (X, Y) → 画像の座標 (x, y)。画像の足元 (w/2, h) を foot+(dx,dy) に置く
    fx, fy = foot[0] + dx, foot[1] + dy
    c, s = math.cos(rot), math.sin(rot)
    # 順変換: p' = R * S * (p - pivot) + F  → 逆変換を求める
    a, b = c / sx, s / sx
    d, e = -s / sy, c / sy
    ox, oy = w / 2, h
    pad = int(h * 0.12)
    X0, Y0 = int(fx - w / 2 - pad), int(fy - h - pad)
    W1, H1 = w + pad * 2, h + pad * 2
    # 出力の小さな窓 (X0, Y0) 基準で: x = a*(X+X0-fx) + b*(Y+Y0-fy) + ox
    coeffs = (
        a,
        b,
        a * (X0 - fx) + b * (Y0 - fy) + ox,
        d,
        e,
        d * (X0 - fx) + e * (Y0 - fy) + oy,
    )
    out = im.transform((W1, H1), Image.Transform.AFFINE, coeffs, resample=Image.Resampling.BICUBIC)
    if bright < 0.995:
        r, g, b_, al = out.split()
        lut = [round(i * bright) for i in range(256)]
        out = Image.merge("RGBA", (r.point(lut), g.point(lut), b_.point(lut), al))
    canvas.alpha_composite(out, (X0, Y0)) if X0 >= 0 and Y0 >= 0 else _paste_clip(
        canvas, out, X0, Y0
    )


def _paste_clip(canvas: Any, im: Any, x: int, y: int) -> None:
    cx0, cy0 = max(0, -x), max(0, -y)
    crop = im.crop((cx0, cy0, im.width, im.height))
    canvas.alpha_composite(crop, (max(0, x), max(0, y)))


def render_overlay(
    casts: list[Cast],
    total_frames: int,
    fps: float,
    size: tuple[int, int],
    out: Path,
    ffmpeg: str,
    progress: Callable[[float], None] | None = None,
) -> tuple[int, int]:
    """立ち絵の帯の動画を書き出す。戻り値は帯の大きさ (幅, 高さ)。"""
    from PIL import Image

    W, H = size
    band_r, height_r, side_x = layout(W, H)
    band = round(H * band_r) // 2 * 2
    char_h = H * height_r
    items = []
    for c in casts:
        toward = 1 if c.side == "left" else -1
        # 部品を合わせた範囲と動きの幅を見込み、画面の端で切れない位置に置く
        left_x = safe_foot_x(W, c.pack.size[0], char_h, side_x)
        foot_x = left_x if c.side == "left" else W - left_x
        foot_y = band - char_h * SHOW_RATIO + char_h
        items.append(
            (c, Composer(c.pack, toward), Animator(c.schedule, fps, c.seed), toward, foot_x, foot_y)
        )
    proc = subprocess.Popen(
        [
            ffmpeg,
            "-v",
            "error",
            "-y",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgba",
            "-s",
            f"{W}x{band}",
            "-r",
            f"{fps:.6f}",
            "-i",
            "-",
            *ENCODE,
            str(out),
        ],
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.stdin is not None
    try:
        for f in range(total_frames):
            canvas = Image.new("RGBA", (W, band), (0, 0, 0, 0))
            for c, comp, anim, toward, fx, fy in items:
                st = anim.state(f, arms_of=c.pack.pose)
                im = comp.image(st)
                # 立ち絵の大きさに合わせて縮小は済んでいる（Pack.size の高さ = char_h）
                bright = 0.66 + 0.34 * st.speaking
                _place(canvas, im, st, (fx, fy), toward, char_h, bright)
            proc.stdin.write(canvas.tobytes())
            if progress and f % 30 == 0:
                progress(f / max(1, total_frames))
        proc.stdin.close()
        err = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
        if proc.wait() != 0:
            raise RuntimeError(f"ffmpeg failed: {err[-400:]}")
    except BaseException:
        proc.kill()
        raise
    return W, band
