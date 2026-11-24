"""キャラクターの口調の規則（一人称・呼び方・語尾）の読み取りと確認・置き換え。"""

from video_automation.services.script.persona import check_line, check_lines, parse_style
from video_automation.services.voice.characters import BUILTIN


def _p(name: str):
    return parse_style(name, BUILTIN[name].text())


def test_parse_builtin_styles():
    z = _p("ずんだもん")
    assert z.first == ["僕", "ずんだもん"] and z.second == ["オマエ", "みんな"]
    assert z.endings == ["のだ", "なのだ"]
    m = _p("四国めたん")
    assert m.first == ["わたくし"] and m.second == ["アンタ", "貴女"] and m.endings == []
    assert "一人称は「わたくし」だけ" in m.rules_text()


def test_wrong_pronouns_are_replaced_but_quotes_and_compounds_are_kept():
    m = _p("四国めたん")
    fixed, iss = check_line(0, "僕もそう思うわ。あなたたちはどう？", m)
    assert fixed == "わたくしもそう思うわ。アンタたちはどう？"
    assert {i.kind for i in iss} == {"first_person", "second_person"}
    # 引用（かぎかっこの中）・「私立」・「田中君」は代名詞として扱わない
    fixed, iss = check_line(0, "私立の田中君が「僕は行く」と言ってたわ", m)
    assert iss == [] and fixed == "私立の田中君が「僕は行く」と言ってたわ"


def test_missing_ending_is_reported_not_rewritten():
    z = _p("ずんだもん")
    fixed, iss = check_line(0, "お前ら、本当にそう思ってんのか？僕は違うと思うのだ。", z)
    assert fixed.startswith("オマエら、")
    assert [i.kind for i in iss] == ["second_person", "ending"]
    # 語尾があれば問題なし。短い相づちは対象外
    assert check_line(0, "すごいのだ！これは便利なのだ。", z)[1] == []
    assert check_line(0, "えっ！", z)[1] == []


def test_check_lines_uses_each_speakers_rules():
    ps = {n: _p(n) for n in ("ずんだもん", "四国めたん")}
    out, iss = check_lines([("ずんだもん", "僕はずんだもんなのだ"), ("四国めたん", "僕よ")], ps)
    assert out == ["僕はずんだもんなのだ", "わたくしよ"]
    assert [(i.index, i.kind) for i in iss] == [(1, "first_person")]
