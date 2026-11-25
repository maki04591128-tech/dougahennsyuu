"""品質検査（P9-02〜05）が問題を検出すること: 音割れ・字幕の重なり/未同期/はみ出し・
解像度の不一致・ライセンス未確認/商用不可/改変禁止。"""

import shutil
import subprocess

from sqlalchemy import select

from video_automation.database.models import (
    AssetLicenseRow,
    AudioFile,
    RenderJob,
    Subtitle,
)


def _ff(*args):
    subprocess.run([shutil.which("ffmpeg"), "-v", "error", "-y", *args], check=True)


async def _run(c, pid, **body):
    r = await c.post(f"/projects/{pid}/qa", json=body)
    assert r.status_code == 202, r.text
    job = await c.app.state.jobs.wait(r.json()["id"])
    assert job.status == "SUCCEEDED", job.error
    rep = (await c.get(f"/qa/{job.result['report_id']}")).json()
    return rep, {s["name"]: s for s in rep["sections"]}


def _codes(sec, level=None):
    return {x["code"] for x in sec["checks"] if level is None or x["level"] == level}


async def test_qa_detects_problems(mclient, media_dir, media_settings, tmp_path):
    c = mclient
    pid = (await c.post("/projects", json={"name": "QA", "width": 640, "height": 360})).json()["id"]
    # 台本なし・書き出しなしでも検査できる（該当項目は skipped）
    rep, by = await _run(c, pid)
    assert by["audio"]["status"] == "skipped" and by["video"]["status"] == "skipped"
    assert "timeline" in _codes(by["project"], "error")  # 空のタイムライン

    sc = (
        await c.post(
            "/scripts",
            json={
                "project_id": pid,
                "text": "A：一つ目のセリフです。\nB：二つ目です。\nA：三つ目です。",
                "default_style_id": 10005,
            },
        )
    ).json()
    sid = sc["id"]
    job = await c.app.state.jobs.wait((await c.post(f"/scripts/{sid}/voice")).json()["id"])
    assert job.status == "SUCCEEDED"
    await c.post("/subtitles/generate", json={"script_id": sid})
    await c.post("/subtitles/synchronize", json={"script_id": sid})
    await c.post(f"/projects/{pid}/timeline/from_script", json={"script_id": sid})
    video = (
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
                        "asset_id": video["id"],
                        "start_frame": 0,
                        "source_in_frame": 15,
                        "duration_frames": 45,
                        "volume": 0,
                    },
                ],
            }
        },
    )
    r = await c.post("/render", json={"project_id": pid, "script_id": sid, "quality": 35})
    assert (await c.app.state.jobs.wait(r.json()["job_id"])).status == "SUCCEEDED"

    # 正常な状態ではエラーなし（ライセンス情報の無い取り込み素材は info）
    rep, by = await _run(c, pid)
    assert [x for s in rep["sections"] for x in s["checks"] if x["level"] == "error"] == []

    db = c.app.state.container.db
    data = media_settings.data_dir
    async with db.session_factory() as s:
        # 音割れする音声に差し替え
        af = (await s.execute(select(AudioFile).order_by(AudioFile.created_at))).scalars().first()
        loud = tmp_path / "loud.wav"
        _ff("-f", "lavfi", "-i", "aevalsrc='0.999*sgn(sin(2*PI*300*t))':s=24000:d=1.5", str(loud))
        shutil.copy(loud, data / af.path)
        # 字幕: 1 件目を伸ばして重ねる・2 件目の時刻を消す
        cues = list(
            (
                await s.execute(
                    select(Subtitle).where(Subtitle.script_id == sid).order_by(Subtitle.index)
                )
            ).scalars()
        )
        cues[0].end_frame = (cues[1].start_frame or 0) + 5
        cues[-1].start_frame = cues[-1].end_frame = None
        # ライセンス: 未確認・商用不可・改変禁止（切り出して使っている）
        s.add(
            AssetLicenseRow(
                asset_id=video["id"],
                source="openverse",
                source_id="x1",
                title="clip",
                license_code="cc-by-nc-nd",
                license={
                    "code": "cc-by-nc-nd",
                    "name": "CC BY-NC-ND",
                    "attribution_required": True,
                    "commercial_use": False,
                    "modification": False,
                    "share_alike": False,
                },
                verdict="needs_ack",
                acknowledged=False,
                attribution="clip by someone (CC BY-NC-ND)",
                snapshot={},
            )
        )
        # 書き出しの指定と実際の解像度が合わない
        rj = (
            (await s.execute(select(RenderJob).where(RenderJob.project_id == pid)))
            .scalars()
            .first()
        )
        rj.profile = "fhd_portrait"
        await s.commit()

    rep, by = await _run(c, pid)
    assert rep["status"] == "failed"
    assert "clipping" in _codes(by["audio"], "error")
    assert {"overlap", "unsynced", "out_of_voice"} <= _codes(by["subtitle"], "error")
    assert "resolution" in _codes(by["video"], "error")
    assert "not_acknowledged" in _codes(by["license"], "error")
    assert {"non_commercial", "no_derivatives"} <= _codes(by["license"], "warning")
    assert "clip by someone (CC BY-NC-ND)" in by["license"]["metrics"]["credits"]
    # 一部の項目だけ・特定の書き出しを指定
    rep2, by2 = await _run(c, pid, sections=["license"], render_id=rj.id)
    assert set(by2) == {"license"} and rep2["render_id"] == rj.id
    r = await c.post(f"/projects/{pid}/qa", json={"render_id": "nope"})
    job = await c.app.state.jobs.wait(r.json()["id"])
    assert job.status == "FAILED"
    assert (await c.get("/qa/nope")).status_code == 404


async def test_qa_detects_voice_not_on_timeline(mclient):
    """声を作っても、タイムラインに置かないと書き出しは無音になる。これを検出する。"""
    c = mclient
    pid = (await c.post("/projects", json={"name": "声", "width": 640, "height": 360})).json()["id"]
    sid = (
        await c.post(
            "/scripts",
            json={
                "project_id": pid,
                "text": "A：一つ目です。\nB：二つ目です。",
                "default_style_id": 10005,
            },
        )
    ).json()["id"]
    job = await c.app.state.jobs.wait((await c.post(f"/scripts/{sid}/voice")).json()["id"])
    assert job.status == "SUCCEEDED"
    await c.post("/subtitles/generate", json={"script_id": sid})
    await c.post("/subtitles/synchronize", json={"script_id": sid})

    _, by = await _run(c, pid, script_id=sid)
    assert "voice_not_placed" in _codes(by["project"], "error")

    # 置けば消える。2 回置いても声は二重にならない
    for _ in range(2):
        r = await c.post(f"/projects/{pid}/timeline/from_script", json={"script_id": sid})
        assert r.status_code < 300, r.text
    tl = (await c.get(f"/projects/{pid}/timeline")).json()
    narr = [t for t in tl["tracks"] if t["name"] == "ナレーション"]
    assert len(narr) == 1
    assert len([x for x in tl["clips"] if x["track_id"] == narr[0]["id"]]) == 2
    _, by = await _run(c, pid, script_id=sid)
    assert "voice_not_placed" not in _codes(by["project"])
