"""文章の指示だけで動画を作る（Blender のアドオン「つくる」画面から使う）。"""

from typing import Literal

from pydantic import BaseModel, Field

from video_automation.schemas.timeline import RichLevel


class BuildRequest(BaseModel):
    """作りたいものの文章と、分かっていれば少しの希望（無ければ用途から自動で決める）。"""

    instruction: str = Field(min_length=1, max_length=2000)
    duration_sec: int | None = Field(default=None, ge=15, le=1800)
    orientation: Literal["auto", "landscape", "vertical"] = "auto"
    # 話し手（VOICEVOX の話者名）。空なら用途から決める
    characters: list[str] = Field(default_factory=list, max_length=4)
    reference: str = Field(default="", max_length=20000)  # 参考資料（要点）


class BuildCharacter(BaseModel):
    name: str
    role: str
    voice_style_id: int | None = None
    tachie_pack: str | None = None


class ReadinessItem(BaseModel):
    key: str  # voice / llm / ffmpeg / blender
    label: str
    ok: bool
    required: bool
    detail: str = ""


class BuildPlan(BaseModel):
    """構成の確認で見せる内容。ここで 1 回承認すると、最後まで続けて作る。"""

    project_id: str
    script_id: str
    title: str
    purpose: str
    purpose_label: str
    purpose_uncertain: bool
    width: int
    height: int
    duration_sec: int
    characters: list[BuildCharacter]
    steps: list[str]
    notes: list[str] = Field(default_factory=list)
    readiness: list[ReadinessItem] = Field(default_factory=list)
    can_run: bool = True


class BuildRunRequest(BaseModel):
    script_id: str
    rich_direction: RichLevel = RichLevel.AUTO
    use_llm_direction: bool = True
