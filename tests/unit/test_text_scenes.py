"""文字だけの場面（統合版 T30・T32・T33・T34）の区間と文節。"""

from types import SimpleNamespace

from video_automation.services.rendering import text_scenes as ts

PAL = SimpleNamespace(base="#000000", text="#FFFFFF", accent="#FF0000")


def test_phrases_split_and_times():
    ph = ts.phrases("今日は新しいリュックを紹介します。", 0, 90)
    assert [p.text for p in ph][0] == "今日は" and ph[-1].last and ph[0].start == 0
    assert all(a.end == b.start for a, b in zip(ph, ph[1:], strict=False))
    # アクセント句の数が合えば、その頭の時刻
    ph2 = ts.phrases("今日は新しい", 0, 60, [0, 33])
    assert [p.start for p in ph2] == [0, 33]


def test_pick_sections_rules():
    fps = 30.0
    lines = [(0, 120, "a"), (120, 240, "b"), (240, 300, "c"), (300, 420, "d")]
    # 図が 240〜300 にあると、その前後で分ける。3 秒未満の区間は作らない
    secs = ts.pick_sections(lines, [(240, 300)], 3000, fps, "promo")
    assert secs == [(0, 1), (1, 2), (3, 4)]
    # 合計は動画の長さの 20% まで（超える分は後ろから外す）
    secs = ts.pick_sections(lines, [], 1200, fps, "promo")
    assert secs == [(0, 1), (1, 2)]
    # 解説は章の最初の文だけ
    secs = ts.pick_sections(lines, [(240, 300)], 3000, fps, "explainer", [300])
    assert secs == [(0, 1), (3, 4)]


def test_beat_snap():
    assert ts.beat_snap(29, 30, 120) == 30  # 拍は 15f ごと
    assert ts.beat_snap(37, 30, 120) == 37  # 差が 4f を超える


def test_frames_each_kind():
    ph = ts.phrases("軽くて丈夫です。", 0, 60)
    for kind in ("T30", "T32", "T33", "T34"):
        fr = list(ts.frames(kind, ph, 0, 60, 160, 90, None, PAL, 30, 120, "軽くて丈夫です。"))
        assert len(fr) == 60 and fr[-1].getbbox() is not None, kind
