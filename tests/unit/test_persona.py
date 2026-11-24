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


def test_fit_to_duration_drops_middle_scenes_then_trailing_lines():
    from video_automation.services.script.studio import _norm, fit_to_duration

    def scene(name, n, chars=28):
        return {
            "purpose": name,
            "turns": [{"speaker": "A", "text": "あ" * chars} for _ in range(n)],
        }

    # 1 発話 ≒ 28/7+0.3 = 4.3 秒。6 場面×4 発話 ≒ 103 秒 → 目標 30 秒
    scenes = [scene(f"s{i}", 4) for i in range(6)]
    out, notes = fit_to_duration(scenes, 30, 7.0)
    total = sum(28 / 7 + 0.3 for sc in out for _ in sc["turns"])
    assert total <= 30 * 1.15 and out[0]["purpose"] == "s0" and out[-1]["purpose"] == "s5"
    assert notes and "省きました" in notes[0]
    # 目標以内ならそのまま
    same, n2 = fit_to_duration(scenes[:2], 60, 7.0)
    assert same == scenes[:2] and n2 == []
    # 固定セリフを含む場面は省かない
    fixed = scene("fixed", 4)
    fixed["turns"][0]["text"] = "これは固定のセリフです"
    out, _ = fit_to_duration(
        [scene("a", 4), fixed, scene("b", 4), scene("c", 4)],
        20,
        7.0,
        {_norm("これは固定のセリフです")},
    )
    assert any(sc["purpose"] == "fixed" for sc in out)
    # 元の場面は変えない
    assert len(scenes) == 6 and all(len(sc["turns"]) == 4 for sc in scenes)


def test_other_characters_ending_is_removed_and_ellipsis_is_not_a_sentence_break():
    from video_automation.services.script.persona import foreign_endings

    ps = {n: _p(n) for n in ("ずんだもん", "四国めたん")}
    fe = foreign_endings(ps, "四国めたん")
    assert set(fe) == {"のだ", "なのだ"} and foreign_endings(ps, "ずんだもん") == ()
    fixed, iss = check_line(
        0, "実用化が進められているのだ。合成体が代表例なのだ！", ps["四国めたん"], fe
    )
    assert fixed == "実用化が進められているの。合成体が代表例なの！"
    assert [i.kind for i in iss] == ["foreign_ending", "foreign_ending"]
    # 「…」は言いよどみで、文の区切りではない（語尾の抜けと数えない）
    z = ps["ずんだもん"]
    assert check_line(0, "え、そんなの…何だかんだで、僕はまだ知らんのだ！", z)[1] == []
    # まとめて調べるときも、ほかのキャラクターの語尾を直す
    out, iss = check_lines([("四国めたん", "そうなのだ。"), ("ずんだもん", "そうなのだ。")], ps)
    assert out == ["そうなの。", "そうなのだ。"] and [i.index for i in iss] == [0]
    # 終助詞が付いても見つける（「実在するのだよ」→「実在するのよ」）
    m = ps["四国めたん"]
    assert check_line(0, "実在するのだよ。", m, fe)[0] == "実在するのよ。"
    assert check_line(0, "進むのだね！", m, fe)[0] == "進むのね！"


def test_stray_leading_ending_is_removed():
    z = _p("ずんだもん")
    fixed, iss = check_line(0, "のだ、長く使えるのだな、バッテリーは長持ちするのだ。", z)
    assert fixed == "長く使えるのだな、バッテリーは長持ちするのだ。"
    assert [i.kind for i in iss] == ["stray_ending"]
    # 語尾だけの発話・文の途中の語尾はそのまま
    assert check_line(0, "のだ！", z)[0] == "のだ！"
    assert check_line(0, "そうなのだ、すごいのだ。", z)[1] == []
