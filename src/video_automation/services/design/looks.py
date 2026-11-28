"""見た目の型（統合版 段階5d: Y1〜Y10）。利用者が設定で 1 つ選ぶ（既定は「なし」）。

型ごとに、配色・題名の書体・背景（R）・仕上げ（書き出しの最後に掛けるフィルター）・コマ打ちを替える。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from video_automation.schemas.design import DesignProfile, Purpose
from video_automation.services.rendering.font_catalog import LOOK_FONTS


@dataclass(frozen=True)
class Look:
    name: str
    colors: dict[str, Any] = field(default_factory=dict)
    background: str | None = None  # R1〜R18
    heading_font: str | None = None
    finish: str | None = None  # finish.look_filter の名前
    stepped: int = 0


LOOKS: dict[str, Look] = {
    "Y1": Look("黒板", {"base": "#2F4A3C", "base2": "#2F4A3C", "surface": "#2F4A3C",
                        "text": "#F4F1E6", "main": "#F4F1E6", "accent": "#FFD966"}, "R10"),
    "Y2": Look("青焼き", {"base": "#1F4E8C", "base2": "#1F4E8C", "surface": "#1F4E8C",
                          "text": "#FFFFFF", "main": "#FFFFFF", "accent": "#FFE27A"}, "R9"),
    "Y3": Look("紙の切り絵", {}, "R10", stepped=2),
    "Y4": Look("リソグラフ", {"surface": "#F4EFE4",
                              "chart": ["#FF4F5E", "#2E5BFF", "#FFC82E"]}, None, finish="riso"),
    "Y5": Look("シンセウェーブ", {"accent": "#FF4FA0"}, "R11", finish="synth"),
    "Y6": Look("ピクセルアート", {}, None, finish="pixel", stepped=2),
    "Y7": Look("古い記録映像", {}, None, finish="film"),
    "Y8": Look("ガラス", {}, "R16"),
    "Y9": Look("ハーフトーンのコミック", {}, "R12"),
    "Y10": Look("墨と和紙", {"base": "#F2ECDD", "base2": "#F2ECDD", "surface": "#F2ECDD",
                            "text": "#1B1B1B", "main": "#1B1B1B", "accent": "#B3312C"}, "R10",
                heading_font="Zen Old Mincho"),
}  # fmt: skip

# 用途 → 選べる見た目の型（統合版 5d の「用途 → 5d の部品」の表）
LOOK_PURPOSES: dict[Purpose, tuple[str, ...]] = {
    Purpose.DOCUMENTARY: ("Y7",),
    Purpose.STORY: ("Y3", "Y10"),
    Purpose.LECTURE: ("Y1",),
    Purpose.TUTORIAL: ("Y2",),
    Purpose.GAME: ("Y5", "Y6"),
    Purpose.RANKING: ("Y9",),
    Purpose.PRODUCT: ("Y8",),
    Purpose.PROMO: ("Y8",),
    Purpose.EVENT: ("Y4",),
    Purpose.KIDS: ("Y3",),
}


def allowed(purpose: Purpose, look: str | None) -> bool:
    return bool(look) and look in LOOK_PURPOSES.get(purpose, ())


def apply_look(p: DesignProfile, look: str | None) -> DesignProfile:
    """選べる見た目の型なら、配色・書体・コマ打ちを替えた型を返す（選べなければそのまま）。"""
    if not look or not allowed(p.purpose, look):
        return p
    lk = LOOKS[look]
    pal = p.palette.model_copy(update=dict(lk.colors))
    typo = p.typography
    head = lk.heading_font or LOOK_FONTS.get(look)  # 指定の無い型は、集めた自由な書体
    if head:
        typo = typo.model_copy(update={"heading_font": head})
    motion = p.motion
    if lk.stepped and not motion.stepped:
        motion = motion.model_copy(update={"stepped": lk.stepped})
    return p.model_copy(update={"palette": pal, "typography": typo, "motion": motion, "look": look})
