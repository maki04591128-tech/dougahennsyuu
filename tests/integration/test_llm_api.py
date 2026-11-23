import asyncio

from fakes import OLLAMA_MODELS
from video_automation.database.models import LlmProvider

QWEN = "ollama-local::qwen3.5-9b-local:latest"
GEMMA = "ollama-local::gemma4-e4b-local:latest"
SWALLOW = "ollama-local::qwen3-swallow-8b-local:latest"


async def _discover(client):
    r = await client.post("/providers/discover")
    assert r.status_code == 200
    return r.json()


async def _run_test(client, key, probes=None):
    r = await client.post("/models/test", json={"model_key": key, "probes": probes})
    assert r.status_code == 202
    job = await client.app.state.jobs.wait(r.json()["id"])
    assert job.status == "SUCCEEDED", job.error
    return job.result


async def test_discover_detects_local_models_and_acestep_separately(client):
    data = await _discover(client)
    by_id = {p["provider_id"]: p for p in data["providers"]}
    assert by_id["ollama-local"]["available"] and by_id["ollama-local"]["models_count"] == 3
    assert not by_id["lmstudio-local"]["available"]
    assert {m["model_id"] for m in data["models"]} == set(OLLAMA_MODELS)
    # ACE-Step は LLM 一覧に混ざらず music として別管理
    assert data["music"][0]["name"] == "ACE-Step 1.5" and not data["music"][0]["available"]
    assert data["music"][0]["installed"] is False
    assert len((await client.get("/models")).json()) == 3
    assert len((await client.get("/providers")).json()) == 2


async def test_models_persist_when_server_goes_down(client, fake):
    await _discover(client)
    fake.ollama_up = False
    data = await _discover(client)
    assert not data["providers"][0]["available"]
    assert len(data["models"]) == 3  # 一時停止で一覧を消さない


async def test_inference_test_probes_reflect_real_behaviour(client):
    await _discover(client)
    sw = await _run_test(client, SWALLOW)
    probes = {p["name"]: p for p in sw["probes"]}
    assert sw["success"] and sw["tokens_per_sec"] == 50.0
    assert probes["streaming"]["ok"] and probes["structured"]["ok"]
    assert probes["tools"]["detail"].startswith("fallback")
    assert sw["capabilities"]["tool_calling"] is False
    assert sw["capabilities"]["tool_calling_fallback"] is True
    assert probes["vision"]["ok"] is False and sw["capabilities"]["image_input"] is False

    gm = await _run_test(client, GEMMA, ["structured", "basic"])
    gp = {p["name"]: p for p in gm["probes"]}
    assert gp["structured"]["ok"] is False and "out of range" in gp["structured"]["detail"]

    caps = (await client.get("/models/capabilities", params={"model_key": SWALLOW})).json()
    assert caps["tool_calling_fallback"] is True and caps["source"]["tool_calling"] == "probed"
    models = {m["key"]: m for m in (await client.get("/models")).json()}
    assert models[SWALLOW]["state"] == "INFERENCE_READY"
    assert models[QWEN]["state"] == "MODEL_DISCOVERED"

    # 再検出しても実測値は申告値で上書きされない
    await _discover(client)
    caps = (await client.get("/models/capabilities", params={"model_key": SWALLOW})).json()
    assert caps["tool_calling"] is False and caps["tool_calling_fallback"] is True


async def test_single_loaded_model_unloads_others(client, fake):
    await _discover(client)
    fake.ollama_loaded = {"gemma4-e4b-local:latest"}
    await _run_test(client, QWEN, ["basic"])
    assert fake.ollama_loaded == {"qwen3.5-9b-local:latest"}


async def test_active_model_and_task_assignment(client):
    await _discover(client)
    assert (await client.get("/models/active")).json() is None
    r = await client.put("/models/active", json={"model_key": QWEN})
    assert r.status_code == 200 and r.json()["is_active"]
    assert (await client.put("/models/active", json={"model_key": "x::y"})).status_code == 404

    await _run_test(client, SWALLOW, ["tools"])
    rows = {a["task"]: a for a in (await client.get("/models/task-assignment")).json()}
    assert rows["tool_selection"]["resolved_model_key"] == QWEN
    assert rows["script_generation"]["resolved_model_key"] == SWALLOW
    assert rows["image_understanding"]["mode"] == "auto"

    r = await client.put("/models/task-assignment", json={"task": "chat", "model_key": GEMMA})
    chat = next(a for a in r.json() if a["task"] == "chat")
    assert chat["mode"] == "manual" and chat["resolved_model_key"] == GEMMA
    r = await client.put("/models/task-assignment", json={"task": "chat", "model_key": None})
    assert next(a for a in r.json() if a["task"] == "chat")["mode"] == "auto"
    bad = await client.put("/models/task-assignment", json={"task": "chat", "model_key": "a::b"})
    assert bad.status_code == 404
    assert (
        await client.put("/models/task-assignment", json={"task": "nope", "model_key": None})
    ).status_code == 422


async def test_register_provider_encrypts_key_and_validates(client):
    r = await client.post(
        "/providers/register",
        json={
            "type": "openai_compatible",
            "name": "llama.cpp",
            "base_url": "http://127.0.0.1:8080/",
            "api_key": "sk-secret-123",
        },
    )
    assert r.status_code == 201
    pid = r.json()["id"]
    assert r.json()["base_url"] == "http://127.0.0.1:8080" and r.json()["has_api_key"]
    assert "sk-secret" not in r.text
    async with client.app.state.db.session_factory() as s:
        row = await s.get(LlmProvider, pid)
        assert row.api_key_encrypted and b"sk-secret-123" not in row.api_key_encrypted
    # 未起動の登録先は検出で未接続として扱う
    data = await _discover(client)
    st = next(p for p in data["providers"] if p["provider_id"] == pid)
    assert not st["available"]

    bad = await client.post(
        "/providers/register",
        json={"type": "openai_compatible", "name": "x", "base_url": "file:///etc/passwd"},
    )
    assert bad.status_code == 422
    assert (await client.delete("/providers/ollama-local")).status_code == 400
    assert (await client.delete(f"/providers/{pid}")).status_code == 204
    assert (await client.delete(f"/providers/{pid}")).status_code == 404


async def test_register_with_key_without_dpapi_is_rejected(settings, fake):
    import httpx
    from asgi_lifespan import LifespanManager

    from video_automation.api.app import create_app
    from video_automation.app.secrets import UnavailableProtector

    app = create_app(settings, http_transport=fake.transport, protector=UnavailableProtector())
    async with LifespanManager(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t/api/v1"
        ) as c:
            r = await c.post(
                "/providers/register",
                json={
                    "type": "openai",
                    "name": "o",
                    "base_url": "https://api.openai.com/v1",
                    "api_key": "sk-x",
                },
            )
            assert r.status_code == 400 and r.json()["code"] == "secret_storage_unavailable"
            assert "sk-x" not in r.text


async def test_unknown_model_test_returns_404(client):
    r = await client.post("/models/test", json={"model_key": "ollama-local::none"})
    assert r.status_code == 404 and r.json()["code"] == "model_not_found"


async def test_concurrent_discovery_is_safe(client):
    results = await asyncio.gather(*(client.post("/providers/discover") for _ in range(5)))
    assert all(r.status_code == 200 for r in results)
    assert len((await client.get("/models")).json()) == 3
