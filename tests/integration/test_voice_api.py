import asyncio
from pathlib import Path

STYLE = 10005  # 女声1（実機 Nemo 0.26.0 の style id）


async def _generate(client, items, project_id=None):
    r = await client.post("/voice/generate", json={"project_id": project_id, "items": items})
    assert r.status_code == 202, r.text
    job = await client.app.state.jobs.wait(r.json()["id"])
    return job


async def test_engines_detect_nemo_capabilities_separately_from_voicevox(client):
    engines = {e["engine_id"]: e for e in (await client.get("/voice/engines")).json()}
    nemo = engines["voicevox-nemo"]
    assert nemo["available"] and nemo["name"] == "VOICEVOX Nemo Engine"
    assert nemo["features"]["adjust_mora_pitch"] and not nemo["features"]["sing"]
    assert nemo["features"]["user_dict"] and nemo["devices"]["cpu"]
    assert "クレジット" in nemo["terms_note"]
    assert not engines["voicevox"]["available"]  # 通常版は未起動


async def test_speakers_from_engine(client):
    sp = (await client.get("/voice/speakers")).json()
    assert len(sp) == 9 and sp[0]["engine_id"] == "voicevox-nemo"
    assert any(s["id"] == STYLE and s["type"] == "streaming_talk" for x in sp for s in x["styles"])


async def test_generate_saves_wav_metadata_and_timings(client, settings):
    pid = (await client.post("/projects", json={"name": "P"})).json()["id"]
    job = await _generate(
        client,
        [
            {
                "text": "こんにちは、せかい",
                "style_id": STYLE,
                "script_segment_id": "seg-1",
                "prosody": {"speed_scale": 1.2},
            },
            {"text": "にばんめ", "style_id": STYLE},
        ],
        pid,
    )
    assert job.status == "SUCCEEDED", job.error
    a = job.result[0]
    assert a["project_id"] == pid and a["script_segment_id"] == "seg-1"
    assert a["sample_rate"] == 24000 and a["credit"] == "VOICEVOX Nemo"
    assert [t["text"] for t in a["timings"]] == ["こんにちは", "せかい"]
    assert a["timings"][-1]["end_sec"] < a["duration_sec"]
    path = Path(settings.data_dir) / a["path"]
    assert path.is_file() and path.read_bytes()[:4] == b"RIFF"
    assert a["path"].startswith(f"projects/{pid}/audio/")
    r = await client.get(f"/voice/audio/{a['id']}/file")
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
    listed = (await client.get("/voice/audio", params={"project_id": pid})).json()
    assert len(listed) == 2


async def test_generate_validations(client):
    assert (await _generate(client, [{"text": "あ", "style_id": 1}])).status == "FAILED"
    r = await client.post("/voice/generate", json={"items": []})
    assert r.status_code == 422
    r = await client.post(
        "/voice/generate", json={"items": [{"text": "あ" * 1001, "style_id": STYLE}]}
    )
    assert r.status_code == 422
    r = await client.post("/voice/generate", json={"items": [{"text": "   ", "style_id": STYLE}]})
    assert r.status_code == 422
    bad_speed = {"text": "あ", "style_id": STYLE, "prosody": {"speed_scale": 10}}
    assert (await client.post("/voice/preview", json=bad_speed)).status_code == 422
    job = await _generate(client, [{"text": "あ", "style_id": STYLE}], project_id="missing")
    assert job.status == "FAILED"


async def test_preview_applies_prosody_without_saving(client, fake):
    r = await client.post(
        "/voice/preview",
        json={
            "text": "てすと",
            "style_id": STYLE,
            "prosody": {"speed_scale": 1.5, "intonation_scale": 0.5, "pitch_scale": 0.05},
        },
    )
    assert r.status_code == 200 and r.content[:4] == b"RIFF"
    q = fake.nemo.synth_queries[-1]
    assert (q["speedScale"], q["intonationScale"], q["pitchScale"]) == (1.5, 0.5, 0.05)
    assert (await client.get("/voice/audio")).json() == []


async def test_unsupported_feature_is_rejected(client, fake):
    fake.nemo.features["adjust_intonation_scale"]["value"] = False
    await client.get("/voice/engines")  # 再検査
    r = await client.post(
        "/voice/preview",
        json={"text": "あ", "style_id": STYLE, "prosody": {"intonation_scale": 1.2}},
    )
    assert r.status_code == 400 and r.json()["code"] == "voice_feature_unsupported"
    ok = await client.post(
        "/voice/preview", json={"text": "あ", "style_id": STYLE, "prosody": {"speed_scale": 1.2}}
    )
    assert ok.status_code == 200


async def test_accent_edit_and_regenerate_keeps_reading(client, fake):
    aps = (
        await client.post("/voice/accent_phrases", json={"text": "あいう", "style_id": STYLE})
    ).json()
    aps[0]["accent"] = 2
    re = await client.post(
        "/voice/accent_phrases/recompute", json={"style_id": STYLE, "accent_phrases": aps}
    )
    assert re.status_code == 200
    assert [m["pitch"] for m in re.json()[0]["moras"]] == [6.0, 6.0, 5.0]
    aps[0]["accent"] = 9
    bad = await client.post(
        "/voice/accent_phrases/recompute", json={"style_id": STYLE, "accent_phrases": aps}
    )
    assert bad.status_code == 422

    job = await _generate(
        client, [{"text": "あいう", "style_id": STYLE, "accent_phrases": re.json()}]
    )
    a = job.result[0]
    r = await client.post(
        "/voice/prosody", json={"audio_id": a["id"], "prosody": {"volume_scale": 0.8}}
    )
    assert r.status_code == 201
    b = r.json()
    assert b["id"] != a["id"] and b["prosody"] == {"volume_scale": 0.8}
    q = fake.nemo.synth_queries[-1]
    assert q["accent_phrases"][0]["accent"] == 2 and q["volumeScale"] == 0.8  # 読み修正を維持
    assert len((await client.get("/voice/audio")).json()) == 2  # 元音声も履歴として残る
    assert (
        await client.post("/voice/prosody", json={"audio_id": "nope", "prosody": {}})
    ).status_code == 404


async def test_kana_parse_error_is_422(client):
    r = await client.post("/voice/accent_phrases", json={"text": "@@", "style_id": STYLE})
    assert r.status_code == 422 and r.json()["code"] == "invalid_voice_request"


async def test_dictionary_crud_and_restore(client, fake):
    r = await client.post(
        "/voice/dictionary", json={"surface": "Nemo", "pronunciation": "ネモ", "accent_type": 1}
    )
    assert r.status_code == 201
    w = r.json()
    assert w["engine_word_uuid"] in fake.nemo.user_dict
    bad = await client.post(
        "/voice/dictionary", json={"surface": "X", "pronunciation": "エックス", "accent_type": 9}
    )
    assert bad.status_code == 422
    up = await client.put(
        f"/voice/dictionary/{w['id']}",
        json={"surface": "Nemo", "pronunciation": "ニモ", "accent_type": 0, "priority": 8},
    )
    assert up.json()["pronunciation"] == "ニモ"
    # エンジン再インストールで辞書が消えても DB から復元できる
    fake.nemo.user_dict.clear()
    assert (await client.post("/voice/dictionary/restore")).json() == {"restored": 1}
    assert len(fake.nemo.user_dict) == 1
    assert (await client.delete(f"/voice/dictionary/{w['id']}")).status_code == 204
    assert fake.nemo.user_dict == {} and (await client.get("/voice/dictionary")).json() == []
    assert (await client.delete(f"/voice/dictionary/{w['id']}")).status_code == 404


async def test_engine_down_returns_503(client, fake):
    fake.nemo.up = False
    await client.get("/voice/engines")
    r = await client.get("/voice/speakers")
    assert r.status_code == 503 and r.json()["code"] == "voice_engine_unavailable"


async def test_audio_file_path_traversal_blocked(client, settings):
    from video_automation.database.models import AudioFile

    async with client.app.state.db.session_factory() as s:
        s.add(
            AudioFile(
                id="evil",
                engine_id="voicevox-nemo",
                style_id=STYLE,
                text="x",
                path="../../etc/passwd",
                sha256="0",
                sample_rate=1,
                duration_sec=0,
                query={},
                prosody={},
                timings=[],
            )
        )
        await s.commit()
    assert (await client.get("/voice/audio/evil/file")).status_code == 404


async def test_concurrent_generation(client):
    jobs = await asyncio.gather(
        *(_generate(client, [{"text": f"へいれつ{i}", "style_id": STYLE}]) for i in range(5))
    )
    assert all(j.status == "SUCCEEDED" for j in jobs)
    assert len((await client.get("/voice/audio")).json()) == 5


async def test_voice_character_style_fills_speaking_style(client):
    # 組み込み（出典のあるキャラクター）
    styles = {s["speaker"]: s for s in (await client.get("/voice/characters")).json()}
    assert styles["ずんだもん"]["builtin"] and "〜のだ" in styles["ずんだもん"]["endings"]
    # 公式の「呼び方」から: 一人称・二人称
    assert styles["四国めたん"]["first_person"] == "わたくし"
    assert styles["春日部つむぎ"]["first_person"] == "あーし"
    assert len([x for x in styles.values() if x["builtin"]]) >= 15
    # Nemo の声には口調が無い → ユーザーが登録する
    by = (await client.get(f"/voice/characters/by_style/{STYLE}")).json()
    assert by["speaker"] == "女声1" and by["style"] is None
    r = await client.put(
        "/voice/characters/女声1",
        json={
            "speaker": "x",
            "first_person": "わたし",
            "endings": ["〜です"],
            "tone": "落ち着いた",
        },
    )
    assert r.status_code == 200 and r.json()["speaker"] == "女声1"
    by = (await client.get(f"/voice/characters/by_style/{STYLE}")).json()
    assert "一人称は「わたし」" in by["speaking_style"]
    # 声を選んで口調が空のキャラクターは、その声の口調で埋まる。書いた口調は変えない
    pid = (await client.post("/projects", json={"name": "C"})).json()["id"]
    sc = (
        await client.post(
            "/scripts/studio",
            json={
                "project_id": pid,
                "mode": "MANUAL",
                "title": "t",
                "characters": [
                    {"name": "A", "voice": {"style_id": STYLE}},
                    {"name": "B", "voice": {"style_id": STYLE}, "speaking_style": "関西弁"},
                ],
            },
        )
    ).json()
    chars = {c["name"]: c for c in sc["characters"]}
    assert "わたし" in chars["A"]["speaking_style"] and chars["B"]["speaking_style"] == "関西弁"
    assert (await client.delete("/voice/characters/女声1")).status_code == 204
    assert (await client.get(f"/voice/characters/by_style/{STYLE}")).json()["style"] is None
