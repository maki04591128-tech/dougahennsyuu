"""OpenAI互換 Adapter (P2-04)。llama.cpp server / vLLM / LocalAI / LM Studio(/v1) 等。

登録URLが /v1 で終わればそのまま、そうでなければ /v1 を付けてアクセスする。
"""

import json
from collections.abc import AsyncIterator
from typing import Any

from video_automation.providers.base import LLMProvider, ProviderError
from video_automation.providers.llm.tool_fallback import parse_tool_calls
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


class OpenAICompatibleProvider(LLMProvider):
    type = ProviderType.OPENAI_COMPATIBLE

    @property
    def v1(self) -> str:
        return "" if self.config.base_url.rstrip("/").endswith("/v1") else "/v1"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}

    async def check_connection(self) -> ProviderStatus:
        try:
            await self._get_json(f"{self.v1}/models")
        except ProviderError as exc:
            return self._status(False, error=str(exc) or exc.code)
        except Exception as exc:  # HTTPStatusError 等
            return self._status(False, error=type(exc).__name__)
        return self._status(True, api_format="openai-compatible(/v1)")

    async def list_models(self) -> list[ModelInfo]:
        data = await self._get_json(f"{self.v1}/models")
        # OpenAI互換APIはロード状態を返さないため UNKNOWN
        return [
            self._model(m["id"], state=ModelState.UNKNOWN, capabilities=Capabilities())
            for m in data.get("data", [])
            if isinstance(m, dict) and m.get("id")
        ]

    # ----- 推論 -----
    @staticmethod
    def _messages(msgs: list[ChatMessage]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for m in msgs:
            content: Any = m.content
            if m.images:
                content = [{"type": "text", "text": m.content}] + [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img}"}}
                    for img in m.images
                ]
            d: dict[str, Any] = {"role": m.role, "content": content}
            if m.tool_calls:
                d["tool_calls"] = [
                    {
                        "id": c.id or f"call_{i}",
                        "type": "function",
                        "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                    }
                    for i, c in enumerate(m.tool_calls)
                ]
            if m.tool_call_id:
                d["tool_call_id"] = m.tool_call_id
            out.append(d)
        return out

    def _body(self, model_id: str, req: GenerateRequest, stream: bool) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": model_id,
            "messages": self._messages(req.messages),
            "stream": stream,
        }
        if req.temperature is not None:
            body["temperature"] = req.temperature
        if req.max_tokens is not None:
            body["max_tokens"] = req.max_tokens
        if req.json_schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "output", "strict": True, "schema": req.json_schema},
            }
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
        return body

    async def generate(self, model_id: str, req: GenerateRequest) -> GenerateResult:
        data = await self._post_json(
            f"{self.v1}/chat/completions", self._body(model_id, req, False), req.timeout_s
        )
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        text = msg.get("content") or ""
        calls: list[ToolCall] = []
        for c in msg.get("tool_calls") or []:
            fn = c.get("function") or {}
            args = fn.get("arguments") or "{}"
            try:
                parsed = json.loads(args) if isinstance(args, str) else args
            except json.JSONDecodeError:
                parsed = {}
            calls.append(ToolCall(id=c.get("id"), name=fn.get("name", ""), arguments=parsed))
        fallback = False
        if req.tools and not calls:
            calls = parse_tool_calls(text, {t.name for t in req.tools})
            fallback = bool(calls)
        usage = data.get("usage") or {}
        stats = data.get("stats") or {}  # LM Studio 拡張
        ct = usage.get("completion_tokens")
        tps = stats.get("tokens_per_second")
        return GenerateResult(
            model_id=model_id,
            text=text,
            tool_calls=calls,
            tool_calls_from_fallback=fallback,
            finish_reason=choice.get("finish_reason"),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=ct,
            tokens_per_sec=round(tps, 1) if isinstance(tps, int | float) else None,
        )

    async def stream(self, model_id: str, req: GenerateRequest) -> AsyncIterator[StreamChunk]:
        body = self._body(model_id, req, True)
        async for line in self._stream_lines(f"{self.v1}/chat/completions", body, req.timeout_s):
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                yield StreamChunk(done=True)
                return
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                continue
            choice = (data.get("choices") or [{}])[0]
            delta = (choice.get("delta") or {}).get("content") or ""
            if delta:
                yield StreamChunk(delta=delta)
            if choice.get("finish_reason"):
                yield StreamChunk(done=True)
                return
