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


async def test_mcp_media_subtitle_tools(settings, fake):
    from video_automation.app.bootstrap import build_container
    from video_automation.mcp_servers import _shared, media_server
    from video_automation.schemas.subtitle import ScriptCreate

    c = await build_container(settings, http_transport=fake.transport, configure_logs=False)
    _shared.set_container(c)
    try:
        from video_automation.database.models import Project

        async with c.db.session_factory() as s:
            p = Project(name="m")
            s.add(p)
            await s.commit()
            pid = p.id
        sc = await c.subtitles.create_script(
            ScriptCreate(project_id=pid, text="こんにちは。", default_style_id=10005)
        )
        await c.subtitles.generate_voices(sc.id)
        cues = await media_server.generate_subtitles(sc.id)
        assert cues[0]["start_frame"] is not None
        out = await media_server.export_subtitles(sc.id, "srt")
        assert out["format"] == "srt" and out["cue_count"] == 1
    finally:
        _shared.set_container(None)
        await c.aclose()


async def test_mcp_render_tools_require_approval(media_settings, fake, media_dir):
    from video_automation.app.bootstrap import build_container
    from video_automation.mcp_servers import _shared, render_server
    from video_automation.schemas.timeline import AddTrack, AssetImportRequest, CommandRequest

    names = {t.name for t in await render_server.mcp.list_tools()}
    assert {
        "inspect_timeline",
        "add_clip",
        "trim_clip",
        "move_clip",
        "apply_edit",
        "preview_timeline",
        "validate_project",
        "render_project",
        "get_render_status",
    } <= names
    c = await build_container(media_settings, http_transport=fake.transport, configure_logs=False)
    _shared.set_container(c)
    try:
        from video_automation.database.models import Project

        async with c.db.session_factory() as s:
            p = Project(name="m", width=320, height=180)
            s.add(p)
            await s.commit()
            pid = p.id
        a = await c.timeline.import_asset(
            AssetImportRequest(project_id=pid, path=str(media_dir / "clip.mp4"))
        )
        await c.timeline.submit(pid, CommandRequest(command=AddTrack(kind="video", track_id="v")))
        r = await render_server.add_clip(pid, "v", a.id, 0, description="LLMの提案")
        assert r["status"] == "PENDING_APPROVAL"
        assert (await render_server.inspect_timeline(pid))["clips"] == []
        await c.timeline.decide(pid, r["command_id"], True)
        assert len((await render_server.inspect_timeline(pid))["clips"]) == 1
        assert (await render_server.validate_project(pid))["valid"] is True
        path = await render_server.preview_timeline(pid, 10)
        assert (media_settings.data_dir / path).is_file()
        rr = await render_server.render_project(pid)
        assert rr["status"] == "PENDING_APPROVAL"
        assert (await render_server.get_render_status(rr["render_id"]))["job_id"] is None
        ae = await render_server.apply_edit(
            pid,
            {
                "type": "trim_clip",
                "clip_id": (await render_server.inspect_timeline(pid))["clips"][0]["id"],
                "end_frame": 30,
            },
        )
        assert ae["status"] == "PENDING_APPROVAL"
    finally:
        _shared.set_container(None)
        await c.aclose()
