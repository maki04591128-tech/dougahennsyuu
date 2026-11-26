import math
from fractions import Fraction

import pytest

from video_automation.schemas.animation import ClipAnimation, Easing, Keyframe
from video_automation.services.animation.motion import (
    BUILTIN_BY_NAME,
    merge_keyframes,
    resolve_preset,
    split_animation,
    track_expr,
    track_value,
)

FPS = Fraction(30)


def ff_eval(expr: str, t: float) -> float:
    """FFmpeg 式（if/lt/min/max/pow）を Python で評価する。"""
    env = {
        "if_": lambda c, a, b: a if c else b,
        "lt": lambda a, b: 1.0 if a < b else 0.0,
        "lte": lambda a, b: 1.0 if a <= b else 0.0,
        "gte": lambda a, b: 1.0 if a >= b else 0.0,
        "cos": math.cos,
        "PI": math.pi,
        "min": min,
        "max": max,
        "pow": pow,
        "T": t,
    }
    return eval(expr.replace("if(", "if_("), {"__builtins__": {}}, env)  # noqa: S307


@pytest.mark.parametrize("easing", list(Easing))
def test_expression_matches_python(easing):
    track = [(0, 0.5, easing), (15, 1.2, Easing.EASE_OUT), (45, 0.8, Easing.LINEAR)]
    e = track_expr(track, FPS, "T", 1.0)
    for t in (0.0, 0.1, 0.25, 0.5, 0.75, 1.0, 1.4, 1.5, 3.0):
        assert ff_eval(e, t) == pytest.approx(track_value(track, FPS, t, 1.0), abs=1e-4), t


def test_track_edges():
    assert track_expr([], FPS, "T", 1.0) == "1"
    assert track_expr([(10, 0.3, Easing.LINEAR)], FPS, "T", 1.0) == "0.3"
    tr = [(10, 0.0, Easing.LINEAR), (20, 1.0, Easing.LINEAR)]
    assert track_value(tr, FPS, 0.0, 5.0) == 0.0  # 最初のキーより前は最初の値
    assert track_value(tr, FPS, 10.0, 5.0) == 1.0
    assert track_value([(0, 0, Easing.BACK_OUT), (10, 1, Easing.LINEAR)], FPS, 0.25, 0) > 1.0


def test_resolve_presets_scale_to_clip():
    kfs = resolve_preset(BUILTIN_BY_NAME["ken_burns_in"], 90, FPS)
    assert [(k.frame, k.zoom) for k in kfs] == [(0, 1.0), (89, 1.15)]
    kfs = resolve_preset(BUILTIN_BY_NAME["ken_burns_in"], 90, FPS, intensity=2)
    assert kfs[-1].zoom == pytest.approx(1.3)
    kfs = resolve_preset(BUILTIN_BY_NAME["fade_out"], 60, FPS)
    assert [(k.frame, k.opacity) for k in kfs] == [(44, 1.0), (59, 0.0)]
    kfs = resolve_preset(BUILTIN_BY_NAME["pop_in"], 60, FPS, duration_sec=1.0)
    assert [k.frame for k in kfs] == [0, 30]
    kfs = resolve_preset(BUILTIN_BY_NAME["slide_in_left"], 5, FPS)  # 短いクリップでは詰める
    assert [k.frame for k in kfs] == [0, 4]
    flo = resolve_preset(BUILTIN_BY_NAME["float"], 100, FPS)
    assert [k.frame for k in flo] == [0, 30, 60, 90]
    sh = resolve_preset(BUILTIN_BY_NAME["shake"], 100, FPS)
    assert sh[-1].x == 0 and len({k.frame for k in sh}) == len(sh)
    # 値域を超えない
    kfs = resolve_preset(BUILTIN_BY_NAME["pan_left"], 60, FPS, intensity=3)
    assert all(-1 <= (k.pan_x or 0) <= 1 for k in kfs)


def test_merge_and_split():
    base = [Keyframe(frame=0, x=-1, opacity=0), Keyframe(frame=10, x=0, opacity=1)]
    add = [Keyframe(frame=0, zoom=1), Keyframe(frame=59, zoom=1.2)]
    m = merge_keyframes(base, add)
    assert [k.frame for k in m] == [0, 10, 59] and m[0].x == -1 and m[2].zoom == 1.2
    m2 = merge_keyframes(m, [Keyframe(frame=5, x=0.5)])  # x だけ置き換え
    assert [k.x for k in m2 if k.x is not None] == [0.5] and m2[0].opacity == 0
    anim = ClipAnimation(
        keyframes=[Keyframe(frame=0, x=0, easing="linear"), Keyframe(frame=60, x=1)]
    )
    a, b = split_animation(anim, 30, FPS)
    assert a is not None and b is not None
    assert [k.frame for k in a.keyframes] == [0, 29]
    assert a.keyframes[-1].x == pytest.approx(29 / 60)
    assert [(k.frame, k.x) for k in b.keyframes] == [(0, 0.5), (30, 1.0)]
    with pytest.raises(ValueError):
        ClipAnimation(keyframes=[Keyframe(frame=5), Keyframe(frame=1)])
