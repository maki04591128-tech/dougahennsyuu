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


def test_filter_script_flag_by_ffmpeg_version():
    """FFmpeg 7 以降は -filter_complex_script が無い（実機の PC で BGM が作れなかった）。"""
    import asyncio

    from video_automation.integrations.ffmpeg.runner import FFmpeg

    f = FFmpeg()
    for v, flag in [
        ("6.1.1-3ubuntu5", "-filter_complex_script"),
        ("7.1.1-full_build-www.gyan.dev", "-/filter_complex"),
        ("2025-01-02-git-0123abcd", "-/filter_complex"),
    ]:
        f._version = v
        assert asyncio.run(f.filter_script_args("g.txt")) == [flag, "g.txt"]


# ---------------------------------------------------------------- G2・G3・M4・Q2 動きの検査


def _clip(track, start, end, keys=(), preset=None):
    from video_automation.services.design.guard import ClipMotion

    return ClipMotion(track=track, start=start, end=end, keys=list(keys), preset=preset)


def _codes(purpose, clips, duration=20.0, transitions=()):
    from video_automation.services.design.guard import DesignManifest, motion_checks

    m = DesignManifest(clips=clips, duration_sec=duration, transitions=list(transitions))
    return {v.code: v.severity for v in motion_checks(PROFILES[purpose], m)}


def test_motion_amount_only_for_calm_purposes():
    far = _clip("a", 1, 3, [(0.0, {"x": -1.0}), (0.5, {"x": 0.0})])  # 画面の幅 100%
    near = _clip("a", 1, 3, [(0.0, {"y": 0.03}), (0.5, {"y": 0.0})])  # 短辺の 3%
    assert "motion_amount" in _codes(Purpose.EXPLAINER, [far], 4)
    assert "motion_amount" not in _codes(Purpose.EXPLAINER, [near], 4)
    assert "motion_amount" not in _codes(Purpose.SHORT, [far], 4)


def test_repeated_ken_burns():
    a = _clip("img", 0, 4, preset="ken_burns_in")
    b = _clip("img", 4, 8, preset="ken_burns_in")
    c = _clip("img", 4, 8, preset="ken_burns_out")
    assert "repeated_motion" in _codes(Purpose.EXPLAINER, [a, b], 8)
    assert "repeated_motion" not in _codes(Purpose.EXPLAINER, [a, c], 8)


def test_concurrent_motion_is_error_except_expressive():
    fade = [(0.0, {"opacity": 0.0}), (0.5, {"opacity": 1.0})]
    three = [_clip(t, 1, 3, fade) for t in "abc"]
    assert _codes(Purpose.EXPLAINER, three, 4)["concurrent_motion"] == "error"
    assert _codes(Purpose.MV, three, 4)["concurrent_motion"] == "warning"
    assert "concurrent_motion" not in _codes(Purpose.EXPLAINER, three[:2], 4)


def test_change_interval():
    clips = [_clip("a", t, t + 2) for t in (0, 5, 10, 15)]
    assert "change_interval" not in _codes(Purpose.EXPLAINER, clips, 20)  # 最大 5 秒
    sparse = [_clip("a", 0, 2), _clip("a", 12, 14)]
    assert "change_interval" in _codes(Purpose.EXPLAINER, sparse, 20)  # 12 秒空く
    assert "change_interval" not in _codes(Purpose.INTERVIEW, sparse, 20)  # 検査しない用途


# ---------------------------------------------------------------- C1・K2・T12・T13


def test_double_outline_layers():
    from fractions import Fraction

    from video_automation.services.subtitle.formats import build_ass
    from video_automation.services.subtitle.subtitle_service import style_from_profile

    st = style_from_profile(PROFILES[Purpose.GAME])
    assert st.double_outline
    ass = build_ass([(0, 30, "", "テスト")], Fraction(30), 1920, 1080, st)
    d = [x for x in ass.splitlines() if x.startswith("Dialogue")]
    assert len(d) == 2 and "\\1a&HFF&" in d[0] and "\\bord" in d[1]
    # 上の層の縁は暗い色、下の層（外側）の縁は明るい色（文字がつぶれないように）
    assert "\\3c&H000000&" in d[1] and "\\3c&H000000&" not in d[0]
    assert not style_from_profile(PROFILES[Purpose.EXPLAINER]).double_outline
    assert style_from_profile(PROFILES[Purpose.KIDS]).double_outline_outer == "#FFF6E5"


def test_character_colors_from_oklch():
    from video_automation.services.design.oklch import ALT_OUTLINE, FRAME, OUTLINE, TEXT, derive

    # 統合版 3節のずんだもん（#33A65E）: 文字 #D7F9DE・縁 #024A22・代替 #01270F・枠 #35A860
    got = [derive("#33A65E", r) for r in (TEXT, OUTLINE, ALT_OUTLINE, FRAME)]
    want = ["#D7F9DE", "#024A22", "#01270F", "#35A860"]
    for g, w in zip(got, want, strict=True):
        assert all(abs(int(g[i : i + 2], 16) - int(w[i : i + 2], 16)) <= 2 for i in (1, 3, 5))


def test_same_color_speakers_get_alt_outline_and_spacing():
    from fractions import Fraction

    from video_automation.services.subtitle.formats import build_ass
    from video_automation.services.subtitle.subtitle_service import style_from_profile

    st = style_from_profile(PROFILES[Purpose.EXPLAINER])
    ass = build_ass(
        [(0, 30, "ずんだもん", "あ"), (30, 60, "東北ずん子", "い")],
        Fraction(30),
        1920,
        1080,
        st,
        speaker_accents={"ずんだもん": "#33A65E", "東北ずん子": "#33A65E"},
        speaker_fonts={"ずんだもん": "M PLUS Rounded 1c", "東北ずん子": "M PLUS Rounded 1c"},
    )
    styles = {x.split(",")[0]: x.split(",") for x in ass.splitlines() if x.startswith("Style:")}
    s1, s2 = styles["Style: Default_S1"], styles["Style: Default_S2"]
    assert s1[5] != s2[5]  # 後から出た話者は代替の縁
    assert int(s2[13]) > int(s1[13])  # 同じ書体なら字間 +4%


def test_line_break_rules_and_digits():
    from video_automation.services.subtitle.text import japanese_digits, wrap_lines

    assert japanese_digits("第1章と第12章、約1/3.7") == "第１章と第12章、約1/3.7"
    for ln in wrap_lines("土星は直径約12万kmの巨大惑星なのよ、すごいわね", 10)[1:]:
        assert ln[0] not in "万km、ー々"
    lines = wrap_lines("それは……すごいことになったのだよ本当に", 6)
    assert all(
        not (a.endswith("…") and b.startswith("…")) for a, b in zip(lines, lines[1:], strict=False)
    )
    assert all(not ln.endswith("「") for ln in wrap_lines("彼は「すごい」と言ったのだよ本当に", 4))
