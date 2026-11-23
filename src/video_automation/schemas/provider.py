"""LLM / 音楽生成 Provider 関連スキーマ (Phase 2)。

モデル名は表示名として扱い、実モデルID・量子化・推論エンジンは検出結果を正とする。
"""

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, SecretStr, field_validator


class ProviderType(StrEnum):
    OLLAMA = "ollama"
    LMSTUDIO = "lmstudio"
    OPENAI_COMPATIBLE = "openai_compatible"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"


LOCAL_PROVIDER_TYPES = {ProviderType.OLLAMA, ProviderType.LMSTUDIO, ProviderType.OPENAI_COMPATIBLE}
CLOUD_PROVIDER_TYPES = {ProviderType.OPENAI, ProviderType.ANTHROPIC, ProviderType.GEMINI}


class ModelState(StrEnum):
    """仕様 §2 のモデル状態。ロード状態をAPIで確認できない場合は UNKNOWN。"""

    INSTALLED = "INSTALLED"
    SERVER_AVAILABLE = "SERVER_AVAILABLE"
    MODEL_DISCOVERED = "MODEL_DISCOVERED"
    MODEL_LOADED = "MODEL_LOADED"
    INFERENCE_READY = "INFERENCE_READY"
    UNKNOWN = "UNKNOWN"


class CapabilitySource(StrEnum):
    DECLARED = "declared"  # サーバー/APIの申告値
    PROBED = "probed"  # 実機テストで確認


CAPABILITY_FIELDS = (
    "text_input",
    "image_input",
    "audio_input",
    "streaming",
    "structured_output",
    "tool_calling",
    "thinking",
)


class Capabilities(BaseModel):
    """None は未確認。True/False は申告または実測の結果 (source参照)。"""

    text_input: bool | None = True
    image_input: bool | None = None
    audio_input: bool | None = None
    streaming: bool | None = None
    structured_output: bool | None = None
    tool_calling: bool | None = None
    # tool_calls を返さず本文にJSONを書くモデル向け。フォールバック解析で利用可能
    tool_calling_fallback: bool = False
    thinking: bool | None = None
    context_length: int | None = None
    source: dict[str, CapabilitySource] = Field(default_factory=dict)

    def supports(self, name: str) -> bool | None:
        value = getattr(self, name)
        if name == "tool_calling" and not value and self.tool_calling_fallback:
            return True
        return value

    def merge_probed(self, **values: Any) -> "Capabilities":
        data = self.model_dump()
        source = dict(self.source)
        for k, v in values.items():
            if v is None:
                continue
            data[k] = v
            if k in CAPABILITY_FIELDS:
                source[k] = CapabilitySource.PROBED
        data["source"] = source
        return Capabilities.model_validate(data)


class ProviderConfig(BaseModel):
    id: str
    type: ProviderType
    name: str
    base_url: str
    enabled: bool = True
    auto_discovered: bool = False
    has_api_key: bool = False


class ProviderStatus(BaseModel):
    provider_id: str
    type: ProviderType
    name: str
    base_url: str
    available: bool
    server_version: str | None = None
    api_format: str | None = None
    models_count: int = 0
    error: str | None = None
    process_detected: bool | None = None  # 補助情報。これだけで推論可能とは判定しない
    checked_at: datetime | None = None


class ModelInfo(BaseModel):
    key: str  # "<provider_id>::<model_id>"
    provider_id: str
    provider_type: ProviderType
    model_id: str  # 実モデルID（検出結果が正）
    display_name: str
    family: str | None = None
    parameter_size: str | None = None
    quantization: str | None = None
    format: str | None = None
    size_bytes: int | None = None
    state: ModelState = ModelState.MODEL_DISCOVERED
    loaded: bool | None = None
    capabilities: Capabilities = Field(default_factory=Capabilities)
    is_active: bool = False
    last_test: dict[str, Any] | None = None

    @staticmethod
    def make_key(provider_id: str, model_id: str) -> str:
        return f"{provider_id}::{model_id}"


class MusicProviderStatus(BaseModel):
    """ACE-Step は LLM Provider に混在させず音楽生成Providerとして独立管理する。"""

    name: str = "ACE-Step 1.5"
    base_url: str
    available: bool
    installed: bool | None = None
    install_path: str | None = None
    endpoints: list[str] = Field(default_factory=list)
    error: str | None = None


class DiscoveryResult(BaseModel):
    providers: list[ProviderStatus]
    models: list[ModelInfo]
    music: list[MusicProviderStatus] = Field(default_factory=list)


# ---------- 推論 ----------


class ToolSpec(BaseModel):
    name: str
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})


class ToolCall(BaseModel):
    id: str | None = None
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    images: list[str] = Field(default_factory=list)  # base64 (PNG/JPEG)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None


class GenerateRequest(BaseModel):
    messages: list[ChatMessage]
    json_schema: dict[str, Any] | None = None
    tools: list[ToolSpec] = Field(default_factory=list)
    temperature: float | None = None
    max_tokens: int | None = None
    think: bool | None = False
    context_length: int | None = None
    timeout_s: float = 300.0


class GenerateResult(BaseModel):
    model_id: str
    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_calls_from_fallback: bool = False
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    duration_ms: float | None = None
    tokens_per_sec: float | None = None


class StreamChunk(BaseModel):
    delta: str = ""
    done: bool = False


# ---------- タスク別割当 ----------


class TaskType(StrEnum):
    EDIT_PLAN = "edit_plan"
    STRUCTURED_COMMAND = "structured_command"
    TOOL_SELECTION = "tool_selection"
    SCRIPT_GENERATION = "script_generation"
    SUBTITLE = "subtitle"
    PROOFREADING = "proofreading"
    IMAGE_UNDERSTANDING = "image_understanding"
    CHAT = "chat"


# ---------- API ----------


class RegisterProviderRequest(BaseModel):
    type: ProviderType
    name: str = Field(min_length=1, max_length=100)
    base_url: str = Field(min_length=1, max_length=500)
    api_key: SecretStr | None = None

    @field_validator("base_url")
    @classmethod
    def _http_only(cls, v: str) -> str:
        v = v.strip().rstrip("/")
        if not v.startswith(("http://", "https://")):
            raise ValueError("base_url must start with http:// or https://")
        return v


class ModelTestRequest(BaseModel):
    model_key: str
    probes: list[Literal["basic", "streaming", "structured", "tools", "vision"]] | None = None


class ProbeResult(BaseModel):
    name: str
    ok: bool | None  # None=未実施
    duration_ms: float | None = None
    detail: str | None = None


class ModelTestResult(BaseModel):
    model_key: str
    success: bool
    probes: list[ProbeResult]
    tokens_per_sec: float | None = None
    sample_text: str | None = None
    capabilities: Capabilities
    vram_note: str | None = None


class ActiveModelRequest(BaseModel):
    model_key: str


class TaskAssignmentRequest(BaseModel):
    task: TaskType
    model_key: str | None = None  # None = 自動選択


class TaskAssignmentRead(BaseModel):
    task: TaskType
    mode: Literal["manual", "auto"]
    model_key: str | None
    resolved_model_key: str | None
    reason: str | None = None
