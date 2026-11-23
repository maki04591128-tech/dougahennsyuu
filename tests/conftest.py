import httpx
import pytest_asyncio
from asgi_lifespan import LifespanManager

from video_automation.api.app import create_app
from video_automation.app.settings import Settings


@pytest_asyncio.fixture
async def client(tmp_path):
    app = create_app(Settings(data_dir=tmp_path))
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test/api/v1") as c:
            c.app = app  # type: ignore[attr-defined]
            yield c
