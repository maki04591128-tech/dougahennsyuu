"""アニメーション定義形式（P7-01）。

クリップごとに「キーフレーム」と「入りのシーン切替（トランジション）」を持つ。
- キーフレームの frame はクリップ先頭からの相対フレーム（タイムライン位置が動いても崩れない）
- 各プロパティは指定したキーフレームの間を easing で補間し、範囲外は端の値を保つ
- カメラ（zoom / pan_x / pan_y）は素材の中での拡大・移動（Ken Burns 等）
- 要素（scale / x / y / rotation / opacity）は画面上での素材そのものの動き
プリセットは秒単位（先頭から at / 末尾から from_end）で定義し、適用時にクリップ長でフレームに直す。
"""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class Easing(StrEnum):
    LINEAR = "linear"
    EASE_IN = "ease_in"
    EASE_OUT = "ease_out"
    EASE_IN_OUT = "ease_in_out"
    BACK_OUT = "back_out"  # 少し行き過ぎて戻る（ポップ）
    HOLD = "hold"  # 次のキーフレームまで値を変えない


# プロパティ名 → (既定値, 最小, 最大)
PROPS: dict[str, tuple[float, float, float]] = {
    "scale": (1.0, 0.01, 10.0),
    "x": (0.0, -3.0, 3.0),  # 画面幅に対するオフセット
    "y": (0.0, -3.0, 3.0),  # 画面高さに対するオフセット（+ が下）
    "rotation": (0.0, -3600.0, 3600.0),  # 度（時計回り）
    "opacity": (1.0, 0.0, 1.0),
    "zoom": (1.0, 1.0, 10.0),  # カメラの拡大率（1=素材全体）
    "pan_x": (0.0, -1.0, 1.0),  # -1=左端 / 1=右端（拡大で生じた余白の範囲内）
    "pan_y": (0.0, -1.0, 1.0),  # -1=上端 / 1=下端
}
CAMERA_PROPS = ("zoom", "pan_x", "pan_y")
ELEMENT_PROPS = ("scale", "x", "y", "rotation", "opacity")


class _Values(BaseModel):
    easing: Easing = Easing.EASE_IN_OUT  # このキーフレームから次までの補間
    scale: float | None = Field(default=None, ge=0.01, le=10)
    x: float | None = Field(default=None, ge=-3, le=3)
    y: float | None = Field(default=None, ge=-3, le=3)
    rotation: float | None = Field(default=None, ge=-3600, le=3600)
    opacity: float | None = Field(default=None, ge=0, le=1)
    zoom: float | None = Field(default=None, ge=1, le=10)
    pan_x: float | None = Field(default=None, ge=-1, le=1)
    pan_y: float | None = Field(default=None, ge=-1, le=1)

    def values(self) -> dict[str, float]:
        return {k: v for k in PROPS if (v := getattr(self, k)) is not None}


class Keyframe(_Values):
    frame: int = Field(ge=0, le=10_000_000)


class TransitionType(StrEnum):
    DISSOLVE = "dissolve"  # 前のクリップと重ねて溶け込む
    DIP_TO_BLACK = "dip_to_black"  # 黒（下のトラック）を経由
    SLIDE_LEFT = "slide_left"  # 右から入って左へ（前のクリップに重なる）
    SLIDE_RIGHT = "slide_right"
    SLIDE_UP = "slide_up"
    SLIDE_DOWN = "slide_down"
    WIPE_LEFT = "wipe_left"  # 左から右へ拭うように現れる
    WIPE_RIGHT = "wipe_right"
    ZOOM_IN = "zoom_in"  # 少し大きい状態から縮みながら現れる


class Transition(BaseModel):
    type: TransitionType = TransitionType.DISSOLVE
    duration_frames: int = Field(default=15, ge=1, le=600)


class ClipAnimation(BaseModel):
    keyframes: list[Keyframe] = Field(default_factory=list, max_length=200)
    transition_in: Transition | None = None
    preset: str | None = Field(default=None, max_length=100)  # 適用したプリセット名（表示用）

    @model_validator(mode="after")
    def _sorted(self) -> "ClipAnimation":
        frames = [k.frame for k in self.keyframes]
        if frames != sorted(set(frames)):
            raise ValueError("keyframes must have strictly increasing frames")
        return self

    def track(self, prop: str) -> list[tuple[int, float, Easing]]:
        """そのプロパティを持つキーフレームだけを (frame, value, easing) で返す。"""
        return [
            (k.frame, v, k.easing) for k in self.keyframes if (v := getattr(k, prop)) is not None
        ]

    def is_empty(self) -> bool:
        return not self.keyframes and self.transition_in is None


# ---------------- プリセット（P7-06） ----------------


class PresetKind(StrEnum):
    CAMERA = "camera"  # 素材内のズーム・パン
    ELEMENT = "element"  # 登場・退場・動き
    TRANSITION = "transition"
    TEXT = "text"  # Blender のタイトル文字アニメーション


class PresetKeyframe(_Values):
    at: float | None = Field(default=None, ge=0, le=3600)  # 先頭からの秒
    from_end: float | None = Field(default=None, ge=0, le=3600)  # 末尾からの秒

    @model_validator(mode="after")
    def _one(self) -> "PresetKeyframe":
        if (self.at is None) == (self.from_end is None):
            raise ValueError("specify exactly one of at / from_end")
        return self


class MotionPreset(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9_\-]{1,60}$")
    kind: PresetKind
    label: str = Field(max_length=100)
    description: str = Field(default="", max_length=500)
    keyframes: list[PresetKeyframe] = Field(default_factory=list, max_length=200)
    transition: Transition | None = None  # kind=transition の既定値
    # 生成型（ゆらぎ・揺れ等、長さに応じて繰り返すもの）。組み込みのみ
    generator: Literal["float", "shake"] | None = None
    builtin: bool = False


class MotionPresetCreate(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9_\-]{1,60}$")
    kind: Literal["camera", "element"]
    label: str = Field(max_length=100)
    description: str = Field(default="", max_length=500)
    keyframes: list[PresetKeyframe] = Field(min_length=1, max_length=200)


class SaveClipAsPreset(BaseModel):
    """クリップに付けたアニメーションを、秒単位のプリセットとして保存する。"""

    clip_id: str
    name: str = Field(pattern=r"^[a-z0-9_\-]{1,60}$")
    label: str = Field(max_length=100)
    kind: Literal["camera", "element"] = "element"


# ---------------- インフォグラフィック・シーン（mcp_visual） ----------------

_HEX = r"^#[0-9A-Fa-f]{6}$"


class InfographicRequest(BaseModel):
    """Blender で簡単な図表アニメーション（横棒グラフ・数値のカウントアップ）を作る。"""

    project_id: str
    kind: Literal["bar", "counter"] = "bar"
    title: str = Field(default="", max_length=60)
    labels: list[str] = Field(default_factory=list, max_length=8)
    values: list[float] = Field(min_length=1, max_length=8)
    unit: str = Field(default="", max_length=10)
    decimals: int = Field(default=0, ge=0, le=3)
    duration_sec: float = Field(default=4.0, gt=0, le=30)
    animation_sec: float = Field(default=1.5, gt=0, le=10)
    text_color: str = Field(default="#FFFFFF", pattern=_HEX)
    colors: list[str] = Field(
        default_factory=lambda: ["#3399FF", "#FF9933", "#33CC66", "#CC66FF"],
        min_length=1,
        max_length=8,
    )
    box: bool = True
    box_color: str = Field(default="#000000", pattern=_HEX)
    box_opacity: float = Field(default=0.45, ge=0, le=1)
    fade_out_sec: float = Field(default=0.3, ge=0, le=5)
    # 強調する項目（その棒だけ colors[0]、他は muted_color）。None は色分け
    highlight: int | None = Field(default=None, ge=0, le=7)
    muted_color: str = Field(default="#6F829E", pattern=_HEX)
    font: str | None = Field(default=None, max_length=60)  # 書体のファミリー名（用途の型）

    @model_validator(mode="after")
    def _labels(self) -> "InfographicRequest":
        if self.kind == "bar" and self.labels and len(self.labels) != len(self.values):
            raise ValueError("labels and values must have the same length")
        return self


class SceneRequest(BaseModel):
    """背景（色または素材）＋タイトル＋カメラ演出＋入りの切替を、1つのシーンとしてまとめて配置する。"""

    start_frame: int = Field(ge=0)
    duration_sec: float = Field(gt=0, le=600)
    background_asset_id: str | None = None
    background_color: str = Field(default="#101820", pattern=_HEX)
    camera_preset: str | None = Field(default=None, max_length=60)
    title: str = Field(default="", max_length=100)
    title_animation: Literal["pop", "slide", "zoom", "typewriter", "bounce", "fade", "none"] = "pop"
    title_position: Literal["center", "lower_third", "top"] = "center"
    transition: Transition | None = None
    origin: Literal["user", "llm"] = "user"


class CardRequest(BaseModel):
    """情報カード（見出し＋本文の静止画、透過 PNG）。FFmpeg で作るので速い。

    style: info=見出し＋本文 / point=要点（本文に「・」）/ summary=まとめ（見出しを強調）
           / keyword=短い語や数値を大きく（テロップ）"""

    project_id: str
    heading: str = Field(default="", max_length=40)
    body: list[str] = Field(default_factory=list, max_length=6)
    style: Literal["info", "point", "summary", "keyword"] = "info"
    accent_color: str = Field(default="#4FC3F7", pattern=_HEX)
    panel_color: str = Field(default="#0F1B2D", pattern=_HEX)
    panel_opacity: float = Field(default=0.88, ge=0, le=1)
    text_color: str = Field(default="#FFFFFF", pattern=_HEX)
    width_ratio: float = Field(default=0.62, ge=0.15, le=0.95)  # 画面幅に対する幅
    scale: float = Field(default=1.0, ge=0.5, le=2.0)  # 文字の大きさの倍率
    # 本文を先頭から何項目まで描くか（順に出す演出用）。None は全部。大きさは全部のときと同じ
    reveal: int | None = Field(default=None, ge=0, le=6)
    # 見出しの左に添える絵記号（pictogram.ICONS の名前）。None は付けない
    icon: str | None = Field(default=None, max_length=20)
    # 書体（ファミリー名。用途の型の見出し書体）。None は既定の書体
    font: str | None = Field(default=None, max_length=60)

    @field_validator("body")
    @classmethod
    def _body(cls, v: list[str]) -> list[str]:
        out = [x.strip() for x in v if x and x.strip()]
        if any(len(x) > 80 for x in out):
            raise ValueError("each body line must be at most 80 characters")
        return out

    @model_validator(mode="after")
    def _some_text(self) -> "CardRequest":
        if not self.heading.strip() and not self.body:
            raise ValueError("heading or body is required")
        return self
