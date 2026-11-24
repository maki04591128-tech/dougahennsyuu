"""書き出し時の画面演出（Blender の rich_direction）で使う部品を決める。

演出の強さ（控えめ・標準・派手）ごとの部品のうち、用途の型（DesignProfile）が許すものだけを使う。
さらに型の motion_level で強さの上限を決める（字幕動画やニュースに派手な演出が付かないようにする）。
"""

from dataclasses import dataclass, field
from typing import Any

from video_automation.schemas.design import Component, DesignProfile
from video_automation.schemas.timeline import RichLevel

ORDER = [RichLevel.OFF, RichLevel.SUBTLE, RichLevel.STANDARD, RichLevel.BOLD]

# 部品 → 必要な画面部品（すべて許されているときだけ使う）
PART_COMPONENTS: dict[str, tuple[Component, ...]] = {
    "intro": (Component.KINETIC_TEXT, Component.TITLE_CARD),
    "letterbox": (Component.LETTERBOX,),
    "transitions": (Component.TRANSITION_SHAPE,),
    "sfx": (Component.SFX,),
    "chapter_labels": (Component.CHAPTER_LABEL,),
    "ken_burns": (Component.BACKGROUND_MOTION,),
    "look": (Component.BLOOM,),
    "punch": (Component.SHAKE_PUNCH,),
    "glitch": (Component.GLITCH,),
    "flash": (Component.FLASH,),
    "iris": (Component.TRANSITION_SHAPE,),
    # 字幕を動く文字（キネティックタイポグラフィ）で出す。歌詞を出す用途（MV・リリック）だけ
    "lyrics": (Component.LYRIC_TEXT, Component.KINETIC_TEXT),
}

LEVEL_PARTS: dict[RichLevel, set[str]] = {
    # 撮影処理（ブルーム・ビネット＝"look"）は派手のときだけ。図・文字の色が濁り、
    # 画面の端が暗くなるため、解説・講義などの控えめ・標準の演出では付けない
    RichLevel.SUBTLE: {"chapter_labels", "ken_burns", "lyrics"},
    RichLevel.STANDARD: {
        "chapter_labels",
        "ken_burns",
        "intro",
        "transitions",
        "sfx",
        "punch",
        "iris",
        "lyrics",
    },
    RichLevel.BOLD: set(PART_COMPONENTS),
}

LEVEL_PARAMS: dict[RichLevel, dict[str, float]] = {
    RichLevel.SUBTLE: {
        "intro_frames": 0,
        "ken_burns": 1.02,
        "bloom": 0.15,
        "diffusion": 0.05,
        "dispersion": 0.0,
        "vignette": 0.3,
        "flash_alpha": 0.0,
    },
    RichLevel.STANDARD: {
        "intro_frames": 72,
        "ken_burns": 1.035,
        "bloom": 0.3,
        "diffusion": 0.08,
        "dispersion": 0.0,
        "vignette": 0.42,
        "flash_alpha": 0.0,
    },
    RichLevel.BOLD: {
        "intro_frames": 96,
        "ken_burns": 1.045,
        "bloom": 0.35,
        "diffusion": 0.1,
        "dispersion": 0.012,
        "vignette": 0.45,
        "flash_alpha": 0.06,
    },
}


def max_level(prof: DesignProfile) -> RichLevel:
    """用途の型の動きの強さ（motion_level 0〜4）で決まる、使ってよい演出の強さの上限。"""
    if prof.motion_level <= 0:
        return RichLevel.OFF
    if prof.motion_level == 1:
        return RichLevel.SUBTLE
    if prof.motion_level == 2:
        return RichLevel.STANDARD
    return RichLevel.BOLD


@dataclass
class RichPlan:
    level: RichLevel  # 実際に使う強さ（上限で下げたもの）
    parts: dict[str, bool]
    params: dict[str, float]
    limits: dict[str, int]
    notes: list[str] = field(default_factory=list)

    @property
    def active(self) -> bool:
        return self.level != RichLevel.OFF and any(self.parts.values())

    def args(self) -> dict[str, Any]:
        return {"parts": self.parts, "params": self.params, "limits": self.limits}


def plan_rich(requested: RichLevel, prof: DesignProfile, fps: float = 30.0) -> RichPlan:
    notes: list[str] = []
    cap = max_level(prof)
    level = cap if requested == RichLevel.AUTO else requested
    if ORDER.index(level) > ORDER.index(cap):
        notes.append(f"用途「{prof.label}」では演出の強さを {cap.value} までに下げました")
        level = cap
    if level == RichLevel.OFF:
        return RichPlan(level, {}, {}, {}, notes)
    parts: dict[str, bool] = {}
    for name in sorted(LEVEL_PARTS[level]):
        need = PART_COMPONENTS[name]
        ok = all(prof.allows(c) for c in need)
        parts[name] = ok
        if not ok:
            notes.append(f"用途の型で使わない部品のため外しました: {name}")
    params = dict(LEVEL_PARAMS[level])
    params["intro_frames"] = round(params["intro_frames"] * fps / 30)  # 秒で同じ長さにする
    if not parts.get("glitch"):
        params["dispersion"] = 0.0  # 色収差は GLITCH の一種
    if not parts.get("flash"):
        params["flash_alpha"] = 0.0
    limits = {
        "punch": prof.allowance(Component.SHAKE_PUNCH),
        "chapter_labels": prof.allowance(Component.CHAPTER_LABEL),
    }
    return RichPlan(level, parts, params, limits, notes)
