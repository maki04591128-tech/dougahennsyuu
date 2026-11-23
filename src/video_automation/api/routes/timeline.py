"""タイムライン・編集命令・素材・プレビューAPI（仕様 §6）。"""

from typing import Any

from fastapi import APIRouter, Depends, Query, Response

from video_automation.api.dependencies import get_container
from video_automation.app.bootstrap import AppContainer
from video_automation.schemas.common import JobRead
from video_automation.schemas.project import ProjectRead
from video_automation.schemas.timeline import (
    AssetImportRequest,
    AssetRead,
    CommandRead,
    CommandRequest,
    CommandStatus,
    FromScriptRequest,
    PreviewRequest,
    ProjectUpdate,
    TimelineRead,
    TitleCardRequest,
    ValidationResult,
)

router = APIRouter(prefix="/projects/{project_id}", tags=["timeline"])
assets = APIRouter(prefix="/assets", tags=["assets"])


@router.put("", response_model=ProjectRead)
async def update_project(
    project_id: str, body: ProjectUpdate, c: AppContainer = Depends(get_container)
):
    return await c.timeline.update_project(project_id, body)


@router.get("/timeline", response_model=TimelineRead)
async def timeline(project_id: str, c: AppContainer = Depends(get_container)):
    return await c.timeline.get_timeline(project_id)


@router.post("/timeline/from_script", response_model=CommandRead, status_code=201)
async def from_script(
    project_id: str, body: FromScriptRequest, c: AppContainer = Depends(get_container)
):
    return await c.timeline.from_script(project_id, body.script_id)


@router.post("/commands", response_model=CommandRead, status_code=201)
async def submit(project_id: str, body: CommandRequest, c: AppContainer = Depends(get_container)):
    return await c.timeline.submit(project_id, body)


@router.post("/commands/validate", response_model=ValidationResult)
async def validate(project_id: str, body: CommandRequest, c: AppContainer = Depends(get_container)):
    return await c.timeline.validate_command(project_id, body)


@router.get("/commands", response_model=list[CommandRead])
async def commands(
    project_id: str,
    status: CommandStatus | None = None,
    c: AppContainer = Depends(get_container),
):
    return await c.timeline.list_commands(project_id, status)


@router.post("/commands/{command_id}/approve", response_model=CommandRead)
async def approve(project_id: str, command_id: str, c: AppContainer = Depends(get_container)):
    return await c.timeline.decide(project_id, command_id, True)


@router.post("/commands/{command_id}/reject", response_model=CommandRead)
async def reject(project_id: str, command_id: str, c: AppContainer = Depends(get_container)):
    return await c.timeline.decide(project_id, command_id, False)


@router.post("/undo", response_model=CommandRead)
async def undo(project_id: str, c: AppContainer = Depends(get_container)):
    return await c.timeline.undo(project_id)


@router.post("/redo", response_model=CommandRead)
async def redo(project_id: str, c: AppContainer = Depends(get_container)):
    return await c.timeline.redo(project_id)


@router.get("/validate", response_model=ValidationResult)
async def validate_project(project_id: str, c: AppContainer = Depends(get_container)):
    return await c.timeline.validate_project(project_id)


@router.post("/preview", response_model=None)
async def preview(
    project_id: str, body: PreviewRequest, c: AppContainer = Depends(get_container)
) -> Response | JobRead:
    """frame 指定時は PNG を返す。それ以外は低解像度の確認用動画を Job で作る。"""
    if body.frame is not None:
        png = await c.render.preview_frame(project_id, body)
        return Response(content=png, media_type="image/png")

    async def run(progress: Any) -> dict[str, Any]:
        return {"path": await c.render.preview_video(project_id, body, progress)}

    return c.jobs.submit("preview", run)


@assets.post("/import", response_model=AssetRead, status_code=201)
async def import_asset(body: AssetImportRequest, c: AppContainer = Depends(get_container)):
    return await c.timeline.import_asset(body)


@assets.post("/title_card", response_model=JobRead, status_code=202)
async def title_card(body: TitleCardRequest, c: AppContainer = Depends(get_container)):
    async def run(progress: Any) -> dict[str, Any]:
        return (await c.titles.create(body)).model_dump(mode="json")

    return c.jobs.submit("title_card", run)


@assets.get("", response_model=list[AssetRead])
async def list_assets(project_id: str = Query(...), c: AppContainer = Depends(get_container)):
    return (await c.timeline.get_timeline(project_id)).assets
