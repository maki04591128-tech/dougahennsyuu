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


def test_add_noda_requests_and_questions():
    # 実機の学習データ: 「聞かせてくれのだ」「飲んでくださいのだ」「でしょなのだ？」「知りたくのだね？」
    assert add_noda("カレーの作り方を聞かせてくれ！") == "カレーの作り方を聞かせてほしいのだ！"
    assert add_noda("データを守っておくれ！") == "データを守ってほしいのだ！"
    assert add_noda("こまめに飲んでください！") == "こまめに飲んでほしいのだ！"
    assert add_noda("面倒くさそうでしょ？") == "面倒くさそうなのだ？"
    assert (
        add_noda("台風が来ると何が起こるか知ってるか？") == "台風が来ると何が起こるか知ってるのだ？"
    )
    assert add_noda("面接の受け方、知りたくね？") == "面接の受け方、知りたくないのだ？"
    assert add_noda("家で筋トレしようか？") == "家で筋トレしようか？"
    assert add_noda("今日も勝つぞ！") == "今日も勝つぞ！"
    assert add_noda("すぐ書く！") == "すぐ書くのだ！"
    assert add_noda("大事なのは長さ！") == "大事なのは長さなのだ！"


def test_noda_after_request_or_question_is_fixed():
    z = _zunda()
    cases = {
        "さあ、みんな、カレーの作り方を聞かせてくれのだ！": "さあ、みんな、カレーの作り方を聞かせてほしいのだ！",
        "さあ、みんな、こまめに飲んでくださいのだ！": "さあ、みんな、こまめに飲んでほしいのだ！",
        "冷蔵庫の整理って面倒くさそうでしょなのだ？": "冷蔵庫の整理って面倒くさそうなのだ？",
        "子どもの勉強、うまく見守れているかのだ": "子どもの勉強、うまく見守れているのだ",
        "ふるさと納税って聞いたことあるかのだ？": "ふるさと納税って聞いたことあるのだ？",
        "わかったかなのだ？": "わかったのだ？",
        "面接の受け方、知りたくのだね？": "面接の受け方、知りたくないのだ？",
    }
    for src, want in cases.items():
        assert check_line(0, src, z)[0] == want, src


def test_tsumugi_su_fixed():
    t = parse_style("春日部つむぎ", "一人称は「あーし」。語尾は「〜っす」・「〜だよ」")
    assert check_line(0, "背中がすっきりするよっす。", t)[0] == "背中がすっきりするっす。"
    assert check_line(0, "みんなもやってみてねっす！", t)[0] == "みんなもやってみてっすね！"
