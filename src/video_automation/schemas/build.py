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
    # 操作説明に使う画面録画のファイル（絶対パス・取り込みを許可したフォルダの中）。
    # 指定すると用途は「操作説明」になり、録画を中央の枠に置いて操作箇所へ寄る
    screen_recording: str = Field(default="", max_length=1000)
    # MV・リリックに使う曲のファイル（絶対パス・取り込みを許可したフォルダの中）と、
    # 分かっていれば歌詞（1 行 1 字幕。利用者が持っている歌詞を貼る）
    music: str = Field(default="", max_length=1000)
    lyrics: str = Field(default="", max_length=20000)


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


class PackChoice(BaseModel):
    id: str
    name: str


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
    packs: list[PackChoice] = Field(default_factory=list)  # 選べる立ち絵（構成の確認で変える）


class BuildChange(BaseModel):
    """構成の確認で変えたいところ（変えない項目は None）。"""

    script_id: str
    purpose: str | None = Field(default=None, max_length=40)
    duration_sec: int | None = Field(default=None, ge=15, le=1800)
    # 話し手の名前 → 立ち絵のパック id（"" は立ち絵なし）
    tachie: dict[str, str] | None = None


class BuildRunRequest(BaseModel):
    script_id: str
    # できあがった後の手直し: 直したいことの文章。keep_script=True なら台本・音声はそのままで
    # 画面の演出から作り直す（見た目だけ）。False なら台本から作り直す
    instruction: str = Field(default="", max_length=2000)
    keep_script: bool = False
    rich_direction: RichLevel = RichLevel.AUTO
    use_llm_direction: bool = True
