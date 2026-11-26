"""図表の種類と動きの長さ（統合版 S10・D3）。

データの形から図を選ぶ（段階5d の「データの形 → 図」のうち、今ある図だけ）:
- 割合（合計 100%）で項目 2〜5 個 → ドーナツ
- 時点 5 個以上 × 値 1 つ → 線グラフ
- 100 人中の人数（値 1 つ）→ 100 点の図
- それ以外 → 横棒
"""

from __future__ import annotations

import re

from video_automation.schemas.direction import Shot

STAGGER, GROW = 3, 10  # 統合版 D3: 1 本ずつ 3f ずらし、各 10f（Blender の infographic と同じ値）
POP = 4  # 点・値が出るのにかかるフレーム
_TIME = re.compile(
    r"^\s*(?:\d{2,4}\s*年|\d{1,2}\s*月|\d{1,2}\s*日|\d{1,2}\s*時|第?\s*\d+\s*(?:期|四半期|週)|"
    r"Q\s*\d|\d{4}|(?:昭和|平成|令和)\s*\d+)"
)
_PER_HUNDRED = re.compile(r"100\s*人\s*(?:中|のうち|に)")


def is_per_hundred(values: list[float], unit: str, text: str) -> bool:
    """「100 人中 N 人」の 1 つの値か（100 点の図）。"""
    return (
        len(values) == 1
        and 0 <= values[0] <= 100
        and unit.strip() in ("人", "")
        and bool(_PER_HUNDRED.search(text))
    )


def chart_kind(shot: Shot, text: str = "") -> str:
    """図の種類（bar / line / donut / dots）。text はその図が説明している発話。"""
    v, labels = shot.values, shot.labels
    if is_per_hundred(v, shot.unit, text):
        return "dots"
    unit = shot.unit.strip()
    if unit in ("%", "％") and 2 <= len(v) <= 5 and all(x >= 0 for x in v):
        if abs(sum(v) - 100) <= 1.0:
            return "donut"
    if len(v) >= 5 and labels and all(_TIME.match(x) for x in labels):
        return "line"
    return "bar"


def anim_frames(kind: str, n: int) -> int:
    """動いている部分のフレーム数（これより後は止まった絵）。"""
    if kind == "dots":
        return STAGGER * 9 + GROW + POP
    if kind == "line":
        return STAGGER * max(0, n - 1) + GROW + POP
    return STAGGER * max(0, n - 1) + GROW
