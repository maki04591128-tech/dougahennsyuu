"""書き出した動画の点滅の検査（光過敏性発作の安全基準の簡易版）。

基準（WCAG 2.3.1・NHK/民放連のガイドライン）:
- 「点滅」= 相対輝度が 10% 以上上がって下がる（またはその逆）対で、暗い方が 0.80 未満
- 画面の 4 分の 1 を超える面積が同時に点滅し、どの 1 秒間でも 3 回を超えたら不合格

方法: フレームを小さく縮めて（WxH の格子）画素ごとの相対輝度を求め、格子ごとに輝度の
上がり・下がりの「転換」を数える。上がって下がる 1 対 = 1 回の点滅（転換 2 回）。
同じフレームで転換した格子が画面の 1/4 を超えたときを「画面の点滅の転換」とし、
どの 1 秒の窓でも転換が 6 回（= 3 回の点滅）を超えたら不合格とする。
これは自作の目安で、放送の公式な判定（Harding テスト）ではない。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from video_automation.integrations.ffmpeg.runner import FFmpeg

GRID_W, GRID_H = 32, 18  # 縮めた大きさ（1 格子 = 画面の 1/576）
STEP = 0.10  # 点滅とみなす相対輝度の変化
DARK = 0.80  # 暗い方の相対輝度がこれ未満のときだけ数える
AREA = 0.25  # 画面のこの割合を超えて同時に変わったら画面の点滅
MAX_FLASHES = 3  # 1 秒間の上限


@dataclass
class FlashResult:
    ok: bool
    max_per_sec: float  # どの 1 秒で最も多かった点滅の回数
    worst_sec: float | None  # その 1 秒の始まり（秒）
    frames: int

    def message(self) -> str:
        at = f"{self.worst_sec:.1f} 秒付近" if self.worst_sec is not None else ""
        return (
            f"画面の広い範囲が 1 秒に {self.max_per_sec:g} 回点滅しています（{at}、"
            f"上限は 1 秒 {MAX_FLASHES} 回）。"
            "光過敏性発作の安全基準を超えるため書き出しを止めました"
        )


def relative_luminance(rgb: np.ndarray) -> np.ndarray:
    """sRGB（0〜255）→ 相対輝度（0〜1）。"""
    c = rgb.astype(np.float32) / 255.0
    lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * lin[..., 0] + 0.7152 * lin[..., 1] + 0.0722 * lin[..., 2]


def count_flashes(lum: np.ndarray, fps: float) -> FlashResult:
    """lum: (フレーム, 高さ, 幅) の相対輝度。"""
    n = lum.shape[0]
    if n < 3:
        return FlashResult(True, 0, None, n)
    cells = lum.shape[1] * lum.shape[2]
    # 格子ごとに、最後に転換した向きと、そのときの輝度（極値）を追う
    ref = lum[0].copy()  # 最後の極値
    direction = np.zeros_like(ref, dtype=np.int8)  # +1 上がり中 / -1 下がり中 / 0 未定
    turns = np.zeros(n, dtype=np.int32)  # フレームごとの「転換した格子」の数
    for i in range(1, n):
        cur = lum[i]
        up = (cur - ref >= STEP) & (np.minimum(cur, ref) < DARK)
        down = (ref - cur >= STEP) & (np.minimum(cur, ref) < DARK)
        flipped = (up & (direction != 1)) | (down & (direction != -1))
        turns[i] = int(flipped.sum())
        moved = up | down
        direction = np.where(up, 1, np.where(down, -1, direction)).astype(np.int8)
        ref = np.where(moved, cur, ref)
        # 同じ向きに進み続けるときは極値を更新する（ゆっくり明るくなる変化は 1 回の転換）
        ref = np.where((direction == 1) & (cur > ref), cur, ref)
        ref = np.where((direction == -1) & (cur < ref), cur, ref)
    screen = (turns > cells * AREA).astype(np.int32)  # 画面の点滅の転換があったフレーム
    win = max(1, round(fps))
    csum = np.concatenate([[0], np.cumsum(screen)])
    per_win = csum[win:] - csum[:-win] if n >= win else np.array([csum[-1]])
    best = int(per_win.argmax())
    worst = float(per_win[best]) / 2  # 転換 2 回 = 点滅 1 回
    return FlashResult(worst <= MAX_FLASHES, worst, best / fps if worst > 0 else None, n)


async def check_video(ffmpeg: FFmpeg, path: Path, fps: float) -> FlashResult:
    """動画を縮めて読み、点滅を数える。"""
    raw = await ffmpeg.run_capture(
        [
            "-v",
            "error",
            "-i",
            str(path),
            "-vf",
            f"scale={GRID_W}:{GRID_H}:flags=area,format=rgb24",
            "-f",
            "rawvideo",
            "-",
        ],
        timeout=600,
    )
    frame = GRID_W * GRID_H * 3
    n = len(raw) // frame
    arr = np.frombuffer(raw[: n * frame], dtype=np.uint8).reshape(n, GRID_H, GRID_W, 3)
    return count_flashes(relative_luminance(arr), fps)
