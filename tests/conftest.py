import json
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


@pytest.fixture(autouse=True)
def _cleanup_qt_widgets():
    """UI テストで作ったウィジェットをテストごとに破棄する。

    親の無いウィジェットを終了時の GC に任せると、破棄の順序によって Qt が解放済みメモリに触れ、
    プロセス終了時に異常終了することがある（malloc_consolidate）。"""
    yield
    qt = sys.modules.get("PySide6.QtWidgets")
    if qt is None:
        return
    app = qt.QApplication.instance()
    if app is None:
        return
    import gc

    for w in app.topLevelWidgets():
        w.close()
        w.deleteLater()
    from PySide6.QtCore import QCoreApplication, QEvent

    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    gc.collect()


def fake_tachie_pack(root: Path) -> Path:
    """テスト用の立ち絵パック（色の四角で体・腕・口・目）。"""
    from PIL import Image

    d = root / "p"
    (d / "parts").mkdir(parents=True)

    def part(name: str, color: tuple[int, int, int], box: tuple[int, int, int, int]) -> dict:
        x0, y0, x1, y1 = box
        Image.new("RGBA", (x1 - x0, y1 - y0), color + (255,)).save(d / "parts" / f"{name}.png")
        return {"file": f"{name}.png", "x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}

    meta = {
        "version": 1,
        "name": "テスト",
        "author": "作者",
        "credit": "立ち絵：作者",
        "faces": "left",
        "canvas": [100, 200],
        "bbox": [10, 0, 90, 200],
        "stack": [
            {"fixed": part("body", (200, 200, 200), (20, 40, 80, 200))},
            {"slot": "arm_r"},
            {"slot": "mouth"},
            {"slot": "eyes"},
        ],
        "parts": {
            "arm_r": {
                "a": part("arm_a", (255, 0, 0), (10, 60, 20, 120)),
                "b": part("arm_b", (0, 0, 255), (10, 20, 20, 60)),
            },
            "mouth": {
                "c": part("m_c", (0, 0, 0), (45, 30, 55, 32)),
                "o": part("m_o", (90, 0, 0), (45, 28, 55, 36)),
            },
            "eyes": {
                "open": part("e_o", (0, 0, 0), (40, 16, 60, 22)),
                "closed": part("e_c", (0, 0, 0), (40, 19, 60, 20)),
            },
        },
        "blink": "closed",
        "expressions": {"neutral": {"eyes": "open", "mouth": ["c", "o", "o"]}},
        "poses": {"neutral": {"arm_r": "a"}, "wave": {"arm_r": "b"}},
        "gesture_arm": {"wave": "arm_r"},
    }
    (d / "pack.json").write_text(json.dumps(meta, ensure_ascii=False), "utf-8")
    return d


@pytest.fixture
def make_tachie_pack():
    return fake_tachie_pack
