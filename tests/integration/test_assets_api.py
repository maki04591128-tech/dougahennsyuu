"""素材検索・取得・ライセンス（Phase 6）。外部サービスは偽サーバー（tests/fake_assets.py）。"""

import pytest

from fake_assets import FREESOUND_KEY, PEXELS_KEY, PIXABAY_KEY


async def _keys(c):
    for name, key in (
        ("pexels", PEXELS_KEY),
        ("pixabay", PIXABAY_KEY),
        ("freesound", FREESOUND_KEY),
    ):
        r = await c.put(f"/asset_sources/{name}/key", json={"api_key": key})
        assert r.status_code == 200 and r.json()["configured"], r.text


async def _project(c, w=1920, h=1080):
    return (await c.post("/projects", json={"name": "A", "width": w, "height": h})).json()["id"]


async def _wait(c, job_id):
    return await c.app.state.jobs.wait(job_id)


async def test_sources_and_keys(aclient):
    c = aclient
    st = {s["name"]: s for s in (await c.get("/asset_sources")).json()}
    assert set(st) == {"pexels", "pixabay", "freesound", "openverse", "wikimedia"}
    assert not st["pexels"]["configured"] and st["pexels"]["requires_key"]
    assert st["openverse"]["configured"] and not st["openverse"]["requires_key"]
    r = await c.put("/asset_sources/openverse/key", json={"api_key": "x" * 10})
    assert r.status_code == 422  # キー不要の取得元
    await _keys(c)
    assert all(s["configured"] for s in (await c.get("/asset_sources")).json())
    assert (await c.delete("/asset_sources/pexels/key")).status_code == 204
    st = {s["name"]: s for s in (await c.get("/asset_sources")).json()}
    assert not st["pexels"]["configured"]
    # キーは暗号化して保存（FakeProtector は反転）。平文がDBに無いこと
    from sqlalchemy import select

    from video_automation.database.models import AssetSourceKey

    async with c.app.state.db.session_factory() as s:
        rows = (await s.execute(select(AssetSourceKey))).scalars().all()
    assert rows and all(PIXABAY_KEY.encode() not in r.api_key_encrypted for r in rows)


async def test_search_images_across_sources_with_license(aclient):
    c = aclient
    r = await c.post("/assets/search", json={"query": "富士山", "media_type": "image"})
    body = r.json()
    # キー未設定の取得元は飛ばし、Freesound は画像非対応
    assert body["skipped"]["pexels"] == "APIキー未設定"
    assert "非対応" in body["skipped"]["freesound"]
    await _keys(c)
    body = (await c.post("/assets/search", json={"query": "富士山", "media_type": "image"})).json()
    assert body["errors"] == {}
    res = body["results"]
    by = {(x["source"], x["source_id"]): x for x in res}
    assert {x["source"] for x in res[:4]} == {"pexels", "pixabay", "openverse", "wikimedia"}
    px = by[("pexels", "101")]
    assert px["license"]["code"] == "pexels" and px["verdict"] == "allowed"
    assert px["attribution"].startswith("Photo by Hanako Pexels on Pexels")
    pb = by[("pixabay", "301")]
    assert pb["variants"][0]["width"] == 1280 and pb["tags"] == ["桜", "春", "花"]
    wm = by[("wikimedia", "701")]
    assert wm["creator"] == "Taro & Co" and wm["license"]["code"] == "cc-by-sa"
    assert "CC BY-SA 4.0" in wm["attribution"] and wm["verdict"] == "allowed"
    nc = by[("openverse", "ov-2")]
    assert nc["verdict"] == "needs_ack" and nc["license"]["commercial_use"] is False
    # Pexels は日本語クエリで locale を付ける
    allowed = (
        await c.post(
            "/assets/search", json={"query": "富士山", "media_type": "image", "allowed_only": True}
        )
    ).json()["results"]
    assert all(x["verdict"] == "allowed" for x in allowed)
    assert ("openverse", "ov-2") not in {(x["source"], x["source_id"]) for x in allowed}


async def test_search_cache_and_errors(aclient):
    c, web = aclient, aclient.fake.web
    await _keys(c)
    q = {"query": "sakura", "media_type": "image", "sources": ["pixabay"]}
    await c.post("/assets/search", json=q)
    n = sum(1 for h, p in web.calls if h == "pixabay.com")
    again = (await c.post("/assets/search", json=q)).json()
    assert again["cached"] == ["pixabay"] and len(again["results"]) == 1
    assert sum(1 for h, p in web.calls if h == "pixabay.com") == n  # 24時間キャッシュ
    # 認証失敗・レート制限は取得元ごとのエラーとして返し、他の結果は返す
    await c.put("/asset_sources/pexels/key", json={"api_key": "wrong-key-000"})
    web.rate_limited.add("api.openverse.org")
    body = (await c.post("/assets/search", json={"query": "x", "media_type": "image"})).json()
    assert "authentication" in body["errors"]["pexels"]
    assert "rate limited" in body["errors"]["openverse"]
    assert {x["source"] for x in body["results"]} == {"pixabay", "wikimedia"}


async def test_download_photo_records_license_and_credits(aclient):
    c = aclient
    await _keys(c)
    pid = await _project(c)
    r = await c.post(
        "/assets/download",
        json={"project_id": pid, "source": "pexels", "source_id": "101", "media_type": "image"},
    )
    assert r.status_code == 202 and r.json()["status"] == "QUEUED"
    job = await _wait(c, r.json()["job_id"])
    assert job.status == "SUCCEEDED", job.error
    a = job.result
    assert (
        a["kind"] == "image"
        and a["source"] == "pexels"
        and (a["width"], a["height"]) == (1920, 1280)
    )
    assert a["duration_sec"] is None and a["name"] == "夜の東京タワー"
    # 同じ素材の再取得は既存の素材を返す
    r2 = await c.post(
        "/assets/download",
        json={"project_id": pid, "source": "pexels", "source_id": "101", "media_type": "image"},
    )
    assert (await _wait(c, r2.json()["job_id"])).result["id"] == a["id"]
    lic = (await c.post("/assets/license/inspect", json={"asset_id": a["id"]})).json()
    assert lic["license"]["code"] == "pexels" and lic["verdict"] == "allowed"
    cr = (await c.get(f"/projects/{pid}/credits")).json()
    assert "画像:" in cr["text"] and "Photo by Hanako Pexels on Pexels" in cr["text"]
    # 取り込み素材（記録なし）の判定
    own = await c.post(
        "/assets/import",
        json={
            "project_id": pid,
            "path": str(c.app.state.container.settings.import_dirs[0] / "tall.png"),
        },
    )
    lic = (await c.post("/assets/license/inspect", json={"asset_id": own.json()["id"]})).json()
    assert lic["license"]["code"] == "own"


async def test_download_video_picks_variant_and_follows_safe_redirect(aclient):
    c = aclient
    await _keys(c)
    big = await _project(c)
    j = await _wait(
        c,
        (
            await c.post(
                "/assets/download",
                json={
                    "project_id": big,
                    "source": "pexels",
                    "source_id": "201",
                    "media_type": "video",
                },
            )
        ).json()["job_id"],
    )
    assert j.status == "SUCCEEDED", j.error
    assert (j.result["width"], j.result["height"]) == (1920, 1080)
    small = await _project(c, 640, 360)
    j = await _wait(
        c,
        (
            await c.post(
                "/assets/download",
                json={
                    "project_id": small,
                    "source": "pexels",
                    "source_id": "201",
                    "media_type": "video",
                },
            )
        ).json()["job_id"],
    )
    assert j.status == "SUCCEEDED", j.error  # sd はリダイレクト経由
    assert (j.result["width"], j.result["height"]) == (640, 360)
    j = await _wait(
        c,
        (
            await c.post(
                "/assets/download",
                json={
                    "project_id": big,
                    "source": "pixabay",
                    "source_id": "401",
                    "media_type": "video",
                },
            )
        ).json()["job_id"],
    )
    assert j.status == "SUCCEEDED", j.error and j.result["width"] == 1920


async def test_license_needs_ack_and_ssrf_guard(aclient):
    c = aclient
    pid = await _project(c)
    body = {"project_id": pid, "source": "openverse", "source_id": "ov-2", "media_type": "image"}
    r = await c.post("/assets/download", json=body)
    assert r.status_code == 409
    assert r.json()["code"] == "license_ack_required"
    assert any("非営利" in x for x in r.json()["license"]["reasons"])
    r = await c.post("/assets/download", json={**body, "acknowledge_license": True})
    job = await _wait(c, r.json()["job_id"])
    assert job.status == "SUCCEEDED", job.error
    # タイムラインで使うと検証で注意が出る
    cmd = lambda b: c.post(f"/projects/{pid}/commands", json={"command": b})  # noqa: E731
    await cmd({"type": "add_track", "kind": "video", "track_id": "v"})
    await cmd(
        {
            "type": "add_clip",
            "track_id": "v",
            "asset_id": job.result["id"],
            "start_frame": 0,
            "duration_frames": 30,
        }
    )
    v = (await c.get(f"/projects/{pid}/validate")).json()
    assert any(i["level"] == "warning" and "cc-by-nc" in i["message"] for i in v["issues"])
    # 内部アドレスに解決されるURLは取得しない
    r = await c.post("/assets/download", json={**body, "source_id": "ov-3"})
    job = await _wait(c, r.json()["job_id"])
    assert job.status == "FAILED" and "non-public" in job.error
    files = list((c.app.state.container.settings.data_dir / "projects" / pid / "assets").iterdir())
    assert all(not f.name.endswith((".part", ".download")) for f in files)
    # 存在しないID
    r = await c.post("/assets/download", json={**body, "source_id": "nope"})
    assert r.status_code == 404


async def test_llm_download_requires_approval(aclient):
    c = aclient
    await _keys(c)
    pid = await _project(c)
    r = await c.post(
        "/assets/download",
        json={
            "project_id": pid,
            "source": "freesound",
            "source_id": "501",
            "media_type": "audio",
            "origin": "llm",
            "acknowledge_license": True,
        },
    )
    assert r.json()["status"] == "PENDING_APPROVAL" and r.json()["job_id"] is None
    r2 = await c.post(
        "/assets/download",
        json={
            "project_id": pid,
            "source": "freesound",
            "source_id": "503",
            "media_type": "audio",
            "origin": "llm",
        },
    )
    reqs = (
        await c.get("/assets/requests", params={"project_id": pid, "status": "PENDING_APPROVAL"})
    ).json()
    assert [x["source_id"] for x in reqs] == ["501", "503"]
    assert reqs[1]["verdict"] == "needs_ack" and reqs[1]["license_code"] == "cc-by-nc"
    ok = (await c.post(f"/assets/requests/{r.json()['request_id']}/approve")).json()
    assert ok["status"] == "APPROVED" and ok["job_id"]
    job = await _wait(c, ok["job_id"])
    assert job.status == "SUCCEEDED", job.error
    assert job.result["kind"] == "audio" and job.result["has_audio"]
    done = (await c.get("/assets/requests", params={"project_id": pid})).json()
    assert done[0]["status"] == "DONE" and done[0]["asset_id"] == job.result["id"]
    rej = (await c.post(f"/assets/requests/{r2.json()['request_id']}/reject")).json()
    assert rej["status"] == "REJECTED"
    assert (await c.post(f"/assets/requests/{r2.json()['request_id']}/approve")).status_code == 422
    cr = (await c.get(f"/projects/{pid}/credits")).json()
    assert "Door knock" in cr["text"] and "CC BY 4.0" in cr["text"]


async def test_audio_search_duration_filter_and_thumbnail(aclient):
    c = aclient
    await _keys(c)
    body = (
        await c.post(
            "/assets/search", json={"query": "rain", "media_type": "audio", "max_duration_sec": 30}
        )
    ).json()
    ids = {(x["source"], x["source_id"]) for x in body["results"]}
    assert ("freesound", "504") not in ids and ("freesound", "502") in ids
    assert ("openverse", "ova-1") in ids
    ova = next(x for x in body["results"] if x["source_id"] == "ova-1")
    assert ova["duration_sec"] == 1.0  # ミリ秒→秒
    fs = next(x for x in body["results"] if x["source_id"] == "501")
    assert any("OAuth2" in n for n in fs["license"]["notes"])
    img = (await c.post("/assets/search", json={"query": "x", "media_type": "image"})).json()
    r = await c.post(
        "/assets/thumbnail", json={"source": "wikimedia", "source_id": "701", "media_type": "image"}
    )
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert len(img["results"]) > 0
    r = await c.post(
        "/assets/thumbnail", json={"source": "wikimedia", "source_id": "999", "media_type": "image"}
    )
    assert r.status_code == 404


@pytest.mark.parametrize(
    "bad", [{"query": ""}, {"query": "x", "per_page": 500}, {"query": "x", "media_type": "music"}]
)
async def test_search_validation(aclient, bad):
    assert (await aclient.post("/assets/search", json=bad)).status_code == 422
