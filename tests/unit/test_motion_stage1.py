"""段階1（統合版 A2・B1・B2・B3・E1・E3）の値の検査。"""

from __future__ import annotations

import math
from fractions import Fraction

import pytest

from video_automation.schemas.animation import Easing
from video_automation.schemas.design import Purpose
from video_automation.services.animation.entrance import (
    element_animation,
    frames_at,
    slide_preset,
)
from video_automation.services.animation.motion import BUILTIN_BY_NAME, ease, ease_value
from video_automation.services.build import audio_bed
from video_automation.services.design.guard import DesignManifest, check, transition_rate
from video_automation.services.design.profiles import MOTION, PROFILES

FPS30 = Fraction(30)


def _eval_expr(expr: str, u: float) -> float:
    """ffmpeg の式（ease が作る範囲の関数だけ）を Python で計算する。"""
    env = {
        "IF": lambda c, a, b: a if c else b,
        "lt": lambda a, b: a < b,
        "lte": lambda a, b: a <= b,
        "gte": lambda a, b: a >= b,
        "pow": math.pow,
        "cos": math.cos,
        "PI": math.pi,
        "u": u,
    }
    return float(eval(expr.replace("if(", "IF("), {"__builtins__": {}}, env))  # noqa: S307


NEW_EASINGS = [
    Easing.CUBIC_IN,
    Easing.CUBIC_OUT,
    Easing.CUBIC_IN_OUT,
    Easing.QUART_IN,
    Easing.QUART_OUT,
    Easing.QUART_IN_OUT,
    Easing.EXPO_IN,
    Easing.EXPO_OUT,
    Easing.EXPO_IN_OUT,
    Easing.SINE_IN_OUT,
]


@pytest.mark.parametrize("e", NEW_EASINGS)
def test_easing_expr_matches_python(e: Easing) -> None:
    expr = ease("u", e)
    for i in range(11):
        u = i / 10
        assert _eval_expr(expr, u) == pytest.approx(ease_value(u, e), abs=1e-3)
    assert ease_value(0.0, e) == pytest.approx(0.0, abs=1e-3)
    assert ease_value(1.0, e) == pytest.approx(1.0, abs=1e-3)


def test_motion_spec_for_every_purpose() -> None:
    assert set(MOTION) == set(Purpose)
    for p in Purpose:
        assert PROFILES[p].motion == MOTION[p]
    calm = MOTION[Purpose.INTERVIEW]
    assert (calm.in_frames, calm.out_frames, calm.pop) == (12, 6, "pop_in_soft")
    assert not calm.far_slide and calm.strong_per_min == 0
    short = MOTION[Purpose.SHORT]
    assert short.far_slide and short.pop == "pop_in" and short.motion_blur


def test_pop_presets_and_near_slide() -> None:
    soft = BUILTIN_BY_NAME["pop_in_soft"]
    assert soft is not None
    for name in ("pop_in_soft", "pop_in", "pop_express", "slide_in_left", "slide_in_left_far"):
        assert name in BUILTIN_BY_NAME
    assert slide_preset("up", MOTION[Purpose.EXPLAINER]) == "slide_in_up"
    assert slide_preset("up", MOTION[Purpose.SHORT]) == "slide_in_up_far"


def test_element_animation_fade_in_out() -> None:
    spec = MOTION[Purpose.EXPLAINER]  # 入り 10f・出 6f
    anim = element_animation(90, spec, FPS30)
    assert anim is not None
    kf = {k.frame: k for k in anim.keyframes}
    assert kf[0].opacity == 0.0 and kf[0].easing == Easing.CUBIC_OUT
    assert kf[spec.in_frames].opacity == 1.0
    assert kf[89 - spec.out_frames].opacity == 1.0
    assert kf[89 - spec.out_frames].easing == Easing.CUBIC_IN
    assert kf[89].opacity == 0.0


def test_element_animation_scales_to_fps_and_title_exit() -> None:
    spec = MOTION[Purpose.EXPLAINER]
    assert frames_at(10, Fraction(60)) == 20
    anim = element_animation(120, spec, Fraction(60), entrance=None, title_exit=True)
    assert anim is not None
    first, last = anim.keyframes[0], anim.keyframes[-1]
    assert last.frame - first.frame == 12  # 6f（30fps）= 12f（60fps）
    assert last.scale == pytest.approx(0.96) and last.opacity == 0.0
    assert element_animation(1, spec, FPS30) is None


def test_element_animation_pop_keeps_preset_name() -> None:
    anim = element_animation(60, MOTION[Purpose.EXPLAINER], FPS30, entrance="pop_in_soft")
    assert anim is not None and anim.preset == "pop_in_soft"
    assert anim.keyframes[-1].opacity == 0.0


def test_transition_rate_warns_per_minute() -> None:
    prof = PROFILES[Purpose.INTERVIEW]  # 1 分に 1 回、強い切り替えなし
    ok = DesignManifest(transitions=[(5.0, "dissolve"), (70.0, "dissolve")], duration_sec=120)
    assert transition_rate(prof, ok) == []
    many = DesignManifest(
        transitions=[(5.0, "dissolve"), (20.0, "dissolve"), (30.0, "slide_left")],
        duration_sec=120,
    )
    codes = {v.code for v in transition_rate(prof, many)}
    assert codes == {"transition_rate", "strong_transition"}
    assert all(v.severity == "warning" for v in check(prof, many).violations if "trans" in v.code)


def test_whoosh_timing_by_kind() -> None:
    assert audio_bed.whoosh_kind("pop_in") == "zoom"
    assert audio_bed.whoosh_kind("slide_in_right") == "slide"
    assert audio_bed.whoosh_kind("dissolve") is None
    # pop_in（16f）を 100f から出す: 山は 100+8+1=109f、立ち上がり 15f → 94f から鳴らす
    assert audio_bed.whoosh_start(100, 16, "zoom", 30.0) == 94
    assert audio_bed.whoosh_start(100, 16, "slide", 30.0) == 101
    assert audio_bed.entrance_frames("pop_express", 10) == 12
    assert audio_bed.entrance_frames(None, 10) == 10
