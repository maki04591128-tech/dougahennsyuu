from video_automation.services.script.persona import check_line, parse_style
from video_automation.services.script.studio import (
    _drop_repeated_openers,
    _simple_fix,
    _strip_label,
)


def test_strip_label():
    names = {"ずんだもん": None, "四国めたん": None}
    assert _strip_label("L9: ずんだもん: 一日に水を飲むのだ", names) == "一日に水を飲むのだ"
    assert _strip_label("[L3] 四国めたん：休むわ", names) == "休むわ"
    assert _strip_label("LEDに替えるのだ", names) == "LEDに替えるのだ"


def test_simple_fix_masu_wa():
    assert _simple_fix("その危険性はよく知っていますわ。") == "その危険性はよく知っているわ。"


def test_drop_repeated_openers():
    meta = parse_style("四国めたん", "一人称は「わたくし」。語尾は「〜よ」・「〜わ」")
    turns = [
        {"speaker": "四国めたん", "text": "わたくし、よく知っているわ。"},
        {"speaker": "ずんだもん", "text": "そうなのだ"},
        {"speaker": "四国めたん", "text": "わたくし、みんなを守るわ"},
    ]
    assert _drop_repeated_openers(turns, {"四国めたん": meta}) == 1
    assert turns[0]["text"] == "わたくし、よく知っているわ。"
    assert turns[2]["text"] == "みんなを守るわ"


def test_te_nanoda():
    p = parse_style("ずんだもん", "一人称は「ボク」。語尾は「〜のだ」・「〜なのだ」")
    fixed, _ = check_line(0, "命に関わるから、真剣に聞いてなのだ", p)
    assert fixed == "命に関わるから、真剣に聞いてほしいのだ"


def test_newline_and_noka_noda():
    p = parse_style("ずんだもん", "一人称は「ボク」。語尾は「〜のだ」・「〜なのだ」")
    fixed, _ = check_line(0, "へー、鋭い指摘なのだ\nそれなら、ストレッチするのだ", p)
    assert fixed == "へー、鋭い指摘なのだ。それなら、ストレッチするのだ"
    fixed, _ = check_line(0, "疲れたのかいなのだ？\nオマエ、腰が痛くないのかのだ", p)
    assert fixed == "疲れたのだ？オマエ、腰が痛くないのだ？"


def test_subtitle_newline_is_sentence_break():
    from video_automation.services.subtitle.text import make_cues

    cues = make_cues("そのまま、肘を伸ばして上に向けるわ\n力を入れすぎないでね", 16, 2, True)
    assert all(c.count("\n") <= 1 for c in cues)
