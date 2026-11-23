from fastapi import APIRouter, Depends

from video_automation.api.dependencies import get_db
from video_automation.app.settings import APP_NAME, APP_VERSION
from video_automation.database.database import Database
from video_automation.schemas.common import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health(db: Database = Depends(get_db)) -> HealthResponse:
    ok = await db.ping()
    return HealthResponse(
        status="ok" if ok else "degraded",
        application=APP_NAME,
        database="connected" if ok else "disconnected",
        version=APP_VERSION,
    )
