"""段階2（統合版 A1・C2・S2・S3・S8）の値の検査。"""

from __future__ import annotations

import pytest

from video_automation.schemas.animation import ClipAnimation, Easing, Keyframe, cubic_bezier
from video_automation.schemas.design import Purpose
from video_automation.schemas.voice import AudioQuery
from video_automation.services.build import audio_bed
from video_automation.services.characters import motion as cmotion
from video_automation.services.design.guard import color_vision, cvd_delta_e, palette_contrast
from video_automation.services.design.profiles import PROFILES
from video_automation.services.subtitle.formats import text_width, tracking

# ---------------------------------------------------------------- A1 cubic-bezier


def test_cubic_bezier_known_points():
    linear = (0.0, 0.0, 1.0, 1.0)
    for u in (0.0, 0.25, 0.5, 1.0):
        assert cubic_bezier(u, linear) == pytest.approx(u, abs=1e-6)
    ease = (0.25, 0.1, 0.25, 1.0)  # CSS の ease
    assert cubic_bezier(0.5, ease) == pytest.approx(0.8024, abs=1e-3)
    md3 = (0.2, 0.0, 0.0, 1.0)  # Material Design 3 の emphasized decelerate に近い形
    assert 0.0 < cubic_bezier(0.1, md3) < cubic_bezier(0.3, md3) < 1.0


def test_bezier_track_is_baked_per_frame():
    anim = ClipAnimation(
        keyframes=[
            Keyframe(frame=0, opacity=0.0, easing=Easing.BEZIER, bezier=(0.25, 0.1, 0.25, 1.0)),
            Keyframe(frame=10, opacity=1.0),
        ]
    )
    tr = anim.track("opacity")
    assert [f for f, _, _ in tr] == list(range(11))
    assert all(e == Easing.LINEAR for _, _, e in tr[:-1])
    assert tr[5][1] == pytest.approx(0.8024, abs=1e-3)


def test_bezier_needs_points():
    with pytest.raises(ValueError):
        Keyframe(frame=0, opacity=0.0, easing=Easing.BEZIER)
    with pytest.raises(ValueError):
        Keyframe(frame=0, opacity=0.0, easing=Easing.BEZIER, bezier=(1.5, 0.0, 0.2, 1.0))


# ---------------------------------------------------------------- C2 字間 −2%


def test_tracking_is_minus_two_percent():
    assert tracking(60) == -1
    assert tracking(100) == -2
    assert text_width("あいう", 100) == pytest.approx(300 * 0.98)


# ---------------------------------------------------------------- S8 色覚


def test_all_palettes_pass_contrast_and_color_vision():
    for p in Purpose:
        assert palette_contrast(PROFILES[p]) == [], p
        assert color_vision(PROFILES[p]) == [], p


def test_color_vision_detects_red_green_confusion():
    # 料理の型の元の配色（緑と橙）は P型で見分けにくかった（統合版 S8 の表）
    assert cvd_delta_e("#8CC084", "#E9A46A", "P型") < 15
    assert cvd_delta_e("#C3FABB", "#E9A46A", "P型") > 15
    assert cvd_delta_e("#1F77B4", "#FF7F0E", "D型") > 15


# ---------------------------------------------------------------- S3 BGM のダッキング


def test_raise_spans_intro_gaps_and_end():
    spans = audio_bed.raise_spans([(2.0, 8.0), (8.5, 12.0), (14.0, 18.0)], 20.0)
    assert spans == [(0.0, 2.0), (12.0, 14.0), (18.0, 20.0)]  # 0.5 秒の間は上げない


def test_duck_levels():
    low = 10 ** ((audio_bed.BGM_DUCK_PEAK_DB - audio_bed.BGM_PEAK_DB) / 20)
    expr = audio_bed.duck_expr([(12.0, 14.0)])
    assert expr.startswith(f"{low:.4f}+")
    # 声の区間は声のピーク（−3 dBFS）より 20 dB 下、間は 7 dB 上
    assert audio_bed.BGM_DUCK_PEAK_DB == -23.0 and audio_bed.BGM_RAISE_DB == 7.0


# ---------------------------------------------------------------- S2 口の形


def _query(moras: list[tuple[str | None, float, str, float]]) -> AudioQuery:
    return AudioQuery.model_validate(
        {
            "accent_phrases": [
                {
                    "moras": [
                        {
                            "text": "x",
                            "consonant": c,
                            "consonant_length": cl,
                            "vowel": v,
                            "vowel_length": vl,
                            "pitch": 5.0,
                        }
                        for c, cl, v, vl in moras
                    ],
                    "accent": 1,
                }
            ],
            "speedScale": 1.0,
            "pitchScale": 0.0,
            "intonationScale": 1.0,
            "volumeScale": 1.0,
            "prePhonemeLength": 0.1,
            "postPhonemeLength": 0.1,
            "outputSamplingRate": 24000,
            "outputStereo": False,
        }
    )


def test_viseme_levels_follow_vowels():
    # 「か（大）・み（m で閉じてから中）・ん（閉じる）・お（丸）」
    q = _query(
        [
            ("k", 0.05, "a", 0.2),
            ("m", 0.1, "i", 0.2),
            (None, 0.0, "N", 0.1),
            (None, 0.0, "o", 0.2),
        ]
    )
    lv = cmotion.viseme_levels(q, 30.0, 30)
    assert lv[0] == 0  # 前の無音
    assert lv[round(0.2 * 30) - 1] == 2  # あ（1f 早い）
    assert 0 in lv[round(0.36 * 30) - 1 : round(0.45 * 30) - 1]  # m の子音で閉じる
    assert lv[round(0.55 * 30) - 1] == 1  # い
    assert lv[round(0.8 * 30) - 1] == 2  # お


def test_viseme_short_shapes_are_held():
    q = _query([(None, 0.0, "a", 0.3), ("p", 0.03, "a", 0.3)])  # p の閉じは 1f 未満
    lv = cmotion.viseme_levels(q, 30.0, 25)
    runs = []
    for v in lv:
        if not runs or runs[-1][0] != v:
            runs.append([v, 0])
        runs[-1][1] += 1
    assert all(n >= 2 for _, n in runs[1:-1])
