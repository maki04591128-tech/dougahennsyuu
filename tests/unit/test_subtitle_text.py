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
    assert len(cues) == 2 and "".join(c.replace("\n", "") for c in cues) == "あ" * 30
    assert all(len(c.split("\n")) <= 2 and max(map(len, c.split("\n"))) <= 10 for c in cues)
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
    # 既定: 名前は出さず、話者の色を薄めた文字色・暗くした縁取り・話者の書体・帯
    ass = build_ass(
        cues,
        fps,
        1920,
        1080,
        SubtitleStyle(),
        None,
        speaker_accents={"A": "#4FC3F7"},
        speaker_fonts={"A": "BIZ UDPMincho", "B": "Meiryo UI"},
    )
    # 文字=薄い水色（#BCE8FC）、縁=暗い水色、書体=話者の書体
    assert "Style: Default_S1,BIZ UDPMincho,67,&H00FCE8BC,&H000000FF,&H00685221" in ass
    assert "Style: Default_S2,Meiryo UI,67,&H00FFFFFF" in ass
    assert "{\\r}" not in ass and ",Default_S1,A," in ass and ",Default_S2,B," in ass
    assert ass.count("Dialogue: 0,") == 2 and "\\p1}m 0 0 l 1920 0" in ass
    # 名前を出す設定
    lab = build_ass(
        cues, fps, 1920, 1080, SubtitleStyle(speaker_label=True), speaker_accents={"A": "#4FC3F7"}
    )
    assert "{\\fs47\\1c&HF7C34F&\\3c&H000000&}A{\\r}\\N一行目" in lab
    # 話者が1人なら書体・色は変えない
    one = build_ass(
        cues[:1], fps, 1920, 1080, SubtitleStyle(), speaker_fonts={"A": "BIZ UDPMincho"}
    )
    assert "{\\r}" not in one and "BIZ UDPMincho" not in one
    srt = build_srt(cues, fps)
    assert srt.startswith("1\r\n00:00:00,000 --> 00:00:01,001\r\n一行目\r\n二行目\r\n\r\n2\r\n")


def test_build_ass_multi_speaker_override():
    fps = Fraction(30, 1)
    cues = [(0, 30, "A", "一行目")]
    ass = build_ass(
        cues,
        fps,
        1920,
        1080,
        SubtitleStyle(speaker_label=True),
        speaker_accents={"A": "#4FC3F7"},
        multi_speaker=True,
    )
    assert "A{\\r}\\N一行目" in ass


def test_wrap_lines_keeps_english_words():
    # 実機の使い方動画で「VideoAutomatio / n」と切れた
    text = "今日は、動画を自動で作るアプリ「VideoAutomation」の使い方を紹介するのだ！"
    lines = wrap_lines(text, 22)
    assert all(len(x) <= 22 for x in lines)
    assert any("VideoAutomation" in x for x in lines)
    assert "".join(lines) == text.replace(" ", "")
    # 1 行より長い英単語は切るしかない
    assert wrap_lines("a" * 30, 10) == ["a" * 10, "a" * 10, "a" * 10]


def test_make_cues_splits_at_clause_not_greedy():
    cues = make_cues(
        "「ローカル環境を検出」を押すと、OllamaやLM Studioのモデルが見つかるわ", 22, 2, True
    )
    assert cues == ["「ローカル環境を検出」を押すと、", "OllamaやLM Studioのモデルが\n見つかるわ"]


def test_make_cues_keeps_particle_after_quote():
    cues = make_cues(
        "今日は、動画を自動で作るアプリ「VideoAutomation」の使い方を紹介するのだ！", 22, 2, True
    )
    assert all(not c.startswith("の") for c in cues)
    assert all(len(line) <= 22 for c in cues for line in c.split("\n"))
    assert (
        "".join(c.replace("\n", "") for c in cues)
        == "今日は、動画を自動で作るアプリ「VideoAutomation」の使い方を紹介するのだ！"
    )


def test_make_cues_short_sentence_is_one_cue():
    assert make_cues("これは短い文です。", 22, 2, True) == ["これは短い文です"]


def test_wrap_avoids_okurigana_compound_and_brackets():
    lines = wrap_lines("Blenderへの入れ方から書き出しまで、全部見せてもらうわよ", 18)
    assert not any(ln.endswith("書き") for ln in lines)
    lines = wrap_lines("台本作成タブに戻って「演出プラン（AI）」", 18)
    assert lines == ["台本作成タブに戻って", "「演出プラン（AI）」"]
