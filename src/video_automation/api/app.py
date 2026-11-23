"""FastAPI初期化 (P1-07)。"""

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from video_automation.api.routes import (
    health,
    jobs,
    models,
    projects,
    providers,
    subtitles,
    voice,
)
from video_automation.api.websocket import events
from video_automation.app.bootstrap import build_container
from video_automation.app.logging import get_logger
from video_automation.app.secrets import SecretProtector, SecretStorageUnavailableError
from video_automation.app.settings import APP_NAME, APP_VERSION, Settings, get_settings
from video_automation.providers.base import ModelNotFoundError, NotSupportedError, ProviderError
from video_automation.services.model_discovery.provider_manager import ProviderNotFoundError
from video_automation.services.subtitle.subtitle_service import SubtitleError
from video_automation.services.voice.voice_service import (
    EngineUnavailableError,
    InvalidVoiceRequestError,
    NotFoundError,
    UnsupportedFeatureError,
    VoiceError,
)


def create_app(
    settings: Settings | None = None,
    *,
    http_transport: httpx.AsyncBaseTransport | None = None,
    protector: SecretProtector | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    log = get_logger("api")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        c = await build_container(settings, http_transport=http_transport, protector=protector)
        app.state.container = c
        app.state.db, app.state.bus, app.state.jobs = c.db, c.bus, c.jobs
        log.info("startup", version=APP_VERSION)
        yield
        await c.aclose()
        log.info("shutdown")

    app = FastAPI(title=APP_NAME, version=APP_VERSION, lifespan=lifespan)

    def _err(status: int, code: str, message: str) -> JSONResponse:
        return JSONResponse(status_code=status, content={"code": code, "message": message})

    @app.exception_handler(ModelNotFoundError)
    async def model_not_found(_: Request, exc: ModelNotFoundError) -> JSONResponse:
        return _err(404, exc.code, "model not found")

    @app.exception_handler(ProviderNotFoundError)
    async def provider_not_found(_: Request, exc: ProviderNotFoundError) -> JSONResponse:
        return _err(404, "provider_not_found", "provider not found")

    @app.exception_handler(NotSupportedError)
    async def not_supported(_: Request, exc: NotSupportedError) -> JSONResponse:
        return _err(400, exc.code, "operation not supported by this provider")

    @app.exception_handler(ProviderError)
    async def provider_error(_: Request, exc: ProviderError) -> JSONResponse:
        return _err(502, exc.code, "provider request failed")

    @app.exception_handler(SecretStorageUnavailableError)
    async def secret_unavailable(_: Request, exc: SecretStorageUnavailableError) -> JSONResponse:
        return _err(400, "secret_storage_unavailable", "API key storage requires Windows DPAPI")

    @app.exception_handler(VoiceError)
    async def voice_error(_: Request, exc: VoiceError) -> JSONResponse:
        status = {
            NotFoundError: 404,
            EngineUnavailableError: 503,
            UnsupportedFeatureError: 400,
            InvalidVoiceRequestError: 422,
        }.get(type(exc), 502)
        # エンジンの検証メッセージ（入力文字列の問題）は返すが、内部情報は返さない
        message = str(exc)[:200] if status in (400, 404, 422) else "voice engine request failed"
        return _err(status, exc.code, message)

    @app.exception_handler(SubtitleError)
    async def subtitle_error(_: Request, exc: SubtitleError) -> JSONResponse:
        # 手順の前提（音声未生成・未同期等）を満たしていない
        return _err(409, exc.code, str(exc)[:200])

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.error("unhandled_exception", error_type=type(exc).__name__)
        return _err(500, "internal_error", "internal server error")

    prefix = "/api/v1"
    for r in (health, projects, jobs, providers, models, voice):
        app.include_router(r.router, prefix=prefix)
    app.include_router(subtitles.scripts, prefix=prefix)
    app.include_router(subtitles.router, prefix=prefix)
    app.include_router(events.router, prefix=prefix)
    return app
