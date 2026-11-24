"""立ち絵 API。PSD（zip）からパックを作る・一覧・台本の立ち絵をタイムラインに置く。"""

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from video_automation.api.dependencies import get_container
from video_automation.app.bootstrap import AppContainer
from video_automation.schemas.common import JobRead

router = APIRouter(prefix="/characters", tags=["characters"])


class PackImportRequest(BaseModel):
    path: str = Field(min_length=1)  # PSD または PSD を含む zip（取り込みを許可したフォルダ内）
    pack_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_\-]{1,64}$")


class TachieApplyRequest(BaseModel):
    script_id: str


@router.get("/packs")
async def packs(c: AppContainer = Depends(get_container)) -> list[dict[str, Any]]:
    """取り込んだ立ち絵パックの一覧（id は台本のキャラクターの tachie_pack に指定する）。"""
    return c.characters.packs()


@router.post("/packs/import", response_model=JobRead, status_code=202)
async def import_pack(body: PackImportRequest, c: AppContainer = Depends(get_container)) -> Any:
    """立ち絵の PSD（PSDTool 形式）を表情・腕の部品に分けてパックにする（1〜2 分かかる）。"""

    async def run(_progress: Any) -> dict[str, Any]:
        return await c.characters.import_pack(Path(body.path), body.pack_id)

    return c.jobs.submit("tachie_import", run)


@router.post("/apply", response_model=JobRead, status_code=202)
async def apply(body: TachieApplyRequest, c: AppContainer = Depends(get_container)) -> Any:
    """台本の立ち絵（表情・ポーズ・動き）を作って「立ち絵」トラックに置く。書き出しでも自動で行う。"""

    async def run(progress: Any) -> dict[str, Any]:
        return await c.characters.apply(body.script_id, progress)

    return c.jobs.submit("tachie_apply", run)
