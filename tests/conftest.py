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


@pytest.fixture(scope="session")
def media_dir(tmp_path_factory):
    """FFmpeg で生成したテスト素材（映像+音声 / 画像 / 音声）。"""
    import shutil
    import subprocess

    ff = shutil.which("ffmpeg")
    if not ff:
        pytest.skip("ffmpeg not installed")
    d = tmp_path_factory.mktemp("media")
    run = lambda *a: subprocess.run([ff, "-v", "error", "-y", *a], check=True)  # noqa: E731
    run(
        "-f",
        "lavfi",
        "-i",
        "testsrc2=s=640x360:r=30:d=3",
        "-f",
        "lavfi",
        "-i",
        "sine=f=440:d=3",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
        str(d / "clip.mp4"),
    )
    run("-f", "lavfi", "-i", "testsrc2=s=1080x1920:d=1", "-frames:v", "1", str(d / "tall.png"))
    run("-f", "lavfi", "-i", "sine=f=880:d=2", "-ar", "48000", str(d / "tone.wav"))
    return d


@pytest.fixture
def media_settings(tmp_path, media_dir):
    return Settings(
        data_dir=tmp_path / "data",
        acestep_path=tmp_path / "no-acestep",
        import_dirs=[media_dir],
        blender_path=str(tmp_path / "no-blender"),
    )


@pytest_asyncio.fixture
async def mclient(media_settings, fake):
    app = create_app(media_settings, http_transport=fake.transport, protector=FakeProtector())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test/api/v1") as c:
            c.app = app  # type: ignore[attr-defined]
            yield c


@pytest_asyncio.fixture
async def aclient(media_settings, fake):
    """素材取得用: 名前解決は偽の resolver（外部DNSに依存しない）。"""
    from fake_assets import fake_resolver, media

    media()
    app = create_app(
        media_settings,
        http_transport=fake.transport,
        protector=FakeProtector(),
        dns_resolver=fake_resolver,
    )
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test/api/v1") as c:
            c.app = app  # type: ignore[attr-defined]
            c.fake = fake  # type: ignore[attr-defined]
            yield c
