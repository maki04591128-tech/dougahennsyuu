"""BGM 生成（ACE-Step 偽サーバー）と BGM 配置・クレジット。"""

import httpx
import pytest
from asgi_lifespan import LifespanManager

from fakes import FakeProtector
from video_automation.api.app import create_app


@pytest.fixture
async def music_client(media_settings, fake):
    from fake_assets import fake_resolver, media

    media()
    fake.acestep.up = True
    s = media_settings.model_copy(update={"music_poll_interval_s": 0.01})
    app = create_app(
        s, http_transport=fake.transport, protector=FakeProtector(), dns_resolver=fake_resolver
    )
    async with LifespanManager(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t/api/v1"
        ) as c:
            c.app, c.fake = app, fake  # type: ignore[attr-defined]
            yield c


async def test_music_generate_records_and_places_bgm(music_client):
    c, fake = music_client, music_client.fake
    eng = (await c.get("/music/engines")).json()
    assert eng["available"] and eng["models"] == ["acestep-v15-turbo"]
    assert eng["default_model"] == "acestep-v15-turbo"
    pid = (await c.post("/projects", json={"name": "M"})).json()["id"]
    fake.ollama_loaded.add("qwen3.5-9b-local:latest")  # VRAM を使っている LLM
    await c.post("/providers/discover")
    r = await c.post(
        "/music/generate",
        json={"project_id": pid, "prompt": "calm lo-fi piano, warm", "duration_sec": 30, "bpm": 80},
    )
    assert r.status_code == 202
    job = await c.app.state.jobs.wait(r.json()["id"])
    assert job.status == "SUCCEEDED", job.error
    (asset,) = job.result["assets"]
    assert asset["kind"] == "audio" and asset["source"] == "acestep"
    assert abs(asset["duration_sec"] - 4.0) < 0.1
    body = fake.acestep.released[-1]
    assert body["lyrics"] == "[intro]\n[inst]\n[outro]" and body["audio_duration"] == 30
    assert body["bpm"] == 80 and body["use_random_seed"] is True and "seed" not in body
    assert body["audio_format"] == "wav" and body["batch_size"] == 1
    assert fake.ollama_loaded == set()  # 生成前に LLM をアンロード
    gens = (await c.get("/music/generations", params={"project_id": pid})).json()
    assert gens[0]["dit_model"] == "acestep-v15-turbo" and gens[0]["seed"] == "12345"
    assert gens[0]["params"]["prompt"] == "calm lo-fi piano, warm"
    f = await c.get(f"/assets/{asset['id']}/file")
    assert f.status_code == 200 and f.content[:4] == b"RIFF"
    assert (await c.get("/assets/nope/file")).status_code == 404
    lic = (await c.post("/assets/license/inspect", json={"asset_id": asset["id"]})).json()
    assert lic["license"]["code"] == "acestep-generated" and lic["verdict"] == "allowed"

    # 10秒(300フレーム)の映像に 4秒の BGM をループ配置
    pngs = c.app.state.container.settings.import_dirs[0] / "tall.png"
    img = (await c.post("/assets/import", json={"project_id": pid, "path": str(pngs)})).json()
    await c.post(
        f"/projects/{pid}/commands",
        json={
            "command": {
                "type": "batch",
                "commands": [
                    {"type": "add_track", "kind": "video", "track_id": "v"},
                    {
                        "type": "add_clip",
                        "track_id": "v",
                        "asset_id": img["id"],
                        "start_frame": 0,
                        "duration_frames": 300,
                    },
                ],
            }
        },
    )
    r = await c.post(f"/projects/{pid}/timeline/add_bgm", json={"asset_id": asset["id"]})
    assert r.status_code == 201, r.text
    tl = (await c.get(f"/projects/{pid}/timeline")).json()
    bgm_track = next(t for t in tl["tracks"] if t["name"] == "BGM")
    clips = sorted(
        (x for x in tl["clips"] if x["track_id"] == bgm_track["id"]), key=lambda x: x["start_frame"]
    )
    assert [(x["start_frame"], x["end_frame"]) for x in clips] == [(0, 120), (120, 240), (240, 300)]
    assert all(x["volume"] == 0.25 for x in clips)
    assert clips[0]["fade_in_frames"] == 30 and clips[-1]["fade_out_frames"] == 60
    assert tl["duration_frames"] == 300
    assert (await c.post(f"/projects/{pid}/undo")).status_code == 200  # 1回で全部戻る
    tl = (await c.get(f"/projects/{pid}/timeline")).json()
    assert not any(t["name"] == "BGM" for t in tl["tracks"])
    bad = await c.post(f"/projects/{pid}/timeline/add_bgm", json={"asset_id": img["id"]})
    assert bad.status_code == 422
    cr = (await c.get(f"/projects/{pid}/credits")).json()
    assert "ACE-Step 1.5" in cr["text"] and "AI生成" in cr["text"]


async def test_music_options_and_failures(music_client):
    c, fake = music_client, music_client.fake
    pid = (await c.post("/projects", json={"name": "M"})).json()["id"]
    r = await c.post(
        "/music/generate", json={"project_id": pid, "prompt": "song", "instrumental": False}
    )
    assert r.status_code == 422  # 歌ありは歌詞が必要
    r = await c.post(
        "/music/generate",
        json={
            "project_id": pid,
            "prompt": "j-pop",
            "instrumental": False,
            "lyrics": "[Verse]\nこんにちは",
            "seed": 42,
            "batch_size": 2,
            "key_scale": "C Major",
        },
    )
    job = await c.app.state.jobs.wait(r.json()["id"])
    assert job.status == "SUCCEEDED", job.error and len(job.result["assets"]) == 2
    body = fake.acestep.released[-1]
    assert body["lyrics"].startswith("[Verse]") and body["seed"] == 42
    assert body["use_random_seed"] is False and body["vocal_language"] == "ja"
    assert len(job.result["assets"]) == 2
    fake.acestep.fail, fake.acestep.polls = True, 0
    job = await c.app.state.jobs.wait(
        (await c.post("/music/generate", json={"project_id": pid, "prompt": "x"})).json()["id"]
    )
    assert job.status == "FAILED" and "generation failed" in job.error
    fake.acestep.up = False
    eng = (await c.get("/music/engines")).json()
    assert not eng["available"] and "ConnectError" in eng["error"]
    job = await c.app.state.jobs.wait(
        (await c.post("/music/generate", json={"project_id": pid, "prompt": "x"})).json()["id"]
    )
    assert job.status == "FAILED" and job.error.startswith("AceStepUnavailableError")
    r = await c.post("/music/generate", json={"project_id": "nope", "prompt": "x"})
    job = await c.app.state.jobs.wait(r.json()["id"])
    assert job.status == "FAILED"
