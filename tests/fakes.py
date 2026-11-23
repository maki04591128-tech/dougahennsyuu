"""テスト用の偽推論サーバー（httpx.MockTransport）。

2026-11-23 の実機調査（Ollama 0.40.16 + 3モデル）で観測した挙動を再現する:
- qwen3.5-9b-local / gemma4-e4b-local: tool_calls をネイティブ返却
- qwen3-swallow-8b-local: tool_calls を返さず本文にJSONを書く
- gemma4-e4b-local: JSON Schema の形式は守るが duration_sec が値域外
- いずれも mmproj 無しのため画像入力は 400
"""

import json
from typing import Any

import httpx

OLLAMA = "127.0.0.1:11434"
LMSTUDIO = "127.0.0.1:1234"

OLLAMA_MODELS: dict[str, dict[str, Any]] = {
    "qwen3.5-9b-local:latest": {
        "family": "qwen35",
        "params": "9.0B",
        "quant": "Q4_K_M",
        "size": 5_290_000_000,
        "ctx_key": "qwen35.context_length",
        "ctx": 262144,
        "tools": "native",
        "json": {"title": "未来都市", "duration_sec": 845, "tags": ["SF"]},
    },
    "gemma4-e4b-local:latest": {
        "family": "gemma4",
        "params": "7.5B",
        "quant": "Q4_0",
        "size": 4_280_000_000,
        "ctx_key": "gemma4.context_length",
        "ctx": 131072,
        "tools": "native",
        "json": {"title": "AI Vlog", "duration_sec": 1245000000000000, "tags": ["AI"]},
    },
    "qwen3-swallow-8b-local:latest": {
        "family": "qwen3",
        "params": "8.2B",
        "quant": "Q4_K_M",
        "size": 4_680_000_000,
        "ctx_key": "qwen3.context_length",
        "ctx": 40960,
        "tools": "content",
        "json": {"title": "攻略ガイド", "duration_sec": 423, "tags": ["ゲーム"]},
    },
}


class FakeServers:
    def __init__(self) -> None:
        self.ollama_up = True
        self.lmstudio_up = False
        self.lmstudio_rest_v1 = True
        self.ollama_loaded: set[str] = set()
        self.lm_loaded: dict[str, str] = {"qwen/qwen3-8b": "inst-1"}
        self.requests: list[tuple[str, str, Any]] = []

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        host = f"{request.url.host}:{request.url.port}"
        body = json.loads(request.content) if request.content else None
        self.requests.append((host, request.url.path, body))
        if host == OLLAMA and self.ollama_up:
            return self._ollama(request, body)
        if host == LMSTUDIO and self.lmstudio_up:
            return self._lmstudio(request, body)
        raise httpx.ConnectError("connection refused", request=request)

    # ---------------- Ollama ----------------
    def _ollama(self, req: httpx.Request, body: Any) -> httpx.Response:
        path = req.url.path
        if path == "/api/version":
            return httpx.Response(200, json={"version": "0.40.16"})
        if path == "/api/tags":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": n,
                            "model": n,
                            "size": m["size"],
                            "details": {
                                "format": "gguf",
                                "family": m["family"],
                                "parameter_size": m["params"],
                                "quantization_level": m["quant"],
                            },
                        }
                        for n, m in OLLAMA_MODELS.items()
                    ]
                },
            )
        if path == "/api/show":
            m = OLLAMA_MODELS.get(body["model"])
            if m is None:
                return httpx.Response(404, json={"error": f"model '{body['model']}' not found"})
            return httpx.Response(
                200,
                json={
                    "capabilities": ["completion", "tools", "thinking"],
                    "model_info": {m["ctx_key"]: m["ctx"]},
                },
            )
        if path == "/api/ps":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {"name": n, "model": n, "size_vram": 5_000_000_000}
                        for n in self.ollama_loaded
                    ]
                },
            )
        if path == "/api/generate":
            if body.get("keep_alive") == 0:
                self.ollama_loaded.discard(body["model"])
            else:
                self.ollama_loaded.add(body["model"])
            return httpx.Response(200, json={"model": body["model"], "done": True})
        if path == "/api/chat":
            return self._ollama_chat(body)
        return httpx.Response(404, text="404 page not found")

    def _ollama_chat(self, body: dict[str, Any]) -> httpx.Response:
        name = body["model"]
        m = OLLAMA_MODELS.get(name)
        if m is None:
            return httpx.Response(404, json={"error": f"model '{name}' not found"})
        if any(msg.get("images") for msg in body["messages"]):
            return httpx.Response(
                400,
                json={
                    "error": "Multimodal data provided, "
                    "but model does not support multimodal requests."
                },
            )
        self.ollama_loaded.add(name)
        msg: dict[str, Any] = {"role": "assistant", "content": ""}
        if body.get("tools"):
            tool = body["tools"][0]["function"]["name"]
            if m["tools"] == "native":
                msg["tool_calls"] = [
                    {
                        "id": "call_1",
                        "function": {"index": 0, "name": tool, "arguments": {"city": "東京"}},
                    }
                ]
            else:
                msg["content"] = json.dumps(
                    {"name": tool, "arguments": {"city": "東京"}}, ensure_ascii=False
                )
        elif body.get("format"):
            msg["content"] = json.dumps(m["json"], ensure_ascii=False)
        else:
            msg["content"] = "動画編集を自動化すると作業時間を短縮できます。品質も安定します。"
        if body.get("stream"):
            lines = [
                json.dumps(
                    {"message": {"content": "こんにちは"}, "done": False}, ensure_ascii=False
                ),
                json.dumps({"message": {"content": "！"}, "done": False}, ensure_ascii=False),
                json.dumps({"message": {"content": ""}, "done": True}),
            ]
            return httpx.Response(200, content=("\n".join(lines) + "\n").encode())
        return httpx.Response(
            200,
            json={
                "model": name,
                "message": msg,
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": 20,
                "eval_count": 60,
                "eval_duration": 1_200_000_000,
                "total_duration": 2_000_000_000,
            },
        )

    # ---------------- LM Studio ----------------
    def _instances(self, key: str) -> list[dict[str, Any]]:
        inst = self.lm_loaded.get(key)
        return [{"id": inst, "config": {"context_length": 4096}}] if inst else []

    def _lmstudio(self, req: httpx.Request, body: Any) -> httpx.Response:
        path = req.url.path
        models = [
            {
                "type": "llm",
                "key": "qwen/qwen3-8b",
                "display_name": "Qwen3 8B",
                "architecture": "qwen3",
                "quantization": {"name": "Q4_K_M", "bits_per_weight": 4},
                "size_bytes": 5_000_000_000,
                "params_string": "8B",
                "loaded_instances": self._instances("qwen/qwen3-8b"),
                "max_context_length": 40960,
                "format": "gguf",
                "capabilities": {"vision": False, "trained_for_tool_use": True},
            },
            {
                "type": "llm",
                "key": "google/gemma-4-e4b",
                "display_name": "Gemma 4 E4B",
                "quantization": {"name": "Q4_K_M"},
                "loaded_instances": self._instances("google/gemma-4-e4b"),
                "max_context_length": 131072,
                "format": "gguf",
                "capabilities": {"vision": True, "trained_for_tool_use": True},
            },
            {"type": "embedding", "key": "text-embedding-nomic", "loaded_instances": []},
        ]
        if path == "/api/v1/models" and self.lmstudio_rest_v1:
            return httpx.Response(200, json={"models": models})
        if path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": m["key"]} for m in models]})
        if path == "/api/v1/models/load":
            self.lm_loaded[body["model"]] = f"inst-{len(self.lm_loaded) + 1}"
            return httpx.Response(200, json={"type": "llm", "instance_id": "x", "status": "loaded"})
        if path == "/api/v1/models/unload":
            self.lm_loaded = {k: v for k, v in self.lm_loaded.items() if v != body["instance_id"]}
            return httpx.Response(200, json={"instance_id": body["instance_id"]})
        if path == "/v1/chat/completions":
            if body.get("stream"):
                chunks = [
                    {"choices": [{"delta": {"content": "やあ"}, "finish_reason": None}]},
                    {"choices": [{"delta": {}, "finish_reason": "stop"}]},
                ]
                sse = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
                return httpx.Response(200, content=sse.encode())
            msg: dict[str, Any] = {"role": "assistant", "content": "こんにちは"}
            if body.get("tools"):
                msg = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {
                                "name": body["tools"][0]["function"]["name"],
                                "arguments": '{"city": "東京"}',
                            },
                        }
                    ],
                }
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": msg, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 30},
                    "stats": {"tokens_per_second": 42.04},
                },
            )
        return httpx.Response(404, json={"error": "Unexpected endpoint"})


class FakeProtector:
    """DPAPIの代替（テスト用）。可逆だが平文ではない。"""

    def protect(self, data: bytes) -> bytes:
        return b"ENC:" + bytes(b ^ 0x5A for b in data)

    def unprotect(self, blob: bytes) -> bytes:
        assert blob.startswith(b"ENC:")
        return bytes(b ^ 0x5A for b in blob[4:])
