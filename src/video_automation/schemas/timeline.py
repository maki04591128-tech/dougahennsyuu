"""タイムライン・編集命令・レンダリングのスキーマ (Phase 5)。

時間はプロジェクト fps のフレーム整数。区間は半開区間 [start, end)。
全編集操作は共通の Command Schema（type で判別）で検証する（仕様 §3）。
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from video_automation.schemas.animation import ClipAnimation, Transition


class AssetKind(StrEnum):
    VIDEO = "video"
    IMAGE = "image"
    AUDIO = "audio"


class TrackKind(StrEnum):
    VIDEO = "video"  # 映像・画像（下の index ほど奥）
    AUDIO = "audio"


class FitMode(StrEnum):
    FIT = "fit"  # 全体を収める（余白あり）
    FILL = "fill"  # 画面を埋める（焦点位置を基準にクロップ）
    BLUR = "blur"  # 収めた上で、背景を同じ映像のぼかしで埋める（縦横変換向け）


class ClipTransform(BaseModel):
    fit: FitMode = FitMode.FILL
    focus_x: float = Field(default=0.5, ge=0, le=1)  # 主題・焦点の位置（0=左, 1=右）
    focus_y: float = Field(default=0.5, ge=0, le=1)
    scale: float = Field(default=1.0, gt=0, le=10)
    x: float = Field(default=0.0, ge=-1, le=1)  # 画面幅に対するオフセット
    y: float = Field(default=0.0, ge=-1, le=1)
    opacity: float = Field(default=1.0, ge=0, le=1)


class AssetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    kind: AssetKind
    name: str
    path: str
    sha256: str
    duration_sec: float | None
    width: int | None
    height: int | None
    fps: float | None
    has_audio: bool
    source: str
    audio_file_id: str | None = None
    created_at: datetime


class AssetImportRequest(BaseModel):
    project_id: str
    path: str = Field(min_length=1, max_length=1000)
    name: str | None = None


class TrackRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: TrackKind
    name: str
    index: int
    muted: bool


class ClipRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    track_id: str
    asset_id: str
    start_frame: int
    end_frame: int
    source_in_frame: int
    source_out_frame: int
    transform: ClipTransform
    volume: float
    fade_in_frames: int
    fade_out_frames: int
    animation: ClipAnimation | None = None


class TimelineRead(BaseModel):
    project_id: str
    width: int
    height: int
    fps_num: int
    fps_den: int
    duration_frames: int
    tracks: list[TrackRead]
    clips: list[ClipRead]
    assets: list[AssetRead]


# ---------------- 編集命令 ----------------


class AddTrack(BaseModel):
    type: Literal["add_track"] = "add_track"
    kind: TrackKind
    name: str = Field(default="", max_length=100)
    track_id: str | None = None


class AddClip(BaseModel):
    type: Literal["add_clip"] = "add_clip"
    track_id: str
    asset_id: str
    start_frame: int = Field(ge=0)
    source_in_frame: int = Field(default=0, ge=0)
    duration_frames: int | None = Field(default=None, gt=0)  # None=素材の最後まで（画像は必須）
    clip_id: str | None = None
    transform: ClipTransform = Field(default_factory=ClipTransform)
    volume: float = Field(default=1.0, ge=0, le=4)


class MoveClip(BaseModel):
    type: Literal["move_clip"] = "move_clip"
    clip_id: str
    start_frame: int = Field(ge=0)
    track_id: str | None = None


class TrimClip(BaseModel):
    """start/end を動かすと、素材側の in/out も同じだけずれる（素材の位置関係を保つ）。"""

    type: Literal["trim_clip"] = "trim_clip"
    clip_id: str
    start_frame: int | None = Field(default=None, ge=0)
    end_frame: int | None = Field(default=None, gt=0)


class SplitClip(BaseModel):
    type: Literal["split_clip"] = "split_clip"
    clip_id: str
    at_frame: int = Field(gt=0)
    new_clip_id: str | None = None


class DeleteClip(BaseModel):
    type: Literal["delete_clip"] = "delete_clip"
    clip_id: str


class SetClipProps(BaseModel):
    type: Literal["set_clip_props"] = "set_clip_props"
    clip_id: str
    volume: float | None = Field(default=None, ge=0, le=4)
    fade_in_frames: int | None = Field(default=None, ge=0)
    fade_out_frames: int | None = Field(default=None, ge=0)
    transform: ClipTransform | None = None


class SetClipAnimation(BaseModel):
    """クリップのアニメーション（P7）。いずれか1つ以上を指定する。

    - animation: キーフレームとトランジションを丸ごと置き換える
    - preset: プリセットを適用（merge=True なら他のプロパティのキーフレームは残す）
    - transition: 入りのシーン切替を設定（外すときは clear_transition）
    - clear: アニメーションをすべて外す"""

    type: Literal["set_clip_animation"] = "set_clip_animation"
    clip_id: str
    animation: ClipAnimation | None = None
    preset: str | None = Field(default=None, max_length=60)
    duration_sec: float | None = Field(default=None, gt=0, le=60)  # プリセットの登場・退場の長さ
    intensity: float = Field(default=1.0, gt=0, le=3)  # プリセットの変化量の倍率
    merge: bool = True
    transition: Transition | None = None
    clear_transition: bool = False
    clear: bool = False

    @model_validator(mode="after")
    def _something(self) -> "SetClipAnimation":
        if not (
            self.animation or self.preset or self.transition or self.clear_transition or self.clear
        ):
            raise ValueError("specify animation, preset, transition or clear")
        return self


_Cmd = (
    AddTrack
    | AddClip
    | MoveClip
    | TrimClip
    | SplitClip
    | DeleteClip
    | SetClipProps
    | SetClipAnimation
)


class Batch(BaseModel):
    type: Literal["batch"] = "batch"
    commands: list[Annotated[_Cmd, Field(discriminator="type")]] = Field(
        min_length=1, max_length=1000
    )


EditCommandBody = Annotated[_Cmd | Batch, Field(discriminator="type")]


class CommandOrigin(StrEnum):
    USER = "user"  # UI/APIから直接（ユーザー操作）
    LLM = "llm"  # MCP/Orchestrator 経由。承認が必要


class CommandRequest(BaseModel):
    command: EditCommandBody
    idempotency_key: str | None = Field(default=None, max_length=100)
    origin: CommandOrigin = CommandOrigin.USER
    description: str = Field(default="", max_length=500)


class CommandStatus(StrEnum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPLIED = "APPLIED"
    UNDONE = "UNDONE"
    DISCARDED = "DISCARDED"  # Undo 後に別の操作をしたため Redo 不可
    REJECTED = "REJECTED"


class CommandRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    sequence: int
    status: CommandStatus
    origin: CommandOrigin
    description: str
    command: dict[str, Any]
    created_at: datetime


class ValidationIssue(BaseModel):
    level: Literal["error", "warning"]
    message: str
    clip_id: str | None = None


class ValidationResult(BaseModel):
    valid: bool
    issues: list[ValidationIssue]


class FromScriptRequest(BaseModel):
    script_id: str


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    width: int | None = Field(default=None, ge=16, le=7680)
    height: int | None = Field(default=None, ge=16, le=7680)
    fps_num: int | None = Field(default=None, ge=1)
    fps_den: int | None = Field(default=None, ge=1)


# ---------------- レンダリング ----------------


class RenderProfileName(StrEnum):
    FHD_LANDSCAPE = "fhd_landscape"
    FHD_PORTRAIT = "fhd_portrait"
    UHD_LANDSCAPE = "4k_landscape"
    UHD_PORTRAIT = "4k_portrait"


class RenderProfile(BaseModel):
    name: RenderProfileName
    label: str
    width: int
    height: int
    aspect: str


RENDER_PROFILES: dict[RenderProfileName, RenderProfile] = {
    p.name: p
    for p in (
        RenderProfile(
            name=RenderProfileName.FHD_LANDSCAPE,
            label="FHD Landscape",
            width=1920,
            height=1080,
            aspect="16:9",
        ),
        RenderProfile(
            name=RenderProfileName.FHD_PORTRAIT,
            label="FHD Portrait",
            width=1080,
            height=1920,
            aspect="9:16",
        ),
        RenderProfile(
            name=RenderProfileName.UHD_LANDSCAPE,
            label="4K Landscape",
            width=3840,
            height=2160,
            aspect="16:9",
        ),
        RenderProfile(
            name=RenderProfileName.UHD_PORTRAIT,
            label="4K Portrait",
            width=2160,
            height=3840,
            aspect="9:16",
        ),
    )
}


class Encoder(StrEnum):
    AUTO = "auto"
    LIBX264 = "libx264"
    NVENC = "h264_nvenc"


class RenderRequest(BaseModel):
    project_id: str
    profile: RenderProfileName = RenderProfileName.FHD_LANDSCAPE
    script_id: str | None = None  # 字幕の元になる台本
    burn_subtitles: bool = True
    external_subtitles: bool = True  # 同じ字幕データから SRT/ASS も書き出す
    encoder: Encoder = Encoder.AUTO
    quality: int = Field(default=20, ge=10, le=40)  # CRF / CQ
    loudnorm: bool = True  # -14 LUFS（動画配信向け）
    start_frame: int | None = Field(default=None, ge=0)
    end_frame: int | None = Field(default=None, gt=0)
    origin: CommandOrigin = CommandOrigin.USER

    @model_validator(mode="after")
    def _range(self) -> "RenderRequest":
        if self.start_frame is not None and self.end_frame is not None:
            if self.end_frame <= self.start_frame:
                raise ValueError("end_frame must be greater than start_frame")
        return self


class RenderStatus(StrEnum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    QUEUED = "QUEUED"
    RENDERING = "RENDERING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class RenderJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    profile: str
    status: RenderStatus
    job_id: str | None
    output_path: str | None
    subtitle_paths: list[str] = Field(default_factory=list)
    encoder: str | None
    error: str | None
    created_at: datetime
    finished_at: datetime | None


class PreviewRequest(BaseModel):
    frame: int | None = Field(default=None, ge=0)  # 指定時は1フレームのPNG
    start_frame: int | None = Field(default=None, ge=0)
    end_frame: int | None = Field(default=None, gt=0)
    width: int = Field(default=640, ge=64, le=1920)
    script_id: str | None = None
    profile: RenderProfileName | None = None  # 縦横変換の確認用


class TitleCardRequest(BaseModel):
    """Blender で文字アニメーションのクリップを作り素材として登録する。"""

    project_id: str
    text: str = Field(min_length=1, max_length=100)
    duration_sec: float = Field(default=2.0, gt=0, le=30)
    # P7-02: 文字アニメーション（typewriter / bounce は1文字ずつ。改行を含む文は全体で動かす）
    animation: Literal["pop", "slide", "zoom", "typewriter", "bounce", "fade", "none"] = "pop"
    animation_sec: float | None = Field(default=None, gt=0, le=10)  # 動く部分の長さ（既定は種類別）
    fade_out_sec: float = Field(default=0.3, ge=0, le=5)
    text_color: str = Field(default="#FFFFFF", pattern=r"^#[0-9A-Fa-f]{6}$")
    outline: bool = True
    outline_color: str = Field(default="#000000", pattern=r"^#[0-9A-Fa-f]{6}$")
    size: float = Field(default=1.0, ge=0.2, le=2.0)
    position: Literal["center", "lower_third", "top"] = "center"
    box: bool = False  # 文字の後ろに半透明の帯
    box_color: str = Field(default="#000000", pattern=r"^#[0-9A-Fa-f]{6}$")
    box_opacity: float = Field(default=0.55, ge=0, le=1)
