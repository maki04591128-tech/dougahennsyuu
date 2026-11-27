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
    # 仕上げ（任意）: カーソルの位置 [秒, x, y]・キー操作 [秒, "Ctrl+S"]（録画の画素と秒）
    cursor: list[tuple[float, float, float]] = Field(default_factory=list, max_length=200000)
    keys: list[tuple[float, str]] = Field(default_factory=list, max_length=20000)
    speedup: bool = True  # 画面の変化も声も無い 2 秒以上の区間を 4 倍速にする
    blur_pii: bool = True  # メール・API キー・電話番号を OCR で見つけてぼかす
    # 統合版 N14: クリックの前後にクリックの位置の外を暗くする（None は用途が操作説明のときだけ）
    spotlight: bool | None = None


@router.post("/zoom", response_model=JobRead, status_code=202)
async def zoom(project_id: str, body: ZoomRequest, c: AppContainer = Depends(get_container)) -> Any:
    """操作している所（クリック・画面が変わった所）へ寄った映像を作り、新しい素材にする。"""

    async def run(progress: Any) -> dict[str, Any]:
        a = await c.screencast.zoom(
            project_id,
            body.asset_id,
            body.clicks,
            body.zoom,
            progress,
            cursor=body.cursor,
            keys=body.keys,
            speedup=body.speedup,
            blur_pii=body.blur_pii,
            spotlight=body.spotlight,
        )
        # 個人情報の一覧（伏せ字）・早送りの区間を付ける。利用者が確かめる
        return {**a.model_dump(mode="json"), "finish": c.screencast.reports.get(a.id, {})}

    return c.jobs.submit("screencast_zoom", run)
