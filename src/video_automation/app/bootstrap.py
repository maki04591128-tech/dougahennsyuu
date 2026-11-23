"""コンポーネント初期化。API と MCP サーバーで同じサービス構成を使う。"""

from dataclasses import dataclass

import httpx

from video_automation.app.logging import configure_logging
from video_automation.app.secrets import SecretProtector
from video_automation.app.settings import Settings
from video_automation.core.events.event_bus import EventBus
from video_automation.core.jobs.job_manager import JobManager
from video_automation.database.database import Database
from video_automation.services.llm_tasks.task_router import TaskRouter
from video_automation.services.model_discovery.inference_test import InferenceTester
from video_automation.services.model_discovery.provider_manager import ProviderManager


@dataclass
class AppContainer:
    settings: Settings
    db: Database
    bus: EventBus
    jobs: JobManager
    providers: ProviderManager
    tester: InferenceTester
    router: TaskRouter

    async def aclose(self) -> None:
        await self.providers.aclose()
        await self.db.dispose()


async def build_container(
    settings: Settings,
    *,
    http_transport: httpx.AsyncBaseTransport | None = None,
    protector: SecretProtector | None = None,
    configure_logs: bool = True,
) -> AppContainer:
    settings.ensure_dirs()
    if configure_logs:
        configure_logging(settings.log_level, settings.log_dir)
    db = Database.from_path(settings.database_path)
    await db.create_all()  # TODO: Alembic migration に置換
    bus = EventBus()
    providers = ProviderManager(settings, db, protector=protector, transport=http_transport)
    return AppContainer(
        settings=settings,
        db=db,
        bus=bus,
        jobs=JobManager(bus),
        providers=providers,
        tester=InferenceTester(providers),
        router=TaskRouter(),
    )
