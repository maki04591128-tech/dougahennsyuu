"""セリフの間の長さ（会話のリズム）。"""

from video_automation.services.subtitle.pacing import line_gap


def test_gaps_follow_conversation_rhythm():
    same = line_gap("A", "これは便利なのだ。", "A", "しかも無料なのだ。")
    cont = line_gap("A", "まずはここを押して、", "A", "次に保存するのだ。")
    quick = line_gap("A", "これは便利なのだ。", "B", "へえ！")
    answer = line_gap("A", "どうやって使うの？", "B", "画面の右上のボタンを押すだけよ。")
    reveal = line_gap("A", "どっちが硬いと思う？", "B", "実はダイヤモンドの方が硬いのよ。")
    scene = line_gap("A", "以上なのだ。", "B", "次は設定の話よ。", scene_changed=True)
    assert cont < same and quick < answer < reveal < scene
    assert 0.10 <= cont <= 0.15 and 0.20 <= same <= 0.30 and 0.25 <= answer <= 0.40
    assert line_gap("A", "x", "B", "y", scale=0.5) == round(line_gap("A", "x", "B", "y") * 0.5, 3)
