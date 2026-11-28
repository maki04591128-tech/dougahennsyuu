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
