"""mcp_core サーバー。MCPは内部サービスを呼ぶアダプターで、編集ロジックを重複実装しない。

Phase 1: ping / Phase 2: discover_models, list_models, select_model
"""

from typing import Any

from mcp.server.fastmcp import FastMCP

from video_automation.app.settings import APP_NAME, APP_VERSION
from video_automation.database.repositories.llm import LlmRepository
from video_automation.mcp_servers._shared import get_container as _get_container
from video_automation.schemas.provider import TaskType

mcp = FastMCP("mcp_core")


def ping() -> dict[str, str]:
    return {"status": "ok", "application": APP_NAME, "version": APP_VERSION}


async def discover_models() -> dict[str, Any]:
    """ローカル推論サーバー（Ollama/LM Studio）と登録済みAPIからモデルを検出する。"""
    res = await (await _get_container()).providers.discover()
    return {
        "providers": [
            {"id": p.provider_id, "available": p.available, "models": p.models_count}
            for p in res.providers
        ],
        "models": [
            {"key": m.key, "model_id": m.model_id, "state": m.state.value} for m in res.models
        ],
        "music": [{"name": m.name, "available": m.available} for m in res.music],
    }


async def list_models() -> list[dict[str, Any]]:
    """保存済みのモデル一覧と Capability を返す。"""
    models = await (await _get_container()).providers.list_models()
    return [m.model_dump(mode="json", exclude={"last_test"}) for m in models]


async def select_model(model_key: str, task: str | None = None) -> dict[str, Any]:
    """メインモデル（task省略時）またはタスク別割当を設定する。"""
    c = await _get_container()
    await c.providers.get_model(model_key)
    if task is None:
        await c.providers.set_active(model_key)
        return {"active_model": model_key}
    task_type = TaskType(task)
    async with c.db.session_factory() as session:
        await LlmRepository(session).set_assignment(task_type.value, model_key)
        await session.commit()
    return {"task": task_type.value, "model_key": model_key}


mcp.tool(name="ping", description="疎通確認用テストツール")(ping)
mcp.tool(name="discover_models", description="ローカルLLM/登録APIのモデルを検出する")(
    discover_models
)
mcp.tool(name="list_models", description="検出済みモデルとCapabilityの一覧")(list_models)
mcp.tool(name="select_model", description="メインモデルまたはタスク別モデルを選択する")(
    select_model
)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
