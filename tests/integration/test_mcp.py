from video_automation.mcp_servers.core_server import mcp


async def test_mcp_tool_registered_and_callable():
    tools = await mcp.list_tools()
    assert "ping" in [t.name for t in tools]
    result = await mcp.call_tool("ping", {})
    assert "ok" in str(result)


async def test_mcp_phase2_tools_registered():
    names = {t.name for t in await mcp.list_tools()}
    assert {"discover_models", "list_models", "select_model"} <= names


async def test_mcp_discover_and_select(settings, fake, monkeypatch):
    from video_automation.app.bootstrap import build_container
    from video_automation.mcp_servers import core_server

    c = await build_container(settings, http_transport=fake.transport, configure_logs=False)
    monkeypatch.setattr(core_server, "_container", c)
    res = await core_server.discover_models()
    assert len(res["models"]) == 3 and res["music"][0]["available"] is False
    key = res["models"][0]["key"]
    assert await core_server.select_model(key) == {"active_model": key}
    assert (await core_server.select_model(key, "chat"))["task"] == "chat"
    listed = await core_server.list_models()
    assert any(m["is_active"] for m in listed)
    await c.aclose()
