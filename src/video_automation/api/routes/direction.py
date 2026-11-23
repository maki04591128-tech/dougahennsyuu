"""演出プラン API。プランの作成（LLM・副作用なし）と、確認後の適用（ジョブ）。"""

from typing import Any

from fastapi import APIRouter, Depends

from video_automation.api.dependencies import get_container
from video_automation.app.bootstrap import AppContainer
from video_automation.schemas.common import JobRead
from video_automation.schemas.direction import (
    DirectionApplyRequest,
    DirectionPlanRequest,
    DirectionPlanResult,
)

router = APIRouter(prefix="/direction", tags=["direction"])


@router.post("/plan", response_model=DirectionPlanResult)
async def plan(body: DirectionPlanRequest, c: AppContainer = Depends(get_container)) -> Any:
    """台本から演出プランを作る（タイムラインは変えない）。"""
    return await c.direction.plan(body)


@router.post("/apply", response_model=JobRead, status_code=202)
async def apply(body: DirectionApplyRequest, c: AppContainer = Depends(get_container)) -> Any:
    """確認したプランで素材を作り、タイムラインに配置する（1 回の編集として元に戻せる）。"""

    async def run(_progress: Any) -> dict[str, Any]:
        return (await c.direction.apply(body)).model_dump(mode="json")

    return c.jobs.submit("direction_apply", run)
