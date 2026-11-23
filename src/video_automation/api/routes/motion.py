"""モーションプリセット API（P7-06）。クリップへの適用は編集命令 set_clip_animation で行う。"""

from typing import Any

from fastapi import APIRouter, Depends, Response

from video_automation.api.dependencies import get_container
from video_automation.app.bootstrap import AppContainer
from video_automation.schemas.animation import (
    InfographicRequest,
    MotionPreset,
    MotionPresetCreate,
    PresetKind,
    SaveClipAsPreset,
    SceneRequest,
)
from video_automation.schemas.common import JobRead

router = APIRouter(prefix="/motion", tags=["animation"])
visual = APIRouter(tags=["animation"])


@router.get("/presets", response_model=list[MotionPreset])
async def list_presets(kind: PresetKind | None = None, c: AppContainer = Depends(get_container)):
    return await c.presets.list(kind)


@router.post("/presets", response_model=MotionPreset, status_code=201)
async def create_preset(body: MotionPresetCreate, c: AppContainer = Depends(get_container)):
    return await c.presets.create(body)


@router.post("/presets/from_clip", response_model=MotionPreset, status_code=201)
async def preset_from_clip(body: SaveClipAsPreset, c: AppContainer = Depends(get_container)):
    return await c.presets.from_clip(body)


@router.delete("/presets/{name}", status_code=204)
async def delete_preset(name: str, c: AppContainer = Depends(get_container)) -> Response:
    await c.presets.delete(name)
    return Response(status_code=204)


@visual.post("/assets/infographic", response_model=JobRead, status_code=202)
async def infographic(body: InfographicRequest, c: AppContainer = Depends(get_container)):
    async def run(progress: Any) -> dict[str, Any]:
        return (await c.titles.infographic(body)).model_dump(mode="json")

    return c.jobs.submit("infographic", run)


@visual.post("/projects/{project_id}/scenes", response_model=JobRead, status_code=202)
async def create_scene(
    project_id: str, body: SceneRequest, c: AppContainer = Depends(get_container)
):
    """背景（色・素材）＋タイトル＋カメラ演出＋切替を配置する（タイトル生成を含むため Job）。"""

    async def run(progress: Any) -> dict[str, Any]:
        return await c.scenes.create(project_id, body)

    return c.jobs.submit("scene", run)
