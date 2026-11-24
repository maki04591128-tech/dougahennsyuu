"""点滅の検査（光過敏性発作の安全基準の簡易版）。"""

import shutil

import numpy as np
import pytest

from video_automation.integrations.ffmpeg.runner import FFmpeg
from video_automation.services.rendering.flash_check import (
    check_video,
    count_flashes,
    relative_luminance,
)


def _frames(values, h=18, w=32):
    return np.stack([np.full((h, w), v, dtype=np.float32) for v in values])


def test_relative_luminance_of_white_and_black():
    rgb = np.array([[[255, 255, 255], [0, 0, 0]]], dtype=np.uint8)
    lum = relative_luminance(rgb)
    assert abs(lum[0, 0] - 1.0) < 1e-4 and lum[0, 1] == 0


def test_fast_full_screen_flashing_fails_and_slow_changes_pass():
    # 2 フレームごとに白黒が入れ替わる（30fps で 1 秒 7.5 回）
    fast = _frames([0.0, 0.0, 1.0, 1.0] * 30)
    r = count_flashes(fast, 30)
    assert not r.ok and r.max_per_sec > 3 and r.worst_sec is not None
    # 1 秒に 1 回の点滅（白は 3 フレーム）は通る
    slow = _frames(([0.0] * 27 + [1.0] * 3) * 4)
    assert count_flashes(slow, 30).ok
    # ゆっくり明るくなるだけ（フェード）は点滅ではない
    fade = _frames(list(np.linspace(0, 1, 90)))
    assert count_flashes(fade, 30).ok


def test_small_area_or_bright_pairs_are_not_counted():
    # 画面の 1/4 以下だけが点滅
    seq = []
    for k in range(120):
        f = np.zeros((18, 32), dtype=np.float32)
        if (k // 2) % 2:
            f[:4, :8] = 1.0
        seq.append(f)
    assert count_flashes(np.stack(seq), 30).ok
    # 暗い方も 0.80 以上（明るい中での小さな揺れ）は数えない
    bright = _frames([0.85, 0.85, 1.0, 1.0] * 30)
    assert count_flashes(bright, 30).ok


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
