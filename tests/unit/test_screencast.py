"""画面録画のカメラ: ショットの計画（まとめる・引く）、イーズで移る、変化の範囲。"""

import numpy as np

from video_automation.services.screencast.camera import (
    CameraParams,
    Focus,
    camera_at,
    change_regions,
    plan_shots,
)

SIZE = (1220, 800)


def test_plan_merges_near_clicks_and_zooms_out_when_idle() -> None:
    clicks = [Focus(3.0, 300, 200), Focus(3.8, 320, 210), Focus(12.0, 900, 600)]
    shots = plan_shots(clicks, 20.0, SIZE)
    zooms = [s.zoom for s in shots]
    assert zooms[0] == 1.0  # 最初は全体
    assert len([s for s in shots if s.zoom > 1 and s.cx < 600]) == 1  # 近いクリックは 1 つに
    i = next(k for k, s in enumerate(shots) if s.cx > 700 and s.zoom > 1)
    assert shots[i - 1].zoom == 1.0  # 間が空いたら一度引いてから寄る
    assert shots[-1].zoom == 1.0  # 最後は引く
    assert shots[1].t <= 3.0 - 0.5  # クリックより先に動き始める


def test_camera_eases_and_holds() -> None:
    shots = plan_shots([Focus(3.0, 300, 200)], 10.0, SIZE)
    p = CameraParams()
    s = shots[1]
    a = camera_at(shots, s.t - 0.01, SIZE, p)
    mid = camera_at(shots, s.t + 0.2, SIZE, p)
    held = [camera_at(shots, s.t + 1.0 + k * 0.1, SIZE, p) for k in range(5)]
    assert a[2] == 1.0 and 1.0 < mid[2] < s.zoom
    assert all(h == held[0] for h in held)  # 着いたら止まる（揺れない）
    # 中心は録画の外を見せない
    cx, cy, z = held[0]
    assert cx - SIZE[0] / z / 2 >= 0 and cy - SIZE[1] / z / 2 >= 0


def test_change_regions_finds_area_and_ignores_full_change() -> None:
    base = np.zeros((50, 80), np.uint8)
    frames = [(i * 0.2, base.copy()) for i in range(10)]
    frames[5] = (1.0, base.copy())
    frames[5][1][10:20, 40:60] = 200  # 一部が変わる
    frames[8] = (1.6, np.full((50, 80), 255, np.uint8))  # 全体が変わる（ページの切り替え）
    found = change_regions(frames, 0.25)
    assert len(found) >= 1 and found[0].kind == "change"
    f = found[0]
    assert 150 < f.x < 250 and 40 < f.y < 80  # 録画の画素に戻す
    assert all(x.t != 1.6 for x in found)
