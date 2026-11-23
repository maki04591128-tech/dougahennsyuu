"""FastAPI初期化 (P1-07)。"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from video_automation.api.routes import health, jobs, projects
from video_automation.api.websocket import events
from video_automation.app.logging import configure_logging, get_logger
from video_automation.app.settings import APP_NAME, APP_VERSION, Settings, get_settings
from video_automation.core.events.event_bus import EventBus
from video_automation.core.jobs.job_manager import JobManager
from video_automation.database.database import Database


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    log = get_logger("api")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.ensure_dirs()
        configure_logging(settings.log_level, settings.log_dir)
        db = Database.from_path(settings.database_path)
        await db.create_all()  # TODO: Alembic migration に置換
        bus = EventBus()
        app.state.db, app.state.bus = db, bus
        app.state.jobs = JobManager(bus)
        log.info("startup", version=APP_VERSION)
        yield
        await db.dispose()
        log.info("shutdown")

    app = FastAPI(title=APP_NAME, version=APP_VERSION, lifespan=lifespan)

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.error("unhandled_exception")
        return JSONResponse(
            status_code=500, content={"code": "internal_error", "message": "internal server error"}
        )

    prefix = "/api/v1"
    app.include_router(health.router, prefix=prefix)
    app.include_router(projects.router, prefix=prefix)
    app.include_router(jobs.router, prefix=prefix)
    app.include_router(events.router, prefix=prefix)
    return app
