"""演出プラン（台本の各発話に合わせて、図・カード・テロップをどう出すか）。"""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

_HEX = r"^#[0-9A-Fa-f]{6}$"


class ShotKind(StrEnum):
    CARD = "card"  # 見出し＋要点のカード
    CHART = "chart"  # 数値の棒グラフ（台本に出てくる数値だけ）
    KEYWORD = "keyword"  # 画面上部の短いテロップ（語・数値）
    IMAGE = "image"  # 画像（素材を選んでから配置。query は検索の候補）


MAIN_KINDS = (ShotKind.CARD, ShotKind.CHART, ShotKind.IMAGE)


class Shot(BaseModel):
    kind: ShotKind
    from_line: int = Field(ge=1)  # 台本の発話番号（1 始まり）
    to_line: int | None = Field(default=None, ge=1)  # ここまで（含む）。None は from_line
    heading: str = Field(default="", max_length=40)
    body: list[str] = Field(default_factory=list, max_length=6)
    style: Literal["info", "point", "summary"] = "point"
    labels: list[str] = Field(default_factory=list, max_length=6)
    values: list[float] = Field(default_factory=list, max_length=6)
    unit: str = Field(default="", max_length=10)
    highlight: int | None = Field(default=None, ge=0, le=5)
    text: str = Field(default="", max_length=24)  # keyword
    query: str = Field(default="", max_length=100)  # image の検索語
    asset_id: str | None = None  # image に使う素材（プロジェクトの素材）


class DirectionPlan(BaseModel):
    title: str = Field(default="", max_length=60)  # 冒頭のタイトル
    subtitle: str = Field(default="", max_length=60)  # タイトルの下の一言
    accent_color: str = Field(default="#4FC3F7", pattern=_HEX)
    background: Literal["navy", "charcoal", "teal", "plum"] = "navy"
    shots: list[Shot] = Field(default_factory=list, max_length=60)
    notes: list[str] = Field(default_factory=list)  # 整えたときに直した点・外した演出


class DirectionPlanRequest(BaseModel):
    script_id: str
    model_key: str | None = None
    use_llm: bool = True  # False は規則だけ（タイトルと数値のテロップ）
    instruction: str = Field(default="", max_length=2000)  # 演出への希望（「カードを減らして」等）


class DirectionPlanResult(BaseModel):
    plan: DirectionPlan
    model_key: str | None = None
    lines: int
    skills: list[str] = Field(default_factory=list)  # 参考にしたスキルの節（スキル / 見出し）


class DirectionApplyRequest(BaseModel):
    script_id: str
    plan: DirectionPlan
    # カード・テロップ・題名の文字の大きさの倍率（手直しの「文字を大きく」等）
    text_scale: float = Field(default=1.0, ge=0.7, le=1.5)


class DirectionApplyResult(BaseModel):
    command_id: str
    status: str
    clips: int
    skipped: list[str] = Field(default_factory=list)
