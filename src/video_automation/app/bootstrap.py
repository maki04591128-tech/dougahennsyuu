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
from video_automation.services.assets.asset_source_service import AssetSourceService
from video_automation.services.assets.downloader import Resolver
from video_automation.services.llm_tasks.task_router import TaskRouter
from video_automation.services.model_discovery.inference_test import InferenceTester
from video_automation.services.model_discovery.provider_manager import ProviderManager
from video_automation.services.music.music_service import MusicService
from video_automation.services.rendering.render_service import RenderService
from video_automation.services.rendering.title_card import TitleCardService
from video_automation.services.subtitle.subtitle_service import SubtitleService
from video_automation.services.timeline.timeline_service import TimelineService
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
    subtitles = SubtitleService(settings, db, voice)
    jobs = JobManager(bus)
    ffmpeg = FFmpeg(settings.ffmpeg_path)
    blender = Blender(settings.blender_path)
    timeline = TimelineService(settings, db, ffmpeg)
    return AppContainer(
        settings=settings,
        db=db,
        bus=bus,
        jobs=jobs,
        providers=providers,
        tester=InferenceTester(providers),
        router=TaskRouter(),
        voice=voice,
        subtitles=subtitles,
        ffmpeg=ffmpeg,
        blender=blender,
        timeline=timeline,
        render=RenderService(settings, db, ffmpeg, timeline, subtitles, jobs),
        titles=TitleCardService(settings, db, blender, ffmpeg, timeline),
        asset_sources=AssetSourceService(
            settings,
            db,
            timeline,
            jobs,
            providers.protector,
            transport=http_transport,
            resolver=dns_resolver,
        ),
        music=MusicService(settings, db, timeline, providers, transport=http_transport),
    )
