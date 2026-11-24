"""台本・字幕スキーマ (Phase 4)。"""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_HEX = "0123456789abcdefABCDEF"


class ScriptMode(StrEnum):
    MANUAL = "MANUAL"
    FULL_AI = "FULL_AI"
    USER_ONE_SIDE = "USER_ONE_SIDE"


class ScriptCreate(BaseModel):
    project_id: str
    title: str = Field(default="", max_length=255)
    mode: ScriptMode = ScriptMode.MANUAL
    # 「話者：セリフ」形式の行は話者付きで分割する。空行はシーン区切り
    text: str = Field(min_length=1, max_length=100_000)
    default_style_id: int | None = None
    speaker_styles: dict[str, int] = Field(default_factory=dict)


class ScriptSegmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    scene_index: int
    sequence: int
    speaker: str
    text: str
    generation_source: str
    locked: bool
    voice_style_id: int | None
    audio_file_id: str | None
    start_frame: int | None
    end_frame: int | None
    reply_to_segment_id: str | None = None


class ScriptRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    title: str
    mode: str
    status: str
    version: int
    segments: list[ScriptSegmentRead] = Field(default_factory=list)


class SubtitleStyle(BaseModel):
    """ASS スタイル (P4-07)。色は #RRGGBB / #AARRGGBB（AA=不透明度）。"""

    name: str = Field(default="Default", pattern=r"^[A-Za-z0-9_\-]{1,40}$")
    font_name: str = "Meiryo"
    font_size: int | None = Field(default=None, ge=8, le=400)  # None=解像度から自動
    font_size_ratio: float = Field(default=0.062, ge=0.02, le=0.2)  # 自動のとき（短辺比）
    bold: bool = True
    primary_color: str = "#FFFFFF"
    outline_color: str = "#000000"
    back_color: str = "#80000000"
    outline: float = Field(default=3.0, ge=0, le=20)
    shadow: float = Field(default=1.0, ge=0, le=20)
    alignment: int = Field(default=2, ge=1, le=9)  # テンキー配置（2=下中央）
    margin_v_ratio: float = Field(default=0.06, ge=0, le=0.5)  # セーフエリア（高さ比）
    margin_h_ratio: float = Field(default=0.05, ge=0, le=0.5)
    fade_in_ms: int = Field(default=80, ge=0, le=2000)
    fade_out_ms: int = Field(default=80, ge=0, le=2000)
    # 話者の表示: 字幕の上に名前を小さく出し、縁取りを話者の色（暗め）にする
    speaker_label: bool = True
    speaker_outline: bool = True
    # 字幕の後ろに画面幅の半透明の帯を敷く（背景が明るくても読める）
    band: bool = True
    band_color: str = "#8C000000"
    # 帯の形: full=画面幅で下端まで / fitted=字幕の文字の幅と高さだけ（画面を覆いすぎない）
    band_mode: Literal["full", "fitted"] = "full"

    @field_validator("primary_color", "outline_color", "back_color", "band_color")
    @classmethod
    def _color(cls, v: str) -> str:
        h = v.lstrip("#")
        if len(h) not in (6, 8) or any(c not in _HEX for c in h):
            raise ValueError("color must be #RRGGBB or #AARRGGBB")
        return "#" + h.upper()


class SubtitleGenerateRequest(BaseModel):
    script_id: str
    max_chars_per_line: int | None = Field(default=None, ge=4, le=60)  # None=縦横比から自動
    max_lines: int = Field(default=2, ge=1, le=4)
    strip_period: bool = True  # 文末の「。」を表示しない


class SubtitleSyncRequest(BaseModel):
    script_id: str
    gap_sec: float = Field(default=0.3, ge=0, le=10)  # セグメント間の無音
    min_duration_sec: float = Field(default=0.6, ge=0, le=10)
    # 行の終わりの字幕を、次の字幕が出るまで（最大この秒数）残す。読み切れる時間を確保する
    hold_sec: float = Field(default=0.5, ge=0, le=5)


class SubtitleFormat(StrEnum):
    ASS = "ass"
    SRT = "srt"


class SubtitleExportRequest(BaseModel):
    script_id: str
    format: SubtitleFormat = SubtitleFormat.ASS
    style: SubtitleStyle | None = None
    speaker_colors: dict[str, str] = Field(default_factory=dict)  # 話者別の文字色


class SubtitleCue(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    index: int
    script_segment_id: str
    speaker: str
    text: str
    start_frame: int | None
    end_frame: int | None


class SubtitleExportResult(BaseModel):
    format: SubtitleFormat
    path: str
    content: str
    cue_count: int
