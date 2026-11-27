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


def test_camera_moves_smoothly_near_the_screen_edge():
    """画面の端に近い所へ寄るときも、途中で急に止まらない（動きが折れない）。"""
    from video_automation.services.screencast.camera import Shot

    p = CameraParams()
    # 右上に寄った所から、左下へ倍率を変えながら移る（以前は端で止まって動きが折れた）
    shots = [Shot(0.0, SIZE[0] - 5, 5, 2.2), Shot(1.0, 5, SIZE[1] - 5, 1.3)]
    fps = 60
    xy = np.array([camera_at(shots, 1.0 + i / fps, SIZE, p)[:2] for i in range(int(0.9 * fps))])
    v = np.diff(xy, axis=0)
    acc = np.abs(np.diff(v, axis=0))
    # 速度の変化（加速度）が滑らか: どのフレームでも、最大の速さの 1/4 を超えて急に変わらない
    assert (acc.max(0) < np.abs(v).max(0) * 0.25).all()
    # 最後は移った先（端に合わせた位置）に止まる
    end = camera_at(shots, 3.0, SIZE, p)
    assert abs(end[0] - SIZE[0] / 1.3 / 2) < 1e-6 and end[2] == 1.3


def test_clip_camera_uses_subpixel_perspective_not_zoompan():
    from fractions import Fraction

    from video_automation.schemas.animation import ClipAnimation
    from video_automation.services.animation.motion import BUILTIN_BY_NAME, resolve_preset
    from video_automation.services.animation.render_anim import clip_filters

    kfs = resolve_preset(BUILTIN_BY_NAME["ken_burns_in"], 300, Fraction(30))
    anim = ClipAnimation(keyframes=kfs)
    f = clip_filters(
        anim,
        fps=Fraction(30),
        k=0.0,
        off=0.0,
        clip_sec=10.0,
        src_size=(1920, 1080),
        canvas=(1920, 1080),
        is_image=True,
    )
    joined = ",".join(f.pre)
    assert "zoompan" not in joined and "perspective=" in joined and "eval=frame" in joined


def test_spotlight_level_and_image():
    """統合版 N14: 0.3 秒前から 1.2 秒間、入り・出は 6f。外だけを 50% 暗く。"""
    from PIL import Image

    from video_automation.services.screencast import finish as fin

    assert fin.spotlight_level(-0.4, 30) == 0.0 and fin.spotlight_level(1.0, 30) == 0.0
    assert fin.spotlight_level(-0.3 + 3 / 30, 30) == 0.5
    assert fin.spotlight_level(0.2, 30) == 1.0
    im = fin.spotlight(Image.new("RGB", (100, 50), (200, 200, 200)), 50, 25, 1.0)
    assert im.getpixel((50, 25)) == (200, 200, 200) and im.getpixel((2, 2))[0] == 100
