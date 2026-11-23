"""素材検索・取得・ライセンス・音楽生成のスキーマ（Phase 6）。"""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SourceName(StrEnum):
    PEXELS = "pexels"
    PIXABAY = "pixabay"
    FREESOUND = "freesound"
    OPENVERSE = "openverse"
    WIKIMEDIA = "wikimedia"


class MediaType(StrEnum):
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"  # 効果音・音源


class LicenseVerdict(StrEnum):
    ALLOWED = "allowed"  # そのまま利用可（帰属表示が必要な場合あり）
    NEEDS_ACK = "needs_ack"  # 不明・非営利限定・改変禁止など。ユーザーの明示承認が必要
    FORBIDDEN = "forbidden"


class LicenseInfo(BaseModel):
    """取得時点のライセンス情報（スナップショットとして保存する）。"""

    code: str  # cc0 / pdm / cc-by / cc-by-sa / cc-by-nc / ... / pexels / pixabay / unknown
    name: str
    version: str | None = None
    url: str | None = None
    attribution_required: bool
    commercial_use: bool | None  # None=不明
    modification: bool | None
    share_alike: bool = False
    notes: list[str] = Field(default_factory=list)


class LicenseCheck(BaseModel):
    license: LicenseInfo
    verdict: LicenseVerdict
    reasons: list[str] = Field(default_factory=list)
    attribution: str | None = None


class MediaVariant(BaseModel):
    url: str
    width: int | None = None
    height: int | None = None
    label: str = ""  # original / hd / sd / large / preview-hq-mp3 等
    mime: str | None = None
    size: int | None = None


class AssetSearchResult(BaseModel):
    source: SourceName
    source_id: str
    media_type: MediaType
    title: str
    creator: str | None = None
    creator_url: str | None = None
    landing_url: str | None = None  # 取得元の作品ページ
    preview_url: str | None = None  # サムネイル（画像）
    variants: list[MediaVariant] = Field(default_factory=list)
    width: int | None = None
    height: int | None = None
    duration_sec: float | None = None
    tags: list[str] = Field(default_factory=list)
    license: LicenseInfo
    verdict: LicenseVerdict = LicenseVerdict.ALLOWED
    attribution: str | None = None


class Orientation(StrEnum):
    ANY = "any"
    LANDSCAPE = "landscape"
    PORTRAIT = "portrait"
    SQUARE = "square"


class AssetSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=100)
    media_type: MediaType = MediaType.IMAGE
    sources: list[SourceName] | None = None  # None=キー設定済み・対応する全サービス
    orientation: Orientation = Orientation.ANY
    page: int = Field(default=1, ge=1, le=20)
    per_page: int = Field(default=20, ge=3, le=50)
    max_duration_sec: float | None = Field(default=None, gt=0)  # 動画・音声
    # True: ライセンス判定が allowed のものだけ返す
    allowed_only: bool = False


class AssetSearchResponse(BaseModel):
    results: list[AssetSearchResult]
    errors: dict[str, str] = Field(default_factory=dict)  # source -> エラー概要
    skipped: dict[str, str] = Field(default_factory=dict)  # 未設定・非対応など
    cached: list[str] = Field(default_factory=list)


class AssetRef(BaseModel):
    source: SourceName
    source_id: str = Field(min_length=1, max_length=200)
    media_type: MediaType


class LicenseInspectRequest(BaseModel):
    ref: AssetRef | None = None
    asset_id: str | None = None

    @model_validator(mode="after")
    def _one(self) -> "LicenseInspectRequest":
        if (self.ref is None) == (self.asset_id is None):
            raise ValueError("specify exactly one of ref or asset_id")
        return self


class AssetDownloadRequest(AssetRef):
    project_id: str
    # ユーザーがライセンス条件（不明・非営利限定等）を確認した上で利用する場合に True
    acknowledge_license: bool = False
    origin: Literal["user", "llm"] = "user"


class AssetRequestStatus(StrEnum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    DONE = "DONE"
    FAILED = "FAILED"


class AssetRequestRead(BaseModel):
    """LLM 由来のダウンロード要求（承認待ち）。"""

    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    source: str
    source_id: str
    media_type: str
    title: str
    license_code: str
    verdict: str
    status: AssetRequestStatus
    job_id: str | None
    asset_id: str | None
    error: str | None
    created_at: datetime


class AssetDownloadResponse(BaseModel):
    status: Literal["QUEUED", "PENDING_APPROVAL"]
    job_id: str | None = None
    request_id: str | None = None
    license: LicenseCheck


class SourceStatus(BaseModel):
    name: SourceName
    label: str
    media_types: list[MediaType]
    requires_key: bool
    configured: bool
    key_from_env: bool = False
    terms_url: str


class SourceKeyRequest(BaseModel):
    api_key: str = Field(min_length=8, max_length=200)


class CreditEntry(BaseModel):
    asset_id: str
    name: str
    kind: str
    source: str
    text: str
    license_code: str | None = None


class CreditsResponse(BaseModel):
    entries: list[CreditEntry]
    text: str  # 動画の概要欄などに貼れる形式


# ---------------- 音楽生成（ACE-Step 1.5） ----------------


class MusicGenerateRequest(BaseModel):
    project_id: str
    prompt: str = Field(min_length=1, max_length=1000)  # 曲調・楽器・雰囲気（英語推奨）
    instrumental: bool = True
    lyrics: str = Field(default="", max_length=4000)
    duration_sec: float = Field(default=60.0, ge=10, le=600)
    bpm: int | None = Field(default=None, ge=30, le=300)
    key_scale: str = Field(default="", max_length=20)  # 例 "C Major"
    time_signature: str = Field(default="", max_length=4)  # 例 "4"
    vocal_language: str = Field(default="ja", max_length=10)
    seed: int | None = Field(default=None, ge=0)
    model: str | None = Field(default=None, max_length=100)
    inference_steps: int = Field(default=8, ge=1, le=200)
    batch_size: int = Field(default=1, ge=1, le=4)
    audio_format: Literal["wav", "flac", "mp3"] = "wav"

    @model_validator(mode="after")
    def _lyrics(self) -> "MusicGenerateRequest":
        if not self.instrumental and not self.lyrics.strip():
            raise ValueError("lyrics are required when instrumental is false")
        return self


class MusicGenerationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    asset_id: str | None
    prompt: str
    lyrics: str
    params: dict
    dit_model: str | None
    lm_model: str | None
    seed: str | None
    duration_sec: float | None
    created_at: datetime


class MusicEngineStatus(BaseModel):
    available: bool
    base_url: str
    installed: bool | None = None
    models: list[str] = Field(default_factory=list)
    default_model: str | None = None
    error: str | None = None


class AddBgmRequest(BaseModel):
    asset_id: str
    volume: float = Field(default=0.25, ge=0, le=2)  # ナレーションより小さく
    fade_in_frames: int = Field(default=30, ge=0)
    fade_out_frames: int = Field(default=60, ge=0)
    start_frame: int = Field(default=0, ge=0)
    end_frame: int | None = Field(default=None, gt=0)  # None=タイムラインの長さまで
    loop: bool = True  # 曲が短い場合に繰り返して埋める
