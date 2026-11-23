"""LM Studio Adapter (P2-03)。

検出: /api/v1/models (ネイティブREST) → /api/v0/models (旧REST) → /v1/models (OpenAI互換)
の順に試し、有効なAPIを記録する。推論は OpenAI 互換 /v1/chat/completions。
ロード/アンロード: POST /api/v1/models/load {model} / POST /api/v1/models/unload {instance_id}
（LM Studio公式ドキュメント記載。実機は未導入のため未検証）
"""

from typing import Any

from video_automation.providers.base import NotSupportedError, ProviderError
from video_automation.providers.llm.openai_compatible import OpenAICompatibleProvider
from video_automation.schemas.provider import (
    Capabilities,
    CapabilitySource,
    ModelInfo,
    ModelState,
    ProviderStatus,
    ProviderType,
)

_DECL = CapabilitySource.DECLARED


class LMStudioProvider(OpenAICompatibleProvider):
    type = ProviderType.LMSTUDIO

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.api_format: str | None = None

    async def _detect(self) -> tuple[str, Any]:
        for fmt, path in (
            ("lmstudio-rest-v1", "/api/v1/models"),
            ("lmstudio-rest-v0", "/api/v0/models"),
            ("openai-compatible", "/v1/models"),
        ):
            try:
                data = await self._get_json(path)
            except NotSupportedError:
                continue
            self.api_format = fmt
            return fmt, data
        raise NotSupportedError("no LM Studio API responded")

    async def check_connection(self) -> ProviderStatus:
        try:
            fmt, _ = await self._detect()
        except ProviderError as exc:
            return self._status(False, error=str(exc) or exc.code)
        except Exception as exc:
            return self._status(False, error=type(exc).__name__)
        return self._status(True, api_format=fmt)

    def _from_v1(self, m: dict[str, Any]) -> ModelInfo | None:
        if m.get("type") not in (None, "llm"):
            return None  # embedding / decision は LLM 一覧に含めない
        caps_raw = m.get("capabilities") or {}
        caps = Capabilities(
            image_input=bool(caps_raw.get("vision")),
            tool_calling=bool(caps_raw.get("trained_for_tool_use")),
            thinking=bool(caps_raw.get("reasoning")) or None,
            streaming=True,
            structured_output=True,
            context_length=m.get("max_context_length"),
            source={"image_input": _DECL, "tool_calling": _DECL},
        )
        loaded = bool(m.get("loaded_instances"))
        q = m.get("quantization")
        return self._model(
            m["key"],
            display_name=m.get("display_name") or m["key"],
            family=m.get("architecture"),
            parameter_size=m.get("params_string"),
            quantization=q.get("name") if isinstance(q, dict) else q,
            format=m.get("format"),
            size_bytes=m.get("size_bytes"),
            loaded=loaded,
            state=ModelState.MODEL_LOADED if loaded else ModelState.MODEL_DISCOVERED,
            capabilities=caps,
        )

    def _from_v0(self, m: dict[str, Any]) -> ModelInfo | None:
        if m.get("type") not in (None, "llm", "vlm"):
            return None
        loaded = m.get("state") == "loaded"
        return self._model(
            m["id"],
            family=m.get("arch"),
            quantization=m.get("quantization"),
            loaded=loaded,
            state=ModelState.MODEL_LOADED if loaded else ModelState.MODEL_DISCOVERED,
            capabilities=Capabilities(
                image_input=m.get("type") == "vlm",
                streaming=True,
                structured_output=True,
                context_length=m.get("max_context_length"),
                source={"image_input": _DECL},
            ),
        )

    async def list_models(self) -> list[ModelInfo]:
        fmt, data = await self._detect()
        if fmt == "lmstudio-rest-v1":
            items = [self._from_v1(m) for m in data.get("models", [])]
        elif fmt == "lmstudio-rest-v0":
            items = [self._from_v0(m) for m in data.get("data", [])]
        else:
            items = [self._model(m["id"], state=ModelState.UNKNOWN) for m in data.get("data", [])]
        models = [m for m in items if m is not None]
        for m in models:
            self._caps_cache[m.model_id] = m.capabilities
        return models

    async def _instances(self) -> dict[str, list[str]]:
        data = await self._get_json("/api/v1/models")
        return {
            m["key"]: [i["id"] for i in m.get("loaded_instances") or []]
            for m in data.get("models", [])
        }

    async def loaded_models(self) -> list[str] | None:
        try:
            return [k for k, ids in (await self._instances()).items() if ids]
        except ProviderError:
            return None

    async def load_model(self, model_id: str) -> None:
        await self._post_json("/api/v1/models/load", {"model": model_id})

    async def unload_model(self, model_id: str) -> None:
        for instance_id in (await self._instances()).get(model_id, []):
            await self._post_json("/api/v1/models/unload", {"instance_id": instance_id}, 60)
