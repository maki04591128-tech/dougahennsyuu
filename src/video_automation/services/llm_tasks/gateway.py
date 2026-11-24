"""LLM 呼び出しの共通窓口（Phase 8）。

- タスク割当（P2-09）または明示指定のモデルで実行する（LLM切替・再実行: P8-09）
- 同時ロード1モデル運用ではほかのモデルをアンロードしてから呼ぶ
- 構造化出力は JSON Schema で検証し、合わなければ誤りを伝えて1回だけやり直させる
- 思考タグ（<think>…</think>）や前後の説明文が付いた出力からも JSON を取り出す
"""

import json
import re
import time
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator

from video_automation.app.logging import get_logger
from video_automation.app.settings import Settings
from video_automation.database.database import Database
from video_automation.database.repositories.llm import LlmRepository
from video_automation.providers.base import ProviderError
from video_automation.providers.llm.tool_fallback import _json_objects
from video_automation.schemas.provider import ChatMessage, GenerateRequest, TaskType
from video_automation.services.llm_tasks.task_router import TaskRouter
from video_automation.services.model_discovery.provider_manager import ProviderManager

log = get_logger("llm")
_THINK = re.compile(r"<think>.*?</think>", re.S)


class LLMError(Exception):
    code = "llm_error"
    public_message = True  # 機密を含めない（モデル名・検証エラーのみ）


class NoModelError(LLMError):
    code = "no_llm_available"


class LLMOutputError(LLMError):
    code = "llm_output_invalid"


@dataclass
class LLMResult:
    data: Any
    text: str
    model_key: str
    duration_ms: float
    attempts: int


def extract_json(text: str) -> Any:
    t = _THINK.sub("", text).strip()
    try:
        return json.loads(t)
    except ValueError:
        pass
    objs = [o for o in _json_objects(t) if isinstance(o, dict)]
    if not objs:
        raise LLMOutputError("no JSON object in model output")
    return max(objs, key=lambda o: len(json.dumps(o, ensure_ascii=False)))


class LLMGateway:
    def __init__(
        self, settings: Settings, db: Database, providers: ProviderManager, router: TaskRouter
    ) -> None:
        self.settings = settings
        self.db = db
        self.providers = providers
        self.router = router

    async def resolve(self, task: TaskType, model_key: str | None = None) -> str:
        if model_key:
            await self.providers.get_model(model_key)  # 存在確認（無ければ 404）
            return model_key
        models = await self.providers.list_models()
        async with self.db.session_factory() as s:
            assigned = await LlmRepository(s).get_assignments()
        r = self.router.resolve(task, models, assigned, await self.providers.active_key())
        if not r.resolved_model_key:
            raise NoModelError(f"no model available for task {task.value}")
        return r.resolved_model_key

    async def _call(
        self, key: str, messages: list[ChatMessage], schema: dict | None, temperature: float | None
    ) -> tuple[str, float]:
        provider, model_id = await self.providers.resolve(key)
        if self.settings.single_loaded_model:
            try:
                await self.providers.unload_others(key)
            except ProviderError:
                pass
        ctx = await self._context_length(key, messages)
        t0 = time.perf_counter()
        res = await provider.generate(
            model_id,
            GenerateRequest(
                messages=messages,
                json_schema=schema,
                temperature=temperature,
                context_length=ctx,
            ),
        )
        return res.text or "", (time.perf_counter() - t0) * 1000

    async def _context_length(self, key: str, messages: list[ChatMessage]) -> int | None:
        """プロンプトが切れないよう、渡す文の長さ（num_ctx）を決める。

        日本語は 1 文字 ≒ 1 トークンとして多めに見積もり、出力の分を足して 4096 単位に切り上げる。
        モデルの上限と設定の上限（llm_max_context）を超えない。短いときは指定しない（既定のまま）。
        """
        need = sum(len(m.content or "") for m in messages) + 4096
        if need <= 4096 + 2048:
            return None
        ctx = -(-need // 4096) * 4096
        try:
            cap = (await self.providers.get_model(key)).capabilities.context_length
        except Exception:  # noqa: BLE001 - 上限が分からなければ設定の上限だけで決める
            cap = None
        limit = min(self.settings.llm_max_context, cap or self.settings.llm_max_context)
        return max(4096, min(ctx, limit))

    async def complete_json(
        self,
        task: TaskType,
        messages: list[ChatMessage],
        schema: dict[str, Any],
        *,
        model_key: str | None = None,
        temperature: float | None = None,
        retries: int = 1,
    ) -> LLMResult:
        key = await self.resolve(task, model_key)
        validator = Draft202012Validator(schema)
        msgs = list(messages)
        total_ms = 0.0
        last_err = ""
        for attempt in range(1, retries + 2):
            text, ms = await self._call(key, msgs, schema, temperature)
            total_ms += ms
            try:
                data = extract_json(text)
                errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
                if not errors:
                    log.info(
                        "llm_json_ok",
                        task=task.value,
                        model=key,
                        attempt=attempt,
                        ms=round(total_ms),
                    )
                    return LLMResult(data, text, key, total_ms, attempt)
                last_err = "; ".join(
                    f"{'/'.join(map(str, e.path)) or '(root)'}: {e.message}" for e in errors[:5]
                )
            except LLMOutputError as exc:
                last_err = str(exc)
            log.warning(
                "llm_json_invalid",
                task=task.value,
                model=key,
                attempt=attempt,
                error=last_err[:300],
            )
            msgs = [
                *messages,
                ChatMessage(role="assistant", content=text[:4000]),
                ChatMessage(
                    role="user",
                    content=f"出力が指定のJSONスキーマに合いません（{last_err[:500]}）。"
                    "説明文を付けず、スキーマどおりのJSONだけを出力し直してください。",
                ),
            ]
        raise LLMOutputError(f"model output did not match the schema: {last_err[:300]}")

    async def complete_text(
        self,
        task: TaskType,
        messages: list[ChatMessage],
        *,
        model_key: str | None = None,
        temperature: float | None = None,
    ) -> LLMResult:
        key = await self.resolve(task, model_key)
        text, ms = await self._call(key, messages, None, temperature)
        return LLMResult(_THINK.sub("", text).strip(), text, key, ms, 1)
