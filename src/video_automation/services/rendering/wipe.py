"""統合版 P6: ゲーム実況のワイプ（顔の映像を、録画の HUD を避けた隅に置く）。

- ワイプは画面の面積の 18%（画面と同じ縦横比なので、幅・高さとも √0.18 ≒ 0.424 倍）、
  画面の上下左右 80px（1080p）の内側の隅に置く。入り・出は 12f
- 隅は、録画の 60 フレームで変わらない領域（HUD）との重なりが最も少ない所。HUD は
  「60 フレームを通して明るさがほとんど変わらず（標準偏差 3 未満）、模様のある（平均の画の
  勾配が 12 以上）画素」とみなす。変わらない無地の背景は HUD に数えない
"""

from __future__ import annotations

import numpy as np

AREA = 0.18
SIDE = AREA**0.5  # 画面の幅・高さに対する比
MARGIN = 80 / 1080  # 画面の高さに対する比
FADE = 12  # 入り・出（f）
SAMPLE_W, SAMPLE_H, SAMPLES = 160, 90, 60
CORNERS = ("bottom_right", "bottom_left", "top_right", "top_left")  # 同点なら前を選ぶ
BORDER = 6 / 1080  # 白の縁（画面の高さに対する比）


def hud_mask(frames: np.ndarray) -> np.ndarray:
    """frames: (n, h, w) の明るさ 0〜255 → HUD らしい画素の真偽の (h, w)。"""
    f = frames.astype(np.float32)
    still = f.std(axis=0) < 3.0
    mean = f.mean(axis=0)
    gx = np.abs(np.diff(mean, axis=1, append=mean[:, -1:]))  # 隣の画素との差（細い線・文字も拾う）
    gy = np.abs(np.diff(mean, axis=0, append=mean[-1:, :]))
    detail = np.maximum(gx, gy) > 12.0
    return still & detail


def box(corner: str, w: int, h: int) -> tuple[int, int, int, int]:
    """隅 → ワイプの枠 (x0, y0, x1, y1)（画素）。"""
    bw, bh = round(w * SIDE), round(h * SIDE)
    m = round(h * MARGIN)
    x0 = w - m - bw if corner.endswith("right") else m
    y0 = h - m - bh if corner.startswith("bottom") else m
    return x0, y0, x0 + bw, y0 + bh


def pick_corner(
    mask: np.ndarray, allowed: tuple[str, ...] = CORNERS
) -> tuple[str, dict[str, float]]:
    """HUD との重なり（枠の中の HUD の画素の割合）が最も少ない隅と、隅ごとの割合。"""
    h, w = mask.shape
    scores = {}
    for c in CORNERS:
        if c not in allowed:
            continue
        x0, y0, x1, y1 = box(c, w, h)
        scores[c] = float(mask[y0:y1, x0:x1].mean()) if x1 > x0 and y1 > y0 else 1.0
    best = min(scores, key=lambda c: (round(scores[c], 3), CORNERS.index(c)))
    return best, scores


def frames_from_raw(raw: bytes) -> np.ndarray:
    """ffmpeg の gray の rawvideo（SAMPLE_W × SAMPLE_H）→ (n, h, w)。"""
    n = len(raw) // (SAMPLE_W * SAMPLE_H)
    return np.frombuffer(raw[: n * SAMPLE_W * SAMPLE_H], np.uint8).reshape(n, SAMPLE_H, SAMPLE_W)


SHRINK = 0.5  # カード・図が出ている間のワイプの大きさ（元の大きさに対する比）
SHRINK_FRAMES = 8  # 縮む・戻る動き（f）
SHRINK_GAP = 20  # これより短い間で続くカード・図は 1 つの区間にまとめる（f）


def busy_spans(spans: list[tuple[int, int]], gap: int = SHRINK_GAP) -> list[tuple[int, int]]:
    """カード・図の出ている区間（フレーム）を、短い間をつないでまとめる。"""
    out: list[tuple[int, int]] = []
    for a, b in sorted(spans):
        if out and a - out[-1][1] < gap:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def shrink_keys(corner: str, w: int, h: int, spans: list[tuple[int, int]],
                length: int) -> list[tuple[int, float, float, float]]:  # fmt: skip
    """カード・図が出ている間は、ワイプを隅に寄せたまま SHRINK 倍に縮める（重ならないように）。

    戻り値は (クリップの中のフレーム, 大きさの倍率, x のずれ, y のずれ) の列。キーフレームの
    scale は置いた大きさへの倍率、x・y は置いた位置からのずれ（画面比）で、拡大縮小は中心で
    かかるので、縮めた分だけ中心を隅へ寄せる。
    PC の確認で、画面の 18% のワイプが真ん中のカードの右上に重なった。"""
    x0, y0, x1, y1 = box(corner, w, h)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    sw, sh = (x1 - x0) * SHRINK, (y1 - y0) * SHRINK
    sx = x1 - sw / 2 if corner.endswith("right") else x0 + sw / 2
    sy = y0 + sh / 2 if corner.startswith("top") else y1 - sh / 2
    full = (1.0, 0.0, 0.0)
    small = (SHRINK, (sx - cx) / w, (sy - cy) / h)
    n = SHRINK_FRAMES
    keys: list[tuple[int, float, float, float]] = []
    for a, b in busy_spans(spans):
        a0, b1 = max(0, a - n), min(length - 1, b + n)
        if b1 - a0 < 2 * n + 1:
            continue
        if keys and a0 <= keys[-1][0]:
            continue
        keys += [(a0, *full), (a0 + n, *small), (b1 - n, *small), (b1, *full)]
    return keys
