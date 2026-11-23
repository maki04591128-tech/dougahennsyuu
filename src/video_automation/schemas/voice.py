"""音声合成スキーマ (Phase 3)。VOICEVOX系エンジンのAudioQuery構造に合わせる。

VOICEVOX Nemo は通常の VOICEVOX Engine と完全一致すると仮定せず、
engine_manifest.supported_features で機能可否を判定する（仕様 §8）。
"""

import re
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_TEXT_CHARS = 1000
_KATAKANA = re.compile(r"^[ァ-ヴー]+$")


class Mora(BaseModel):
    model_config = ConfigDict(extra="allow")

    text: str
    consonant: str | None = None
    consonant_length: float | None = None
    vowel: str
    vowel_length: float
    pitch: float


class AccentPhrase(BaseModel):
    model_config = ConfigDict(extra="allow")

    moras: list[Mora]
    accent: int
    pause_mora: Mora | None = None
    is_interrogative: bool = False


class AudioQuery(BaseModel):
    """エンジンから受け取ったクエリ。未知の項目も保持して送り返す（extra=allow）。"""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    accent_phrases: list[AccentPhrase]
    speedScale: float = 1.0  # noqa: N815 - エンジンのフィールド名に合わせる
    pitchScale: float = 0.0  # noqa: N815
    intonationScale: float = 1.0  # noqa: N815
    volumeScale: float = 1.0  # noqa: N815
    prePhonemeLength: float = 0.1  # noqa: N815
    postPhonemeLength: float = 0.1  # noqa: N815
    pauseLength: float | None = None  # noqa: N815
    pauseLengthScale: float = 1.0  # noqa: N815
    outputSamplingRate: int = 24000  # noqa: N815
    outputStereo: bool = False  # noqa: N815
    kana: str | None = None


class EngineFeature(StrEnum):
    ADJUST_MORA_PITCH = "adjust_mora_pitch"
    ADJUST_PHONEME_LENGTH = "adjust_phoneme_length"
    ADJUST_SPEED_SCALE = "adjust_speed_scale"
    ADJUST_PITCH_SCALE = "adjust_pitch_scale"
    ADJUST_INTONATION_SCALE = "adjust_intonation_scale"
    ADJUST_VOLUME_SCALE = "adjust_volume_scale"
    ADJUST_PAUSE_LENGTH = "adjust_pause_length"
    INTERROGATIVE_UPSPEAK = "interrogative_upspeak"
    SYNTHESIS_MORPHING = "synthesis_morphing"
    SING = "sing"
    MANAGE_LIBRARY = "manage_library"
    STREAMING_SYNTHESIS = "streaming_synthesis"
    USER_DICT = "user_dict"  # manifest外。/user_dict の応答で判定


class EngineStatus(BaseModel):
    engine_id: str
    base_url: str
    available: bool
    name: str | None = None
    version: str | None = None
    default_sampling_rate: int | None = None
    features: dict[str, bool] = Field(default_factory=dict)
    devices: dict[str, bool] = Field(default_factory=dict)
    terms_note: str | None = None
    error: str | None = None


class VoiceStyle(BaseModel):
    id: int  # エンジンの style id（= speaker パラメータ）
    name: str
    type: str = "talk"

    @property
    def is_talk(self) -> bool:
        return self.type in ("talk", "streaming_talk")


class VoiceSpeaker(BaseModel):
    engine_id: str
    name: str
    speaker_uuid: str
    styles: list[VoiceStyle]
    version: str | None = None


class Prosody(BaseModel):
    """全体の抑揚・話速等（仕様 P3-05）。None の項目はエンジン既定値のまま。"""

    speed_scale: float | None = Field(default=None, gt=0.3, le=3.0)
    pitch_scale: float | None = Field(default=None, ge=-0.3, le=0.3)
    intonation_scale: float | None = Field(default=None, ge=0.0, le=3.0)
    volume_scale: float | None = Field(default=None, ge=0.0, le=3.0)
    pre_phoneme_length: float | None = Field(default=None, ge=0.0, le=2.0)
    post_phoneme_length: float | None = Field(default=None, ge=0.0, le=2.0)
    pause_length_scale: float | None = Field(default=None, ge=0.0, le=3.0)


PROSODY_FEATURES: dict[str, tuple[str, EngineFeature | None]] = {
    "speed_scale": ("speedScale", EngineFeature.ADJUST_SPEED_SCALE),
    "pitch_scale": ("pitchScale", EngineFeature.ADJUST_PITCH_SCALE),
    "intonation_scale": ("intonationScale", EngineFeature.ADJUST_INTONATION_SCALE),
    "volume_scale": ("volumeScale", EngineFeature.ADJUST_VOLUME_SCALE),
    "pre_phoneme_length": ("prePhonemeLength", None),
    "post_phoneme_length": ("postPhonemeLength", None),
    "pause_length_scale": ("pauseLengthScale", EngineFeature.ADJUST_PAUSE_LENGTH),
}


class _TextReq(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)

    @field_validator("text")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("text is empty")
        return v


class AccentPhrasesRequest(_TextReq):
    engine_id: str | None = None
    style_id: int


class MoraEditRequest(BaseModel):
    """アクセント位置を変更した accent_phrases から音高・長さを再計算する。"""

    engine_id: str | None = None
    style_id: int
    accent_phrases: list[AccentPhrase]
    recompute: str = Field(default="mora_data", pattern="^(mora_data|mora_pitch|mora_length)$")


class VoiceGenerateItem(_TextReq):
    style_id: int
    prosody: Prosody = Field(default_factory=Prosody)
    # 読み・アクセントを手動修正した場合に渡す（テキストからの再解析を行わない）
    accent_phrases: list[AccentPhrase] | None = None
    script_segment_id: str | None = None


class VoiceGenerateRequest(BaseModel):
    engine_id: str | None = None
    project_id: str | None = None
    items: list[VoiceGenerateItem] = Field(min_length=1, max_length=500)


class VoicePreviewRequest(VoiceGenerateItem):
    engine_id: str | None = None


class ProsodyRequest(BaseModel):
    """既存の音声ファイルを抑揚を変えて作り直す。"""

    audio_id: str
    prosody: Prosody
    accent_phrases: list[AccentPhrase] | None = None


class WordType(StrEnum):
    PROPER_NOUN = "PROPER_NOUN"
    COMMON_NOUN = "COMMON_NOUN"
    VERB = "VERB"
    ADJECTIVE = "ADJECTIVE"
    SUFFIX = "SUFFIX"


class DictionaryWordRequest(BaseModel):
    engine_id: str | None = None
    surface: str = Field(min_length=1, max_length=100)
    pronunciation: str = Field(min_length=1, max_length=100)
    accent_type: int = Field(ge=0)
    word_type: WordType = WordType.PROPER_NOUN
    priority: int = Field(default=5, ge=0, le=10)

    @field_validator("pronunciation")
    @classmethod
    def _katakana(cls, v: str) -> str:
        v = v.strip()
        if not _KATAKANA.match(v):
            raise ValueError("pronunciation must be katakana")
        return v


class DictionaryWordRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    engine_id: str
    engine_word_uuid: str | None
    surface: str
    pronunciation: str
    accent_type: int
    word_type: str
    priority: int
    created_at: datetime


class PhraseTiming(BaseModel):
    index: int
    text: str
    start_sec: float
    end_sec: float


class AudioFileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str | None
    script_segment_id: str | None
    engine_id: str
    engine_version: str | None
    style_id: int
    text: str
    path: str
    sha256: str
    sample_rate: int
    duration_sec: float
    credit: str | None
    created_at: datetime
    timings: list[PhraseTiming] = Field(default_factory=list)
    prosody: dict[str, Any] = Field(default_factory=dict)
