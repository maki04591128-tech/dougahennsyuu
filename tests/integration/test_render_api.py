"""レンダリング（実 FFmpeg）。台本→音声→字幕→タイムライン→MP4 の通し確認を含む。"""

import asyncio
import subprocess
import sys
from pathlib import Path

from video_automation.integrations.ffmpeg.runner import FFmpeg


def _ffmpeg_procs(pattern: str) -> list[str]:
    """コマンドラインに pattern を含む ffmpeg プロセス（Windows / POSIX 両対応）。"""
    if sys.platform == "win32":
        ps = (
            "Get-CimInstance Win32_Process -Filter \"Name='ffmpeg.exe'\" | "
            f"Where-Object {{ $_.CommandLine -like '*{pattern}*' }} | "
            "ForEach-Object { $_.ProcessId }"
        )
        cmd = ["powershell", "-NoProfile", "-Command", ps]
    else:
        cmd = ["pgrep", "-f", f"ffmpeg.*{pattern}"]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout
    return [x for x in out.split() if x.strip()]


async def _project_with_timeline(c, media_dir, w=640, h=360):
    pid = (await c.post("/projects", json={"name": "R", "width": w, "height": h})).json()["id"]
    imp = lambda n: c.post("/assets/import", json={"project_id": pid, "path": str(media_dir / n)})  # noqa: E731
    video, image, tone = [(await imp(n)).json() for n in ("clip.mp4", "tall.png", "tone.wav")]
    cmd = lambda body: c.post(f"/projects/{pid}/commands", json={"command": body})  # noqa: E731
    vt = (await cmd({"type": "add_track", "kind": "video", "track_id": "v1"})).json()
    ov = (await cmd({"type": "add_track", "kind": "video", "track_id": "v2"})).json()
    at = (await cmd({"type": "add_track", "kind": "audio", "track_id": "a1"})).json()
    assert vt and ov and at
    await cmd(
        {
            "type": "add_clip",
            "track_id": "v1",
            "asset_id": video["id"],
            "start_frame": 0,
            "transform": {"fit": "fill"},
        }
    )
    await cmd(
        {
            "type": "add_clip",
            "track_id": "v2",
            "asset_id": image["id"],
            "start_frame": 30,
            "duration_frames": 30,
            "transform": {"fit": "fit", "scale": 0.5, "x": 0.25},
        }
    )
    await cmd(
        {
            "type": "add_clip",
            "track_id": "a1",
            "asset_id": tone["id"],
            "start_frame": 15,
            "volume": 0.5,
            "clip_id": "tone",
        }
    )
    await cmd(
        {"type": "set_clip_props", "clip_id": "tone", "fade_in_frames": 10, "fade_out_frames": 10}
    )
    return pid


async def test_preview_frame_png(mclient, media_dir):
    pid = await _project_with_timeline(mclient, media_dir)
    r = await mclient.post(f"/projects/{pid}/preview", json={"frame": 45, "width": 320})
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"
    w = int.from_bytes(r.content[16:20], "big")
    h = int.from_bytes(r.content[20:24], "big")
    assert (w, h) == (320, 180)
    # 9:16 への再レイアウト確認
    r = await mclient.post(
        f"/projects/{pid}/preview", json={"frame": 45, "width": 270, "profile": "fhd_portrait"}
    )
    assert int.from_bytes(r.content[20:24], "big") == 480


async def test_render_fhd_and_portrait(mclient, media_dir, media_settings):
    c = mclient
    pid = await _project_with_timeline(c, media_dir)
    r = await c.post("/render", json={"project_id": pid, "profile": "fhd_landscape", "quality": 30})
    assert r.status_code == 202 and r.json()["status"] == "QUEUED"
    job = await c.app.state.jobs.wait(r.json()["job_id"])
    assert job.status == "SUCCEEDED", job.error
    # encoder=auto: NVENC が実際に使える環境では h264_nvenc、それ以外は libx264
    nvenc = await c.app.state.container.ffmpeg.nvenc_usable()
    assert job.result["encoder"] == ("h264_nvenc" if nvenc else "libx264")
    out = Path(media_settings.data_dir) / job.result["output_path"]
    info = await FFmpeg().probe(out)
    assert (info.width, info.height) == (1920, 1080) and info.has_audio
    assert abs(info.duration_sec - 3.0) < 0.1
    rj = (await c.get(f"/render/{r.json()['id']}")).json()
    assert rj["status"] == "SUCCEEDED" and rj["output_path"] == job.result["output_path"]

    # 16:9 プロジェクトを 9:16 で出力（ぼかし背景で再レイアウト）
    await c.post(
        f"/projects/{pid}/commands",
        json={
            "command": {
                "type": "set_clip_props",
                "clip_id": (await c.get(f"/projects/{pid}/timeline")).json()["clips"][0]["id"],
                "transform": {"fit": "blur"},
            }
        },
    )
    r = await c.post(
        "/render",
        json={
            "project_id": pid,
            "profile": "fhd_portrait",
            "quality": 35,
            "loudnorm": False,
            "end_frame": 30,
        },
    )
    job = await c.app.state.jobs.wait(r.json()["job_id"])
    assert job.status == "SUCCEEDED", job.error
    info = await FFmpeg().probe(Path(media_settings.data_dir) / job.result["output_path"])
    assert (info.width, info.height) == (1080, 1920) and abs(info.duration_sec - 1.0) < 0.1


async def test_render_requires_approval_for_llm_and_can_be_cancelled(mclient, media_dir):
    c = mclient
    pid = await _project_with_timeline(c, media_dir)
    r = await c.post("/render", json={"project_id": pid, "origin": "llm"})
    assert r.json()["status"] == "PENDING_APPROVAL" and r.json()["job_id"] is None
    rej = await c.post(f"/render/{r.json()['id']}/reject")
    assert rej.json()["status"] == "REJECTED"
    assert (await c.post(f"/render/{r.json()['id']}/approve")).status_code == 422

    r = await c.post("/render", json={"project_id": pid, "profile": "4k_landscape", "quality": 18})
    await asyncio.sleep(0.5)
    cancel = await c.post(f"/jobs/{r.json()['job_id']}/cancel")
    assert cancel.json()["status"] == "CANCELLED"
    for _ in range(20):
        st = (await c.get(f"/render/{r.json()['id']}")).json()["status"]
        if st == "CANCELLED":
            break
        await asyncio.sleep(0.1)
    assert st == "CANCELLED"
    # ffmpeg プロセスが残っていない
    assert _ffmpeg_procs("3840x2160") == []


async def test_empty_timeline_and_errors(mclient):
    pid = (await mclient.post("/projects", json={"name": "E"})).json()["id"]
    r = await mclient.post("/render", json={"project_id": pid})
    job = await mclient.app.state.jobs.wait(r.json()["job_id"])
    assert job.status == "FAILED"
    assert (await mclient.get(f"/render/{r.json()['id']}")).json()["status"] == "FAILED"
    assert (await mclient.post("/render", json={"project_id": "nope"})).status_code == 404
    bad = {"project_id": pid, "start_frame": 10, "end_frame": 5}
    assert (await mclient.post("/render", json=bad)).status_code == 422
    profiles = (await mclient.get("/render/profiles")).json()
    assert {p["name"] for p in profiles} == {
        "fhd_landscape",
        "fhd_portrait",
        "4k_landscape",
        "4k_portrait",
    }
    eng = (await mclient.get("/render/engines")).json()
    assert eng["ffmpeg"]["version"] and eng["blender"] is None


async def test_script_to_video_end_to_end(mclient, media_dir, media_settings):
    """台本 → 音声(偽Nemo) → 字幕同期 → タイムライン配置 → 字幕焼き込みMP4 + SRT/ASS。"""
    c = mclient
    pid = (await c.post("/projects", json={"name": "E2E", "width": 640, "height": 360})).json()[
        "id"
    ]
    sc = (
        await c.post(
            "/scripts",
            json={
                "project_id": pid,
                "text": "司会：こんにちは、テストです。\nゲスト：はい。",
                "default_style_id": 10005,
            },
        )
    ).json()
    job = await c.app.state.jobs.wait((await c.post(f"/scripts/{sc['id']}/voice")).json()["id"])
    assert job.status == "SUCCEEDED"
    await c.post("/subtitles/generate", json={"script_id": sc["id"]})
    await c.post("/subtitles/synchronize", json={"script_id": sc["id"]})
    r = await c.post(f"/projects/{pid}/timeline/from_script", json={"script_id": sc["id"]})
    assert r.status_code == 201
    tl = (await c.get(f"/projects/{pid}/timeline")).json()
    segs = (await c.get(f"/scripts/{sc['id']}")).json()["segments"]
    assert [x["start_frame"] for x in tl["clips"]] == [s["start_frame"] for s in segs]
    assert {a["source"] for a in tl["assets"]} == {"voice"}
    # 背景に映像を敷く
    v = (
        await c.post(
            "/assets/import", json={"project_id": pid, "path": str(media_dir / "clip.mp4")}
        )
    ).json()
    await c.post(
        f"/projects/{pid}/commands",
        json={
            "command": {
                "type": "batch",
                "commands": [
                    {"type": "add_track", "kind": "video", "track_id": "bg"},
                    {
                        "type": "add_clip",
                        "track_id": "bg",
                        "asset_id": v["id"],
                        "start_frame": 0,
                        "volume": 0,
                    },
                ],
            }
        },
    )
    # 字幕付きプレビュー（1フレーム）
    first = (await c.get(f"/subtitles/{sc['id']}")).json()[0]
    mid = (first["start_frame"] + first["end_frame"]) // 2
    with_sub = await c.post(
        f"/projects/{pid}/preview", json={"frame": mid, "width": 320, "script_id": sc["id"]}
    )
    without = await c.post(f"/projects/{pid}/preview", json={"frame": mid, "width": 320})
    assert with_sub.content != without.content
    r = await c.post("/render", json={"project_id": pid, "script_id": sc["id"], "quality": 35})
    job = await c.app.state.jobs.wait(r.json()["job_id"])
    assert job.status == "SUCCEEDED", job.error
    assert sorted(Path(p).suffix for p in job.result["subtitle_paths"]) == [".ass", ".srt"]
    info = await FFmpeg().probe(Path(media_settings.data_dir) / job.result["output_path"])
    total = (await c.get(f"/projects/{pid}/timeline")).json()["duration_frames"]
    assert info.has_audio and abs(info.duration_sec - total / 30) < 0.15
    # クレジット: 既定は概要欄用の文を書き出す（タイムラインで使っている声だけ）
    cpath = Path(media_settings.data_dir) / job.result["credits_path"]
    ctext = cpath.read_text(encoding="utf-8")
    assert ctext.startswith("音声: ") and "VOICEVOX" in ctext
    used = (await c.get(f"/projects/{pid}/credits", params={"used_only": True})).json()
    assert used["text"].strip() == ctext.strip()
    # 終わりにクレジットの画面を足す
    r = await c.post(
        "/render",
        json={
            "project_id": pid,
            "script_id": sc["id"],
            "quality": 35,
            "credits": "end_card",
            "credits_sec": 3,
        },
    )
    job = await c.app.state.jobs.wait(r.json()["job_id"])
    assert job.status == "SUCCEEDED", job.error
    out = Path(media_settings.data_dir) / job.result["output_path"]
    info2 = await FFmpeg().probe(out)
    assert info2.has_audio and abs(info2.duration_sec - (total / 30 + 3)) < 0.2
    assert not list(out.parent.glob("*.withcredits.mp4")) and not list(
        out.parent.glob("*.credits.ass")
    )
    files = (await c.get(f"/render/{r.json()['id']}/files")).json()
    assert files["credits"] and files["credits"].endswith(".credits.txt")
    # 出さない
    r = await c.post(
        "/render",
        json={"project_id": pid, "script_id": sc["id"], "quality": 35, "credits": "none"},
    )
    job = await c.app.state.jobs.wait(r.json()["job_id"])
    assert job.result["credits_path"] is None
    # 不足時のエラー
    sc2 = (await c.post("/scripts", json={"project_id": pid, "text": "未生成。"})).json()
    r = await c.post(f"/projects/{pid}/timeline/from_script", json={"script_id": sc2["id"]})
    assert r.status_code == 422


def test_credit_card_ass_drops_urls_and_wraps():
    from video_automation.services.rendering.render_service import credit_card_ass

    text = (
        "音声: VOICEVOX:ずんだもん\n画像:\n"
        "- “A-(b)-c” by X (https://e.org/wiki/File:A-(b)-c.png), CC BY 4.0 (https://cc.org/by/4.0/)"
        "\n" + "- " + "長い説明" * 40
    )
    ass = credit_card_ass(text, 1080, 1920, 5, "#EEEEEE", "#3BB8DB", "Meiryo", "Meiryo")
    body = ass.splitlines()[-1].split("}", 1)[1]
    assert "http" not in body and "“A-(b)-c” by X, CC BY 4.0" in body
    assert body.count(r"\N") >= 5  # 長い行は幅で折り返す
    assert "0:00:05.00" in ass and "PlayResX: 1080" in ass
