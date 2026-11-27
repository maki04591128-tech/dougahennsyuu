"""花字（統合版 T42）の層と動き。"""

from video_automation.services.rendering import flower as fl

COL = fl.FlowerColors("#024A22", "#35A860", "#CCEBC5", "#D7F9DE")


def test_glyph_has_five_layers():
    g = fl.glyph("あ", None, 60, COL)
    px = g.load()
    w, h = g.size
    colors = {px[x, y][:3] for x in range(0, w, 2) for y in range(0, h, 2) if px[x, y][3] > 250}
    assert (255, 255, 255) in colors  # 白の縁
    assert any(c[1] > c[0] and c[1] > c[2] for c in colors)  # 緑の縁・押し出し


def test_frames_pop_idle_and_exit():
    (cw, ch), gen = fl.frames("やった", None, 40, COL, "surprise", 40, 30, 1080)
    fr = list(gen)
    assert len(fr) == 40 and fr[0].size == (cw, ch)
    # 1 文字ずつ: 最初の 2f は 1 文字目だけ
    b0 = fr[1].getchannel("A").getbbox()
    b_late = fr[20].getchannel("A").getbbox()
    assert b0 is not None and b_late is not None and (b0[2] - b0[0]) < (b_late[2] - b_late[0])
    # 出: 最後のフレームはほぼ消える
    last = fr[-1].getchannel("A").getbbox()
    assert last is None or (last[2] - last[0]) < (b_late[2] - b_late[0]) * 0.3
