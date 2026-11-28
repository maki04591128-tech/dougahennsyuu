"""統合版 P6: ゲーム実況のワイプの隅の選び方。"""

import numpy as np

from video_automation.services.rendering import wipe


def _frames(hud: str | None) -> np.ndarray:
    rng = np.random.default_rng(0)
    f = rng.integers(0, 255, (60, 90, 160), dtype=np.uint8)  # 動いているゲームの画面
    if hud:
        x0, y0, x1, y1 = wipe.box(hud, 160, 90)
        pat = np.tile(
            np.array([[0, 255], [255, 0]], np.uint8), ((y1 - y0) // 2 + 1, (x1 - x0) // 2 + 1)
        )
        f[:, y0:y1, x0:x1] = pat[: y1 - y0, : x1 - x0]  # 動かない模様（HUD）
    return f


def test_wipe_avoids_hud_corner():
    corner, scores = wipe.pick_corner(wipe.hud_mask(_frames("bottom_right")))
    assert corner != "bottom_right" and scores["bottom_right"] > 0.5
    assert wipe.pick_corner(wipe.hud_mask(_frames(None)))[0] == "bottom_right"  # 既定は右下
    # 立ち絵があるときは上の隅だけから選ぶ
    c, _ = wipe.pick_corner(wipe.hud_mask(_frames("top_right")), ("top_right", "top_left"))
    assert c == "top_left"


def test_wipe_box_is_18_percent_inside_margin():
    x0, y0, x1, y1 = wipe.box("top_left", 1920, 1080)
    assert abs((x1 - x0) * (y1 - y0) / (1920 * 1080) - 0.18) < 0.002
    assert (x0, y0) == (80, 80)
    x0, y0, x1, y1 = wipe.box("bottom_right", 1920, 1080)
    assert (1920 - x1, 1080 - y1) == (80, 80)


def test_wipe_shrinks_in_its_corner_while_cards_show():
    """カード・図の間はワイプを半分にし、隅（右上）に寄せたままにする。"""
    from video_automation.services.rendering import wipe

    assert wipe.busy_spans([(100, 200), (210, 300), (400, 450)]) == [(100, 300), (400, 450)]
    w, h = 1920, 1080
    keys = wipe.shrink_keys("top_right", w, h, [(100, 200), (210, 300)], 600)
    assert [k[0] for k in keys] == [92, 100, 300, 308]
    assert keys[0][1:] == (1.0, 0.0, 0.0) and keys[-1][1:] == (1.0, 0.0, 0.0)
    _, m, dx, dy = keys[1]
    assert m == wipe.SHRINK
    # 拡大縮小は中心でかかる。縮めた後の右端・上端が元の右端・上端と同じ（隅に寄せたまま）
    x0, y0, x1, y1 = wipe.box("top_right", w, h)
    cx, cy = (x0 + x1) / 2 + dx * w, (y0 + y1) / 2 + dy * h
    assert abs(cx + (x1 - x0) * m / 2 - x1) < 1 and abs(cy - (y1 - y0) * m / 2 - y0) < 1
    # 左下でも同じ（左端・下端が動かない）
    _, m, dx, dy = wipe.shrink_keys("bottom_left", w, h, [(100, 200)], 600)[1]
    x0, y0, x1, y1 = wipe.box("bottom_left", w, h)
    cx, cy = (x0 + x1) / 2 + dx * w, (y0 + y1) / 2 + dy * h
    assert abs(cx - (x1 - x0) * m / 2 - x0) < 1 and abs(cy + (y1 - y0) * m / 2 - y1) < 1
    # 動画の端に近すぎる区間は縮めない
    assert wipe.shrink_keys("top_right", w, h, [(0, 5)], 10) == []
