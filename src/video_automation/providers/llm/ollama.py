"""Ollama Adapter (P2-02)。ネイティブAPI /api/* を使用。

実機確認 (2026-11-23, Ollama 0.40.16): /api/tags, /api/show(capabilities),
/api/ps, /api/chat(format/tools/images/think) が応答。
"""

import json
from collections.abc import AsyncIterator
from typing import Any

from video_automation.providers.base import (
    LLMProvider,
    ModelNotFoundError,
    ProviderError,
    tokens_per_sec,
)
from video_automation.providers.llm.tool_fallback import parse_tool_calls
from video_automation.schemas.provider import (
    Capabilities,
    CapabilitySource,
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


def _caps_from_show(show: dict[str, Any]) -> Capabilities:
    caps = set(show.get("capabilities") or [])
    ctx = None
    for k, v in (show.get("model_info") or {}).items():
        if k.endswith(".context_length") and isinstance(v, int):
            ctx = v
    declared = CapabilitySource.DECLARED
    if not caps:  # 古いOllamaはcapabilities未提供
        return Capabilities(context_length=ctx)
    return Capabilities(
        text_input="completion" in caps,
        image_input="vision" in caps,
        audio_input="audio" in caps,
        streaming=True,
        structured_output=True,  # format(JSON Schema)はサーバー側で全モデル対応
        tool_calling="tools" in caps,
        thinking="thinking" in caps,
        context_length=ctx,
        source={
            k: declared
            for k in ("text_input", "image_input", "audio_input", "tool_calling", "thinking")
        },
    )


class OllamaProvider(LLMProvider):
    type = ProviderType.OLLAMA

    async def check_connection(self) -> ProviderStatus:
        try:
            data = await self._get_json("/api/version")
        except ProviderError as exc:
            return self._status(False, error=str(exc) or exc.code)
        return self._status(
            True, server_version=data.get("version"), api_format="ollama-native + openai(/v1)"
        )

    async def _show(self, model_id: str) -> dict[str, Any]:
        try:
            return await self._post_json("/api/show", {"model": model_id}, timeout=15)
        except ProviderError as exc:
            if "not found" in str(exc):
                raise ModelNotFoundError(model_id) from exc
            raise

    async def get_capabilities(self, model_id: str) -> Capabilities:
        # 対象モデルだけ /api/show で取得（全モデル照会を避ける）
        if model_id not in self._caps_cache:
            self._caps_cache[model_id] = _caps_from_show(await self._show(model_id))
        return self._caps_cache[model_id]

    async def loaded_models(self) -> list[str] | None:
        try:
            data = await self._get_json("/api/ps")
        except ProviderError:
            return None
        return [m.get("name") or m.get("model") for m in data.get("models", [])]

    async def list_models(self) -> list[ModelInfo]:
        data = await self._get_json("/api/tags")
        loaded = set(await self.loaded_models() or [])
        out: list[ModelInfo] = []
        for m in data.get("models", []):
            name = m.get("name") or m.get("model")
            details = m.get("details") or {}
            try:
                caps = _caps_from_show(await self._show(name))
            except ProviderError:
                caps = Capabilities()
            self._caps_cache[name] = caps
            is_loaded = name in loaded
            out.append(
                self._model(
                    name,
                    family=details.get("family"),
                    parameter_size=details.get("parameter_size"),
                    quantization=details.get("quantization_level"),
                    format=details.get("format"),
                    size_bytes=m.get("size"),
                    loaded=is_loaded,
                    state=ModelState.MODEL_LOADED if is_loaded else ModelState.MODEL_DISCOVERED,
                    capabilities=caps,
                )
            )
        return out

    # ----- 推論 -----
    @staticmethod
    def _messages(msgs: list[ChatMessage]) -> list[dict[str, Any]]:
        out = []
        for m in msgs:
            d: dict[str, Any] = {"role": m.role, "content": m.content}
            if m.images:
                d["images"] = m.images
            if m.tool_calls:
                d["tool_calls"] = [
                    {"function": {"name": c.name, "arguments": c.arguments}} for c in m.tool_calls
                ]
            out.append(d)
        return out

    async def _body(self, model_id: str, req: GenerateRequest, stream: bool) -> dict[str, Any]:
        options: dict[str, Any] = {}
        if req.temperature is not None:
            options["temperature"] = req.temperature
        if req.max_tokens is not None:
            options["num_predict"] = req.max_tokens
        if req.context_length is not None:
            options["num_ctx"] = req.context_length
        body: dict[str, Any] = {
            "model": model_id,
            "messages": self._messages(req.messages),
            "stream": stream,
            "options": options,
        }
        if req.json_schema is not None:
            body["format"] = req.json_schema
        if req.tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in req.tools
            ]
        # think は thinking 対応モデルにのみ渡す（非対応モデルはエラーになる）
        if req.think is not None:
            try:
                caps = await self.get_capabilities(model_id)
            except ProviderError:
                caps = Capabilities()
            if caps.thinking:
                body["think"] = req.think
        return body

    async def generate(self, model_id: str, req: GenerateRequest) -> GenerateResult:
        body = await self._body(model_id, req, stream=False)
        data = await self._post_json("/api/chat", body, timeout=req.timeout_s)
        msg = data.get("message") or {}
        text = msg.get("content") or ""
        calls = [
            ToolCall(
                id=c.get("id"),
                name=c["function"]["name"],
                arguments=c["function"].get("arguments") or {},
            )
            for c in msg.get("tool_calls") or []
        ]
        fallback = False
        if req.tools and not calls:
            calls = parse_tool_calls(text, {t.name for t in req.tools})
            fallback = bool(calls)
        eval_count = data.get("eval_count")
        eval_s = (data.get("eval_duration") or 0) / 1e9
        return GenerateResult(
            model_id=model_id,
            text=text,
            tool_calls=calls,
            tool_calls_from_fallback=fallback,
            finish_reason=data.get("done_reason"),
            prompt_tokens=data.get("prompt_eval_count"),
            completion_tokens=eval_count,
            duration_ms=(data.get("total_duration") or 0) / 1e6 or None,
            tokens_per_sec=tokens_per_sec(eval_count, eval_s),
        )

    async def stream(self, model_id: str, req: GenerateRequest) -> AsyncIterator[StreamChunk]:
        body = await self._body(model_id, req, stream=True)
        async for line in self._stream_lines("/api/chat", body, timeout=req.timeout_s):
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "error" in data:
                raise ProviderError(str(data["error"])[:300])
            delta = (data.get("message") or {}).get("content") or ""
            done = bool(data.get("done"))
            if delta or done:
                yield StreamChunk(delta=delta, done=done)

    async def load_model(self, model_id: str) -> None:
        await self._post_json("/api/generate", {"model": model_id, "keep_alive": "10m"})

    async def unload_model(self, model_id: str) -> None:
        await self._post_json("/api/generate", {"model": model_id, "keep_alive": 0}, 60)
