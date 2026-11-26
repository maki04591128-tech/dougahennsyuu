"""対話型台本作成モード（仕様 §17）のスキーマ。

- FULL_AI: テーマ・キャラクター設定から AI が全キャラクターの台本を作る
- USER_ONE_SIDE: ユーザーが片方のキャラクターのセリフを入力し、相手役を AI が作る
- MANUAL: 「話者：セリフ」形式のテキストから作る（Phase 4）
発話ターンは ScriptSegment（DialogueTurn を兼ねる）。generation_source は USER / AI / EDITED。
"""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from video_automation.schemas.subtitle import ScriptMode


class GenerationSource(StrEnum):
    USER = "USER"
    AI = "AI"
    EDITED = "EDITED"  # AI 生成後にユーザーが手で直した


class VoiceSettings(BaseModel):
    """キャラクターの音声設定（voice_settings）。"""

    style_id: int | None = None
    speed_scale: float | None = Field(default=None, ge=0.5, le=2.0)
    pitch_scale: float | None = Field(default=None, ge=-0.15, le=0.15)
    intonation_scale: float | None = Field(default=None, ge=0.0, le=2.0)
    volume_scale: float | None = Field(default=None, ge=0.0, le=2.0)
    # 抑揚づけの強さ（0=エンジンのまま、1=標準、2=強め）。None は設定の既定値
    expressiveness: float | None = Field(default=None, ge=0.0, le=2.0)


class CharacterIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    role: str = Field(default="", max_length=200)
    # 統合版 P1: 名前帯の 2 行目に出す肩書き（話者の設定から取り、LLM には作らせない）
    job_title: str = Field(default="", max_length=100)
    personality: str = Field(default="", max_length=500)
    speaking_style: str = Field(default="", max_length=500)
    is_user: bool = False  # USER_ONE_SIDE でユーザーが担当するキャラクター
    voice: VoiceSettings = Field(default_factory=VoiceSettings)
    # 字幕の縁取り・名前の色（#RRGGBB）。None は自動で割り当てる
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    # 立ち絵のパック（GET /characters/packs の id）。None は立ち絵なし
    tachie_pack: str | None = Field(default=None, max_length=64)
    # 字幕の書体（話者を書体で区別する）。None は自動で割り当てる
    subtitle_font: str | None = Field(default=None, max_length=100)


class CharacterRead(CharacterIn):
    id: str


class ResponsePolicy(StrEnum):
    SUPPLEMENT = "補足"
    QUESTION = "質問"
    TSUKKOMI = "ツッコミ"
    EXPLAIN = "解説"
    FREE = "おまかせ"


class ScriptSettings(BaseModel):
    topic: str = Field(default="", max_length=500)
    purpose: str = Field(default="", max_length=500)
    audience: str = Field(default="", max_length=300)
    target_duration_sec: int | None = Field(default=None, ge=10, le=7200)
    structure: str = Field(default="", max_length=1000)  # 構成の希望（導入・本題・まとめ等）
    tempo: str = Field(default="", max_length=100)
    must_keywords: list[str] = Field(default_factory=list, max_length=30)
    banned: list[str] = Field(default_factory=list, max_length=30)
    reference: str = Field(default="", max_length=20000)  # 参考資料（要点）
    # USER_ONE_SIDE: AI 側の応答方針・分量
    response_policy: ResponsePolicy = ResponsePolicy.FREE
    ai_ratio: float = Field(default=1.0, ge=0.2, le=3.0)  # ユーザーの発話量に対する AI 側の分量
    replies_per_input: int = Field(default=1, ge=1, le=3)
    # 読み上げ速度の目安（文字/秒）。推定尺の計算に使う
    chars_per_sec: float = Field(default=6.0, ge=2, le=15)  # 実機の VOICEVOX で約 5.9 文字/秒


class ScriptStudioCreate(BaseModel):
    project_id: str
    title: str = Field(default="", max_length=255)
    mode: ScriptMode = ScriptMode.FULL_AI
    settings: ScriptSettings = Field(default_factory=ScriptSettings)
    characters: list[CharacterIn] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def _chars(self) -> "ScriptStudioCreate":
        names = [c.name for c in self.characters]
        if len(names) != len(set(names)):
            raise ValueError("character names must be unique")
        if self.mode == ScriptMode.USER_ONE_SIDE:
            if len(self.characters) < 2:
                raise ValueError("USER_ONE_SIDE needs at least two characters")
            if sum(c.is_user for c in self.characters) != 1:
                raise ValueError("exactly one character must be is_user=true")
        elif self.mode == ScriptMode.FULL_AI and not self.characters:
            raise ValueError("FULL_AI needs at least one character")
        return self


class ScriptUpdate(BaseModel):
    """台本設定の更新。作成途中のモード変更も可（仕様 §17）。"""

    title: str | None = Field(default=None, max_length=255)
    mode: ScriptMode | None = None
    settings: ScriptSettings | None = None
    characters: list[CharacterIn] | None = Field(default=None, max_length=8)


class DialogueLine(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    speaker: str | None = Field(default=None, max_length=100)  # 省略時はユーザー担当キャラクター
    scene_index: int | None = Field(default=None, ge=0)


class DialogueInput(BaseModel):
    """ユーザーのセリフ登録（モードB）。既定は末尾に追加。replace=True で入力セリフを置き換え。"""

    lines: list[DialogueLine] = Field(min_length=1, max_length=500)
    replace: bool = False


class TurnUpdate(BaseModel):
    text: str | None = Field(default=None, min_length=1, max_length=2000)
    speaker: str | None = Field(default=None, max_length=100)
    scene_index: int | None = Field(default=None, ge=0)
    sequence: int | None = Field(default=None, ge=0)  # 並べ替え


class LockRequest(BaseModel):
    locked: bool = True


class ScriptGenerateRequest(BaseModel):
    """モードに応じて生成する（FULL_AI: 全体 / USER_ONE_SIDE: 相手役）。"""

    model_key: str | None = None  # 省略時はタスク割当（台本生成）
    instruction: str = Field(default="", max_length=2000)  # 追加の指示


class RegenerateRequest(BaseModel):
    model_key: str | None = None
    instruction: str = Field(default="", max_length=1000)
    candidates: int = Field(default=1, ge=1, le=3)  # 2以上は候補を返すだけ（選んで適用）


class ApplyCandidate(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class ScriptValidateRequest(BaseModel):
    use_llm: bool = False  # 会話の整合性・キャラクターの一貫性を LLM でも検査
    model_key: str | None = None


class TurnRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    scene_index: int
    sequence: int
    speaker: str
    text: str
    generation_source: str
    reply_to_segment_id: str | None
    locked: bool
    voice_style_id: int | None
    audio_file_id: str | None
    start_frame: int | None
    end_frame: int | None
    estimated_sec: float = 0.0


class SceneRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sequence: int
    purpose: str
    estimated_duration_sec: float | None


class ScriptDetail(BaseModel):
    id: str
    project_id: str
    title: str
    mode: str
    status: str
    version: int
    settings: ScriptSettings
    characters: list[CharacterRead]
    scenes: list[SceneRead]
    segments: list[TurnRead]
    estimated_total_sec: float
    notes: list[str] = Field(default_factory=list)  # 直近の生成で AI が明示した仮定・質問


class ScriptIssue(BaseModel):
    level: Literal["error", "warning", "info"]
    code: str
    message: str
    turn_id: str | None = None


class ScriptValidation(BaseModel):
    valid: bool
    issues: list[ScriptIssue]
    estimated_total_sec: float
    target_duration_sec: int | None
    ratio_ai_to_user: float | None = None


class CandidatesRead(BaseModel):
    turn_id: str
    candidates: list[str]
    generation_run_id: str


class VersionRead(BaseModel):
    version: int
    reason: str
    turn_count: int
    created_at: datetime


class TurnChange(BaseModel):
    kind: Literal["added", "removed", "changed", "moved"]
    turn_id: str
    speaker: str
    before: str | None = None
    after: str | None = None


class VersionDiff(BaseModel):
    from_version: int
    to_version: int
    changes: list[TurnChange]
    settings_changed: bool
    characters_changed: bool


class GenerationRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: str
    model_key: str | None
    prompt_version: str
    status: str
    error: str | None
    duration_ms: float | None
    created_at: datetime


class ProposalRead(BaseModel):
    """承認待ちの台本修正案（LLM 由来の操作）。"""

    model_config = ConfigDict(from_attributes=True)

    id: str
    script_id: str
    project_id: str
    base_version: int
    reason: str
    status: str
    notes: list[str] | None = None
    created_at: datetime
    diff: VersionDiff | None = None
