"""台本・字幕API。仕様 §6: /subtitles/generate, /subtitles/synchronize, /subtitles/export。
台本は Phase 4 で必要な作成・取得・音声生成のみ（生成モード等は Phase 8）。"""

from typing import Any

from fastapi import APIRouter, Depends

from video_automation.api.dependencies import get_container
from video_automation.app.bootstrap import AppContainer
from video_automation.schemas.common import JobRead
from video_automation.schemas.subtitle import (
    ScriptCreate,
    ScriptRead,
    SubtitleCue,
    SubtitleExportRequest,
    SubtitleExportResult,
    SubtitleGenerateRequest,
    SubtitleSyncRequest,
)

scripts = APIRouter(prefix="/scripts", tags=["scripts"])
router = APIRouter(prefix="/subtitles", tags=["subtitles"])


@scripts.post("", response_model=ScriptRead, status_code=201)
async def create_script(body: ScriptCreate, c: AppContainer = Depends(get_container)):
    return await c.subtitles.create_script(body)


@scripts.get("", response_model=list[ScriptRead])
async def list_scripts(project_id: str, c: AppContainer = Depends(get_container)):
    return await c.subtitles.list_scripts(project_id)


@scripts.get("/{script_id}", response_model=ScriptRead)
async def get_script(script_id: str, c: AppContainer = Depends(get_container)):
    return await c.subtitles.get_script(script_id)


@scripts.post("/{script_id}/voice", response_model=JobRead, status_code=202)
async def generate_voices(
    script_id: str, regenerate: bool = False, c: AppContainer = Depends(get_container)
):
    await c.subtitles.get_script(script_id)  # 存在確認

    async def run(progress: Any) -> dict[str, Any]:
        r = await c.subtitles.generate_voices(script_id, regenerate, progress)
        return r.model_dump(mode="json")

    return c.jobs.submit("script_voice", run)


@router.post("/generate", response_model=list[SubtitleCue])
async def generate(body: SubtitleGenerateRequest, c: AppContainer = Depends(get_container)):
    return await c.subtitles.generate(body)


@router.post("/synchronize", response_model=list[SubtitleCue])
async def synchronize(body: SubtitleSyncRequest, c: AppContainer = Depends(get_container)):
    return await c.subtitles.synchronize(body)


@router.get("/{script_id}", response_model=list[SubtitleCue])
async def list_cues(script_id: str, c: AppContainer = Depends(get_container)):
    await c.subtitles.get_script(script_id)
    return await c.subtitles.cues(script_id)


@router.post("/export", response_model=SubtitleExportResult)
async def export(body: SubtitleExportRequest, c: AppContainer = Depends(get_container)):
    return await c.subtitles.export(body)
