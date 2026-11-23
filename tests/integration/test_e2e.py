"""E2E（P9-01）: 対話型台本作成 → 音声 → 字幕同期 → タイムライン → AI アシスタントの編集（承認）
→ 書き出し → 品質検査 までを 1 本で通す。外部エンジンは偽物、FFmpeg は実物。

仕様 §17.6 の Phase 9 要件（全台本生成・片側入力の E2E、固定セリフ不変、会話整合性）も確認する。
"""

import json
from pathlib import Path

QWEN = "ollama-local::qwen3.5-9b-local:latest"
USER_LINES = ["今日は自動化の話をします。", "まずは字幕からです。"]


def _kind(body):
    props = (body.get("format") or {}).get("properties", {})
    for k in ("actions", "replies", "scenes", "issues"):
        if k in props:
            return k
    return None


def _responder(state: dict):
    def respond(body):
        k = _kind(body)
        if k == "replies":
            return {
                "replies": [
                    {"after": "L1", "turns": [{"text": "楽しみです。"}]},
                    {"after": "L2", "turns": [{"text": "字幕は自動ですか？"}]},
                ],
                "notes": [],
            }
        if k == "scenes":
            return {
                "scenes": [
                    {
                        "purpose": "導入",
                        "turns": [
                            {"speaker": "司会", "text": "動画編集を自動化しましょう。"},
                            {"speaker": "ゲスト", "text": "よろしくお願いします。"},
                        ],
                    }
                ],
                "notes": [],
            }
        if k == "issues":
            return {"issues": []}
        if k == "actions":
            st = json.loads(body["messages"][0]["content"].split("プロジェクトの状態:\n", 1)[1])
            video = next(a for a in st["assets"] if a["kind"] == "video")
            state["planned"] = True
            return {
                "reply": "背景に動画を敷く案です。",
                "actions": [
                    {
                        "tool": "edit_timeline",
                        "args": {
                            "description": "背景動画",
                            "command": {
                                "type": "batch",
                                "commands": [
                                    {"type": "add_track", "kind": "video", "track_id": "bg"},
                                    {
                                        "type": "add_clip",
                                        "track_id": "bg",
                                        "asset_id": video["asset_id"],
                                        "start_frame": 0,
                                        "volume": 0,
                                        "fade_in_frames": 6,
                                    },
                                ],
                            },
                        },
                    }
                ],
            }
        return None

    return respond


async def test_full_pipeline_end_to_end(mclient, fake, media_dir, media_settings):
    c = mclient
    state: dict = {}
    fake.chat_responder = _responder(state)
    await c.post("/providers/discover")
    pid = (await c.post("/projects", json={"name": "E2E", "width": 640, "height": 360})).json()[
        "id"
    ]
    chars = [
        {"name": "司会", "is_user": True, "voice": {"style_id": 10005}},
        {"name": "ゲスト", "voice": {"style_id": 10007, "speed_scale": 1.1}},
    ]
    # 1) 片側入力の台本 → 相手役を生成
    sc = (
        await c.post(
            "/scripts/studio",
            json={
                "project_id": pid,
                "mode": "USER_ONE_SIDE",
                "characters": chars,
                "settings": {"topic": "自動化", "target_duration_sec": 10},
            },
        )
    ).json()
    sid = sc["id"]
    await c.post(f"/scripts/{sid}/dialogue", json={"lines": [{"text": t} for t in USER_LINES]})
    d = (await c.post(f"/scripts/{sid}/generate", json={"model_key": QWEN})).json()
    assert [t["text"] for t in d["segments"] if t["speaker"] == "司会"] == USER_LINES
    assert [t["speaker"] for t in d["segments"]] == ["司会", "ゲスト", "司会", "ゲスト"]
    v = (await c.post(f"/scripts/{sid}/validate", json={"use_llm": True})).json()
    assert not [i for i in v["issues"] if i["level"] == "error"]

    # 2) 音声 → 字幕 → 同期 → タイムライン
    job = await c.app.state.jobs.wait((await c.post(f"/scripts/{sid}/voice")).json()["id"])
    assert job.status == "SUCCEEDED", job.error
    synth = fake.nemo.synth_queries
    assert any(abs(q.get("speedScale", 1) - 1.1) < 1e-6 for q in synth)  # キャラクターの話速
    await c.post("/subtitles/generate", json={"script_id": sid})
    await c.post("/subtitles/synchronize", json={"script_id": sid})
    assert (
        await c.post(f"/projects/{pid}/timeline/from_script", json={"script_id": sid})
    ).status_code == 201

    # 3) AI アシスタントで背景を敷く → 承認
    await c.post("/assets/import", json={"project_id": pid, "path": str(media_dir / "clip.mp4")})
    conv = (await c.post("/conversations", json={"project_id": pid})).json()
    m = (
        await c.post(f"/conversations/{conv['id']}/messages", json={"text": "背景に動画を敷いて"})
    ).json()
    assert state.get("planned") and m["actions"][0]["approval"]["kind"] == "command"
    pend = (await c.get(f"/projects/{pid}/approvals")).json()
    assert len(pend) == 1
    assert (await c.post(f"/approvals/command/{pend[0]['id']}", json={"approve": True})).json()[
        "status"
    ] == "APPLIED"

    # 4) 書き出し
    r = await c.post("/render", json={"project_id": pid, "script_id": sid, "quality": 35})
    job = await c.app.state.jobs.wait(r.json()["job_id"])
    assert job.status == "SUCCEEDED", job.error
    assert (Path(media_settings.data_dir) / job.result["output_path"]).is_file()

    # 5) 品質検査
    r = await c.post(f"/projects/{pid}/qa", json={})
    assert r.status_code == 202
    job = await c.app.state.jobs.wait(r.json()["id"])
    assert job.status == "SUCCEEDED", job.error
    rep = (await c.get(f"/qa/{job.result['report_id']}")).json()
    by = {s["name"]: s for s in rep["sections"]}
    assert set(by) == {"project", "audio", "subtitle", "video", "license"}
    errors = [
        (s["name"], x["code"])
        for s in rep["sections"]
        for x in s["checks"]
        if x["level"] == "error"
    ]
    assert errors == []
    assert by["video"]["metrics"]["width"] == 1920 and by["video"]["metrics"]["fps"] == 30
    assert by["video"]["metrics"]["loudness_lufs"] is not None
    assert by["audio"]["metrics"]["voiced"] == 4
    assert by["subtitle"]["metrics"]["cues"] >= 4
    assert any(x["code"] == "local_assets" for x in by["license"]["checks"])
    assert any(x["code"] == "credits" for x in by["license"]["checks"])  # VOICEVOX のクレジット
    # 背景は 3 秒、声は約 6.7 秒 → 後半が黒画面として検出される
    assert [x["code"] for x in by["video"]["checks"]] == ["black"]
    assert rep["status"] == "warnings"
    lst = (await c.get(f"/projects/{pid}/qa")).json()
    assert lst[0]["id"] == rep["id"] and lst[0]["status"] == rep["status"]
