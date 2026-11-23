"""FastAPI初期化 (P1-07)。"""

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from video_automation.api.routes import (
    asset_sources,
    assistant,
    direction,
    health,
    jobs,
    models,
    motion,
    projects,
    providers,
    qa,
    render,
    script_studio,
    subtitles,
    timeline,
    voice,
)
from video_automation.api.websocket import events
from video_automation.app.bootstrap import build_container
from video_automation.app.logging import get_logger
from video_automation.app.secrets import SecretProtector, SecretStorageUnavailableError
from video_automation.app.settings import APP_NAME, APP_VERSION, Settings, get_settings
from video_automation.integrations.blender.runner import BlenderError, BlenderNotFoundError
from video_automation.integrations.ffmpeg.runner import FFmpegError, FFmpegNotFoundError
from video_automation.providers.assets.base import (
    AssetSourceError,
    SourceAuthError,
    SourceNotConfiguredError,
    SourceNotFoundError,
    SourceRateLimitedError,
    UnsupportedMediaTypeError,
)
from video_automation.providers.audio_generation.acestep import (
    AceStepError,
    AceStepUnavailableError,
)
from video_automation.providers.base import ModelNotFoundError, NotSupportedError, ProviderError
from video_automation.services.assets.asset_source_service import (
    LicenseAckRequiredError,
    LicenseForbiddenError,
)
from video_automation.services.assets.downloader import (
    DownloadError,
    DownloadRejectedError,
    Resolver,
)
from video_automation.services.llm_tasks.gateway import LLMError, NoModelError
from video_automation.services.model_discovery.provider_manager import ProviderNotFoundError
from video_automation.services.orchestrator.approvals import ApprovalError
from video_automation.services.orchestrator.orchestrator import (
    ConversationNotFoundError,
    OrchestratorError,
)
from video_automation.services.script.studio import (
    FixedTurnError,
    ProposalStateError,
    ScriptNotFoundError,
    ScriptStudioError,
)
from video_automation.services.subtitle.subtitle_service import SubtitleError
from video_automation.services.timeline.timeline_service import (
    CommandError,
    ForbiddenPathError,
    TimelineError,
    TimelineNotFoundError,
)
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
    dns_resolver: Resolver | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    log = get_logger("api")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        c = await build_container(
            settings, http_transport=http_transport, protector=protector, dns_resolver=dns_resolver
        )
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

    @app.exception_handler(TimelineError)
    async def timeline_error(_: Request, exc: TimelineError) -> JSONResponse:
        status = {TimelineNotFoundError: 404, ForbiddenPathError: 403, CommandError: 422}.get(
            type(exc), 400
        )
        return _err(status, exc.code, str(exc)[:300])

    @app.exception_handler(FFmpegNotFoundError)
    @app.exception_handler(BlenderNotFoundError)
    async def tool_missing(_: Request, exc: Exception) -> JSONResponse:
        return _err(503, "tool_not_found", str(exc)[:200])

    @app.exception_handler(FFmpegError)
    @app.exception_handler(BlenderError)
    async def tool_failed(_: Request, exc: Exception) -> JSONResponse:
        log.error("tool_failed", error_type=type(exc).__name__, detail=str(exc)[-600:])
        return _err(502, "tool_failed", "external tool failed")

    @app.exception_handler(AssetSourceError)
    async def asset_source_error(_: Request, exc: AssetSourceError) -> JSONResponse:
        status = {
            SourceNotConfiguredError: 400,
            SourceAuthError: 400,  # 登録したAPIキーが無効
            SourceNotFoundError: 404,
            UnsupportedMediaTypeError: 400,
            SourceRateLimitedError: 429,
        }.get(type(exc), 502)
        resp = _err(status, exc.code, str(exc)[:200])
        if isinstance(exc, SourceRateLimitedError) and exc.retry_after:
            resp.headers["Retry-After"] = str(int(exc.retry_after))
        return resp

    @app.exception_handler(LicenseAckRequiredError)
    async def license_ack(_: Request, exc: LicenseAckRequiredError) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={
                "code": exc.code,
                "message": "ライセンス条件の確認が必要です（acknowledge_license=true で取得）",
                "license": exc.check.model_dump(mode="json"),
            },
        )

    @app.exception_handler(LicenseForbiddenError)
    async def license_forbidden(_: Request, exc: LicenseForbiddenError) -> JSONResponse:
        return _err(403, exc.code, str(exc)[:200])

    @app.exception_handler(DownloadError)
    async def download_error(_: Request, exc: DownloadError) -> JSONResponse:
        status = 422 if isinstance(exc, DownloadRejectedError) else 502
        return _err(status, exc.code, str(exc)[:200])

    @app.exception_handler(AceStepError)
    async def music_error(_: Request, exc: AceStepError) -> JSONResponse:
        status = 503 if isinstance(exc, AceStepUnavailableError) else 502
        return _err(status, exc.code, str(exc)[:200])

    @app.exception_handler(ScriptStudioError)
    async def studio_error(_: Request, exc: ScriptStudioError) -> JSONResponse:
        status = {ScriptNotFoundError: 404, FixedTurnError: 409, ProposalStateError: 409}.get(
            type(exc), 422
        )
        return _err(status, exc.code, str(exc)[:300])

    @app.exception_handler(OrchestratorError)
    async def orchestrator_error(_: Request, exc: OrchestratorError) -> JSONResponse:
        status = 404 if isinstance(exc, ConversationNotFoundError) else 422
        return _err(status, exc.code, str(exc)[:300])

    @app.exception_handler(ApprovalError)
    async def approval_error(_: Request, exc: ApprovalError) -> JSONResponse:
        return _err(404 if "not found" in str(exc) else 422, exc.code, str(exc)[:300])

    @app.exception_handler(LLMError)
    async def llm_error(_: Request, exc: LLMError) -> JSONResponse:
        status = 503 if isinstance(exc, NoModelError) else 502
        return _err(status, exc.code, str(exc)[:300])

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.error("unhandled_exception", error_type=type(exc).__name__)
        return _err(500, "internal_error", "internal server error")

    prefix = "/api/v1"
    for r in (health, projects, jobs, providers, models, voice):
        app.include_router(r.router, prefix=prefix)
    app.include_router(timeline.router, prefix=prefix)
    app.include_router(timeline.assets, prefix=prefix)
    app.include_router(render.router, prefix=prefix)
    app.include_router(script_studio.router, prefix=prefix)
    app.include_router(voice.scripts, prefix=prefix)
    app.include_router(assistant.conversations, prefix=prefix)
    app.include_router(assistant.approvals, prefix=prefix)
    app.include_router(assistant.projects, prefix=prefix)
    app.include_router(qa.projects, prefix=prefix)
    app.include_router(qa.reports, prefix=prefix)
    app.include_router(subtitles.scripts, prefix=prefix)
    app.include_router(subtitles.router, prefix=prefix)
    app.include_router(asset_sources.sources, prefix=prefix)
    app.include_router(asset_sources.assets, prefix=prefix)
    app.include_router(asset_sources.projects, prefix=prefix)
    app.include_router(asset_sources.music, prefix=prefix)
    app.include_router(motion.router, prefix=prefix)
    app.include_router(motion.visual, prefix=prefix)
    app.include_router(direction.router, prefix=prefix)
    app.include_router(events.router, prefix=prefix)
    return app
