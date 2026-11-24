"""文章の指示だけで動画を作る（/build）。Blender のアドオンの「つくる」画面が使う。"""

from typing import Any

from fastapi import APIRouter, Depends

from video_automation.api.dependencies import get_container
from video_automation.app.bootstrap import AppContainer
from video_automation.schemas.build import (
    BuildChange,
    BuildPlan,
    BuildRequest,
    BuildRunRequest,
    ReadinessItem,
)
from video_automation.schemas.common import JobRead

router = APIRouter(prefix="/build", tags=["build"])


@router.get("/readiness", response_model=list[ReadinessItem])
async def readiness(c: AppContainer = Depends(get_container)) -> Any:
    """作るのに要るもの（声・AI・FFmpeg・Blender）が揃っているか。"""
    return await c.build.readiness()


@router.post("/plan", response_model=BuildPlan, status_code=201)
async def plan(body: BuildRequest, c: AppContainer = Depends(get_container)) -> Any:
    """用途を判定し、プロジェクト・台本の設定・話し手を決めた「構成」を返す（すぐ返る）。"""
    return await c.build.plan(body)


@router.put("/{project_id}/plan", response_model=BuildPlan)
async def change(
    project_id: str, body: BuildChange, c: AppContainer = Depends(get_container)
) -> Any:
    """構成の確認で、用途・長さ・立ち絵を変える（まだ何も作らない）。"""
    return await c.build.change(project_id, body)


@router.post("/{project_id}/run", response_model=JobRead, status_code=202)
async def run(
    project_id: str, body: BuildRunRequest, c: AppContainer = Depends(get_container)
) -> Any:
    """構成の確認で承認された内容を、台本から書き出しまで続けて作る（ジョブ）。

    できあがった後の手直しにも使う: instruction に直したいこと、keep_script=True なら
    台本・音声はそのままで演出から作り直す。"""

    async def work(progress: Any) -> dict[str, Any]:
        return await c.build.run(project_id, body, progress)

    return c.jobs.submit("build", work)
