"""キーフレーム → FFmpeg 式の変換と、プリセットの解決（P7-01〜06）。

FFmpeg の式は filtergraph 中で '...' に包んで使う（式中のカンマを保護するため）。
"""

import math
from collections.abc import Callable
from fractions import Fraction
from typing import Any

from video_automation.schemas.animation import (
    PROPS,
    ClipAnimation,
    Easing,
    Keyframe,
    MotionPreset,
    PresetKeyframe,
    PresetKind,
    Transition,
    TransitionType,
)

BACK_C1 = 1.70158


def num(v: float) -> str:
    s = f"{v:.6f}".rstrip("0").rstrip(".")
    return s if s not in ("-0", "") else "0"


def ease(u: str, e: Easing) -> str:
    """0..1 の進行度 u（式）に緩急を付けた式。"""
    if e == Easing.LINEAR:
        return u
    if e == Easing.EASE_IN:
        return f"({u})*({u})"
    if e == Easing.EASE_OUT:
        return f"({u})*(2-({u}))"
    if e == Easing.EASE_IN_OUT:
        return f"if(lt({u},0.5),2*({u})*({u}),1-2*(1-({u}))*(1-({u})))"
    if e == Easing.BACK_OUT:
        c1, c3 = BACK_C1, BACK_C1 + 1
        return f"(1+{num(c3)}*pow(({u})-1,3)+{num(c1)}*pow(({u})-1,2))"
    return "0"  # HOLD


def ease_value(u: float, e: Easing) -> float:
    """式と同じ緩急を Python で計算する（検証・テスト用）。"""
    if e == Easing.LINEAR:
        return u
    if e == Easing.EASE_IN:
        return u * u
    if e == Easing.EASE_OUT:
        return u * (2 - u)
    if e == Easing.EASE_IN_OUT:
        return 2 * u * u if u < 0.5 else 1 - 2 * (1 - u) * (1 - u)
    if e == Easing.BACK_OUT:
        return 1 + (BACK_C1 + 1) * (u - 1) ** 3 + BACK_C1 * (u - 1) ** 2
    return 0.0


def track_expr(
    track: list[tuple[int, float, Easing]], fps: Fraction, tvar: str, default: float
) -> str:
    """プロパティのトラック → 時刻 tvar（クリップ先頭からの秒）の式。"""
    if not track:
        return num(default)
    if len(track) == 1:
        return num(track[0][1])
    pts = [(float(Fraction(f) / fps), v, e) for f, v, e in track]
    expr = num(pts[-1][1])
    for (t0, v0, e0), (t1, v1, _) in reversed(list(zip(pts, pts[1:], strict=False))):
        span = max(t1 - t0, 1e-6)
        u = f"min(max(({tvar}-{num(t0)})/{num(span)},0),1)"
        seg = (
            num(v0)
            if v1 == v0 or e0 == Easing.HOLD
            else f"({num(v0)}+({num(v1 - v0)})*{ease(u, e0)})"
        )
        expr = f"if(lt({tvar},{num(t1)}),{seg},{expr})"
    return f"if(lt({tvar},{num(pts[0][0])}),{num(pts[0][1])},{expr})"


def track_value(
    track: list[tuple[int, float, Easing]], fps: Fraction, t: float, default: float
) -> float:
    """track_expr と同じ値を Python で計算する。"""
    if not track:
        return default
    pts = [(float(Fraction(f) / fps), v, e) for f, v, e in track]
    if t < pts[0][0]:
        return pts[0][1]
    for (t0, v0, e0), (t1, v1, _) in zip(pts, pts[1:], strict=False):
        if t < t1:
            if e0 == Easing.HOLD:
                return v0
            u = min(max((t - t0) / max(t1 - t0, 1e-6), 0.0), 1.0)
            return v0 + (v1 - v0) * ease_value(u, e0)
    return pts[-1][1]


def is_static(track: list[tuple[int, float, Easing]], default: float) -> bool:
    return all(abs(v - default) < 1e-9 for _, v, _ in track)


def windows(
    track: list[tuple[int, float, Easing]],
    fps: Fraction,
    cond: Callable[[float], bool],
    end: float,
) -> list[tuple[float, float]]:
    """値が cond を満たしうる時間帯（秒）。重い処理を必要な区間だけに限るために使う。"""
    if not track:
        return []
    pts = [(float(Fraction(f) / fps), v) for f, v, _ in track]
    out: list[tuple[float, float]] = []
    if cond(pts[0][1]) and pts[0][0] > 0:
        out.append((0.0, pts[0][0]))
    for (t0, v0), (t1, v1) in zip(pts, pts[1:], strict=False):
        if cond(v0) or cond(v1) or cond(min(v0, v1)):
            out.append((t0, t1))
    if cond(pts[-1][1]):
        out.append((pts[-1][0], max(end, pts[-1][0]) + 1.0))
    merged: list[tuple[float, float]] = []
    for a, b in out:
        if merged and a <= merged[-1][1] + 1e-6:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


# ---------------- プリセット ----------------


def _kf(at: float | None = None, from_end: float | None = None, **kw: Any) -> PresetKeyframe:
    return PresetKeyframe(at=at, from_end=from_end, **kw)  # type: ignore[arg-type]


def _p(
    name: str, kind: PresetKind, label: str, desc: str, kfs: list[PresetKeyframe], **kw: Any
) -> MotionPreset:
    return MotionPreset(
        name=name, kind=kind, label=label, description=desc, keyframes=kfs, builtin=True, **kw
    )


C, E, T = PresetKind.CAMERA, PresetKind.ELEMENT, PresetKind.TRANSITION
LIN, OUT, INOUT, BACK = Easing.LINEAR, Easing.EASE_OUT, Easing.EASE_IN_OUT, Easing.BACK_OUT

BUILTIN_PRESETS: list[MotionPreset] = [
    # カメラ（クリップ全体の長さで動く）
    _p(
        "ken_burns_in",
        C,
        "ゆっくりズームイン",
        "写真に奥行きを出す定番の演出",
        [_kf(0, zoom=1.0, easing=LIN), _kf(from_end=0, zoom=1.15)],
    ),
    _p(
        "ken_burns_out",
        C,
        "ゆっくりズームアウト",
        "寄りから全体を見せる",
        [_kf(0, zoom=1.15, easing=LIN), _kf(from_end=0, zoom=1.0)],
    ),
    _p(
        "pan_left",
        C,
        "左へパン",
        "右端から左端へ視点を動かす",
        [_kf(0, zoom=1.2, pan_x=1.0, easing=INOUT), _kf(from_end=0, zoom=1.2, pan_x=-1.0)],
    ),
    _p(
        "pan_right",
        C,
        "右へパン",
        "左端から右端へ視点を動かす",
        [_kf(0, zoom=1.2, pan_x=-1.0, easing=INOUT), _kf(from_end=0, zoom=1.2, pan_x=1.0)],
    ),
    _p(
        "pan_up",
        C,
        "上へパン",
        "下から上へ（縦長の画像に）",
        [_kf(0, zoom=1.2, pan_y=1.0, easing=INOUT), _kf(from_end=0, zoom=1.2, pan_y=-1.0)],
    ),
    _p(
        "pan_down",
        C,
        "下へパン",
        "上から下へ",
        [_kf(0, zoom=1.2, pan_y=-1.0, easing=INOUT), _kf(from_end=0, zoom=1.2, pan_y=1.0)],
    ),
    _p(
        "zoom_punch",
        C,
        "ズームで強調",
        "冒頭で素早く寄る",
        [_kf(0, zoom=1.0, easing=OUT), _kf(0.3, zoom=1.12)],
    ),
    # 要素（登場・退場）
    _p("fade_in", E, "フェードイン", "", [_kf(0, opacity=0.0, easing=LIN), _kf(0.5, opacity=1.0)]),
    _p(
        "fade_out",
        E,
        "フェードアウト",
        "",
        [_kf(from_end=0.5, opacity=1.0, easing=LIN), _kf(from_end=0, opacity=0.0)],
    ),
    _p(
        "pop_in",
        E,
        "ポップイン",
        "小さい状態から弾むように現れる",
        [_kf(0, scale=0.5, opacity=0.0, easing=BACK), _kf(0.4, scale=1.0, opacity=1.0)],
    ),
    _p("slide_in_left", E, "左からスライドイン", "", [_kf(0, x=-1.0, easing=OUT), _kf(0.5, x=0.0)]),
    _p("slide_in_right", E, "右からスライドイン", "", [_kf(0, x=1.0, easing=OUT), _kf(0.5, x=0.0)]),
    _p("slide_in_up", E, "下からスライドイン", "", [_kf(0, y=1.0, easing=OUT), _kf(0.5, y=0.0)]),
    _p("slide_in_down", E, "上からスライドイン", "", [_kf(0, y=-1.0, easing=OUT), _kf(0.5, y=0.0)]),
    _p(
        "slide_out_left",
        E,
        "左へスライドアウト",
        "",
        [_kf(from_end=0.5, x=0.0, easing=Easing.EASE_IN), _kf(from_end=0, x=-1.0)],
    ),
    _p(
        "slide_out_right",
        E,
        "右へスライドアウト",
        "",
        [_kf(from_end=0.5, x=0.0, easing=Easing.EASE_IN), _kf(from_end=0, x=1.0)],
    ),
    _p(
        "zoom_enter",
        E,
        "拡大から登場",
        "",
        [_kf(0, scale=1.4, opacity=0.0, easing=OUT), _kf(0.4, scale=1.0, opacity=1.0)],
    ),
    _p(
        "spin_in",
        E,
        "回転しながら登場",
        "",
        [
            _kf(0, scale=0.3, rotation=-180.0, opacity=0.0, easing=OUT),
            _kf(0.6, scale=1.0, rotation=0.0, opacity=1.0),
        ],
    ),
    _p("float", E, "ふわふわ", "上下にゆっくり揺れる", [], generator="float"),
    _p("shake", E, "揺れ", "冒頭で小刻みに揺れる（衝撃・強調）", [], generator="shake"),
    # シーン切替（クリップの入りに付ける）
    *[
        _p(t.value, T, label, desc, [], transition=Transition(type=t, duration_frames=d))
        for t, label, desc, d in (
            (TransitionType.DISSOLVE, "ディゾルブ", "前のクリップと重ねて溶け込む", 15),
            (TransitionType.DIP_TO_BLACK, "暗転", "一度黒を経由して切り替える", 20),
            (TransitionType.SLIDE_LEFT, "スライド（左へ）", "右から入ってくる", 15),
            (TransitionType.SLIDE_RIGHT, "スライド（右へ）", "左から入ってくる", 15),
            (TransitionType.SLIDE_UP, "スライド（上へ）", "下から入ってくる", 15),
            (TransitionType.SLIDE_DOWN, "スライド（下へ）", "上から入ってくる", 15),
            (TransitionType.WIPE_LEFT, "ワイプ（左から）", "左から右へ拭うように現れる", 15),
            (TransitionType.WIPE_RIGHT, "ワイプ（右から）", "右から左へ拭うように現れる", 15),
            (TransitionType.ZOOM_IN, "ズーム", "少し大きい状態から縮みながら現れる", 12),
        )
    ],
]

TEXT_ANIMATIONS = {
    "pop": "ポップ（弾んで登場）",
    "slide": "下からスライド",
    "fade": "フェード",
    "zoom": "拡大から登場",
    "typewriter": "タイプライター（1文字ずつ）",
    "bounce": "1文字ずつ跳ねる",
    "none": "なし",
}
BUILTIN_PRESETS += [
    _p(f"text_{k}", PresetKind.TEXT, v, "Blender タイトルカードの文字アニメーション", [])
    for k, v in TEXT_ANIMATIONS.items()
]
BUILTIN_BY_NAME = {p.name: p for p in BUILTIN_PRESETS}


def _generated(p: MotionPreset, length: int, fps: Fraction, intensity: float) -> list[Keyframe]:
    f = float(fps)
    if p.generator == "float":
        period = max(2, round(f * 1.0))
        amp = 0.012 * intensity
        out, i = [], 0
        while i * period < length:
            out.append(
                Keyframe(frame=i * period, y=amp if i % 2 else -amp, easing=Easing.EASE_IN_OUT)
            )
            i += 1
        return out
    # shake: 最初の 0.4 秒を 2 フレームごとに左右へ
    steps = max(2, round(f * 0.4 / 2))
    amp = 0.01 * intensity
    out = [
        Keyframe(
            frame=min(i * 2, length - 1),
            x=(amp if i % 2 else -amp) * (1 - i / steps),
            easing=Easing.LINEAR,
        )
        for i in range(steps)
    ]
    out.append(Keyframe(frame=min(steps * 2, length - 1), x=0.0, easing=Easing.LINEAR))
    dedup: dict[int, Keyframe] = {}
    for k in out:
        dedup[k.frame] = k
    return [dedup[k] for k in sorted(dedup)]


def resolve_preset(
    p: MotionPreset,
    length_frames: int,
    fps: Fraction,
    *,
    duration_sec: float | None = None,
    intensity: float = 1.0,
) -> list[Keyframe]:
    """秒単位のプリセット → クリップ長に合わせたキーフレーム。

    duration_sec は登場・退場の長さ（プリセット内の最大の at / from_end を合わせる）。
    intensity は既定値からの変化量の倍率。"""
    if p.generator:
        return _generated(p, length_frames, fps, intensity)
    last = length_frames - 1
    times = [k.at if k.at is not None else k.from_end for k in p.keyframes]
    span = max((t for t in times if t), default=0.0)
    scale_t = (duration_sec / span) if duration_sec and span else 1.0
    out: dict[int, dict[str, object]] = {}
    for k in p.keyframes:
        sec = (k.at if k.at is not None else 0.0) * scale_t
        frame = (
            round(sec * float(fps))
            if k.at is not None
            else last - round((k.from_end or 0) * scale_t * float(fps))
        )
        frame = min(max(frame, 0), last)
        vals = {}
        for prop, v in k.values().items():
            default = PROPS[prop][0]
            lo, hi = PROPS[prop][1], PROPS[prop][2]
            vals[prop] = min(max(default + (v - default) * intensity, lo), hi)
        cur = out.setdefault(frame, {"easing": k.easing})
        cur.update(vals)
    return [Keyframe(frame=f, **v) for f, v in sorted(out.items())]  # type: ignore[arg-type]


def merge_keyframes(base: list[Keyframe], add: list[Keyframe]) -> list[Keyframe]:
    """add が持つプロパティは base から取り除いてから重ねる（他のプロパティは残す）。"""
    add_props = {p for k in add for p in k.values()}
    merged: dict[int, dict[str, object]] = {}
    for k in base:
        vals = {p: v for p, v in k.values().items() if p not in add_props}
        if vals:
            merged.setdefault(k.frame, {"easing": k.easing}).update(vals)
    for k in add:
        cur = merged.setdefault(k.frame, {"easing": k.easing})
        cur.update(k.values())
        cur["easing"] = k.easing
    return [Keyframe(frame=f, **v) for f, v in sorted(merged.items())]  # type: ignore[arg-type]


def keyframes_to_preset(kfs: list[Keyframe], fps: Fraction) -> list[PresetKeyframe]:
    f = float(fps)
    return [PresetKeyframe(at=round(k.frame / f, 4), easing=k.easing, **k.values()) for k in kfs]  # type: ignore[arg-type]


def clamp_keyframes(anim: ClipAnimation, length_frames: int) -> list[str]:
    """クリップ長を超えるキーフレームの警告。"""
    over = [k.frame for k in anim.keyframes if k.frame >= length_frames]
    return [f"keyframe at frame {f} is beyond clip length {length_frames}" for f in over]


def isfinite_all(vals: list[float]) -> bool:
    return all(math.isfinite(v) for v in vals)


def _values_at(anim: ClipAnimation, frame: int, fps: Fraction) -> dict[str, float]:
    t = float(Fraction(frame) / fps)
    return {
        prop: track_value(tr, fps, t, PROPS[prop][0]) for prop in PROPS if (tr := anim.track(prop))
    }


def split_animation(
    anim: ClipAnimation, at: int, fps: Fraction
) -> tuple[ClipAnimation | None, ClipAnimation | None]:
    """クリップを at フレームで分割したときの前半・後半のアニメーション。

    分割点の前後に補間した値のキーフレームを置いて動きを連続させる。
    入りのトランジションは前半にだけ残す。"""
    first = [k for k in anim.keyframes if k.frame < at]
    second = [
        Keyframe(frame=k.frame - at, easing=k.easing, **k.values())  # type: ignore[arg-type]
        for k in anim.keyframes
        if k.frame > at
    ]
    prev = next((k for k in reversed(anim.keyframes) if k.frame <= at), None)
    easing = prev.easing if prev else Easing.LINEAR
    if vals := _values_at(anim, at, fps):
        second.insert(0, Keyframe(frame=0, easing=easing, **vals))  # type: ignore[arg-type]
    if (
        at > 0
        and (vals := _values_at(anim, at - 1, fps))
        and (not first or first[-1].frame != at - 1)
    ):
        if any(k.frame > at - 1 for k in anim.keyframes):  # 前半の最後で値を止める
            first.append(Keyframe(frame=at - 1, easing=easing, **vals))  # type: ignore[arg-type]
    a = ClipAnimation(keyframes=first, transition_in=anim.transition_in, preset=anim.preset)
    b = ClipAnimation(keyframes=second, preset=anim.preset)
    return (None if a.is_empty() else a), (None if b.is_empty() else b)
