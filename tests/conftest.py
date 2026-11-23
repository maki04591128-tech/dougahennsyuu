import sys
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager

sys.path.insert(0, str(Path(__file__).parent))

from fakes import FakeProtector, FakeServers  # noqa: E402
from video_automation.api.app import create_app  # noqa: E402
from video_automation.app.settings import Settings  # noqa: E402


@pytest.fixture
def fake() -> FakeServers:
    return FakeServers()


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(data_dir=tmp_path, acestep_path=tmp_path / "no-acestep")


@pytest_asyncio.fixture
async def client(settings, fake):
    app = create_app(settings, http_transport=fake.transport, protector=FakeProtector())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test/api/v1") as c:
            c.app = app  # type: ignore[attr-defined]
            yield c
