import json

import httpx

from video_automation.providers.llm.cloud import AnthropicProvider, GeminiProvider, OpenAIProvider
from video_automation.providers.llm.tool_fallback import parse_tool_calls
from video_automation.schemas.provider import (
    ChatMessage,
    GenerateRequest,
    ProviderConfig,
    ProviderType,
    ToolSpec,
)


def _cfg(t, url):
    return ProviderConfig(id=t.value, type=t, name=t.value, base_url=url)


def _recording(handler):
    seen = []

    def h(request: httpx.Request):
        seen.append(request)
        return handler(request)

    return httpx.MockTransport(h), seen


async def test_cloud_without_key_is_unavailable():
    p = OpenAIProvider(_cfg(ProviderType.OPENAI, "https://api.openai.com/v1"))
    status = await p.check_connection()
    assert not status.available and status.error == "api key not configured"
    await p.aclose()


async def test_anthropic_structured_output_uses_forced_tool():
    def handler(req):
        body = json.loads(req.content)
        assert body["tool_choice"] == {"type": "tool", "name": "emit_structured_output"}
        assert body["system"] == "sys" and body["max_tokens"] == 1024
        return httpx.Response(
            200,
            json={
                "content": [
                    {
                        "type": "tool_use",
                        "id": "t",
                        "name": "emit_structured_output",
                        "input": {"a": 1},
                    }
                ],
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 5, "output_tokens": 7},
            },
        )

    transport, seen = _recording(handler)
    p = AnthropicProvider(
        _cfg(ProviderType.ANTHROPIC, "https://api.anthropic.com"), api_key="k", transport=transport
    )
    r = await p.generate(
        "claude-x",
        GenerateRequest(
            messages=[
                ChatMessage(role="system", content="sys"),
                ChatMessage(role="user", content="q"),
            ],
            json_schema={"type": "object"},
        ),
    )
    assert json.loads(r.text) == {"a": 1} and r.completion_tokens == 7
    assert seen[0].headers["x-api-key"] == "k"
    await p.aclose()


async def test_gemini_parses_text_and_function_call():
    def handler(req):
        assert req.url.path.endswith("/models/gemini-x:generateContent")
        body = json.loads(req.content)
        assert body["tools"][0]["functionDeclarations"][0]["name"] == "f"
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "ok"},
                                {"functionCall": {"name": "f", "args": {"x": 1}}},
                            ]
                        },
                        "finishReason": "STOP",
                    }
                ]
            },
        )

    transport, seen = _recording(handler)
    p = GeminiProvider(
        _cfg(ProviderType.GEMINI, "https://generativelanguage.googleapis.com/v1beta"),
        api_key="g",
        transport=transport,
    )
    r = await p.generate(
        "gemini-x",
        GenerateRequest(
            messages=[ChatMessage(role="user", content="q")], tools=[ToolSpec(name="f")]
        ),
    )
    assert r.text == "ok" and r.tool_calls[0].arguments == {"x": 1}
    assert seen[0].headers["x-goog-api-key"] == "g"
    await p.aclose()


def test_tool_fallback_variants():
    allowed = {"get_weather"}
    assert parse_tool_calls('{"name": "get_weather", "arguments": {"city": "東京"}}', allowed)[
        0
    ].arguments == {"city": "東京"}
    tagged = (
        '考え中\n<tool_call>{"name":"get_weather",'
        '"arguments":"{\\"city\\":\\"大阪\\"}"}</tool_call>'
    )
    assert parse_tool_calls(tagged, allowed)[0].arguments == {"city": "大阪"}
    fenced = '```json\n[{"function": {"name": "get_weather", "parameters": {}}}]\n```'
    assert parse_tool_calls(fenced, allowed)[0].name == "get_weather"
    assert parse_tool_calls('{"name": "rm_rf", "arguments": {}}', allowed) == []
    assert parse_tool_calls("ただの文章です", allowed) == []
    assert parse_tool_calls("{broken json", allowed) == []
