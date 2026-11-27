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


async def test_mcp_media_asset_and_music_tools(media_settings, fake):
    from fake_assets import PEXELS_KEY, fake_resolver, media
    from fakes import FakeProtector
    from video_automation.app.bootstrap import build_container
    from video_automation.database.models import Project
    from video_automation.mcp_servers import _shared, media_server
    from video_automation.schemas.asset_source import SourceName

    media()
    names = {t.name for t in await media_server.mcp.list_tools()}
    assert {
        "search_assets",
        "inspect_license",
        "download_asset",
        "generate_music",
        "add_bgm",
    } <= names
    fake.acestep.up = True
    s = media_settings.model_copy(update={"music_poll_interval_s": 0.01})
    c = await build_container(
        s,
        http_transport=fake.transport,
        protector=FakeProtector(),
        configure_logs=False,
        dns_resolver=fake_resolver,
    )
    _shared.set_container(c)
    try:
        await c.asset_sources.set_key(SourceName.PEXELS, PEXELS_KEY)
        async with c.db.session_factory() as ses:
            p = Project(name="m")
            ses.add(p)
            await ses.commit()
            pid = p.id
        r = await media_server.search_assets("夜景", "image", limit=3)
        assert r["results"] and all(x["verdict"] == "allowed" for x in r["results"])
        assert "freesound" in r["skipped"]
        lic = await media_server.inspect_license("openverse", "ov-2", "image")
        assert lic["verdict"] == "needs_ack"
        d = await media_server.download_asset(pid, "pexels", "101", "image")
        assert d["status"] == "PENDING_APPROVAL" and d["job_id"] is None
        assert (await c.timeline.get_timeline(pid)).assets == []  # 承認前は取得しない
        g = await media_server.generate_music(pid, "upbeat chiptune", duration_sec=20)
        job = await c.jobs.wait(g["job_id"])
        assert job.status == "SUCCEEDED", job.error
        aid = job.result["assets"][0]["id"]
        b = await media_server.add_bgm(pid, aid)
        assert b["status"] == "PENDING_APPROVAL"
        assert (await c.timeline.get_timeline(pid)).clips == []
    finally:
        _shared.set_container(None)
        await c.aclose()


async def test_mcp_visual_tools(media_settings, fake, tmp_path, media_dir):
    from fakes import FakeProtector, fake_blender
    from video_automation.app.bootstrap import build_container
    from video_automation.database.models import Project
    from video_automation.mcp_servers import _shared, visual_server
    from video_automation.schemas.design import Component, ProjectDesign, Purpose
    from video_automation.schemas.timeline import (
        AddClip,
        AddTrack,
        AssetImportRequest,
        Batch,
        CommandRequest,
    )

    names = {t.name for t in await visual_server.mcp.list_tools()}
    assert {
        "create_scene",
        "animate_image",
        "animate_text",
        "create_transition",
        "create_infographic",
        "set_camera_motion",
        "list_motion_presets",
        "classify_purpose",
        "set_project_purpose",
        "check_design",
    } <= names
    s = media_settings.model_copy(update={"blender_path": fake_blender(tmp_path)})
    c = await build_container(
        s, http_transport=fake.transport, protector=FakeProtector(), configure_logs=False
    )
    _shared.set_container(c)
    try:
        async with c.db.session_factory() as ses:
            p = Project(name="v", width=320, height=180)
            ses.add(p)
            await ses.commit()
            pid = p.id
        a = await c.timeline.import_asset(
            AssetImportRequest(project_id=pid, path=str(media_dir / "tall.png"))
        )
        await c.timeline.submit(
            pid,
            CommandRequest(
                command=Batch(
                    commands=[
                        AddTrack(kind="video", track_id="v"),
                        AddClip(
                            track_id="v",
                            asset_id=a.id,
                            start_frame=0,
                            duration_frames=60,
                            clip_id="k",
                        ),
                    ]
                )
            ),
        )
        cams = await visual_server.list_motion_presets("camera")
        assert "ken_burns_in" in {p["name"] for p in cams}
        for r in (
            await visual_server.set_camera_motion(pid, "k", "ken_burns_in"),
            await visual_server.animate_image(pid, "k", "pop_in"),
            await visual_server.create_transition(pid, "k", "dissolve", 10),
        ):
            assert r["status"] == "PENDING_APPROVAL"
        assert (await c.timeline.get_timeline(pid)).clips[0].animation is None  # 承認前
        j = await visual_server.animate_text(pid, "見出し", 0, 2, "typewriter")
        job = await c.jobs.wait(j["job_id"])
        assert job.status == "SUCCEEDED", job.error
        assert job.result["status"] == "PENDING_APPROVAL"
        await c.timeline.decide(pid, job.result["command_id"], True)
        tl = await c.timeline.get_timeline(pid)
        assert any(t.name == "テロップ" for t in tl.tracks) and len(tl.clips) == 2
        j = await visual_server.create_infographic(pid, [1, 2], ["a", "b"], title="x")
        assert (await c.jobs.wait(j["job_id"])).status == "SUCCEEDED"
        j = await visual_server.create_scene(pid, 90, 1.0, title="章", transition="dissolve")
        job = await c.jobs.wait(j["job_id"])
        assert job.status == "SUCCEEDED", job.error
        assert job.result["command"]["status"] == "PENDING_APPROVAL"
        assert (await visual_server.get_job(j["job_id"]))["status"] == "SUCCEEDED"
        # 用途（デザインの型）: 判定・設定（例外の extras は LLM からは変えない）・検査
        r = await visual_server.classify_purpose("ゲーム実況の切り抜き")
        assert r["purpose"] == "game"
        await c.design.set(pid, ProjectDesign(purpose=Purpose.SUBTITLE, extras=[Component.SFX]))
        r = await visual_server.set_project_purpose(pid, "explainer")
        assert r == {
            "purpose": "explainer",
            "extras": ["sfx"],
            "series_id": None,
            "genre": None,
            "hearing_aid": False,
        }
        assert (await visual_server.check_design(pid))["purpose"] == "explainer"
    finally:
        _shared.set_container(None)
        await c.aclose()


async def test_mcp_script_tools(settings, fake):
    from fakes import FakeProtector
    from video_automation.app.bootstrap import build_container
    from video_automation.database.models import Project
    from video_automation.mcp_servers import _shared, script_server

    names = {t.name for t in await script_server.mcp.list_tools()}
    assert {
        "create_script",
        "generate_full_script",
        "set_user_script",
        "generate_counterpart_dialogue",
        "regenerate_dialogue",
        "lock_dialogue_turn",
        "validate_script",
        "get_script_versions",
    } <= names
    c = await build_container(
        settings, http_transport=fake.transport, protector=FakeProtector(), configure_logs=False
    )
    _shared.set_container(c)
    try:
        await c.providers.discover()
        async with c.db.session_factory() as ses:
            p = Project(name="s", width=320, height=180)
            ses.add(p)
            await ses.commit()
            pid = p.id
        sc = await script_server.create_script(
            pid,
            "USER_ONE_SIDE",
            characters=[{"name": "A", "is_user": True}, {"name": "B"}],
        )
        sid = sc["script_id"]
        prop = await script_server.set_user_script(sid, ["こんにちは", "では始めます"])
        assert prop["status"] == "PENDING_APPROVAL" and len(prop["changes"]) == 2
        assert (await c.studio.detail(sid)).segments == []  # 承認前は反映しない
        await c.studio.decide_proposal(prop["proposal_id"], True)
        assert [t.generation_source for t in (await c.studio.detail(sid)).segments] == [
            "USER",
            "USER",
        ]

        def responder(body):
            props = (body.get("format") or {}).get("properties", {})
            if "replies" in props:
                return {"replies": [{"after": "L1", "turns": [{"text": "よろしく"}]}], "notes": []}
            if "candidates" in props:
                return {"candidates": ["どうも", "はじめまして"]}
            return None

        fake.chat_responder = responder
        prop = await script_server.generate_counterpart_dialogue(sid)
        assert prop["changes"] == [
            {
                "kind": "added",
                "turn_id": prop["changes"][0]["turn_id"],
                "speaker": "B",
                "after": "よろしく",
            }
        ]
        await c.studio.decide_proposal(prop["proposal_id"], True)
        d = script_server._brief(await c.studio.detail(sid))
        assert [t["text"] for t in d["turns"]] == ["こんにちは", "よろしく", "では始めます"]
        ai_turn = d["turns"][1]["turn_id"]
        cand = await script_server.regenerate_dialogue(sid, ai_turn)
        assert cand["candidates"] == ["どうも", "はじめまして"]
        # 候補は未適用
        v = await script_server.get_script_versions(sid, diff_from=1)
        assert len(v["versions"]) == 3 and len(v["diff"]["changes"]) == 3
        d = await script_server.lock_dialogue_turn(sid, ai_turn)
        assert d["turns"][1]["locked"] and d["turns"][1]["text"] == "よろしく"
        assert (await script_server.validate_script(sid))["valid"] is True
    finally:
        _shared.set_container(None)
        await c.aclose()


async def test_mcp_core_plan_edit_requires_approval(settings, fake):
    from fakes import FakeProtector
    from video_automation.app.bootstrap import build_container
    from video_automation.mcp_servers import _shared, core_server

    names = {t.name for t in await core_server.mcp.list_tools()}
    assert {"create_project", "get_project_state", "plan_edit"} <= names
    c = await build_container(
        settings, http_transport=fake.transport, protector=FakeProtector(), configure_logs=False
    )
    _shared.set_container(c)
    try:
        await c.providers.discover()
        p = await core_server.create_project("mcp", width=1280, height=720)
        pid = p["project_id"]
        fake.chat_responder = lambda b: (
            {
                "reply": "映像トラックを追加します",
                "actions": [
                    {
                        "tool": "edit_timeline",
                        "args": {
                            "description": "トラック追加",
                            "command": {"type": "add_track", "kind": "video", "name": "V"},
                        },
                    }
                ],
            }
            if "actions" in (b.get("format") or {}).get("properties", {})
            else None
        )
        r = await core_server.plan_edit(pid, "映像トラックを作って")
        assert r["actions"][0]["approval"]["kind"] == "command"
        st = await core_server.get_project_state(pid)
        assert st["tracks"] == [] and st["pending_approvals"][0]["kind"] == "command"
        assert st["project"]["width"] == 1280
    finally:
        _shared.set_container(None)
        await c.aclose()


async def test_mcp_render_quality_tools(settings, fake):
    from fakes import FakeProtector
    from video_automation.app.bootstrap import build_container
    from video_automation.database.models import Project
    from video_automation.mcp_servers import _shared, render_server

    names = {t.name for t in await render_server.mcp.list_tools()}
    assert {"run_quality_check", "get_quality_report"} <= names
    c = await build_container(
        settings, http_transport=fake.transport, protector=FakeProtector(), configure_logs=False
    )
    _shared.set_container(c)
    try:
        async with c.db.session_factory() as s:
            p = Project(name="q", width=320, height=180)
            s.add(p)
            await s.commit()
            pid = p.id
        r = await render_server.run_quality_check(pid)
        job = await c.jobs.wait(r["job_id"])
        rep = await render_server.get_quality_report(job.result["report_id"])
        assert rep["status"] == "failed"  # 空のタイムライン
        assert {s["name"] for s in rep["sections"]} == {
            "project",
            "audio",
            "subtitle",
            "video",
            "license",
        }
    finally:
        _shared.set_container(None)
        await c.aclose()
