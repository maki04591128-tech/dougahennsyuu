"""演出の要素（カード・図・画像・テロップ・題名）の入りと出のキーフレーム（統合版 A2・B1・B2・B4）。

入りはプリセット（pop の 3 種・スライド）か、不透明度だけのフェード
（用途の入りの長さ・easeOutCubic）。
出は不透明度 1→0 を用途の出の長さ・easeInCubic で付ける。値は 30fps のフレーム数で書いてあり、
fps が違うときは秒に直して合わせる。
"""

from __future__ import annotations

from fractions import Fraction

from video_automation.schemas.animation import ClipAnimation, Easing, Keyframe
from video_automation.schemas.design import MotionSpec
from video_automation.services.animation.motion import BUILTIN_BY_NAME, resolve_preset

BASE_FPS = 30
# 統合版 B4: 題名の出（6f、easeInCubic、scale 1→0.96・不透明度 1→0）
TITLE_OUT_FRAMES = 6
TITLE_OUT_SCALE = 0.96
# 統合版 D1: カードの新しい項目は、Y +16px から不透明度 0→1、11f easeOutCubic
ITEM_IN_FRAMES = 11
ITEM_IN_PX = 16


def frames_at(n30: int, fps: Fraction) -> int:
    """30fps のフレーム数 → その fps のフレーム数。"""
    return max(1, round(n30 * float(fps) / BASE_FPS))


def slide_preset(direction: str, spec: MotionSpec) -> str:
    """スライドの入り。画面の外から入るのは、遠くからのスライドを使う用途だけ（統合版 B1）。"""
    return f"slide_in_{direction}{'_far' if spec.far_slide else ''}"


def element_animation(
    length: int,
    spec: MotionSpec,
    fps: Fraction,
    *,
    entrance: str | None = "fade",
    exit_: bool = True,
    title_exit: bool = False,
    item_dy: float = 0.0,
) -> ClipAnimation | None:
    """長さ length フレームのクリップの、入りと出のアニメーション。

    entrance: "fade"（不透明度だけ）、"item"（カードの新しい項目。D1。item_dy は画面の高さに
    対する下へのずれ）、プリセット名（"pop_in_soft" など）、None（入りなし）。
    title_exit: 題名の出（B4）。拡大率も 1→0.96 にし、長さは 6f。"""
    if length < 2:
        return None
    last = length - 1
    keys: dict[int, dict[str, object]] = {}

    def put(frame: int, easing: Easing, **vals: float) -> None:
        cur = keys.setdefault(frame, {"easing": easing})
        cur.update(vals)
        cur["easing"] = easing

    entrance_end = 0
    name = None
    if entrance == "fade":
        n = min(frames_at(spec.in_frames, fps), last // 2) if spec.in_frames else 0
        if n > 0:
            put(0, Easing.CUBIC_OUT, opacity=0.0)
            put(n, Easing.LINEAR, opacity=1.0)
            entrance_end = n
    elif entrance == "item":
        n = min(frames_at(ITEM_IN_FRAMES, fps), last // 2)
        if n > 0:
            put(0, Easing.CUBIC_OUT, opacity=0.0, y=item_dy)
            put(n, Easing.LINEAR, opacity=1.0, y=0.0)
            entrance_end = n
    elif entrance is not None and entrance in BUILTIN_BY_NAME:
        name = entrance
        for k in resolve_preset(BUILTIN_BY_NAME[entrance], length, fps):
            put(k.frame, k.easing, **k.values())
            entrance_end = max(entrance_end, k.frame)
    if exit_:
        want = TITLE_OUT_FRAMES if title_exit else spec.out_frames
        n = min(frames_at(want, fps), last - entrance_end - 1) if want else 0
        if n > 0:
            start = last - n
            if title_exit:
                put(start, Easing.CUBIC_IN, opacity=1.0, scale=1.0)
                put(last, Easing.LINEAR, opacity=0.0, scale=TITLE_OUT_SCALE)
            else:
                put(start, Easing.CUBIC_IN, opacity=1.0)
                put(last, Easing.LINEAR, opacity=0.0)
    if not keys:
        return None
    kfs = [Keyframe(frame=f, **v) for f, v in sorted(keys.items())]  # type: ignore[arg-type]
    return ClipAnimation(keyframes=kfs, preset=name)
