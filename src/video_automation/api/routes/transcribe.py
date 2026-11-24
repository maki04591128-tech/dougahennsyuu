"""文字起こしAPI（⑤）: 取り込んだ音声・動画から字幕・歌詞を作る。"""

from typing import Any

from fastapi import APIRouter, Depends

from video_automation.api.dependencies import get_container
from video_automation.app.bootstrap import AppContainer
from video_automation.schemas.common import JobRead
from video_automation.services.transcribe.service import TranscribeRequest

router = APIRouter(prefix="/transcribe", tags=["transcribe"])


@router.get("/status")
async def status(c: AppContainer = Depends(get_container)) -> dict[str, Any]:
    """faster-whisper とモデルが使えるか（モデルの場所は設定 VA_WHISPER_MODEL_DIR）。"""
    return c.transcriber.status()


@router.post("", response_model=JobRead, status_code=202)
async def transcribe(body: TranscribeRequest, c: AppContainer = Depends(get_container)):
    async def run(progress: Any) -> dict[str, Any]:
        r = await c.transcriber.transcribe(body, progress)
        return r.model_dump()

    return c.jobs.submit("transcribe", run)
