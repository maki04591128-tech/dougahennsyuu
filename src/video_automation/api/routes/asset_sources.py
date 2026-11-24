"""素材検索・取得・ライセンス・BGM生成 API（仕様 §6: /assets/search, /assets/download,
/assets/license/inspect）。"""

from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import FileResponse

from video_automation.api.dependencies import get_container
from video_automation.app.bootstrap import AppContainer
from video_automation.schemas.asset_source import (
    AddBgmRequest,
    AssetDownloadRequest,
    AssetDownloadResponse,
    AssetRef,
    AssetRequestRead,
    AssetRequestStatus,
    AssetSearchRequest,
    AssetSearchResponse,
    CreditsResponse,
    LicenseCheck,
    LicenseInspectRequest,
    MusicEngineStatus,
    MusicGenerateRequest,
    MusicGenerationRead,
    SourceKeyRequest,
    SourceName,
    SourceStatus,
)
from video_automation.schemas.common import JobRead
from video_automation.schemas.timeline import CommandRead

sources = APIRouter(prefix="/asset_sources", tags=["assets"])
assets = APIRouter(prefix="/assets", tags=["assets"])
projects = APIRouter(prefix="/projects/{project_id}", tags=["assets"])
music = APIRouter(prefix="/music", tags=["music"])


@sources.get("", response_model=list[SourceStatus])
async def list_sources(c: AppContainer = Depends(get_container)):
    return await c.asset_sources.list_sources()


@sources.put("/{name}/key", response_model=SourceStatus)
async def set_key(
    name: SourceName, body: SourceKeyRequest, c: AppContainer = Depends(get_container)
):
    return await c.asset_sources.set_key(name, body.api_key)


@sources.delete("/{name}/key", status_code=204)
async def delete_key(name: SourceName, c: AppContainer = Depends(get_container)) -> Response:
    await c.asset_sources.delete_key(name)
    return Response(status_code=204)


@assets.post("/search", response_model=AssetSearchResponse)
async def search(body: AssetSearchRequest, c: AppContainer = Depends(get_container)):
    return await c.asset_sources.search(body)


@assets.post("/thumbnail")
async def thumbnail(body: AssetRef, c: AppContainer = Depends(get_container)) -> Response:
    data, ctype = await c.asset_sources.thumbnail(body)
    return Response(content=data, media_type=ctype)


@assets.post("/license/inspect", response_model=LicenseCheck)
async def inspect(body: LicenseInspectRequest, c: AppContainer = Depends(get_container)):
    return await c.asset_sources.inspect(body)


@assets.post("/download", response_model=AssetDownloadResponse, status_code=202)
async def download(body: AssetDownloadRequest, c: AppContainer = Depends(get_container)):
    return await c.asset_sources.download(body)


@assets.get("/{asset_id}/file")
async def asset_file(asset_id: str, c: AppContainer = Depends(get_container)) -> FileResponse:
    """素材の実体（UI の試聴・確認用）。DB に登録されたデータフォルダ内のファイルのみ返す。"""
    path, name = await c.asset_sources.asset_path(asset_id)
    return FileResponse(path, filename=name)


@assets.get("/requests", response_model=list[AssetRequestRead])
async def list_requests(
    project_id: str = Query(...),
    status: AssetRequestStatus | None = None,
    c: AppContainer = Depends(get_container),
):
    return await c.asset_sources.list_requests(project_id, status)


@assets.post("/requests/{request_id}/approve", response_model=AssetRequestRead)
async def approve(request_id: str, c: AppContainer = Depends(get_container)):
    return await c.asset_sources.decide(request_id, True)


@assets.post("/requests/{request_id}/reject", response_model=AssetRequestRead)
async def reject(request_id: str, c: AppContainer = Depends(get_container)):
    return await c.asset_sources.decide(request_id, False)


@projects.get("/credits", response_model=CreditsResponse)
async def credits(
    project_id: str, used_only: bool = False, c: AppContainer = Depends(get_container)
):
    """クレジットの一覧と、概要欄に貼れる文。used_only はタイムラインで使っている素材だけ。"""
    return await c.asset_sources.credits(project_id, used_only)


@projects.post("/timeline/add_bgm", response_model=CommandRead, status_code=201)
async def add_bgm(project_id: str, body: AddBgmRequest, c: AppContainer = Depends(get_container)):
    return await c.timeline.add_bgm(project_id, body)


@music.get("/engines", response_model=MusicEngineStatus)
async def music_engines(c: AppContainer = Depends(get_container)):
    return await c.music.status()


@music.post("/generate", response_model=JobRead, status_code=202)
async def music_generate(body: MusicGenerateRequest, c: AppContainer = Depends(get_container)):
    async def run(progress: Any) -> dict[str, Any]:
        out = await c.music.generate(body, progress)
        return {"assets": [a.model_dump(mode="json") for a in out]}

    return c.jobs.submit("music_generate", run)


@music.get("/generations", response_model=list[MusicGenerationRead])
async def music_generations(project_id: str = Query(...), c: AppContainer = Depends(get_container)):
    return await c.music.list(project_id)
