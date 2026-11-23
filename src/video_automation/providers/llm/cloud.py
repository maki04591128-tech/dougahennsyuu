"""クラウドLLM Adapter (P2-05)：OpenAI / Anthropic / Gemini。

APIキー未設定のため実機未検証。各社のWeb契約とAPI契約は別扱い（APIキーが必要）。
"""

import json
from collections.abc import AsyncIterator
from typing import Any

from video_automation.providers.base import LLMProvider, ProviderError
from video_automation.providers.llm.openai_compatible import OpenAICompatibleProvider
from video_automation.schemas.provider import (
    Capabilities,
    ChatMessage,
    GenerateRequest,
    GenerateResult,
    ModelInfo,
    ModelState,
    ProviderStatus,
    ProviderType,
    StreamChunk,
    ToolCall,
)

DEFAULT_URLS = {
    ProviderType.OPENAI: "https://api.openai.com/v1",
    ProviderType.ANTHROPIC: "https://api.anthropic.com",
    ProviderType.GEMINI: "https://generativelanguage.googleapis.com/v1beta",
}

_CLOUD_CAPS = Capabilities(streaming=True, structured_output=True, tool_calling=True)


class _CloudMixin:
    trust_env = True  # クラウドはシステムプロキシ設定に従う
    connect_timeout = 10.0
    _api_key: str | None

    def _require_key(self) -> None:
        if not self._api_key:
            raise ProviderError("api key not configured")


class OpenAIProvider(_CloudMixin, OpenAICompatibleProvider):
    type = ProviderType.OPENAI

    async def check_connection(self) -> ProviderStatus:
        if not self._api_key:
            return self._status(False, error="api key not configured")
        status = await super().check_connection()
        status.api_format = "openai"
        return status

    async def list_models(self) -> list[ModelInfo]:
        models = await super().list_models()
        for m in models:
            m.state = ModelState.SERVER_AVAILABLE
            m.capabilities = _CLOUD_CAPS.model_copy()
        return models


class AnthropicProvider(_CloudMixin, LLMProvider):
    type = ProviderType.ANTHROPIC
    API_VERSION = "2023-06-01"
    OUTPUT_TOOL = "emit_structured_output"

    def _headers(self) -> dict[str, str]:
        h = {"anthropic-version": self.API_VERSION}
        if self._api_key:
            h["x-api-key"] = self._api_key
        return h

    async def check_connection(self) -> ProviderStatus:
        if not self._api_key:
            return self._status(False, error="api key not configured")
        try:
            await self._get_json("/v1/models")
        except Exception as exc:
            return self._status(False, error=str(exc)[:200] or type(exc).__name__)
        return self._status(True, api_format="anthropic-messages")

    async def list_models(self) -> list[ModelInfo]:
        data = await self._get_json("/v1/models")
        return [
            self._model(
                m["id"],
                display_name=m.get("display_name") or m["id"],
                state=ModelState.SERVER_AVAILABLE,
                capabilities=Capabilities(
                    image_input=True, streaming=True, structured_output=True, tool_calling=True
                ),
            )
            for m in data.get("data", [])
        ]

    @staticmethod
    def _messages(msgs: list[ChatMessage]) -> tuple[str | None, list[dict[str, Any]]]:
        system = "\n".join(m.content for m in msgs if m.role == "system") or None
        out: list[dict[str, Any]] = []
        for m in msgs:
            if m.role == "system":
                continue
            role = "assistant" if m.role == "assistant" else "user"
            blocks: list[dict[str, Any]] = [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": img},
                }
                for img in m.images
            ]
            if m.content:
                blocks.append({"type": "text", "text": m.content})
            out.append({"role": role, "content": blocks})
        return system, out

    def _body(self, model_id: str, req: GenerateRequest, stream: bool) -> dict[str, Any]:
        system, messages = self._messages(req.messages)
        body: dict[str, Any] = {
            "model": model_id,
            "max_tokens": req.max_tokens or 1024,
            "messages": messages,
            "stream": stream,
        }
        if system:
            body["system"] = system
        if req.temperature is not None:
            body["temperature"] = req.temperature
        tools = [
            {"name": t.name, "description": t.description, "input_schema": t.parameters}
            for t in req.tools
        ]
        if req.json_schema is not None:
            # 構造化出力はツール強制で実現
            tools.append(
                {
                    "name": self.OUTPUT_TOOL,
                    "description": "Return the final answer in this schema.",
                    "input_schema": req.json_schema,
                }
            )
            body["tool_choice"] = {"type": "tool", "name": self.OUTPUT_TOOL}
        if tools:
            body["tools"] = tools
        return body

    async def generate(self, model_id: str, req: GenerateRequest) -> GenerateResult:
        self._require_key()
        data = await self._post_json(
            "/v1/messages", self._body(model_id, req, False), req.timeout_s
        )
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for block in data.get("content", []):
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                if block.get("name") == self.OUTPUT_TOOL:
                    text_parts.append(json.dumps(block.get("input") or {}, ensure_ascii=False))
                else:
                    calls.append(
                        ToolCall(
                            id=block.get("id"),
                            name=block["name"],
                            arguments=block.get("input") or {},
                        )
                    )
        usage = data.get("usage") or {}
        return GenerateResult(
            model_id=model_id,
            text="".join(text_parts),
            tool_calls=calls,
            finish_reason=data.get("stop_reason"),
            prompt_tokens=usage.get("input_tokens"),
            completion_tokens=usage.get("output_tokens"),
        )

    async def stream(self, model_id: str, req: GenerateRequest) -> AsyncIterator[StreamChunk]:
        self._require_key()
        async for line in self._stream_lines("/v1/messages", self._body(model_id, req, True)):
            if not line.startswith("data:"):
                continue
            try:
                data = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            if data.get("type") == "content_block_delta":
                text = (data.get("delta") or {}).get("text") or ""
                if text:
                    yield StreamChunk(delta=text)
            elif data.get("type") == "message_stop":
                yield StreamChunk(done=True)
                return


class GeminiProvider(_CloudMixin, LLMProvider):
    type = ProviderType.GEMINI

    def _headers(self) -> dict[str, str]:
        return {"x-goog-api-key": self._api_key} if self._api_key else {}

    async def check_connection(self) -> ProviderStatus:
        if not self._api_key:
            return self._status(False, error="api key not configured")
        try:
            await self._get_json("/models")
        except Exception as exc:
            return self._status(False, error=str(exc)[:200] or type(exc).__name__)
        return self._status(True, api_format="gemini-generateContent")

    async def list_models(self) -> list[ModelInfo]:
        data = await self._get_json("/models")
        out = []
        for m in data.get("models", []):
            if "generateContent" not in (m.get("supportedGenerationMethods") or []):
                continue
            mid = m["name"].removeprefix("models/")
            out.append(
                self._model(
                    mid,
                    display_name=m.get("displayName") or mid,
                    state=ModelState.SERVER_AVAILABLE,
                    capabilities=Capabilities(
                        image_input=True,
                        streaming=True,
                        structured_output=True,
                        tool_calling=True,
                        context_length=m.get("inputTokenLimit"),
                    ),
                )
            )
        return out

    @staticmethod
    def _contents(msgs: list[ChatMessage]) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        system_text = "\n".join(m.content for m in msgs if m.role == "system")
        contents = []
        for m in msgs:
            if m.role == "system":
                continue
            parts: list[dict[str, Any]] = []
            if m.content:
                parts.append({"text": m.content})
            parts += [{"inline_data": {"mime_type": "image/png", "data": i}} for i in m.images]
            contents.append({"role": "model" if m.role == "assistant" else "user", "parts": parts})
        system = {"parts": [{"text": system_text}]} if system_text else None
        return system, contents

    def _body(self, req: GenerateRequest) -> dict[str, Any]:
        system, contents = self._contents(req.messages)
        cfg: dict[str, Any] = {}
        if req.temperature is not None:
            cfg["temperature"] = req.temperature
        if req.max_tokens is not None:
            cfg["maxOutputTokens"] = req.max_tokens
        if req.json_schema is not None:
            cfg["responseMimeType"] = "application/json"
            cfg["responseJsonSchema"] = req.json_schema
        body: dict[str, Any] = {"contents": contents, "generationConfig": cfg}
        if system:
            body["systemInstruction"] = system
        if req.tools:
            body["tools"] = [
                {
                    "functionDeclarations": [
                        {"name": t.name, "description": t.description, "parameters": t.parameters}
                        for t in req.tools
                    ]
                }
            ]
        return body

    async def generate(self, model_id: str, req: GenerateRequest) -> GenerateResult:
        self._require_key()
        data = await self._post_json(
            f"/models/{model_id}:generateContent", self._body(req), req.timeout_s
        )
        cand = (data.get("candidates") or [{}])[0]
        text, calls = "", []
        for part in (cand.get("content") or {}).get("parts", []):
            if "text" in part:
                text += part["text"]
            if "functionCall" in part:
                fc = part["functionCall"]
                calls.append(ToolCall(name=fc.get("name", ""), arguments=fc.get("args") or {}))
        usage = data.get("usageMetadata") or {}
        return GenerateResult(
            model_id=model_id,
            text=text,
            tool_calls=calls,
            finish_reason=cand.get("finishReason"),
            prompt_tokens=usage.get("promptTokenCount"),
            completion_tokens=usage.get("candidatesTokenCount"),
        )

    async def stream(self, model_id: str, req: GenerateRequest) -> AsyncIterator[StreamChunk]:
        self._require_key()
        path = f"/models/{model_id}:streamGenerateContent?alt=sse"
        async for line in self._stream_lines(path, self._body(req), req.timeout_s):
            if not line.startswith("data:"):
                continue
            try:
                data = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            for part in ((data.get("candidates") or [{}])[0].get("content") or {}).get("parts", []):
                if part.get("text"):
                    yield StreamChunk(delta=part["text"])
        yield StreamChunk(done=True)
