from video_automation.services.script.persona import add_noda, check_line, fill_endings, parse_style


def _zunda():
    return parse_style("ずんだもん", "一人称は「ボク」。語尾は「〜のだ」・「〜なのだ」")


def test_tai_nanoda_fixed():
    fixed, iss = check_line(0, "ずんだ餅が食べたいなのだ！", _zunda())
    assert fixed == "ずんだ餅が食べたいのだ！"
    assert any(x.kind == "noda" for x in iss)


def test_add_noda_forms():
    assert add_noda("28 度だ！") == "28 度なのだ！"
    assert add_noda("すぐ消す！") == "すぐ消すのだ！"
    assert add_noda("待機電力まで！？") == "待機電力までなのだ！？"
    assert add_noda("大事だよ。") == "大事なのだよ。"
    assert add_noda("知らなかったのだ！") == "知らなかったのだ！"
    assert add_noda("そうです。") == "そうです。"


def test_fill_endings_only_long_missing():
    p = _zunda()
    assert fill_endings("えっ！エアコンは28 度だ！", p) == "えっ！エアコンは28 度なのだ！"
    meta = parse_style("四国めたん", "一人称は「わたくし」。語尾は「〜よ」・「〜わ」")
    assert fill_endings("エアコンは28 度だ！", meta) == "エアコンは28 度だ！"


def test_add_noda_skips_particle_endings():
    assert add_noda("毎日同じ時間に寝て起きる習慣を！") == "毎日同じ時間に寝て起きる習慣を！"


def test_whole_line_quote_removed():
    fixed, iss = check_line(0, "「ブルーライトで眠れなくなるわ！」", _zunda())
    assert fixed == "ブルーライトで眠れなくなるわ！"
    assert any(x.kind == "quote" for x in iss)
    fixed, _ = check_line(0, "「1234」は危ないのだ！", _zunda())
    assert fixed == "「1234」は危ないのだ！"
