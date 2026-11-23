import json

from video_automation.app.logging import configure_logging, get_logger
from video_automation.app.settings import Settings
from video_automation.core.events.event_bus import EventBus
from video_automation.mcp_servers.core_server import ping


def test_settings_defaults(tmp_path):
    s = Settings(data_dir=tmp_path)
    assert s.host == "127.0.0.1" and s.port == 8765
    assert s.api_base_url == "http://127.0.0.1:8765/api/v1"


async def test_event_bus():
    bus = EventBus()
    async with bus.subscribe() as q:
        await bus.publish({"a": 1})
        assert await q.get() == {"a": 1}


def test_log_redacts_secrets(tmp_path):
    configure_logging("INFO", tmp_path)
    get_logger().info("x", api_key="sk-123", ok="v")
    line = (tmp_path / "app.log").read_text().strip().splitlines()[-1]
    data = json.loads(line)
    assert data["api_key"] == "***" and data["ok"] == "v"


def test_mcp_ping():
    assert ping()["status"] == "ok"


async def test_create_all_adds_missing_nullable_columns(tmp_path):
    """後の Phase で増えた列（NULL 可）を既存の DB に追加する。"""
    from sqlalchemy import text

    from video_automation.database.database import Database

    db = Database.from_path(tmp_path / "old.db")
    await db.create_all()
    async with db.engine.begin() as conn:
        await conn.execute(text('ALTER TABLE clips DROP COLUMN "animation"'))
        cols = [r[1] for r in (await conn.execute(text("PRAGMA table_info(clips)"))).all()]
    assert "animation" not in cols
    await db.create_all()
    async with db.engine.begin() as conn:
        cols = [r[1] for r in (await conn.execute(text("PRAGMA table_info(clips)"))).all()]
    assert "animation" in cols
    await db.dispose()
