"""画面録画のカメラ（ズーム）の計画。

上手な画面録画の編集と同じく、カメラは「ポーズからポーズへ」動かす。
1. 見せたい所（クリック・画面が変わった範囲）を時刻つきで集める
2. 近い所・短い間隔のものはまとめ、1 つのショット（中心と倍率）にする。
   何も起きない時間が続いたら全体に引く
3. ショットの間は止め、切り替えは 0.45〜0.8 秒のイーズ（加速→減速）で移る。
   移る前に少し早く動き始め（クリックの 0.6 秒前）、視線が追いつくようにする
細かい揺れ（カーソルを追い続ける、毎フレーム寄る先が変わる）は作らない。
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class Focus:
    t: float  # 秒
    x: float  # 録画の画素
    y: float
    w: float = 0.0  # 範囲（クリックは 0）
    h: float = 0.0
    kind: str = "click"  # click / change


@dataclass
class Shot:
    t: float  # このショットに着く時刻
    cx: float
    cy: float
    zoom: float  # 1.0 = 全体
    until: float = 0.0  # 寄ったまま保つ終わりの時刻（計画の途中で使う）


@dataclass
class CameraParams:
    zoom: float = 1.8  # クリックに寄るときの倍率
    max_zoom: float = 2.2
    lead: float = 0.6  # クリックの何秒前から動くか
    hold: float = 2.4  # クリックの後に寄ったまま保つ秒
    min_shot: float = 1.2  # 1 つのショットの最短の秒
    idle: float = 2.8  # 何も起きない時間がこれを超えたら全体に引く
    merge_view: float = 0.18  # 画面のこの割合より近い寄る先は同じショットにする
    intro: float = 1.0  # 最初はこの秒だけ全体を見せる
    move_min: float = 0.45
    move_max: float = 0.8


def _view(size: tuple[int, int], zoom: float) -> tuple[float, float]:
    return size[0] / zoom, size[1] / zoom


def clamp_center(cx: float, cy: float, zoom: float, size: tuple[int, int]) -> tuple[float, float]:
    vw, vh = _view(size, zoom)
    w, h = size
    cx = w / 2 if vw >= w else min(max(cx, vw / 2), w - vw / 2)
    cy = h / 2 if vh >= h else min(max(cy, vh / 2), h - vh / 2)
    return cx, cy


def plan_shots(
    focus: list[Focus], duration: float, size: tuple[int, int], p: CameraParams | None = None
) -> list[Shot]:
    """見せたい所の一覧からショットの並びを作る（最初と、引くときは全体）。"""
    p = p or CameraParams()
    w, h = size
    full = Shot(0.0, w / 2, h / 2, 1.0)
    shots = [full]
    # 寄る先の候補: (動き始める時刻, 保つ終わり, 中心, 倍率)
    cands: list[tuple[float, float, float, float, float]] = []
    for f in sorted(focus, key=lambda x: x.t):
        if f.kind == "click":
            z = p.zoom
            t0, t1 = f.t - p.lead, f.t + p.hold
        else:
            bw, bh = max(f.w, w * 0.12), max(f.h, h * 0.12)
            z = min(p.max_zoom, max(1.0, min(w / (bw * 1.35), h / (bh * 1.35))))
            t0, t1 = f.t - 0.2, f.t + p.hold
        if z < 1.15:  # ほとんど全体なら寄らない（全体を見せる）
            z = 1.0
        cands.append((max(p.intro, t0), t1, f.x, f.y, z))
    for t0, t1, x, y, z in cands:
        last = shots[-1]
        cx, cy = clamp_center(x, y, z, size)
        vw, _ = _view(size, max(z, last.zoom))
        near = math.hypot(cx - last.cx, cy - last.cy) < vw * p.merge_view
        if near and abs(z - last.zoom) < 0.25 and last.zoom > 1.0:
            # 同じ所: ショットを延ばす（前のショットの保つ時間に含める）
            last.until = max(last.until, t1)
            continue
        prev_end = last.until or last.t + p.min_shot
        start = max(t0, last.t + p.min_shot)
        if start - prev_end > p.idle and last.zoom > 1.0:
            # 間が空いた: 一度全体に引く
            back = Shot(prev_end + 0.3, w / 2, h / 2, 1.0)
            shots.append(back)
            start = max(start, back.t + p.min_shot * 0.8)
        shots.append(Shot(start, cx, cy, z, until=t1))
    last = shots[-1]
    end = last.until or last.t
    if last.zoom > 1.0 and end + 0.3 < duration - 0.5:
        shots.append(Shot(end + 0.3, w / 2, h / 2, 1.0))
    return shots


def _ease(u: float) -> float:
    u = min(1.0, max(0.0, u))
    return 4 * u**3 if u < 0.5 else 1 - (-2 * u + 2) ** 3 / 2  # 加速→減速（cubic in-out）


def camera_at(
    shots: list[Shot], t: float, size: tuple[int, int], p: CameraParams | None = None
) -> tuple[float, float, float]:
    """時刻 t のカメラ（中心 x, y と倍率）。ショットの間はイーズで移る。"""
    p = p or CameraParams()
    prev = shots[0]
    for s in shots[1:]:
        dist = math.hypot(s.cx - prev.cx, s.cy - prev.cy) / max(size) + abs(
            math.log(s.zoom / prev.zoom)
        )
        dur = min(p.move_max, max(p.move_min, 0.35 + 0.6 * dist))
        if t < s.t:
            break
        if t < s.t + dur:
            u = _ease((t - s.t) / dur)
            # 倍率は対数で補間（寄り・引きの速さが均一に見える）
            z = math.exp(math.log(prev.zoom) + (math.log(s.zoom) - math.log(prev.zoom)) * u)
            cx = prev.cx + (s.cx - prev.cx) * u
            cy = prev.cy + (s.cy - prev.cy) * u
            return (*clamp_center(cx, cy, z, size), z)
        prev = s
    return (*clamp_center(prev.cx, prev.cy, prev.zoom, size), prev.zoom)


def change_regions(
    frames: list[tuple[float, list[list[int]]]],
    scale: float,
    lag: int = 2,
    thr: int = 18,
    min_frac: float = 0.004,
    max_frac: float = 0.5,
) -> list[Focus]:
    """縮小した灰色のフレーム列から、画面が変わった範囲（見せたい所）を探す。

    frames: (秒, 2 次元の画素の配列)。scale は縮小率（録画の画素 = 配列の座標 / scale）。"""
    import numpy as np

    out: list[Focus] = []
    for i in range(lag, len(frames)):
        t, a = frames[i]
        b = frames[i - lag][1]
        d = np.abs(np.asarray(a, np.int16) - np.asarray(b, np.int16)) > thr
        frac = float(d.mean())
        if not (min_frac < frac < max_frac):
            continue
        ys, xs = np.nonzero(d)
        x0, x1 = np.percentile(xs, [3, 97]) / scale
        y0, y1 = np.percentile(ys, [3, 97]) / scale
        out.append(Focus(t, (x0 + x1) / 2, (y0 + y1) / 2, x1 - x0, y1 - y0, kind="change"))
    # 続けて変わっている所（文字が流れる等）は最初の 1 つにまとめる
    merged: list[Focus] = []
    for f in out:
        if merged:
            m = merged[-1]
            if f.t - m.t < 1.5 and math.hypot(f.x - m.x, f.y - m.y) < max(m.w, m.h, 80):
                continue
        merged.append(f)
    return merged
