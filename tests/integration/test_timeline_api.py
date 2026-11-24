"""タイムライン・編集命令（実 FFmpeg/ffprobe を使用）。"""


async def _setup(c, media_dir, fps=(30, 1)):
    pid = (
        await c.post("/projects", json={"name": "T", "fps_num": fps[0], "fps_den": fps[1]})
    ).json()["id"]

    def imp(name):
        return c.post("/assets/import", json={"project_id": pid, "path": str(media_dir / name)})

    video, image, tone = [(await imp(n)).json() for n in ("clip.mp4", "tall.png", "tone.wav")]
    return pid, video, image, tone


async def _cmd(c, pid, command, **kw):
    return await c.post(f"/projects/{pid}/commands", json={"command": command, **kw})


async def _tracks(c, pid):
    v = (await _cmd(c, pid, {"type": "add_track", "kind": "video"})).json()
    a = (await _cmd(c, pid, {"type": "add_track", "kind": "audio"})).json()
    return v["command"]["track_id"], a["command"]["track_id"]


async def test_import_assets_probe_and_restrict_paths(mclient, media_dir, tmp_path):
    pid, video, image, tone = await _setup(mclient, media_dir)
    assert video["kind"] == "video" and video["has_audio"] and video["width"] == 640
    assert abs(video["duration_sec"] - 3.0) < 0.1 and video["path"].startswith(f"projects/{pid}/")
    assert image["kind"] == "image" and image["duration_sec"] is None
    assert tone["kind"] == "audio" and len(tone["sha256"]) == 64
    outside = tmp_path / "secret.mp4"
    outside.write_bytes(b"x")
    r = await mclient.post("/assets/import", json={"project_id": pid, "path": str(outside)})
    assert r.status_code == 403
    r = await mclient.post(
        "/assets/import", json={"project_id": pid, "path": str(media_dir / "no.mp4")}
    )
    assert r.status_code == 404
    r = await mclient.post("/assets/import", json={"project_id": pid, "path": "relative.mp4"})
    assert r.status_code == 403


async def test_edit_commands_validation_and_history(mclient, media_dir):
    c = mclient
    pid, video, image, tone = await _setup(c, media_dir)
    vt, at = await _tracks(c, pid)
    r = await _cmd(
        c,
        pid,
        {
            "type": "add_clip",
            "track_id": vt,
            "asset_id": video["id"],
            "start_frame": 0,
            "clip_id": "c1",
        },
    )
    assert r.status_code == 201 and r.json()["status"] == "APPLIED"
    tl = (await c.get(f"/projects/{pid}/timeline")).json()
    assert tl["clips"][0]["end_frame"] == 90 and tl["duration_frames"] == 90

    # 不正な命令は 422（重なり / 種類違い / 素材範囲外 / 画像の長さ未指定）
    bad = [
        {"type": "add_clip", "track_id": vt, "asset_id": video["id"], "start_frame": 45},
        {"type": "add_clip", "track_id": vt, "asset_id": tone["id"], "start_frame": 200},
        {
            "type": "add_clip",
            "track_id": vt,
            "asset_id": video["id"],
            "start_frame": 200,
            "source_in_frame": 60,
            "duration_frames": 60,
        },
        {"type": "add_clip", "track_id": vt, "asset_id": image["id"], "start_frame": 200},
        {"type": "trim_clip", "clip_id": "c1", "end_frame": 120},
        {"type": "split_clip", "clip_id": "c1", "at_frame": 90},
        {"type": "set_clip_props", "clip_id": "c1", "fade_in_frames": 60, "fade_out_frames": 60},
    ]
    for cmd in bad:
        r = await _cmd(c, pid, cmd)
        assert r.status_code == 422, (cmd, r.text)
    v = await c.post(f"/projects/{pid}/commands/validate", json={"command": bad[0]})
    assert v.json()["valid"] is False and "overlaps" in v.json()["issues"][0]["message"]
    assert (await _cmd(c, pid, {"type": "nope"})).status_code == 422

    # トリム（素材側の in も連動）→ 分割 → 移動 → 削除
    await _cmd(c, pid, {"type": "trim_clip", "clip_id": "c1", "start_frame": 15})
    clip = (await c.get(f"/projects/{pid}/timeline")).json()["clips"][0]
    assert (clip["start_frame"], clip["source_in_frame"], clip["end_frame"]) == (15, 15, 90)
    await _cmd(c, pid, {"type": "split_clip", "clip_id": "c1", "at_frame": 45, "new_clip_id": "c2"})
    await _cmd(c, pid, {"type": "move_clip", "clip_id": "c2", "start_frame": 100})
    clips = {x["id"]: x for x in (await c.get(f"/projects/{pid}/timeline")).json()["clips"]}
    assert (clips["c1"]["end_frame"], clips["c1"]["source_out_frame"]) == (45, 45)
    assert (clips["c2"]["start_frame"], clips["c2"]["source_in_frame"]) == (100, 45)

    # Undo ×2 → Redo → 新規操作で Redo 不可
    assert (await c.post(f"/projects/{pid}/undo")).json()["command"]["type"] == "move_clip"
    assert (await c.post(f"/projects/{pid}/undo")).json()["command"]["type"] == "split_clip"
    clips = (await c.get(f"/projects/{pid}/timeline")).json()["clips"]
    assert len(clips) == 1 and clips[0]["end_frame"] == 90
    assert (await c.post(f"/projects/{pid}/redo")).json()["command"]["type"] == "split_clip"
    assert len((await c.get(f"/projects/{pid}/timeline")).json()["clips"]) == 2
    await _cmd(c, pid, {"type": "delete_clip", "clip_id": "c2"})
    assert (await c.post(f"/projects/{pid}/redo")).status_code == 422
    hist = (await c.get(f"/projects/{pid}/commands")).json()
    assert [h["status"] for h in hist].count("DISCARDED") == 1

    # 冪等性キー：同じキーの再送は二重適用しない
    body = {"type": "add_clip", "track_id": at, "asset_id": tone["id"], "start_frame": 0}
    r1 = await _cmd(c, pid, body, idempotency_key="k1")
    r2 = await _cmd(c, pid, body, idempotency_key="k1")
    assert r1.json()["id"] == r2.json()["id"]
    assert (
        len(
            [
                x
                for x in (await c.get(f"/projects/{pid}/timeline")).json()["clips"]
                if x["track_id"] == at
            ]
        )
        == 1
    )


async def test_llm_commands_require_approval(mclient, media_dir):
    c = mclient
    pid, video, image, _ = await _setup(c, media_dir)
    vt, _ = await _tracks(c, pid)
    cmd = {
        "type": "add_clip",
        "track_id": vt,
        "asset_id": image["id"],
        "start_frame": 0,
        "duration_frames": 30,
    }
    r = await _cmd(c, pid, cmd, origin="llm", description="画像を追加")
    assert r.json()["status"] == "PENDING_APPROVAL"
    assert (await c.get(f"/projects/{pid}/timeline")).json()["clips"] == []
    bad = await _cmd(c, pid, {**cmd, "asset_id": "missing"}, origin="llm")
    assert bad.status_code == 422  # 承認待ちでも事前検証はする
    ok = await c.post(f"/projects/{pid}/commands/{r.json()['id']}/approve")
    assert ok.json()["status"] == "APPLIED"
    assert len((await c.get(f"/projects/{pid}/timeline")).json()["clips"]) == 1
    assert (await c.post(f"/projects/{pid}/commands/{r.json()['id']}/approve")).status_code == 422
    r2 = await _cmd(c, pid, {**cmd, "start_frame": 60}, origin="llm")
    rej = await c.post(f"/projects/{pid}/commands/{r2.json()['id']}/reject")
    assert rej.json()["status"] == "REJECTED"
    pending = (
        await c.get(f"/projects/{pid}/commands", params={"status": "PENDING_APPROVAL"})
    ).json()
    assert pending == []


async def test_batch_is_atomic(mclient, media_dir):
    c = mclient
    pid, video, _, _ = await _setup(c, media_dir)
    vt, _ = await _tracks(c, pid)
    r = await _cmd(
        c,
        pid,
        {
            "type": "batch",
            "commands": [
                {"type": "add_clip", "track_id": vt, "asset_id": video["id"], "start_frame": 0},
                {"type": "add_clip", "track_id": vt, "asset_id": video["id"], "start_frame": 30},
            ],
        },
    )
    assert r.status_code == 422
    assert (await c.get(f"/projects/{pid}/timeline")).json()["clips"] == []


async def test_project_update_and_validate(mclient, media_dir):
    c = mclient
    pid, video, _, _ = await _setup(c, media_dir)
    r = await c.put(f"/projects/{pid}", json={"width": 1080, "height": 1920})
    assert (r.json()["width"], r.json()["height"]) == (1080, 1920)
    v = (await c.get(f"/projects/{pid}/validate")).json()
    assert not v["valid"] and v["issues"][0]["message"] == "timeline is empty"


async def test_duplicate_track_or_clip_id_is_a_command_error(mclient, media_dir):
    """トラック・クリップの ID は全プロジェクトで一意。重なったら 500 ではなく 422 を返す。"""
    c = mclient
    pid, video, _, _ = await _setup(c, media_dir)
    pid2, video2, _, _ = await _setup(c, media_dir)
    r = await _cmd(c, pid, {"type": "add_track", "kind": "video", "track_id": "scr"})
    assert r.status_code == 201
    r = await _cmd(c, pid2, {"type": "add_track", "kind": "video", "track_id": "scr"})
    assert r.status_code == 422 and "already exists" in r.text
    clip = {"type": "add_clip", "track_id": "scr", "asset_id": video["id"], "start_frame": 0}
    assert (await _cmd(c, pid, {**clip, "clip_id": "k1"})).status_code == 201
    t2 = (await _cmd(c, pid2, {"type": "add_track", "kind": "video"})).json()["command"]["track_id"]
    r = await _cmd(c, pid2, {**clip, "track_id": t2, "asset_id": video2["id"], "clip_id": "k1"})
    assert r.status_code == 422 and "already exists" in r.text


async def test_move_track_reorders_and_undo_restores(mclient):
    c = mclient
    pid = (await c.post("/projects", json={"name": "M"})).json()["id"]
    ids = []
    for n in ("立ち絵", "背景", "文字"):
        r = await _cmd(c, pid, {"type": "add_track", "kind": "video", "name": n})
        ids.append(r.json()["command"]["track_id"])

    async def order():
        tl = (await c.get(f"/projects/{pid}/timeline")).json()
        return [t["name"] for t in sorted(tl["tracks"], key=lambda t: t["index"])]

    assert await order() == ["立ち絵", "背景", "文字"]
    # 範囲外の位置は一番上に寄せる。他のトラックは詰める
    r = await _cmd(c, pid, {"type": "move_track", "track_id": ids[0], "index": 1000})
    assert r.status_code == 201, r.text
    assert await order() == ["背景", "文字", "立ち絵"]
    r = await _cmd(c, pid, {"type": "move_track", "track_id": ids[0], "index": 1})
    assert await order() == ["背景", "立ち絵", "文字"]
    await c.post(f"/projects/{pid}/undo")
    await c.post(f"/projects/{pid}/undo")
    assert await order() == ["立ち絵", "背景", "文字"]
    r = await _cmd(c, pid, {"type": "move_track", "track_id": "nope", "index": 0})
    assert r.status_code >= 400
