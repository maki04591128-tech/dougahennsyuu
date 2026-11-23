from typing import Any

from fastapi import APIRouter, Depends, Query

from video_automation.api.dependencies import get_container
from video_automation.app.bootstrap import AppContainer
from video_automation.database.repositories.llm import LlmRepository
from video_automation.schemas.common import JobRead
from video_automation.schemas.provider import (
    ActiveModelRequest,
    Capabilities,
    ModelInfo,
    ModelTestRequest,
    TaskAssignmentRead,
    TaskAssignmentRequest,
)

router = APIRouter(prefix="/models", tags=["models"])


@router.get("", response_model=list[ModelInfo])
async def list_models(c: AppContainer = Depends(get_container)):
    return await c.providers.list_models()


@router.get("/capabilities", response_model=Capabilities)
async def capabilities(model_key: str = Query(...), c: AppContainer = Depends(get_container)):
    return (await c.providers.get_model(model_key)).capabilities


@router.post("/test", response_model=JobRead, status_code=202)
async def test_model(body: ModelTestRequest, c: AppContainer = Depends(get_container)):
    await c.providers.get_model(body.model_key)  # 存在確認（無ければ404）

    async def run(progress: Any) -> dict[str, Any]:
        probes: list[str] | None = list(body.probes) if body.probes is not None else None
        result = await c.tester.run(body.model_key, probes, progress)
        return result.model_dump(mode="json")

    return c.jobs.submit("model_test", run)


@router.get("/active", response_model=ModelInfo | None)
async def get_active(c: AppContainer = Depends(get_container)):
    key = await c.providers.active_key()
    if key is None:
        return None
    return await c.providers.get_model(key)


@router.put("/active", response_model=ModelInfo)
async def set_active(body: ActiveModelRequest, c: AppContainer = Depends(get_container)):
    return await c.providers.set_active(body.model_key)


async def _assignments(c: AppContainer) -> list[TaskAssignmentRead]:
    models = await c.providers.list_models()
    async with c.db.session_factory() as session:
        assigned = await LlmRepository(session).get_assignments()
    return c.router.resolve_all(models, assigned, await c.providers.active_key())


@router.get("/task-assignment", response_model=list[TaskAssignmentRead])
async def get_assignments(c: AppContainer = Depends(get_container)):
    return await _assignments(c)


@router.put("/task-assignment", response_model=list[TaskAssignmentRead])
async def set_assignment(body: TaskAssignmentRequest, c: AppContainer = Depends(get_container)):
    if body.model_key is not None:
        await c.providers.get_model(body.model_key)  # 存在確認
    async with c.db.session_factory() as session:
        await LlmRepository(session).set_assignment(body.task.value, body.model_key)
        await session.commit()
    return await _assignments(c)
