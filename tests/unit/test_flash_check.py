"""点滅の検査（統合版 S1: 4×4 区画・10%・面積 1/4・1 秒 3 回、子ども向け 8%、飽和した赤）。"""

import shutil

import numpy as np
import pytest

from video_automation.integrations.ffmpeg.runner import FFmpeg
from video_automation.services.rendering.flash_check import (
    KIDS_STEP,
    check_video,
    count_flashes,
    relative_luminance,
)


def _gray(values, h=36, w=64):
    """各フレームを全面同じ灰色（0〜255）にした RGB。"""
    return np.stack([np.full((h, w, 3), v, dtype=np.uint8) for v in values])


def test_relative_luminance_of_white_and_black():
    rgb = np.array([[[255, 255, 255], [0, 0, 0]]], dtype=np.uint8)
    lum = relative_luminance(rgb)
    assert abs(lum[0, 0] - 1.0) < 1e-4 and lum[0, 1] == 0


def test_fast_full_screen_flashing_fails_and_slow_changes_pass():
    # 2 フレームごとに白黒が入れ替わる（30fps で 1 秒 7.5 回）
    r = count_flashes(_gray([0, 0, 255, 255] * 30), 30)
    assert not r.ok and r.max_per_sec > 3 and r.worst_sec is not None
    # 1 秒に 1 回の点滅（白は 3 フレーム）は通る
    assert count_flashes(_gray(([0] * 27 + [255] * 3) * 4), 30).ok
    # ゆっくり明るくなるだけ（フェード）は点滅ではない
    assert count_flashes(_gray(list(np.linspace(0, 255, 90).astype(int))), 30).ok


def _quadrants(cells: int, n: int = 120) -> np.ndarray:
    """4×4 区画のうち cells 個だけを 2 フレームごとに白黒させる。"""
    seq = []
    for k in range(n):
        f = np.zeros((36, 64, 3), dtype=np.uint8)
        if (k // 2) % 2:
            for c in range(cells):
                y, x = divmod(c, 4)
                f[y * 9 : (y + 1) * 9, x * 16 : (x + 1) * 16] = 255
        seq.append(f)
    return np.stack(seq)


def test_area_must_exceed_a_quarter():
    assert count_flashes(_quadrants(4), 30).ok  # ちょうど 1/4 は数えない
    assert not count_flashes(_quadrants(5), 30).ok  # 1/4 を超える


def test_kids_threshold_is_stricter():
    # 明るい灰色の小さな明滅（相対輝度の差 約 0.09）
    vals = [200, 200, 212, 212] * 30
    lum = relative_luminance(np.array([[[212] * 3, [200] * 3]], dtype=np.uint8))[0]
    assert 0.08 <= lum[0] - lum[1] < 0.10
    assert count_flashes(_gray(vals), 30).ok
    assert not count_flashes(_gray(vals), 30, KIDS_STEP).ok


def test_saturated_red_flash_is_a_warning_even_once():
    # 赤 #FF0000 と、明るさのほぼ同じ灰色 #7F7F7F を 1 往復（明るさの点滅には数えない）
    red = np.full((36, 64, 3), (255, 0, 0), dtype=np.uint8)
    gray = np.full((36, 64, 3), 127, dtype=np.uint8)
    seq = np.stack([gray] * 10 + [red] * 3 + [gray] * 20)
    r = count_flashes(seq, 30)
    assert r.ok and r.red_flashes == 1 and r.warnings()
    assert count_flashes(np.stack([gray] * 30), 30).warnings() == []


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
async def test_check_video_reads_real_file(tmp_path):
    ff = FFmpeg()
    out = tmp_path / "strobe.mp4"
    await ff.run(
        [
            "-f",
            "lavfi",
            "-i",
            "color=c=white:s=320x180:d=2:r=30",
            "-vf",
            "geq=lum='if(lt(mod(N,4),2),0,255)':cb=128:cr=128",
            "-pix_fmt",
            "yuv420p",
            str(out),
        ]
    )
    r = await check_video(ff, out, 30)
    assert r.frames >= 59 and not r.ok
