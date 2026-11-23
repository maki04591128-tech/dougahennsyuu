from fractions import Fraction

from video_automation.schemas.subtitle import SubtitleStyle
from video_automation.services.subtitle.formats import (
    ass_color,
    ass_time,
    build_ass,
    build_srt,
    srt_time,
)
from video_automation.services.subtitle.text import (
    default_max_chars,
    make_cues,
    split_script,
    wrap_lines,
)


def test_split_script_speakers_scenes_and_sentences():
    segs = split_script(
        "ナレーター：今日は字幕の話です。よろしくお願いします！\n"
        "続けて話します。\n\n"
        "A：「それは本当？」と聞いた。\n"
        "B: はい。"
    )
    assert [(s.scene_index, s.speaker, s.text) for s in segs] == [
        (0, "ナレーター", "今日は字幕の話です。"),
        (0, "ナレーター", "よろしくお願いします！"),
        (0, "ナレーター", "続けて話します。"),  # 話者を引き継ぐ
        (1, "A", "「それは本当？」と聞いた。"),  # 括弧内の「？」では切らない
        (1, "B", "はい。"),
    ]
    assert split_script("\n\n  \n") == []


def test_wrap_prefers_punctuation_and_particle_boundaries():
    lines = wrap_lines("動画編集の自動化システムで字幕と音声のタイミングを合わせます", 14)
    assert all(len(x) <= 14 for x in lines)
    assert lines[0].endswith("で") or lines[0].endswith("の")  # ひらがな→漢字の境界
    assert wrap_lines("今日は、とても良い天気でしたね。", 10)[0] == "今日は、"


def test_wrap_respects_line_start_kinsoku():
    lines = wrap_lines("あいうえおかきくけ。こさしすせそ", 9)
    assert all(not ln.startswith(("。", "、", "ー")) for ln in lines)
    assert "".join(lines) == "あいうえおかきくけ。こさしすせそ"


def test_make_cues_groups_lines_and_strips_period():
    cues = make_cues("あ" * 30 + "。", 10, 2, strip_period=True)
    assert cues == ["あ" * 10 + "\n" + "あ" * 10, "あ" * 10]
    assert make_cues("短い。", 10, 2, strip_period=False) == ["短い。"]
    assert default_max_chars(1920, 1080) == 22 and default_max_chars(1080, 1920) == 14


def test_time_formats_and_colors():
    assert srt_time(Fraction(3661_123, 1000)) == "01:01:01,123"
    assert ass_time(Fraction(61_255, 1000)) == "0:01:01.26"
    assert ass_color("#FF8000") == "&H000080FF"
    assert ass_color("#80000000") == "&H7F000000"


def test_build_ass_and_srt():
    fps = Fraction(30000, 1001)
    cues = [(0, 30, "A", "一行目\n二行目"), (30, 60, "B", "{危険}な文字")]
    plain = SubtitleStyle(speaker_label=False, band=False, font_size_ratio=0.055)
    ass = build_ass(cues, fps, 1080, 1920, plain, {"B": "#FFFF00"})
    assert "PlayResX: 1080\r\nPlayResY: 1920" in ass
    assert "Style: Default_S1,Meiryo,59,&H0000FFFF" in ass
    assert "Dialogue: 1,0:00:00.00,0:00:01.00,Default,A,0,0,0,,{\\fad(80,80)}一行目\\N二行目" in ass
    assert "｛危険｝" in ass and ",Default_S1,B," in ass
    # 既定: 話者名・話者色の縁取り・帯
    ass = build_ass(cues, fps, 1920, 1080, SubtitleStyle(), None, speaker_accents={"A": "#4FC3F7"})
    assert "Style: Default_S1,Meiryo,67,&H00FFFFFF,&H000000FF,&H00685221" in ass  # 縁=暗い水色
    assert "{\\fs47\\1c&HF7C34F&\\3c&H000000&}A{\\r}\\N一行目" in ass
    assert ass.count("Dialogue: 0,") == 2 and "\\p1}m 0 0 l 1920 0" in ass
    # 話者が1人なら名前は出さない
    one = build_ass(cues[:1], fps, 1920, 1080, SubtitleStyle(), speaker_accents={"A": "#4FC3F7"})
    assert "{\\r}" not in one
    srt = build_srt(cues, fps)
    assert srt.startswith("1\r\n00:00:00,000 --> 00:00:01,001\r\n一行目\r\n二行目\r\n\r\n2\r\n")
