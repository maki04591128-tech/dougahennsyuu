"""対話型編集（Phase 8 Part A）: 会話 → 計画 → 検証・修正 → 承認待ち → 承認 → 履歴・取り消し。"""

import json

QWEN = "ollama-local::qwen3.5-9b-local:latest"
GEMMA = "ollama-local::gemma4-e4b-local:latest"


def kind_of(body):
    props = (body.get("format") or {}).get("properties", {})
    if "actions" in props:
        return "plan"
    if "action" in props:
        return "repair"
    if "scenes" in props:
        return "scenes"
    return None


def system_state(body):
    sys = body["messages"][0]["content"]
    return json.loads(sys.split("プロジェクトの状態:\n", 1)[1])


async def _setup(c, media_dir):
    await c.post("/providers/discover")
    pid = (await c.post("/projects", json={"name": "A", "fps_num": 30, "fps_den": 1})).json()["id"]
    video = (
        await c.post(
            "/assets/import", json={"project_id": pid, "path": str(media_dir / "clip.mp4")}
        )
    ).json()
    r = await c.post(
        f"/projects/{pid}/commands",
        json={"command": {"type": "add_track", "kind": "video", "name": "映像", "track_id": "v1"}},
    )
    assert r.status_code == 201
    conv = (await c.post("/conversations", json={"project_id": pid})).json()
    return pid, video, conv["id"]


async def test_plan_is_validated_repaired_and_waits_for_approval(mclient, fake, media_dir):
    c = mclient
    pid, video, cid = await _setup(c, media_dir)
    seen = {}

    def responder(body):
        k = kind_of(body)
        if k == "plan":
            st = system_state(body)
            seen["state"] = st
            return {
                "reply": "冒頭に動画を置き、0.5秒のフェードインを付けます。",
                "actions": [
                    # 存在しない asset ID（検証で弾かれ、修正させる）
                    {
                        "tool": "edit_timeline",
                        "args": {
                            "description": "動画を配置",
                            "command": {
                                "type": "add_clip",
                                "track_id": "v1",
                                "asset_id": "nope",
                                "start_frame": 0,
                                "clip_id": "k1",
                            },
                        },
                    },
                    {"tool": "search_assets", "args": {"query": "city", "media_type": "image"}},
                    {"tool": "render_project", "args": {"profile": "8k"}},
                ],
                "questions": ["BGM も入れますか？"],
            }
        if k == "repair":
            last = body["messages"][-1]["content"]
            if "nope" in last or "asset" in last:
                return {
                    "action": {
                        "tool": "edit_timeline",
                        "args": {
                            "description": "動画を配置",
                            "command": {
                                "type": "batch",
                                "commands": [
                                    {
                                        "type": "add_clip",
                                        "track_id": "v1",
                                        "asset_id": video["id"],
                                        "start_frame": 0,
                                        "clip_id": "k1",
                                    },
                                    {
                                        "type": "set_clip_props",
                                        "clip_id": "k1",
                                        "fade_in_frames": 15,
                                    },
                                ],
                            },
                        },
                    }
                }
            return {"action": {"tool": "render_project", "args": {}}, "give_up": True}
        return None

    fake.chat_responder = responder
    r = await c.post(
        f"/conversations/{cid}/messages",
        json={"text": "冒頭に動画を置いてフェードインして", "model_key": QWEN},
    )
    assert r.status_code == 200, r.text
    m = r.json()
    assert m["role"] == "assistant" and m["model_key"] == QWEN
    assert m["questions"] == ["BGM も入れますか？"]
    assert seen["state"]["tracks"][0]["track_id"] == "v1"
    assert seen["state"]["assets"][0]["asset_id"] == video["id"]
    edit, search, bad = m["actions"]
    assert edit["ok"] and edit["approval"]["kind"] == "command"
    assert any("クリップ追加" in d for d in edit["approval"]["details"])
    assert any("フェードイン 15f" in d for d in edit["approval"]["details"])
    assert search["ok"] and "results" in search["data"]
    assert not bad["ok"] and "profile" in bad["error"]

    # まだ反映されていない
    tl = (await c.get(f"/projects/{pid}/timeline")).json()
    assert tl["clips"] == []
    pend = (await c.get(f"/projects/{pid}/approvals")).json()
    assert [p["kind"] for p in pend] == ["command"]
    r = await c.post(f"/approvals/command/{pend[0]['id']}", json={"approve": True})
    assert r.json()["status"] == "APPLIED"
    tl = (await c.get(f"/projects/{pid}/timeline")).json()
    assert tl["clips"][0]["id"] == "k1" and tl["clips"][0]["fade_in_frames"] == 15

    # 履歴（差分つき）と取り消し
    h = (await c.get(f"/projects/{pid}/history")).json()
    assert h[0]["origin"] == "llm" and any("クリップ追加" in x for x in h[0]["changes"])
    first = h[-1]["command_id"]  # add_track
    r = await c.post(f"/projects/{pid}/history/revert", json={"to_command_id": first})
    assert len(r.json()["undone"]) == 1
    assert (await c.get(f"/projects/{pid}/timeline")).json()["clips"] == []

    conv = (await c.get(f"/conversations/{cid}")).json()
    assert conv["title"].startswith("冒頭に動画") and len(conv["messages"]) == 2


async def test_rerun_with_other_model_withdraws_pending_and_script_proposals(
    mclient, fake, media_dir
):
    c = mclient
    pid, video, cid = await _setup(c, media_dir)
    sc = (
        await c.post(
            "/scripts/studio",
            json={
                "project_id": pid,
                "mode": "FULL_AI",
                "characters": [{"name": "A"}, {"name": "B"}],
            },
        )
    ).json()

    def responder(body):
        k = kind_of(body)
        if k == "plan":
            return {
                "reply": "台本を作ります",
                "actions": [{"tool": "generate_script", "args": {"script_id": sc["id"]}}],
            }
        if k == "scenes":
            return {
                "scenes": [
                    {
                        "purpose": "導入",
                        "turns": [{"speaker": "A", "text": f"こんにちは（{body['model']}）"}],
                    }
                ],
                "notes": [],
            }
        return None

    fake.chat_responder = responder
    m1 = (
        await c.post(
            f"/conversations/{cid}/messages", json={"text": "台本を作って", "model_key": QWEN}
        )
    ).json()
    p1 = m1["actions"][0]["approval"]
    assert p1["kind"] == "script"
    assert (await c.get(f"/scripts/{sc['id']}/detail")).json()["segments"] == []

    m2 = (
        await c.post(f"/conversations/messages/{m1['id']}/rerun", json={"model_key": GEMMA})
    ).json()
    assert m2["model_key"] == GEMMA and m2["reply_to"] == m1["reply_to"]
    conv = (await c.get(f"/conversations/{cid}")).json()
    assert [x["superseded"] for x in conv["messages"]] == [False, True, False]
    pend = (await c.get(f"/projects/{pid}/approvals")).json()
    assert [p["id"] for p in pend] == [m2["actions"][0]["approval"]["id"]]  # 古い案は取り下げ
    assert any("追加 A: こんにちは" in d for d in pend[0]["details"])

    # 承認すると台本に反映。承認待ちの間に台本が変わった案は適用しない
    r = await c.post(f"/approvals/script/{pend[0]['id']}", json={"approve": True})
    assert r.json()["status"] == "APPLIED"
    d = (await c.get(f"/scripts/{sc['id']}/detail")).json()
    assert (
        d["segments"][0]["text"].startswith("こんにちは") and "gemma4" in d["segments"][0]["text"]
    )
    m3 = (await c.post(f"/conversations/{cid}/messages", json={"text": "もう一度"})).json()
    await c.put(f"/scripts/{sc['id']}", json={"title": "手で変更"})
    r = await c.post(
        f"/approvals/script/{m3['actions'][0]['approval']['id']}", json={"approve": True}
    )
    assert r.status_code == 409 and r.json()["code"] == "proposal_state"
    r = await c.post(f"/approvals/script/{p1['id']}", json={"approve": True})
    assert r.status_code == 409  # 取り下げ済み


async def test_approval_policy_and_render_requests(mclient, fake, media_dir, media_settings):
    c = mclient
    pid, video, cid = await _setup(c, media_dir)
    pol = {p["key"]: p for p in (await c.get("/approvals/policy")).json()}
    assert (
        pol["timeline_change"]["mode"] == "required" and not pol["timeline_change"]["configurable"]
    )
    assert pol["asset_download"]["mode"] == "required" and pol["asset_download"]["configurable"]
    r = await c.put(
        "/approvals/policy", json={"asset_download": "auto", "unknown_license": "forbidden"}
    )
    pol = {p["key"]: p for p in r.json()}
    assert pol["asset_download"]["mode"] == "auto" and pol["unknown_license"]["mode"] == "forbidden"
    assert media_settings.llm_asset_download_requires_approval is False
    r = await c.put("/approvals/policy", json={"timeline_change": "auto"})
    assert (
        r.status_code == 422
        or {p["key"]: p for p in r.json()}["timeline_change"]["mode"] == "required"
    )
    await c.put("/approvals/policy", json={"asset_download": "required", "unknown_license": "ack"})

    fake.chat_responder = lambda b: (
        {
            "reply": "書き出します",
            "actions": [{"tool": "render_project", "args": {"profile": "fhd_landscape"}}],
        }
        if kind_of(b) == "plan"
        else None
    )
    m = (await c.post(f"/conversations/{cid}/messages", json={"text": "書き出して"})).json()
    item = m["actions"][0]["approval"]
    assert item["kind"] == "render"
    pend = (await c.get(f"/projects/{pid}/approvals")).json()
    assert [p["kind"] for p in pend] == ["render"]
    r = await c.post(f"/approvals/render/{item['id']}", json={"approve": False})
    assert r.json()["status"] == "REJECTED"
    assert (await c.get(f"/projects/{pid}/approvals")).json() == []


async def test_llm_unavailable_and_not_found(mclient, fake, media_dir):
    c = mclient
    pid, video, cid = await _setup(c, media_dir)
    r = await c.post("/conversations/nope/messages", json={"text": "x"})
    assert r.status_code == 404
    fake.chat_responder = lambda b: "JSONではない"
    r = await c.post(f"/conversations/{cid}/messages", json={"text": "x"})
    assert r.status_code == 502
    st = (await c.get(f"/projects/{pid}/state")).json()
    assert st["project"]["fps"] == 30 and st["tracks"][0]["name"] == "映像"
