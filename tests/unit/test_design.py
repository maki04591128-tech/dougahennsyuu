"""デザインの型（用途ごとの部品・配色）・用途の判定・書き出し前の検査。"""

from fractions import Fraction

import pytest

from video_automation.schemas.design import ANY, DENY, ClassifyRequest, Component, Purpose
from video_automation.schemas.direction import DirectionPlan, Shot, ShotKind
from video_automation.schemas.subtitle import SubtitleStyle
from video_automation.services.design.classify import classify
from video_automation.services.design.guard import (
    DesignManifest,
    Placement,
    SubtitleUse,
    check,
    font_key,
    hue_groups,
)
from video_automation.services.design.profiles import PROFILES, summarize
from video_automation.services.direction.service import fit_to_profile
from video_automation.services.subtitle.formats import build_ass
from video_automation.services.subtitle.subtitle_service import style_from_profile

C = Component


def test_every_purpose_has_a_consistent_profile():
    assert set(PROFILES) == set(Purpose)
    for p in PROFILES.values():
        pal = p.palette
        # 配色そのものが型の色相数の上限に収まる（話者・図表の色も明暗違いで作る）
        colors = [pal.main, pal.accent, *pal.speakers, *pal.chart]
        assert len(hue_groups(colors)) <= p.max_hues, p.purpose
        assert all(v == ANY or v > 0 for v in p.components.values())
        s = summarize(p)
        assert set(s.allowed) | set(s.limited) | set(s.denied) == set(Component)
        lo, hi = p.subtitle.size_range
        assert lo <= p.subtitle.size_ratio <= hi


def test_purposes_keep_their_identity():
    calm, info, mv = PROFILES[Purpose.SUBTITLE], PROFILES[Purpose.EXPLAINER], PROFILES[Purpose.MV]
    # 字幕動画にはモーショングラフィックスの演出を使わない
    for c in (C.TRANSITION_SHAPE, C.GLITCH, C.SHAKE_PUNCH, C.FLASH, C.KINETIC_TEXT, C.CARD):
        assert calm.allowance(c) == DENY
    # MV には説明用のカード・字幕を使わない
    for c in (C.CARD, C.CHART, C.SUBTITLE, C.KEYWORD):
        assert mv.allowance(c) == DENY
    # 解説は図とカードが主役で、刺激の強い演出は使わない
    assert info.allowance(C.CARD) == ANY and info.allowance(C.GLITCH) == DENY
    assert info.allowance(C.SHAKE_PUNCH) == 3
    assert PROFILES[Purpose.SHORT].orientation == "vertical"
    assert PROFILES[Purpose.NEWS].allowance(C.CHARACTER) == DENY
    assert PROFILES[Purpose.KIDS].allowance(C.FLASH) == DENY


@pytest.mark.parametrize(
    ("text", "purpose"),
    [
        ("ウルツァイト窒化ホウ素の解説動画を作って", Purpose.EXPLAINER),
        ("この曲のMVを作ってほしい", Purpose.MV),
        ("インタビュー映像に字幕を付けて", Purpose.INTERVIEW),
        ("TikTok向けの縦動画にして", Purpose.SHORT),
        ("歌詞動画を作成", Purpose.LYRIC),
        ("今日のニュースをまとめた動画", Purpose.NEWS),
        ("新商品のレビュー動画", Purpose.PRODUCT),
        ("カレーのレシピ動画", Purpose.RECIPE),
        ("採用向けの会社紹介ムービー", Purpose.CORPORATE),
        ("Excelの使い方を説明する動画", Purpose.TUTORIAL),
    ],
)
def test_classify_explicit_words(text, purpose):
    r = classify(ClassifyRequest(text=text))
    assert r.purpose == purpose, r


def test_classify_mixed_instruction_borrows_only_explicit_parts():
    r = classify(ClassifyRequest(text="解説動画だけどMVっぽくしてほしい"))
    assert r.purpose == Purpose.EXPLAINER
    assert r.mixed_from == [Purpose.MV]
    assert C.GLITCH in r.suggested_extras and C.FLASH in r.suggested_extras
    assert C.LYRIC_TEXT not in r.suggested_extras  # 見た目・動きの部品だけ借りる
    assert "extras" in r.note


def test_classify_unclear_asks_and_falls_back_to_calm():
    r = classify(ClassifyRequest(text="いい感じの動画を作って"))
    assert r.needs_confirmation and r.confidence == 0
    assert r.purpose == Purpose.SUBTITLE
    r = classify(ClassifyRequest(text="いい感じの動画を作って", has_script=True))
    assert r.purpose == Purpose.EXPLAINER and r.needs_confirmation
    # 縦長の素材だけでも、ヒントとして使う
    assert classify(ClassifyRequest(text="動画にして", vertical=True)).purpose == Purpose.SHORT


def test_guard_blocks_parts_from_other_purposes():
    calm = PROFILES[Purpose.SUBTITLE]
    m = DesignManifest()
    m.add(C.SUBTITLE, 10)
    m.add(C.SHAKE_PUNCH, 2)
    m.add(C.TRANSITION_SHAPE, 1)
    r = check(calm, m)
    assert not r.ok
    codes = {(v.code, v.component) for v in r.violations}
    assert ("denied_component", C.SHAKE_PUNCH) in codes
    assert ("denied_component", C.TRANSITION_SHAPE) in codes
    # 混ぜる指示が明示されたときだけ例外にできる
    assert check(calm, m, [C.SHAKE_PUNCH, C.TRANSITION_SHAPE]).ok


def test_guard_limits_colors_fonts_subtitles_and_safe_area():
    info = PROFILES[Purpose.EXPLAINER]
    m = DesignManifest()
    m.add(C.TRANSITION_SHAPE, 9)  # 解説は 8 回まで
    m.colors = ["#4CB3E6", "#F2B544", "#E53935", "#43A047", "#8E24AA"]
    m.fonts = {"meiryob", "Meiryo", "Yu Gothic", "Impact"}
    m.add(C.SUBTITLE, 2)
    m.subtitle = SubtitleUse(
        size_ratio=0.09,
        band="full",
        font="Meiryo",
        lines=[("あ" * 30, 2.0), ("短い", 2.0)],
    )
    m.placements = [Placement(C.KEYWORD, 0.02, 0.02, 0.3, 0.1)]
    r = check(info, m)
    codes = {v.code for v in r.violations}
    assert {"over_limit", "hue_count", "font_count", "subtitle_size", "subtitle_band"} <= codes
    assert {"subtitle_line_length", "subtitle_speed", "safe_area"} <= codes
    assert not r.ok  # over_limit と subtitle_size は error
    assert font_key("meiryob") == font_key("Meiryo")


def test_guard_orientation():
    m = DesignManifest(vertical=False)
    r = check(PROFILES[Purpose.SHORT], m)
    assert any(v.code == "orientation" and v.severity == "error" for v in r.violations)


def test_fit_plan_to_profile_drops_denied_shots_and_limits():
    plan = DirectionPlan(
        title="題名",
        shots=[
            Shot(kind=ShotKind.CARD, from_line=1, heading="カード"),
            Shot(kind=ShotKind.CHART, from_line=2, labels=["a"], values=[1]),
            Shot(kind=ShotKind.KEYWORD, from_line=3, text="語"),
        ],
    )
    out, notes = fit_to_profile(plan, PROFILES[Purpose.SUBTITLE])
    assert out.shots == [] and out.title == "題名"  # 字幕動画は題名だけ（1 回）
    assert len(notes) == 3
    out, notes = fit_to_profile(plan, PROFILES[Purpose.MV])
    assert out.shots == [] and out.title == "題名"
    out, notes = fit_to_profile(plan, PROFILES[Purpose.EXPLAINER])
    assert len(out.shots) == 3 and not notes
    # 混ぜる指示の例外（extras）は残す
    out, _ = fit_to_profile(plan, PROFILES[Purpose.SUBTITLE], [C.CARD])
    assert [s.kind for s in out.shots] == [ShotKind.CARD]
    assert fit_to_profile(plan, None) == (plan, [])


def test_subtitle_style_from_profile_and_fitted_band():
    st = style_from_profile(PROFILES[Purpose.EXPLAINER])
    assert st.band and st.band_mode == "fitted" and st.font_size_ratio == 0.054
    short = style_from_profile(PROFILES[Purpose.SHORT])
    assert not short.band and short.outline > st.outline  # 帯の無い型は縁取りを太く
    ass = build_ass(
        [(0, 30, "A", "短い字幕"), (30, 60, "B", "二人目の字幕")],
        Fraction(30),
        1920,
        1080,
        st,
        speaker_accents={"A": "#4CB3E6", "B": "#F2B544"},
    )
    band = [ln for ln in ass.splitlines() if "\\p1" in ln][0]
    # 画面幅いっぱい（0〜1920）ではなく、文字の幅だけの帯
    assert "l 1920 0" not in band and "m 0 0" not in band
    full = build_ass(
        [(0, 30, "A", "短い字幕")], Fraction(30), 1920, 1080, SubtitleStyle(band_mode="full")
    )
    assert "l 1920 0" in full


def test_chart_unit_symbol():
    from video_automation.services.direction.plan import unit_symbol

    assert unit_symbol("ギガパスカル") == "GPa"
    assert unit_symbol("パーセント") == "%"
    assert unit_symbol("GPa") == "GPa"


def test_guard_warns_long_background_only_time():
    m = DesignManifest()
    m.empty_spans = [(0.0, 3.0), (40.0, 55.0)]
    r = check(PROFILES[Purpose.EXPLAINER], m)  # 情報系は 10 秒まで
    v = [x for x in r.violations if x.code == "empty_screen"]
    assert len(v) == 1 and v[0].severity == "warning" and "40" in v[0].message
    # 字幕動画は背景だけでもよい
    calm = check(PROFILES[Purpose.SUBTITLE], m)
    assert not [x for x in calm.violations if x.code == "empty_screen"]


def test_card_font_follows_profile(tmp_path, monkeypatch):
    from video_automation.services.rendering import title_card as tc

    ud = tmp_path / "BIZ-UDGothicB.ttc"
    ud.write_bytes(b"x")
    monkeypatch.setitem(tc.FONT_FILES, "bizudgothic", [str(ud)])
    svc = object.__new__(tc.TitleCardService)
    svc.settings = type("S", (), {"title_font_path": None})()
    assert svc.font_path("BIZ UDPGothic") == str(ud)
    assert font_key("BIZ UDPGothic") == font_key("BIZ-UDGothicB")
    svc.settings.title_font_path = "/x/custom.ttf"  # 設定の指定が優先
    assert svc.font_path("BIZ UDPGothic") == "/x/custom.ttf"


def test_light_profiles_get_light_outline_and_denied_speaker_label_is_off():
    from video_automation.services.design.guard import is_dark

    lecture = style_from_profile(PROFILES[Purpose.LECTURE])
    assert is_dark(lecture.primary_color)
    assert not is_dark(lecture.outline_color)  # 明るい背景の型は明るい縁取り
    assert lecture.speaker_outline is False
    explainer = style_from_profile(PROFILES[Purpose.EXPLAINER])
    assert explainer.outline_color == "#000000" and explainer.speaker_label
    kids = style_from_profile(PROFILES[Purpose.KIDS])
    assert kids.speaker_label is False  # 子ども向けは話者名を使わない


def test_voice_synced_subtitles_are_allowed_up_to_speech_speed():
    info = PROFILES[Purpose.EXPLAINER]  # 読める速さ 1 秒 7 字
    m = DesignManifest()
    m.add(C.SUBTITLE, 3)
    m.subtitle = SubtitleUse(
        size_ratio=info.subtitle.size_ratio,
        band=info.subtitle.band,
        font=info.typography.body_font,
        lines=[("あ" * 18, 2.0), ("い" * 16, 2.0), ("う" * 30, 2.0)],  # 9, 8, 15 字/秒
        voice_rates=[8.0, 8.0, 8.0],
    )
    speed = [v for v in check(info, m).violations if v.code == "subtitle_speed"]
    # 音声（8 字/秒）の 1.25 倍=10 字/秒 までは許し、15 字/秒だけ警告
    assert len(speed) == 1 and "1 件" in speed[0].message and "音声" in speed[0].message
    # 音声に合わせていない字幕は従来どおり「読める速さ」で判定
    m.subtitle.voice_rates = []
    speed = [v for v in check(info, m).violations if v.code == "subtitle_speed"]
    assert len(speed) == 1 and "3 件" in speed[0].message


def test_blender_colors_are_linear():
    from video_automation.services.rendering.title_card import _rgba

    assert _rgba("#FFFFFF") == [1.0, 1.0, 1.0, 1.0]
    r, g, b, _ = _rgba("#808080")
    assert abs(r - 0.2159) < 1e-3  # sRGB 0.5 → 線形 0.216（暗い色を浮かせない）
