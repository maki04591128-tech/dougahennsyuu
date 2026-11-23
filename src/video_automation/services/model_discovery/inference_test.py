"""Capability判定・推論テスト (P2-06 / P2-07)。

申告値（/api/show 等）を実測（プローブ）で上書きする。実機で判明した注意点:
- capabilities に tools があっても tool_calls を返さないモデルがある（Swallow）→ フォールバック判定
- JSON Schema の形式には従っても値域は保証されない（Gemma）→ 値域も検証
- mmproj 無し GGUF は画像入力不可
"""

import base64
import json
import struct
import time
import zlib
from collections.abc import Awaitable, Callable
from typing import Any

from video_automation.app.logging import get_logger
from video_automation.database.repositories.llm import LlmRepository
from video_automation.providers.base import ProviderError
from video_automation.schemas.provider import (
    ChatMessage,
    GenerateRequest,
    ModelState,
    ModelTestResult,
    ProbeResult,
    ToolSpec,
)
from video_automation.services.model_discovery.provider_manager import ProviderManager

log = get_logger("inference_test")
ProgressFn = Callable[[float], Awaitable[None]]

ALL_PROBES = ("basic", "streaming", "structured", "tools", "vision")

PROBE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "duration_sec": {"type": "integer", "minimum": 1, "maximum": 86400},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "duration_sec", "tags"],
    "additionalProperties": False,
}
PROBE_TOOL = ToolSpec(
    name="get_weather",
    description="指定した都市の現在の天気を取得する",
    parameters={
        "type": "object",
        "properties": {"city": {"type": "string", "description": "都市名"}},
        "required": ["city"],
    },
)


def solid_png_b64(rgb: tuple[int, int, int] = (255, 0, 0), size: int = 32) -> str:
    """依存ライブラリなしで単色PNGを作る（画像入力プローブ用）。"""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    row = b"\x00" + bytes(rgb) * size
    raw = row * size
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    return base64.b64encode(png).decode("ascii")


def validate_probe_json(text: str) -> tuple[bool, str]:
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`").removeprefix("json").strip()
    try:
        obj = json.loads(t)
    except json.JSONDecodeError:
        return False, "not JSON"
    if not isinstance(obj, dict):
        return False, "not an object"
    if not isinstance(obj.get("title"), str) or not obj["title"]:
        return False, "title invalid"
    d = obj.get("duration_sec")
    if not isinstance(d, int) or isinstance(d, bool):
        return False, "duration_sec not integer"
    if not 1 <= d <= 86400:
        return False, f"duration_sec out of range ({d})"
    tags = obj.get("tags")
    if not isinstance(tags, list) or not all(isinstance(x, str) for x in tags):
        return False, "tags invalid"
    return True, "schema and value range ok"


class InferenceTester:
    def __init__(self, manager: ProviderManager) -> None:
        self.manager = manager

    async def run(
        self,
        model_key: str,
        probes: list[str] | None = None,
        progress: ProgressFn | None = None,
    ) -> ModelTestResult:
        selected = [p for p in ALL_PROBES if probes is None or p in probes]
        model = await self.manager.get_model(model_key)
        provider, model_id = await self.manager.resolve(model_key)
        if self.manager.settings.single_loaded_model:
            unloaded = await self.manager.unload_others(model_key)
            if unloaded:
                log.info("unloaded_for_test", models=unloaded)

        results: list[ProbeResult] = []
        probed: dict[str, Any] = {}
        tps: float | None = None
        sample: str | None = None
        think = False if model.capabilities.thinking else None

        def req(**kw: Any) -> GenerateRequest:
            kw.setdefault("temperature", 0.2)
            kw.setdefault("max_tokens", 200)
            return GenerateRequest(think=think, **kw)

        for i, name in enumerate(selected):
            t0 = time.perf_counter()
            ok: bool | None = None
            detail: str | None = None
            try:
                if name == "basic":
                    r = await provider.generate(
                        model_id,
                        req(
                            messages=[
                                ChatMessage(
                                    role="user",
                                    content="日本語で、動画編集を自動化する利点を2文で説明してください。",
                                )
                            ]
                        ),
                    )
                    ok = bool(r.text.strip())
                    tps, sample = r.tokens_per_sec, r.text.strip()[:400]
                    probed["text_input"] = ok
                    detail = f"{r.completion_tokens} tokens"
                elif name == "streaming":
                    chunks, text = 0, ""
                    async for c in provider.stream(
                        model_id,
                        req(
                            messages=[ChatMessage(role="user", content="一言で挨拶してください。")],
                            max_tokens=32,
                        ),
                    ):
                        if c.delta:
                            chunks += 1
                            text += c.delta
                    ok = chunks >= 1 and bool(text.strip())
                    probed["streaming"] = ok
                    detail = f"{chunks} chunks"
                elif name == "structured":
                    r = await provider.generate(
                        model_id,
                        req(
                            messages=[
                                ChatMessage(
                                    role="user",
                                    content="架空のYouTube動画のメタデータを1件、指定スキーマのJSONだけで出力してください。"
                                    "duration_secは秒数(1〜86400)です。",
                                )
                            ],
                            json_schema=PROBE_SCHEMA,
                        ),
                    )
                    ok, detail = validate_probe_json(r.text)
                    probed["structured_output"] = ok
                elif name == "tools":
                    r = await provider.generate(
                        model_id,
                        req(
                            messages=[
                                ChatMessage(role="user", content="東京の今の天気を調べてください。")
                            ],
                            tools=[PROBE_TOOL],
                        ),
                    )
                    call = next((c for c in r.tool_calls if c.name == PROBE_TOOL.name), None)
                    if call and not r.tool_calls_from_fallback:
                        ok, detail = True, "native tool_calls"
                        probed["tool_calling"], probed["tool_calling_fallback"] = True, False
                    elif call:
                        ok, detail = True, "fallback (JSON in content)"
                        probed["tool_calling"], probed["tool_calling_fallback"] = False, True
                    else:
                        ok, detail = False, "no tool call"
                        probed["tool_calling"], probed["tool_calling_fallback"] = False, False
                elif name == "vision":
                    r = await provider.generate(
                        model_id,
                        req(
                            messages=[
                                ChatMessage(
                                    role="user",
                                    content="この画像は何色ですか？色の名前だけ答えてください。",
                                    images=[solid_png_b64()],
                                )
                            ],
                            max_tokens=32,
                        ),
                    )
                    ok = any(w in r.text.lower() for w in ("赤", "red", "レッド"))
                    probed["image_input"] = ok
                    detail = r.text.strip()[:60]
            except ProviderError as exc:
                ok, detail = False, str(exc)[:200]
                if name == "vision":
                    probed["image_input"] = False
                elif name == "basic":
                    probed["text_input"] = False
            results.append(
                ProbeResult(
                    name=name,
                    ok=ok,
                    duration_ms=round((time.perf_counter() - t0) * 1000),
                    detail=detail,
                )
            )
            if progress:
                await progress((i + 1) / len(selected))

        caps = model.capabilities.merge_probed(**probed)
        basic = next((p for p in results if p.name == "basic"), None)
        success = basic.ok is True if basic else all(p.ok for p in results if p.ok is not None)
        result = ModelTestResult(
            model_key=model_key,
            success=success,
            probes=results,
            tokens_per_sec=tps,
            sample_text=sample,
            capabilities=caps,
        )
        info = model.model_dump(mode="json", exclude={"is_active", "last_test"})
        info["capabilities"] = caps.model_dump(mode="json")
        if success:
            info["state"] = ModelState.INFERENCE_READY.value
        async with self.manager.db.session_factory() as session:
            await LlmRepository(session).save_model_test(
                model_key, info, result.model_dump(mode="json")
            )
            await session.commit()
        return result
