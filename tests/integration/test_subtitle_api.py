from pathlib import Path

STYLE = 10005
SCRIPT = (
    "司会：こんにちは、字幕の自動同期を試します。\n"
    "これは長い文章なので字幕は複数行に分かれて表示されるはずですね。\n\n"
    "ゲスト：はい。"
)


async def _project(client, w=1920, h=1080, fps=(30, 1)):
    r = await client.post(
        "/projects",
        json={"name": "S", "width": w, "height": h, "fps_num": fps[0], "fps_den": fps[1]},
    )
    return r.json()["id"]


async def _script_with_audio(client, pid, text=SCRIPT):
    r = await client.post(
        "/scripts",
        json={
            "project_id": pid,
            "text": text,
            "default_style_id": STYLE,
            "speaker_styles": {"ゲスト": 10001},
        },
    )
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    job = await client.post(f"/scripts/{sid}/voice")
    done = await client.app.state.jobs.wait(job.json()["id"])
    assert done.status == "SUCCEEDED", done.error
    return sid, done.result


async def test_full_flow_generate_sync_export(client, settings):
    pid = await _project(client)
    sid, script = await _script_with_audio(client, pid)
    segs = script["segments"]
    assert [s["speaker"] for s in segs] == ["司会", "司会", "ゲスト"]
    assert [s["voice_style_id"] for s in segs] == [STYLE, STYLE, 10001]
    assert segs[2]["scene_index"] == 1 and all(s["audio_file_id"] for s in segs)

    # 同期前のエクスポートは不可
    gen = await client.post("/subtitles/generate", json={"script_id": sid})
    assert gen.status_code == 200
    assert (await client.post("/subtitles/export", json={"script_id": sid})).status_code == 409
    cues = (await client.post("/subtitles/synchronize", json={"script_id": sid})).json()
    assert len(cues) == 3 and all(len(ln) <= 22 for c in cues for ln in c["text"].split("\n"))
    assert cues[1]["text"].count("\n") == 1  # 長文は2行に折り返し
    # 半開区間で単調・非重複
    for a, b in zip(cues, cues[1:], strict=False):
        assert a["start_frame"] < a["end_frame"] <= b["start_frame"]
    # キューはセグメントの音声区間内に収まる
    seg_rows = (await client.get(f"/scripts/{sid}")).json()["segments"]
    by_id = {s["id"]: s for s in seg_rows}
    for c in cues:
        s = by_id[c["script_segment_id"]]
        assert s["start_frame"] <= c["start_frame"] and c["end_frame"] <= s["end_frame"] + 18
    # セグメント間に 0.3s(=9frame) の間
    assert seg_rows[1]["start_frame"] == seg_rows[0]["end_frame"] + 9

    ass = (
        await client.post(
            "/subtitles/export",
            json={"script_id": sid, "format": "ass", "speaker_colors": {"ゲスト": "#FFFF00"}},
        )
    ).json()
    assert ass["cue_count"] == len(cues) and "[Events]" in ass["content"]
    assert ",Default_S1,ゲスト," in ass["content"]
    path = Path(settings.data_dir) / ass["path"]
    assert path.is_file() and path.read_bytes()[:3] == b"\xef\xbb\xbf"
    # 話者の色（自動）: 司会=水色、ゲスト=オレンジ。名前と縁取りに使う
    assert "司会{\\r}\\N" in ass["content"] and "\\1c&HF7C34F&" in ass["content"]
    # プロジェクトの字幕スタイルを保存すると出力に使われる
    st = (await client.get(f"/subtitles/style/{pid}")).json()
    assert st["speaker_label"] and st["band"]
    st.update(speaker_label=False, band=False, font_size=50)
    assert (await client.put(f"/subtitles/style/{pid}", json=st)).status_code == 200
    ass2 = (await client.post("/subtitles/export", json={"script_id": sid})).json()["content"]
    assert ",Meiryo,50," in ass2 and "{\\r}" not in ass2 and "\\p1" not in ass2
    assert (await client.get("/subtitles/style/nope")).status_code == 404
    srt = (await client.post("/subtitles/export", json={"script_id": sid, "format": "srt"})).json()
    assert srt["content"].startswith("1\r\n00:00:") and srt["path"].endswith(".srt")


async def test_multiline_cue_split_follows_mora_timing(client):
    pid = await _project(client)
    # 偽エンジンは1文字=1モーラ。行のモーラ数に比例して時間が配分される
    sid, _ = await _script_with_audio(
        client, pid, "あいうえおかきくけこ、さしすせそたちつてとなにぬねのはひふへほ"
    )
    await client.post(
        "/subtitles/generate", json={"script_id": sid, "max_chars_per_line": 11, "max_lines": 1}
    )
    cues = (await client.post("/subtitles/synchronize", json={"script_id": sid})).json()
    assert [c["text"] for c in cues][:2] == ["あいうえおかきくけこ、", "さしすせそたちつてとな"]
    d = [c["end_frame"] - c["start_frame"] for c in cues]
    assert abs(d[1] / d[0] - 11 / 10) < 0.05  # 10モーラ : 11モーラ


async def test_portrait_and_ntsc_fps(client):
    pid = await _project(client, 1080, 1920, (30000, 1001))
    sid, _ = await _script_with_audio(client, pid)
    await client.post("/subtitles/generate", json={"script_id": sid})
    cues = (await client.post("/subtitles/synchronize", json={"script_id": sid})).json()
    assert all(len(ln) <= 14 for c in cues for ln in c["text"].split("\n"))
    ass = (await client.post("/subtitles/export", json={"script_id": sid})).json()["content"]
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass


async def test_state_errors(client):
    pid = await _project(client)
    r = await client.post("/scripts", json={"project_id": pid, "text": "スタイル未指定です。"})
    sid = r.json()["id"]
    job = await client.post(f"/scripts/{sid}/voice")
    done = await client.app.state.jobs.wait(job.json()["id"])
    assert done.status == "FAILED"
    await client.post("/subtitles/generate", json={"script_id": sid})
    r = await client.post("/subtitles/synchronize", json={"script_id": sid})
    assert r.status_code == 409 and "audio not generated" in r.json()["message"]
    assert (
        await client.post("/scripts", json={"project_id": "nope", "text": "あ"})
    ).status_code == 404
    assert (
        await client.post("/scripts", json={"project_id": pid, "text": "\n\n"})
    ).status_code == 409
    assert (await client.get("/scripts/nope")).status_code == 404
    assert (
        await client.post("/subtitles/export", json={"script_id": sid, "format": "vtt"})
    ).status_code == 422
    bad_style = {"script_id": sid, "style": {"primary_color": "red"}}
    assert (await client.post("/subtitles/export", json=bad_style)).status_code == 422


async def test_regenerating_voice_resets_sync(client):
    pid = await _project(client)
    sid, _ = await _script_with_audio(client, pid, "あいう。")
    await client.post("/subtitles/generate", json={"script_id": sid})
    await client.post("/subtitles/synchronize", json={"script_id": sid})
    job = await client.post(f"/scripts/{sid}/voice", params={"regenerate": True})
    done = await client.app.state.jobs.wait(job.json()["id"])
    assert done.result["segments"][0]["start_frame"] is None
