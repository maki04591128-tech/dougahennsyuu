"""統合版 T41: 一本の線で題名を描く。"""

import numpy as np

from video_automation.services.rendering import line_title as lt


def test_outer_contour_of_square() -> None:
    m = np.zeros((10, 10), dtype=bool)
    m[2:6, 3:8] = True
    c = lt.outer_contour(m)
    assert c[0] == (3, 2)
    assert len(c) == 2 * (5 - 1) + 2 * (4 - 1)  # 外周の画素だけ
    assert {x for x, _ in c} == set(range(3, 8)) and {y for _, y in c} == set(range(2, 6))


def test_largest_component_and_path_starts_at_left_edge() -> None:
    m = np.zeros((10, 10), dtype=bool)
    m[0:2, 0:2] = True
    m[5:9, 5:9] = True
    big = lt._largest_component(m)
    assert big is not None and big.sum() == 16
    path, face, size = lt.title_path("工場", 320, 180, None)
    assert path[0] == (0.0, 90.0) and len(path) > 20
    assert face.getextrema()[1] == 255 and size == 18


def test_frames_draw_line_then_fill() -> None:
    fr = list(lt.line_title_frames("工", 60, 320, 180, None, "#2A6FDB", "#111111"))
    assert len(fr) == 60
    a = [im.getchannel("A").getbbox() for im in fr]
    assert a[0] is not None and a[0][0] == 0  # 左端から描き始める
    # 線だけの間は文字の色が無く、描き終えた後に面が出る
    assert (0x11, 0x11, 0x11, 255) not in {fr[20].getpixel((x, 90)) for x in range(320)}
    assert fr[59].getchannel("A").histogram()[255] > fr[30].getchannel("A").histogram()[255]
