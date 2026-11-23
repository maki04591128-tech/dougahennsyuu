"""コンポーネント初期化。API と MCP サーバーで同じサービス構成を使う。"""

from dataclasses import dataclass

import httpx

from video_automation.app.logging import configure_logging
from video_automation.app.secrets import SecretProtector
from video_automation.app.settings import Settings
from video_automation.core.events.event_bus import EventBus
from video_automation.core.jobs.job_manager import JobManager
from video_automation.database.database import Database
from video_automation.integrations.blender.runner import Blender
from video_automation.integrations.ffmpeg.runner import FFmpeg
from video_automation.services.animation.preset_service import PresetService
from video_automation.services.animation.scene_service import SceneService
from video_automation.services.assets.asset_source_service import AssetSourceService
from video_automation.services.assets.downloader import Resolver
from video_automation.services.design.service import DesignService
from video_automation.services.direction.service import DirectionService
from video_automation.services.llm_tasks.gateway import LLMGateway
from video_automation.services.llm_tasks.task_router import TaskRouter
from video_automation.services.model_discovery.inference_test import InferenceTester
from video_automation.services.model_discovery.provider_manager import ProviderManager
from video_automation.services.music.music_service import MusicService
from video_automation.services.orchestrator.approvals import ApprovalService
from video_automation.services.orchestrator.history import HistoryService
from video_automation.services.orchestrator.orchestrator import Orchestrator, ToolRouter
from video_automation.services.qa.qa_service import QAService
from video_automation.services.rendering.render_service import RenderService
from video_automation.services.rendering.title_card import TitleCardService
from video_automation.services.script.studio import ScriptStudio
from video_automation.services.subtitle.subtitle_service import SubtitleService
from video_automation.services.timeline.timeline_service import TimelineService
from video_automation.services.voice.reading_service import ReadingService
from video_automation.services.voice.voice_service import VoiceService


@dataclass
class AppContainer:
    settings: Settings
    db: Database
    bus: EventBus
    jobs: JobManager
    providers: ProviderManager
    tester: InferenceTester
    router: TaskRouter
    voice: VoiceService
    subtitles: SubtitleService
    ffmpeg: FFmpeg
    blender: Blender
    timeline: TimelineService
    render: RenderService
    titles: TitleCardService
    asset_sources: AssetSourceService
    music: MusicService
    presets: PresetService
    scenes: SceneService
    llm: LLMGateway
    studio: ScriptStudio
    approvals: ApprovalService
    history: HistoryService
    orchestrator: Orchestrator
    qa: QAService
    readings: ReadingService
    direction: DirectionService
    design: DesignService

    async def aclose(self) -> None:
        await self.asset_sources.aclose()
        await self.music.aclose()
        await self.providers.aclose()
        await self.voice.aclose()
        await self.db.dispose()


async def build_container(
    settings: Settings,
    *,
    http_transport: httpx.AsyncBaseTransport | None = None,
    protector: SecretProtector | None = None,
    configure_logs: bool = True,
    dns_resolver: Resolver | None = None,
) -> AppContainer:
    settings.ensure_dirs()
    if configure_logs:
        configure_logging(settings.log_level, settings.log_dir)
    db = Database.from_path(settings.database_path)
    await db.create_all()  # TODO: Alembic migration に置換
    bus = EventBus()
    providers = ProviderManager(settings, db, protector=protector, transport=http_transport)
    voice = VoiceService(settings, db, transport=http_transport)
    router = TaskRouter()
    llm = LLMGateway(settings, db, providers, router)
    readings = ReadingService(db, voice, llm)
    design = DesignService(db)
    subtitles = SubtitleService(settings, db, voice, readings, design)
    jobs = JobManager(bus)
    ffmpeg = FFmpeg(settings.ffmpeg_path)
    blender = Blender(settings.blender_path)
    timeline = TimelineService(settings, db, ffmpeg)
    titles = TitleCardService(settings, db, blender, ffmpeg, timeline)
    render = RenderService(settings, db, ffmpeg, timeline, subtitles, jobs, design)
    asset_sources = AssetSourceService(
        settings,
        db,
        timeline,
        jobs,
        providers.protector,
        transport=http_transport,
        resolver=dns_resolver,
    )
    studio = ScriptStudio(db, llm)
    approvals = ApprovalService(settings, db, timeline, render, asset_sources, studio)
    await approvals.load_policy()
    orchestrator = Orchestrator(
        db, llm, ToolRouter(timeline, studio, render, asset_sources, approvals), approvals
    )
    return AppContainer(
        settings=settings,
        db=db,
        bus=bus,
        jobs=jobs,
        providers=providers,
        tester=InferenceTester(providers),
        router=router,
        voice=voice,
        subtitles=subtitles,
        ffmpeg=ffmpeg,
        blender=blender,
        timeline=timeline,
        render=render,
        titles=titles,
        asset_sources=asset_sources,
        music=MusicService(settings, db, timeline, providers, transport=http_transport),
        presets=PresetService(db),
        scenes=SceneService(settings, db, ffmpeg, timeline, titles),
        llm=llm,
        studio=studio,
        approvals=approvals,
        history=HistoryService(db, timeline, approvals),
        orchestrator=orchestrator,
        qa=QAService(settings, db, ffmpeg, timeline, subtitles, jobs),
        readings=readings,
        direction=DirectionService(settings, db, llm, timeline, titles, ffmpeg, design),
        design=design,
    )
