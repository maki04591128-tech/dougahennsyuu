"""画面録画の自動ズーム API。"""

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from video_automation.api.dependencies import get_container
from video_automation.app.bootstrap import AppContainer
from video_automation.schemas.common import JobRead

router = APIRouter(prefix="/projects/{project_id}/screencast", tags=["screencast"])


class ZoomRequest(BaseModel):
    asset_id: str
    # クリックの記録（任意）: [秒, x, y]（録画の画素）。無ければ画面の変化だけで寄る
    clicks: list[tuple[float, float, float]] = Field(default_factory=list, max_length=5000)
    zoom: float = Field(default=1.8, ge=1.0, le=4.0)


@router.post("/zoom", response_model=JobRead, status_code=202)
async def zoom(project_id: str, body: ZoomRequest, c: AppContainer = Depends(get_container)) -> Any:
    """操作している所（クリック・画面が変わった所）へ寄った映像を作り、新しい素材にする。"""

    async def run(progress: Any) -> dict[str, Any]:
        a = await c.screencast.zoom(project_id, body.asset_id, body.clicks, body.zoom, progress)
        return a.model_dump(mode="json")

    return c.jobs.submit("screencast_zoom", run)
