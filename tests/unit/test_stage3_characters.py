"""段階3 K1: キャラクターの字幕の色・書体を、登録と手元のデータから読む。"""

from fractions import Fraction

import pytest

from video_automation.schemas.subtitle import SubtitleStyle
from video_automation.services.subtitle.formats import build_ass
from video_automation.services.subtitle.subtitle_service import font_installed
from video_automation.services.voice.characters import VoiceCharacterStyle, load_data_file


def test_character_design_fields(tmp_path):
    f = tmp_path / "voicevox_characters.json"
    f.write_text(
        '[{"speaker": "ずんだもん", "color": "#33A65E", "fill": "#D7F9DE", "outline": "#024A22",'
        ' "alt_outline": "#01270F", "frame": "#35A860", "subtitle_font": "M PLUS Rounded 1c",'
        ' "subtitle_weight": "Bold", "entrance": "ポップ小"}]',
        encoding="utf-8",
    )
    st = load_data_file(f)["ずんだもん"]
    assert st.color == "#33A65E" and st.alt_outline == "#01270F" and st.entrance == "ポップ小"
    with pytest.raises(ValueError):
        VoiceCharacterStyle(speaker="x", color="green")


def test_speaker_design_overrides_derived_colors():
    cues = [(0, 30, "A", "あ"), (30, 60, "B", "い")]
    acc = {"A": "#33A65E", "B": "#386CB0"}
    design = {"A": {"fill": "#112233", "outline": "#445566"}}
    ass = build_ass(cues, Fraction(30), 1920, 1080, SubtitleStyle(), speaker_accents=acc,
                    speaker_design=design)  # fmt: skip
    s1 = next(x for x in ass.splitlines() if x.startswith("Style: Default_S1"))
    assert "&H00332211" in s1 and "&H00665544" in s1  # 手元のデータの色（BGR）


def test_font_installed_is_false_for_unknown_font():
    assert not font_installed("No Such Font Family 123")


def test_character_entrances_only_where_allowed():
    from video_automation.schemas.design import Purpose
    from video_automation.services.design.profiles import PROFILES
    from video_automation.services.subtitle.subtitle_service import style_from_profile

    cues = [(0, 30, "A", "あ")]
    ex = style_from_profile(PROFILES[Purpose.EXPLAINER])
    ass = build_ass(cues, Fraction(30), 1920, 1080, ex, speaker_entrance={"A": "ポップ小"})
    d = next(x for x in ass.splitlines() if x.startswith("Dialogue: 1"))
    assert "\\fscx92" in d and "\\fad(133,100)" in d
    iv = style_from_profile(PROFILES[Purpose.INTERVIEW])  # 入りを使わない用途は 3f のフェード
    ass = build_ass(cues, Fraction(30), 1920, 1080, iv, speaker_entrance={"A": "ポップ小"})
    d = next(x for x in ass.splitlines() if x.startswith("Dialogue: 1"))
    assert "\\fscx92" not in d and "\\fad(100,100)" in d
    ass = build_ass(cues, Fraction(30), 1920, 1080, ex, speaker_entrance={"A": "上昇フェード"})
    assert "\\move(960," in ass


def test_font_aliases_and_extra_dirs(tmp_path, monkeypatch):
    from video_automation.services.subtitle import subtitle_service as ss

    d = tmp_path / "fonts"
    d.mkdir()
    (d / "MPLUSRounded1c-Bold.ttf").write_bytes(b"x")
    monkeypatch.setattr(ss, "EXTRA_FONT_DIRS", [d])
    ss._installed_font_keys.cache_clear()
    try:
        assert ss.font_installed("M PLUS Rounded 1c")
        assert ss.FONT_ALIASES["M PLUS Rounded 1c"] == "Rounded Mplus 1c"
    finally:
        ss._installed_font_keys.cache_clear()


def test_named_weight_fonts_are_not_faux_bold():
    ass = build_ass(
        [(0, 30, "A", "あ"), (30, 60, "B", "い")],
        Fraction(30),
        1920,
        1080,
        SubtitleStyle(bold=True),
        speaker_fonts={"A": "Zen Kaku Gothic New Medium", "B": "Noto Sans CJK JP"},
    )
    styles = {x.split(",")[1]: x.split(",") for x in ass.splitlines() if x.startswith("Style:")}
    assert styles["Zen Kaku Gothic New Medium"][7] == "0"  # 太さを名前で選んだら太字を重ねない
    assert styles["Noto Sans CJK JP"][7] == "-1"  # Bold は書体名＋太字の指定
