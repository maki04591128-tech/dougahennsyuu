"""デザインの型 API。用途の一覧・指示文からの判定・プロジェクトの用途の設定・書き出し前の検査。"""

from typing import Any

from fastapi import APIRouter, Depends

from video_automation.api.dependencies import get_container
from video_automation.app.bootstrap import AppContainer
from video_automation.schemas.design import (
    ClassifyRequest,
    ClassifyResult,
    DesignCheck,
    DesignProfile,
    ProfileSummary,
    ProjectDesign,
    Purpose,
)
from video_automation.schemas.timeline import RenderRequest
from video_automation.services.design.profiles import PROFILES, summarize

router = APIRouter(prefix="/design", tags=["design"])
projects = APIRouter(prefix="/projects/{project_id}/design", tags=["design"])


@router.get("/profiles", response_model=list[ProfileSummary])
async def profiles() -> Any:
    """用途の一覧（使ってよい部品・回数の上限・使わない部品）。"""
    return [summarize(p) for p in PROFILES.values()]


@router.get("/profiles/{purpose}", response_model=DesignProfile)
async def profile(purpose: Purpose) -> Any:
    """用途の型の全体（配色・書体・字幕・安全範囲・部品の許可）。"""
    return PROFILES[purpose]


@router.post("/classify", response_model=ClassifyResult)
async def classify(body: ClassifyRequest, c: AppContainer = Depends(get_container)) -> Any:
    """指示文から用途を判定する。needs_confirmation のときはユーザーに確認する。"""
    return c.design.classify(body)


@projects.get("", response_model=ProjectDesign)
async def get_design(project_id: str, c: AppContainer = Depends(get_container)) -> Any:
    return await c.design.get(project_id)


@projects.put("", response_model=ProjectDesign)
async def set_design(
    project_id: str, body: ProjectDesign, c: AppContainer = Depends(get_container)
) -> Any:
    """用途と、混ぜる指示が明示されたときだけ使う例外（extras）を設定する。"""
    return await c.design.set(project_id, body)


@projects.get("/check", response_model=DesignCheck)
async def check(
    project_id: str,
    script_id: str | None = None,
    burn_subtitles: bool = True,
    c: AppContainer = Depends(get_container),
) -> Any:
    """書き出す前と同じ検査を、書き出さずに行う。"""
    chk = await c.render.design_check(
        RenderRequest(project_id=project_id, script_id=script_id, burn_subtitles=burn_subtitles)
    )
    assert chk is not None
    return chk
