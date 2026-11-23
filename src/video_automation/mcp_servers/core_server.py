"""mcp_core サーバー。MCPは内部サービスを呼ぶアダプターで、編集ロジックを重複実装しない。

Phase 1: ping / Phase 2: discover_models, list_models, select_model
Phase 8: create_project, get_project_state, plan_edit（計画の変更操作はすべて承認待ちになる）
"""

from typing import Any

from mcp.server.fastmcp import FastMCP

from video_automation.app.settings import APP_NAME, APP_VERSION
from video_automation.database.models import Project
from video_automation.database.repositories.llm import LlmRepository
from video_automation.mcp_servers._shared import get_container as _get_container
from video_automation.schemas.orchestrator import ConversationCreate
from video_automation.schemas.project import ProjectCreate
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


async def create_project(
    name: str, width: int = 1920, height: int = 1080, fps_num: int = 30, fps_den: int = 1
) -> dict[str, Any]:
    """新しい動画プロジェクトを作る（解像度・フレームレート）。"""
    req = ProjectCreate(name=name, width=width, height=height, fps_num=fps_num, fps_den=fps_den)
    c = await _get_container()
    async with c.db.session_factory() as s:
        p = Project(**req.model_dump())
        s.add(p)
        await s.commit()
        return {"project_id": p.id, **req.model_dump()}


async def get_project_state(project_id: str) -> dict[str, Any]:
    """プロジェクトの状態（トラック・クリップ・素材・台本の ID と時間）と承認待ちの一覧。"""
    c = await _get_container()
    state = await c.orchestrator.project_state(project_id)
    state["pending_approvals"] = [
        {"kind": a.kind.value, "id": a.id, "title": a.title}
        for a in await c.approvals.pending(project_id)
    ]
    return state


async def plan_edit(
    project_id: str,
    instruction: str,
    conversation_id: str | None = None,
    model_key: str | None = None,
) -> dict[str, Any]:
    """自然言語の編集指示を計画・検証し、変更を承認待ちとして登録する（ユーザーが承認すると反映）。"""
    c = await _get_container()
    if conversation_id is None:
        conversation_id = (
            await c.orchestrator.create(ConversationCreate(project_id=project_id))
        ).id
    m = await c.orchestrator.send(conversation_id, instruction, model_key)
    return {
        "conversation_id": conversation_id,
        "message_id": m.id,
        "reply": m.content,
        "questions": m.questions,
        "actions": [a.model_dump(mode="json", exclude_none=True) for a in m.actions],
        "note": "変更はユーザーの承認後に反映されます",
    }


mcp.tool(name="ping", description="疎通確認用テストツール")(ping)
for _fn in (create_project, get_project_state, plan_edit):
    mcp.tool(name=_fn.__name__, description=(_fn.__doc__ or "").strip().split("\n")[0])(_fn)
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
