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


def test_ball_events_jump_over_phrases():
    """統合版 T51: 文節の上に玉。次の文節の頭に着くよう、前の文節から放物線で跳ぶ。"""
    from fractions import Fraction

    from video_automation.services.subtitle.formats import _ball_events

    ev = _ball_events("今日は新しいリュックです", 0, 90, Fraction(30), "S", "A", 54, 1920, 1080,
                      54, "#FF0000")  # fmt: skip
    assert ev and all(e.startswith("Dialogue: 4,") and "\\p1}m " in e for e in ev)
    ys = [float(e.split("\\pos(")[1].split(",")[1].split(")")[0]) for e in ev]
    # 跳んでいる間は着く位置より上（y が小さい）
    assert min(ys) < max(ys) - 54 * 0.5


def test_marker_on_last_sentence():
    """統合版 T37: 印を付けた字幕だけに、箱の型（Marker）で下の 45% を左から右へ伸ばす。"""
    from fractions import Fraction

    from video_automation.schemas.design import Purpose
    from video_automation.services.design.profiles import PROFILES
    from video_automation.services.subtitle.formats import build_ass
    from video_automation.services.subtitle.subtitle_service import style_from_profile

    st = style_from_profile(PROFILES[Purpose.LECTURE])
    cues = [(0, 60, "A", "はじめの文"), (60, 120, "A", "まとめの文です")]
    s = build_ass(cues, Fraction(30), 1280, 720, st, markers={1}, marker_color="#FFD966")
    assert "Style: Marker," in s and ",0,3," in s.split("Style: Marker,")[1].splitlines()[0]
    marks = [ln for ln in s.splitlines() if ",Marker," in ln and ln.startswith("Dialogue:")]
    assert len(marks) == 1 and "まとめの文です" in marks[0] and "\\t(" in marks[0]
    assert "Style: Marker," not in build_ass(cues, Fraction(30), 1280, 720, st)


def test_t31_camera_flies_to_each_phrase_then_pulls_back():
    """統合版 T31: 文節の頭にその文節の正面へ寄り（幅 70%）、最後の 1.5 秒で全部を 90% に収める。"""
    from video_automation.services.rendering import text_scenes as ts

    assert ts.KIND["corporate"] == "T31"
    ph = [
        ts.Phrase("私たちは", 0, 30),
        ts.Phrase("未来を", 30, 60),
        ts.Phrase("つくる", 60, 120, True),
    ]
    lay = ts.t31_layout(ph, 320, 180, None)
    assert lay[1]["x"] == 0.6 * 320 and lay[1]["z"] == -0.3 * 320 and lay[1]["y"] == -0.15 * 180
    fr = list(ts.t31_frames(ph, 0, 120, 320, 180, None, "#FFFFFF", "#000000", "#FF0000", 30))
    assert len(fr) == 120

    def ink_box(im):
        m = im.convert("L").point(lambda v: 255 if v < 200 else 0)
        return m.getbbox()

    b = ink_box(fr[25])  # 1 つ目の文節の正面: 幅は画面の 70% 前後・中央
    assert b is not None and 0.55 * 320 < b[2] - b[0] < 0.8 * 320
    assert abs((b[0] + b[2]) / 2 - 160) < 20
    last = fr[-1]  # 引き: 全部が画面の幅の 90% ほどに収まる
    b2 = ink_box(last)
    assert b2 is not None and b2[0] >= 0 and b2[2] <= 320 and b2[2] - b2[0] > 0.6 * 320
    assert any(
        last.getpixel((x, y))[0] > 200 and last.getpixel((x, y))[1] < 80
        for x in range(320)
        for y in range(180)
    )  # 最後の文節は accent
