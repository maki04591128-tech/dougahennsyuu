"""文章の指示だけで動画を作る（/build）。LLM・音声エンジン・Blender は偽物、FFmpeg は本物。"""

import shutil
from pathlib import Path

import httpx
import pytest
from asgi_lifespan import LifespanManager

from fakes import FakeProtector, fake_blender
from video_automation.api.app import create_app

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg")


@pytest.fixture
async def bclient(tmp_path, media_settings, fake):
    s = media_settings.model_copy(update={"blender_path": fake_blender(tmp_path)})
    app = create_app(s, http_transport=fake.transport, protector=FakeProtector())
    async with LifespanManager(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t/api/v1", timeout=300
        ) as c:
            c.app = app  # type: ignore[attr-defined]
            yield c


async def test_build_from_one_sentence(bclient, fake, media_settings):
    c = bclient
    await c.post("/providers/discover")
    ready = {r["key"]: r for r in (await c.get("/build/readiness")).json()}
    assert ready["voice"]["ok"] and ready["llm"]["ok"] and ready["ffmpeg"]["ok"]

    r = await c.post(
        "/build/plan", json={"instruction": "ダイヤモンドより硬い物質の解説動画を作って"}
    )
    assert r.status_code == 201, r.text
    plan = r.json()
    assert plan["purpose"] == "explainer" and plan["can_run"]
    assert (plan["width"], plan["height"]) == (1920, 1080) and plan["duration_sec"] == 120
    # 掛け合いの 2 人。偽の音声エンジンには同じ名前の話者がいないので、使える声で代わりにする
    assert len(plan["characters"]) >= 1 and all(ch["voice_style_id"] for ch in plan["characters"])
    assert plan["steps"][0] == "台本を作る" and plan["steps"][-1] == "書き出す"
    # 構成を作っただけでは、タイムラインは空
    tl = (await c.get(f"/projects/{plan['project_id']}/timeline")).json()
    assert tl["clips"] == []

    names = [ch["name"] for ch in plan["characters"]]

    def responder(body):
        props = (body.get("format") or {}).get("properties", {})
        if "scenes" not in props:
            return None
        return {
            "scenes": [
                {
                    "purpose": "導入",
                    "turns": [
                        {"speaker": names[0], "text": "今日は硬い物質の話をします。"},
                        {"speaker": names[-1], "text": "ダイヤモンドより硬いものがあるの？"},
                        {
                            "speaker": names[0],
                            "text": "計算では114ギガパスカルという値が出ています。",
                        },
                    ],
                }
            ],
            "notes": [],
        }

    fake.chat_responder = responder
    r = await c.post(
        f"/build/{plan['project_id']}/run",
        json={"script_id": plan["script_id"], "use_llm_direction": False},
    )
    assert r.status_code == 202, r.text
    job = await c.app.state.jobs.wait(r.json()["id"])
    assert job.status == "SUCCEEDED", job.error
    assert job.message == "できあがりました" and job.progress == 1.0
    video = Path(job.result["video"])
    assert video.is_file() and video.stat().st_size > 0
    tl = (await c.get(f"/projects/{plan['project_id']}/timeline")).json()
    assert tl["clips"]


async def test_build_needs_music_for_mv(bclient):
    c = bclient
    await c.post("/providers/discover")
    plan = (
        await c.post("/build/plan", json={"instruction": "この曲のリリックビデオを作って"})
    ).json()
    assert plan["purpose"] in ("lyric", "mv") and not plan["can_run"]
    assert any("曲のファイル" in n for n in plan["notes"])
