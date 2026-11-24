"""レンダリングAPI（仕様 §6: POST /render, GET /render/profiles）。"""

from fastapi import APIRouter, Depends, Query

from video_automation.api.dependencies import get_container
from video_automation.app.bootstrap import AppContainer
from video_automation.integrations.ffmpeg.runner import FFmpegNotFoundError
from video_automation.schemas.timeline import (
    RENDER_PROFILES,
    RenderJobRead,
    RenderProfile,
    RenderRequest,
)

router = APIRouter(prefix="/render", tags=["render"])


@router.get("/profiles", response_model=list[RenderProfile])
async def profiles():
    return list(RENDER_PROFILES.values())


@router.get("/engines")
async def engines(c: AppContainer = Depends(get_container)) -> dict:
    out: dict = {"ffmpeg": None, "blender": None}
    try:
        out["ffmpeg"] = {
            "version": await c.ffmpeg.version(),
            "nvenc": await c.ffmpeg.nvenc_usable(),
        }
    except FFmpegNotFoundError:
        pass
    if c.blender.available:
        out["blender"] = {"version": await c.blender.version()}
    return out


@router.post("", response_model=RenderJobRead, status_code=202)
async def render(body: RenderRequest, c: AppContainer = Depends(get_container)):
    return await c.render.request(body)


@router.get("", response_model=list[RenderJobRead])
async def list_renders(project_id: str = Query(...), c: AppContainer = Depends(get_container)):
    return await c.render.list(project_id)


@router.get("/{render_id}", response_model=RenderJobRead)
async def get_render(render_id: str, c: AppContainer = Depends(get_container)):
    return await c.render.get(render_id)


@router.get("/{render_id}/files")
async def render_files(render_id: str, c: AppContainer = Depends(get_container)) -> dict:
    """書き出した動画・字幕の絶対パス（同じ PC の Blender アドオンが読み込むため）。
    API は localhost にだけ開いているので、パスが外へ出ることはない。"""
    r = await c.render.get(render_id)
    base = c.settings.data_dir
    return {
        "status": r.status,
        "video": str(base / r.output_path) if r.output_path else None,
        "subtitles": [str(base / x) for x in r.subtitle_paths],
    }


@router.post("/{render_id}/approve", response_model=RenderJobRead)
async def approve(render_id: str, c: AppContainer = Depends(get_container)):
    return await c.render.decide(render_id, True)


@router.post("/{render_id}/reject", response_model=RenderJobRead)
async def reject(render_id: str, c: AppContainer = Depends(get_container)):
    return await c.render.decide(render_id, False)
