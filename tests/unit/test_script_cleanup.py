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


def test_more_simple_fixes():
    assert _simple_fix("すぐに片付けを始めてみないか。") == "すぐに片付けを始めてみなさい。"
    assert (
        _simple_fix("スマホの充電器も忘れずに持てよ。")
        == "スマホの充電器も忘れずに持っていきなさい。"
    )


def test_volitional_noda():
    p = parse_style("ずんだもん", "一人称は「ボク」。語尾は「〜のだ」・「〜なのだ」")
    assert check_line(0, "さあ、準備を始めようのだ。", p)[0] == "さあ、準備を始めるのだ。"
    assert check_line(0, "一緒に確認しようのだ！", p)[0] == "一緒に確認するのだ！"
    assert check_line(0, "懐中電灯も備えろなのだ。", p)[0] == "懐中電灯も備えるのだ。"
    assert check_line(0, "身分証も入れておけなのだ。", p)[0] == "身分証も入れておくのだ。"
    assert check_line(0, "今ちょうどそのところなのだ。", p)[0] == "今ちょうどそのところなのだ。"


def test_reading_skeleton():
    from video_automation.services.voice.reading_service import _skeleton

    assert _skeleton("ショクリョオオ") == _skeleton("ショクリョオ")
    assert _skeleton("ショメエワ") == _skeleton("ショオメエワ")
    assert _skeleton("ジュウブン") != _skeleton("ジップン")


def test_round7_rules():
    z = parse_style("ずんだもん", "一人称は「ボク」。語尾は「〜のだ」・「〜なのだ」")
    m = parse_style("四国めたん", "一人称は「わたくし」。語尾は「〜よ」・「〜わ」")
    assert check_line(0, "オマエらも、試してみななのだ！", z)[0] == "オマエらも、試してみるのだ！"
    assert (
        check_line(0, "手帳の方が、触り心地良かしら。", m)[0] == "手帳の方が、触り心地良いかしら。"
    )
    assert check_line(0, "これは最高かしら。", m)[0] == "これは最高かしら。"


def test_nodaka():
    z = parse_style("ずんだもん", "一人称は「ボク」。語尾は「〜のだ」・「〜なのだ」")
    assert (
        check_line(0, "つむぎ、その理由を知ってるのだか？", z)[0]
        == "つむぎ、その理由を知ってるのだ？"
    )
    assert check_line(0, "大事なのだから、守るのだ", z)[0] == "大事なのだから、守るのだ"


def test_copied_example_sentence_is_dropped():
    from video_automation.services.script.persona import Persona

    m = Persona(
        "四国めたん",
        first=["わたくし"],
        examples=["わたくしは四国めたん、すべての白地図を四国に染める女よ"],
    )
    t = "わたくしは四国めたん、すべての白地図を四国に染める女よ。朝の使い方を教えるわ。"
    fixed, iss = check_line(0, t, m)
    assert fixed == "朝の使い方を教えるわ。"
    assert any(x.kind == "example_copy" for x in iss)
    fixed, iss = check_line(0, "すべての白地図を四国に染める女よ。", m)
    assert any(x.kind == "avoid" for x in iss)


def test_ssu_grammar():
    from video_automation.services.script.persona import Persona

    t = Persona("春日部つむぎ", first=["あーし"])
    assert check_line(0, "今日から早起きしてみまっす！", t)[0] == "今日から早起きしてみるっす！"
    assert check_line(0, "運動してから勉強するんだっすか。", t)[0] == "運動してから勉強するんすか。"
    assert check_line(0, "背筋を少しまっすぐにするっす", t)[0] == "背筋を少しまっすぐにするっす"


def test_simplified_chinese_chars():
    m = parse_style("四国めたん", "一人称は「わたくし」。語尾は「〜よ」・「〜わ」")
    assert check_line(0, "贵女も気をつけてね", m)[0] == "貴女も気をつけてね"
    z = parse_style("ずんだもん", "一人称は「ボク」。語尾は「〜のだ」・「〜なのだ」")
    assert check_line(0, "これなら细菌も退散するのだ", z)[0] == "これなら細菌も退散するのだ"
    _, iss = check_line(0, "我们も手を洗うのだ", z)
    assert any(x.kind == "avoid" for x in iss)


def test_round10_rules():
    z = parse_style("ずんだもん", "一人称は「ボク」。語尾は「〜のだ」・「〜なのだ」")
    assert check_line(0, "めたんの言うことを聞けのだ。", z)[0] == "めたんの言うことを聞くのだ。"
    assert _simple_fix("その件で貴女に指導しますよ。") == "その件で貴女に指導するわよ。"
    assert (
        _simple_fix("無理をしないでくださいわ、アンタら。")
        == "無理をしないでちょうだい、アンタら。"
    )


def test_round12_rules():
    z = parse_style("ずんだもん", "一人称は「ボク」。語尾は「〜のだ」・「〜なのだ」")
    assert check_line(0, "みんな、気をつけてねのだ。", z)[0] == "みんな、気をつけてほしいのだ。"
    assert _simple_fix("わたくし、その罠に気づきますの") == "わたくし、その罠に気づくの"


def test_round13_rules():
    from video_automation.services.voice.characters import BUILTIN

    m = parse_style("四国めたん", BUILTIN["四国めたん"].text())
    _, iss = check_line(0, "むにゃむにゃしてないで、水分を取れよ。", m)
    assert any(x.kind == "avoid" and x.expected == "命令口調" for x in iss)
    _, iss = check_line(0, "加湿器が一番よ。", m)
    assert not any(x.expected == "命令口調" for x in iss)
    _, iss = check_line(0, "ここが大事なところよ。", m)
    assert not any(x.expected == "命令口調" for x in iss)
    z = parse_style("ずんだもん", "一人称は「ボク」。語尾は「〜のだ」・「〜なのだ」")
    assert check_line(0, "喉も渇いて声が出ないのだ！'", z)[0] == "喉も渇いて声が出ないのだ！"


def test_round14_labels():
    z = parse_style("ずんだもん", "一人称は「ボク」。語尾は「〜のだ」・「〜なのだ」")
    assert (
        check_line(0, "L3 ずんだもん: まず、この道に慣れるのだ！", z)[0]
        == "まず、この道に慣れるのだ！"
    )
    assert check_line(0, "L5サイズの箱を使うのだ", z)[0] == "L5サイズの箱を使うのだ"
    assert check_line(0, "おはようのだ！", z)[0] == "おはようなのだ！"


def test_swallow_noda_forms():
    z = parse_style("ずんだもん", "一人称は「ボク」。語尾は「〜のだ」・「〜なのだ」")
    assert (
        check_line(0, "まずは対策をまとめてみようのだ。", z)[0] == "まずは対策をまとめてみるのだ。"
    )
    assert check_line(0, "今日から実践してごらんのだ。", z)[0] == "今日から実践してほしいのだ。"
    assert check_line(0, "覚えておくんなのだ", z)[0] == "覚えておくのだ"


def test_round15_particles():
    z = parse_style("ずんだもん", "一人称は「ボク」。語尾は「〜のだ」・「〜なのだ」")
    assert (
        check_line(0, "ネットの闇に足を踏み入れないでよのだ。", z)[0]
        == "ネットの闇に足を踏み入れないでほしいのだ。"
    )
    assert check_line(0, "まずは深呼吸だぜのだ。", z)[0] == "まずは深呼吸なのだ。"
    assert check_line(0, "さあ、しっかり守ろうねのだ。", z)[0] == "さあ、しっかり守るのだ。"
    assert check_line(0, "さあ、一緒に行こうのだ。", z)[0] == "さあ、一緒に行くのだ。"
    assert check_line(0, "ボクはそう思うのだ。", z)[0] == "ボクはそう思うのだ。"
    m = parse_style("四国めたん", "一人称は「わたくし」。語尾は「〜よ」・「〜わ」")
    assert check_line(0, "それは危険を防ぐのわ。", m)[0] == "それは危険を防ぐのよ。"


def test_reading_skeleton_wo():
    from video_automation.services.voice.reading_service import _skeleton

    assert _skeleton("コジンジョオホオオ") == _skeleton("コジンジョオホオヲ")


def test_round16_rules():
    z = parse_style("ずんだもん", "一人称は「ボク」。語尾は「〜のだ」・「〜なのだ」")
    assert check_line(0, "毎晩、ぼんやりしたままだのだ。", z)[0] == "毎晩、ぼんやりしたままなのだ。"
    assert check_line(0, "今日から実践してみなのだよ。", z)[0] == "今日から実践してみるのだよ。"
    assert check_line(0, "朝のだ、カーテンを開けるのだ。", z)[0] == "朝は、カーテンを開けるのだ。"
    m = parse_style("四国めたん", "一人称は「わたくし」。語尾は「〜よ」・「〜わ」")
    assert check_line(0, "睡眠の質がぐんと上がりるわ。", m)[0] == "睡眠の質がぐんと上がるわ。"
    assert _simple_fix("貴女たちの朝を待っていますの") == "貴女たちの朝を待っているの"
