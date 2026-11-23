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
    from video_automation.mcp_servers import _shared, core_server

    c = await build_container(settings, http_transport=fake.transport, configure_logs=False)
    _shared.set_container(c)
    res = await core_server.discover_models()
    assert len(res["models"]) == 3 and res["music"][0]["available"] is False
    key = res["models"][0]["key"]
    assert await core_server.select_model(key) == {"active_model": key}
    assert (await core_server.select_model(key, "chat"))["task"] == "chat"
    listed = await core_server.list_models()
    assert any(m["is_active"] for m in listed)
    _shared.set_container(None)
    await c.aclose()


async def test_mcp_media_voice_tools(settings, fake):
    from video_automation.app.bootstrap import build_container
    from video_automation.mcp_servers import _shared, media_server

    names = {t.name for t in await media_server.mcp.list_tools()}
    assert {"generate_voice", "adjust_prosody", "register_pronunciation", "list_speakers"} <= names
    c = await build_container(settings, http_transport=fake.transport, configure_logs=False)
    _shared.set_container(c)
    try:
        assert len(await media_server.list_speakers()) == 9
        a = await media_server.generate_voice("こんにちは", 10005, speed_scale=1.1)
        assert a["credit"] == "VOICEVOX Nemo"
        b = await media_server.adjust_prosody(a["id"], intonation_scale=1.3)
        assert b["prosody"] == {"speed_scale": 1.1, "intonation_scale": 1.3}
        w = await media_server.register_pronunciation("動画", "ドウガ", 0)
        assert w["pronunciation"] == "ドウガ"
    finally:
        _shared.set_container(None)
        await c.aclose()
