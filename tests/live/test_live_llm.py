"""実機テスト。VA_LIVE_TESTS=1 のときだけ実行（実際の Ollama 等に接続する）。

set VA_LIVE_TESTS=1 && uv run pytest tests/live -s
"""

import json
import os

import httpx
import pytest
from asgi_lifespan import LifespanManager

from video_automation.api.app import create_app
from video_automation.app.settings import Settings

pytestmark = pytest.mark.skipif(os.environ.get("VA_LIVE_TESTS") != "1", reason="live only")


async def test_live_discover_and_test_all_models(tmp_path):
    out = os.environ.get("VA_LIVE_REPORT")
    app = create_app(Settings(data_dir=tmp_path))
    report: dict = {}
    async with LifespanManager(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t/api/v1", timeout=900
        ) as c:
            disc = (await c.post("/providers/discover")).json()
            report["providers"] = disc["providers"]
            report["music"] = disc["music"]
            models = [m for m in disc["models"] if m["provider_type"] in ("ollama", "lmstudio")]
            assert models, "ローカルモデルが検出されませんでした"
            report["tests"] = []
            for m in models:
                job = (await c.post("/models/test", json={"model_key": m["key"]})).json()
                done = await app.state.jobs.wait(job["id"])
                report["tests"].append(
                    {"key": m["key"], "status": done.status, "result": done.result}
                )
                assert done.status == "SUCCEEDED"
            report["assignments"] = (await c.get("/models/task-assignment")).json()
    if out:
        with open(out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    assert all(t["result"]["success"] for t in report["tests"])
