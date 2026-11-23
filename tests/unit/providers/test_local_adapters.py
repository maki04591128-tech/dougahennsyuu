import pytest

from video_automation.providers.base import ProviderError
from video_automation.providers.llm.lmstudio import LMStudioProvider
from video_automation.providers.llm.ollama import OllamaProvider
from video_automation.providers.llm.openai_compatible import OpenAICompatibleProvider
from video_automation.schemas.provider import (
    ChatMessage,
    GenerateRequest,
    ModelState,
    ProviderConfig,
    ProviderType,
    ToolSpec,
)

TOOL = ToolSpec(name="get_weather", parameters={"type": "object", "properties": {}})


def _ollama(fake):
    cfg = ProviderConfig(
        id="ollama-local",
        type=ProviderType.OLLAMA,
        name="Ollama",
        base_url="http://127.0.0.1:11434",
    )
    return OllamaProvider(cfg, transport=fake.transport)


def _lms(fake, url="http://127.0.0.1:1234"):
    cfg = ProviderConfig(id="lms", type=ProviderType.LMSTUDIO, name="LM Studio", base_url=url)
    return LMStudioProvider(cfg, transport=fake.transport)


def _user(text, **kw):
    return GenerateRequest(messages=[ChatMessage(role="user", content=text, **kw)])


async def test_ollama_discover_reads_real_ids_and_capabilities(fake):
    p = _ollama(fake)
    status, models = await p.discover()
    assert status.available and status.server_version == "0.40.16"
    assert status.models_count == 3
    swallow = next(m for m in models if "swallow" in m.model_id)
    assert swallow.key == "ollama-local::qwen3-swallow-8b-local:latest"
    assert swallow.quantization == "Q4_K_M" and swallow.family == "qwen3"
    assert swallow.capabilities.context_length == 40960
    assert swallow.capabilities.tool_calling is True  # 申告値（実測で覆る）
    assert swallow.capabilities.image_input is False
    assert swallow.state == ModelState.MODEL_DISCOVERED and swallow.loaded is False
    await p.aclose()


async def test_ollama_unavailable(fake):
    fake.ollama_up = False
    p = _ollama(fake)
    status, models = await p.discover()
    assert not status.available and models == []
    await p.aclose()


async def test_ollama_generate_passes_think_and_measures_speed(fake):
    p = _ollama(fake)
    r = await p.generate("qwen3.5-9b-local:latest", _user("こんにちは"))
    assert r.text and r.completion_tokens == 60 and r.tokens_per_sec == 50.0
    sent = [b for h, path, b in fake.requests if path == "/api/chat"][-1]
    assert sent["think"] is False  # thinking 対応モデルにのみ付与
    assert "qwen3.5-9b-local:latest" in await p.loaded_models()
    await p.unload_model("qwen3.5-9b-local:latest")
    assert await p.loaded_models() == []
    await p.aclose()


async def test_ollama_tool_calls_native_and_fallback(fake):
    p = _ollama(fake)
    req = _user("東京の天気")
    req.tools = [TOOL]
    native = await p.generate("qwen3.5-9b-local:latest", req)
    assert native.tool_calls[0].name == "get_weather" and not native.tool_calls_from_fallback
    fb = await p.generate("qwen3-swallow-8b-local:latest", req)
    assert fb.tool_calls[0].arguments == {"city": "東京"} and fb.tool_calls_from_fallback
    await p.aclose()


async def test_ollama_stream_and_vision_error(fake):
    p = _ollama(fake)
    chunks = [c async for c in p.stream("gemma4-e4b-local:latest", _user("hi"))]
    assert "".join(c.delta for c in chunks) == "こんにちは！" and chunks[-1].done
    with pytest.raises(ProviderError, match="400"):
        await p.generate("gemma4-e4b-local:latest", _user("色は？", images=["AAAA"]))
    await p.aclose()


async def test_lmstudio_rest_v1_models_and_load_unload(fake):
    fake.lmstudio_up = True
    p = _lms(fake)
    status = await p.check_connection()
    assert status.available and status.api_format == "lmstudio-rest-v1"
    models = await p.list_models()
    assert [m.model_id for m in models] == ["qwen/qwen3-8b", "google/gemma-4-e4b"]  # embedding除外
    q, g = models
    assert q.loaded and q.state == ModelState.MODEL_LOADED and q.quantization == "Q4_K_M"
    assert g.capabilities.image_input is True and g.capabilities.context_length == 131072
    await p.unload_model("qwen/qwen3-8b")
    assert await p.loaded_models() == []
    await p.load_model("google/gemma-4-e4b")
    assert await p.loaded_models() == ["google/gemma-4-e4b"]
    await p.aclose()


async def test_lmstudio_falls_back_to_openai_models(fake):
    fake.lmstudio_up, fake.lmstudio_rest_v1 = True, False
    p = _lms(fake)
    status = await p.check_connection()
    assert status.api_format == "openai-compatible"
    models = await p.list_models()
    assert all(m.state == ModelState.UNKNOWN for m in models)
    await p.aclose()


async def test_openai_compatible_generate_tools_and_stream(fake):
    fake.lmstudio_up = True
    cfg = ProviderConfig(
        id="gen",
        type=ProviderType.OPENAI_COMPATIBLE,
        name="llama.cpp",
        base_url="http://127.0.0.1:1234/v1",
    )
    p = OpenAICompatibleProvider(cfg, transport=fake.transport)
    assert p.v1 == ""
    req = _user("天気")
    req.tools = [TOOL]
    req.json_schema = {"type": "object"}
    r = await p.generate("qwen/qwen3-8b", req)
    assert r.tool_calls[0].arguments == {"city": "東京"} and r.tokens_per_sec == 42.0
    sent = [b for h, path, b in fake.requests if path == "/v1/chat/completions"][-1]
    assert sent["response_format"]["type"] == "json_schema"
    chunks = [c async for c in p.stream("qwen/qwen3-8b", _user("hi"))]
    assert chunks[0].delta == "やあ" and chunks[-1].done
    await p.aclose()
