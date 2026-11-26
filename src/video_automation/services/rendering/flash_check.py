"""書き出した動画の点滅の検査（光過敏性発作の安全基準。統合版 S1）。

基準（民放連のアニメーション等の映像手法に関するガイドライン・WCAG 2.3.1）:
- 画面を 4×4 の区画に分け、区画ごとに Rec.709 の相対輝度の平均を見る
- 同じ向きに 10% 以上変わった区画の面積の合計が画面の 1/4 を超えたら「変化」
- 逆向きの変化の組を 1 回の点滅とし、どの 1 秒の窓でも 3 回を超えたら error（書き出しを止める）
- 子ども向けだけ、変化のしきい値を 8% にする
- 飽和した赤の点滅（WCAG 2.2 の作業上の定義: 一方の状態の R/(R+G+B) が 0.8 以上で、
  2 つの状態の CIE 1976 u′v′ の色度の差が 0.2 を超える、逆向きの変化の組）は回数によらず warning

区画の変化は、区画ごとに最後の極値（明るさの山・谷）からの差で見る。ゆっくり明るくなる変化は
1 回の変化と数える。これは自作の検査で、放送の公式な判定（Harding テスト）ではない。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from video_automation.integrations.ffmpeg.runner import FFmpeg

READ_W, READ_H = 64, 36  # 読み込む大きさ（1 区画 = 16×9 画素）
GRID = 4  # 4×4 の区画
STEP = 0.10  # 変化とみなす相対輝度の差
KIDS_STEP = 0.08  # 子ども向けのしきい値
AREA = 0.25  # 画面のこの割合を超えて同じ向きに変わったら「変化」
MAX_FLASHES = 3  # 1 秒間の上限
RED_RATIO = 0.8  # 飽和した赤: R/(R+G+B)
RED_UV = 0.2  # 飽和した赤の点滅: 2 つの状態の u′v′ の差


@dataclass
class FlashResult:
    ok: bool
    max_per_sec: float  # どの 1 秒で最も多かった点滅の回数
    worst_sec: float | None  # その 1 秒の始まり（秒）
    frames: int
    red_flashes: int = 0  # 飽和した赤の点滅の回数（1 回でもあれば warning）
    red_sec: float | None = None

    def message(self) -> str:
        at = f"{self.worst_sec:.1f} 秒付近" if self.worst_sec is not None else ""
        return (
            f"画面の広い範囲が 1 秒に {self.max_per_sec:g} 回点滅しています（{at}、"
            f"上限は 1 秒 {MAX_FLASHES} 回）。"
            "光過敏性発作の安全基準を超えるため書き出しを止めました"
        )

    def warnings(self) -> list[str]:
        if not self.red_flashes:
            return []
        at = f"{self.red_sec:.1f} 秒付近" if self.red_sec is not None else ""
        return [
            f"鮮やかな赤の点滅が {self.red_flashes} 回あります（{at}）。"
            "赤の点滅は回数が少なくても発作を起こしやすいので、色か明るさの変化を弱めてください"
        ]


def relative_luminance(rgb: np.ndarray) -> np.ndarray:
    """sRGB（0〜255）→ 相対輝度（0〜1）。"""
    lin = _linear(rgb)
    return 0.2126 * lin[..., 0] + 0.7152 * lin[..., 1] + 0.0722 * lin[..., 2]


def _linear(rgb: np.ndarray) -> np.ndarray:
    c = rgb.astype(np.float32) / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _regions(a: np.ndarray) -> np.ndarray:
    """(フレーム, 高さ, 幅, ...) を 4×4 の区画の平均にする → (フレーム, 16, ...)。"""
    n, h, w = a.shape[:3]
    rest = a.shape[3:]
    r = a.reshape(n, GRID, h // GRID, GRID, w // GRID, *rest).mean(axis=(2, 4))
    return r.reshape(n, GRID * GRID, *rest)


def _uv(lin: np.ndarray) -> np.ndarray:
    """線形 RGB（Rec.709 の原色）→ CIE 1976 u′v′。"""
    m = np.array(
        [[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]],
        dtype=np.float32,
    )
    xyz = lin @ m.T
    d = xyz[..., 0] + 15 * xyz[..., 1] + 3 * xyz[..., 2]
    d = np.where(d <= 1e-6, 1e-6, d)
    return np.stack([4 * xyz[..., 0] / d, 9 * xyz[..., 1] / d], axis=-1)


def _screen_turns(lum: np.ndarray, step: float) -> np.ndarray:
    """区画の輝度（フレーム, 16）から、フレームごとの画面の変化の向き（+1/-1/0）を求める。"""
    n, cells = lum.shape
    ref = lum[0].copy()  # 区画ごとの最後の極値
    direction = np.zeros(cells, dtype=np.int8)  # +1 明るくなる途中 / -1 暗くなる途中
    out = np.zeros(n, dtype=np.int8)
    for i in range(1, n):
        cur = lum[i]
        up = cur - ref >= step
        down = ref - cur >= step
        new_up = up & (direction != 1)
        new_down = down & (direction != -1)
        if new_up.sum() / cells > AREA:
            out[i] = 1
        elif new_down.sum() / cells > AREA:
            out[i] = -1
        moved = up | down
        direction = np.where(up, 1, np.where(down, -1, direction)).astype(np.int8)
        ref = np.where(moved, cur, ref)
        # 同じ向きに進み続けるときは極値を更新する（ゆっくりした変化は 1 回の変化）
        ref = np.where((direction == 1) & (cur > ref), cur, ref)
        ref = np.where((direction == -1) & (cur < ref), cur, ref)
    return out


def _flashes_per_window(turns: np.ndarray, fps: float) -> tuple[float, float | None]:
    """画面の変化の列から、どの 1 秒の窓でも数えた点滅（逆向きの変化の組）の最大。"""
    n = len(turns)
    # 向きが前の変化と逆のときだけ数える（同じ向きが続くのは 1 回の変化）
    flips = np.zeros(n, dtype=np.int32)
    last = 0
    for i, d in enumerate(turns):
        if d != 0:
            if d != last:
                flips[i] = 1
            last = int(d)
    win = max(1, round(fps))
    csum = np.concatenate([[0], np.cumsum(flips)])
    per_win = csum[win:] - csum[:-win] if n >= win else np.array([csum[-1]])
    best = int(per_win.argmax())
    worst = float(per_win[best]) / 2  # 変化 2 回（逆向きの組）= 点滅 1 回
    return worst, (best / fps if worst > 0 else None)


def _red_flashes(rgb: np.ndarray, fps: float) -> tuple[int, float | None]:
    """飽和した赤の点滅（区画の面積の 1/4 を超えて、赤と赤でない状態を行き来した組）の回数。"""
    gamma = _regions(rgb.astype(np.float32) / 255.0)  # (n, 16, 3)
    lin = _regions(_linear(rgb))
    s = gamma.sum(axis=-1)
    red = gamma[..., 0] / np.where(s <= 1e-6, 1e-6, s) >= RED_RATIO
    uv = _uv(lin)
    n, cells = red.shape
    state = red[0].copy()
    state_uv = uv[0].copy()
    events = []
    for i in range(1, n):
        changed = (red[i] != state) & (np.linalg.norm(uv[i] - state_uv, axis=-1) > RED_UV)
        if changed.sum() / cells > AREA:
            events.append(i)
        state = np.where(changed, red[i], state)
        state_uv = np.where(changed[:, None], uv[i], state_uv)
    pairs = len(events) // 2
    return pairs, (events[0] / fps if pairs else None)


def count_flashes(rgb: np.ndarray, fps: float, step: float = STEP) -> FlashResult:
    """rgb: (フレーム, 高さ, 幅, 3) の sRGB（0〜255）。高さ・幅は 4 の倍数。"""
    n = rgb.shape[0]
    if n < 3:
        return FlashResult(True, 0, None, n)
    lum = _regions(relative_luminance(rgb))
    worst, at = _flashes_per_window(_screen_turns(lum, step), fps)
    reds, red_at = _red_flashes(rgb, fps)
    return FlashResult(worst <= MAX_FLASHES, worst, at, n, reds, red_at)


async def check_video(ffmpeg: FFmpeg, path: Path, fps: float, kids: bool = False) -> FlashResult:
    """動画を縮めて読み、点滅を数える。"""
    raw = await ffmpeg.run_capture(
        [
            "-v",
            "error",
            "-i",
            str(path),
            "-vf",
            f"scale={READ_W}:{READ_H}:flags=area,format=rgb24",
            "-f",
            "rawvideo",
            "-",
        ],
        timeout=600,
    )
    frame = READ_W * READ_H * 3
    n = len(raw) // frame
    arr = np.frombuffer(raw[: n * frame], dtype=np.uint8).reshape(n, READ_H, READ_W, 3)
    return count_flashes(arr, fps, KIDS_STEP if kids else STEP)
