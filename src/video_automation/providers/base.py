"""Provider 共通インターフェース (P2-01)。

discover / list_models / check_connection / get_capabilities / generate / stream /
load_model / unload_model を共通APIとする。
"""

import json
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx

from video_automation.schemas.provider import (
    Capabilities,
    GenerateRequest,
    GenerateResult,
    ModelInfo,
    ProviderConfig,
    ProviderStatus,
    ProviderType,
    StreamChunk,
)


class ProviderError(Exception):
    code = "provider_error"


class ProviderUnavailableError(ProviderError):
    code = "provider_unavailable"


class NotSupportedError(ProviderError):
    code = "not_supported"


class ModelNotFoundError(ProviderError):
    code = "model_not_found"


def _now() -> datetime:
    return datetime.now(UTC)


class LLMProvider(ABC):
    type: ProviderType
    #: ローカルAPIはシステムプロキシを経由させない
    trust_env: bool = False
    connect_timeout: float = 3.0

    def __init__(
        self,
        config: ProviderConfig,
        *,
        api_key: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config
        self._api_key = api_key
        self._client = httpx.AsyncClient(
            base_url=config.base_url,
            headers=self._headers(),
            transport=transport,
            trust_env=self.trust_env,
            timeout=httpx.Timeout(30.0, connect=self.connect_timeout),
        )
        self._caps_cache: dict[str, Capabilities] = {}

    # ----- 共通ヘルパー -----
    def _headers(self) -> dict[str, str]:
        return {}

    def _status(self, available: bool, **kw: Any) -> ProviderStatus:
        return ProviderStatus(
            provider_id=self.config.id,
            type=self.config.type,
            name=self.config.name,
            base_url=self.config.base_url,
            available=available,
            checked_at=_now(),
            **kw,
        )

    def _model(self, model_id: str, **kw: Any) -> ModelInfo:
        kw.setdefault("display_name", model_id)
        return ModelInfo(
            key=ModelInfo.make_key(self.config.id, model_id),
            provider_id=self.config.id,
            provider_type=self.config.type,
            model_id=model_id,
            **kw,
        )

    async def _get_json(self, path: str, **kw: Any) -> Any:
        try:
            r = await self._client.get(path, **kw)
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(type(exc).__name__) from exc
        if r.status_code == 404:
            raise NotSupportedError(f"GET {path}: 404")
        r.raise_for_status()
        return r.json()

    async def _post_json(self, path: str, body: dict[str, Any], timeout: float = 300.0) -> Any:
        try:
            r = await self._client.post(path, json=body, timeout=timeout)
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(type(exc).__name__) from exc
        if r.status_code >= 400:
            raise ProviderError(_error_text(r))
        return r.json()

    async def _stream_lines(
        self, path: str, body: dict[str, Any], timeout: float = 300.0
    ) -> AsyncIterator[str]:
        try:
            async with self._client.stream("POST", path, json=body, timeout=timeout) as r:
                if r.status_code >= 400:
                    await r.aread()
                    raise ProviderError(_error_text(r))
                async for line in r.aiter_lines():
                    if line.strip():
                        yield line
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(type(exc).__name__) from exc

    # ----- 共通インターフェース -----
    @abstractmethod
    async def check_connection(self) -> ProviderStatus: ...

    @abstractmethod
    async def list_models(self) -> list[ModelInfo]: ...

    async def discover(self) -> tuple[ProviderStatus, list[ModelInfo]]:
        status = await self.check_connection()
        if not status.available:
            return status, []
        try:
            models = await self.list_models()
        except ProviderError as exc:
            status.error = f"list_models: {exc}"
            return status, []
        status.models_count = len(models)
        return status, models

    async def get_capabilities(self, model_id: str) -> Capabilities:
        if model_id not in self._caps_cache:
            for m in await self.list_models():
                self._caps_cache[m.model_id] = m.capabilities
        if model_id not in self._caps_cache:
            raise ModelNotFoundError(model_id)
        return self._caps_cache[model_id]

    @abstractmethod
    async def generate(self, model_id: str, req: GenerateRequest) -> GenerateResult: ...

    @abstractmethod
    def stream(self, model_id: str, req: GenerateRequest) -> AsyncIterator[StreamChunk]: ...

    async def load_model(self, model_id: str) -> None:
        raise NotSupportedError("load_model")

    async def unload_model(self, model_id: str) -> None:
        raise NotSupportedError("unload_model")

    async def loaded_models(self) -> list[str] | None:
        """ロード中モデルID。APIで確認できない場合は None (=UNKNOWN)。"""
        return None

    async def aclose(self) -> None:
        await self._client.aclose()


def _error_text(r: httpx.Response) -> str:
    """エラー本文から機密を含まない短いメッセージを作る。"""
    try:
        data = r.json()
        msg = data.get("error") if isinstance(data, dict) else None
        if isinstance(msg, dict):
            msg = msg.get("message")
    except (json.JSONDecodeError, ValueError):
        msg = None
    text = str(msg or r.text or "")[:300]
    return f"HTTP {r.status_code}: {text}"


def tokens_per_sec(tokens: int | None, seconds: float | None) -> float | None:
    if not tokens or not seconds or seconds <= 0:
        return None
    return round(tokens / seconds, 1)
