import asyncio


async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {
        "status": "ok",
        "application": "VideoAutomation",
        "database": "connected",
        "version": "0.6.0",
    }


async def test_project_save_and_get(client):
    r = await client.post(
        "/projects",
        json={"name": "Test Project", "width": 1920, "height": 1080, "fps_num": 30, "fps_den": 1},
    )
    assert r.status_code == 201
    pid = r.json()["id"]
    got = await client.get(f"/projects/{pid}")
    assert got.status_code == 200 and got.json()["name"] == "Test Project"
    assert len((await client.get("/projects")).json()) == 1


async def test_project_validation_and_404(client):
    assert (await client.post("/projects", json={"name": ""})).status_code == 422
    assert (await client.get("/projects/nope")).status_code == 404


async def test_job_lifecycle_and_cancel(client):
    jobs = client.app.state.jobs

    async def ok(progress):
        await progress(0.5)
        return {"v": 1}

    job = jobs.submit("t", ok)
    done = await jobs.wait(job.id)
    assert done.status == "SUCCEEDED" and done.result == {"v": 1}
    assert (await client.get(f"/jobs/{job.id}")).json()["status"] == "SUCCEEDED"

    async def slow(progress):
        await asyncio.sleep(60)

    j2 = jobs.submit("slow", slow)
    await asyncio.sleep(0.05)
    r = await client.post(f"/jobs/{j2.id}/cancel")
    assert r.json()["status"] == "CANCELLED"

    async def bad(progress):
        raise RuntimeError("secret-detail")

    j3 = await jobs.wait(jobs.submit("bad", bad).id)
    assert j3.status == "FAILED" and "secret" not in (j3.error or "")
    assert (await client.get("/jobs/missing")).status_code == 404
