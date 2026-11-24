"""立ち絵 API: パックの一覧、台本の立ち絵を「立ち絵」トラックに置く（同じ内容なら作り直さない）。"""

import shutil

import pytest
from sqlalchemy import select

from video_automation.database.models import ScriptCharacter, ScriptSegment

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


async def _project(client, w=320, h=180):
    r = await client.post(
        "/projects", json={"name": "T", "width": w, "height": h, "fps_num": 30, "fps_den": 1}
    )
    return r.json()["id"]


async def _script_with_audio(client, pid):
    text = "A：こんにちは、立ち絵の試験なのだ！\n\nB：ボタンを押すのね。"
    r = await client.post(
        "/scripts", json={"project_id": pid, "text": text, "default_style_id": 10005}
    )
    sid = r.json()["id"]
    job = await client.post(f"/scripts/{sid}/voice")
    done = await client.app.state.jobs.wait(job.json()["id"])
    assert done.status == "SUCCEEDED", done.error
    return sid, done.result


async def test_apply_tachie_track_and_cache(client, make_tachie_pack):
    c = client.app.state.container
    pack_dir = make_tachie_pack(c.settings.data_dir / "_src")
    shutil.copytree(pack_dir, c.settings.data_dir / "characters" / "testpack")
    packs = (await client.get("/characters/packs")).json()
    assert [p["id"] for p in packs] == ["testpack"] and packs[0]["credit"] == "立ち絵：作者"

    pid = await _project(client)
    sid, _ = await _script_with_audio(client, pid)
    await client.post("/subtitles/generate", json={"script_id": sid})
    await client.post("/subtitles/synchronize", json={"script_id": sid})
    # 立ち絵が無ければ何もしない
    job = await client.post("/characters/apply", json={"script_id": sid})
    done = await c.jobs.wait(job.json()["id"])
    assert done.status == "SUCCEEDED" and done.result["applied"] is False
    async with c.db.session_factory() as s:
        speakers = list(
            dict.fromkeys(
                (
                    await s.execute(
                        select(ScriptSegment.speaker).where(ScriptSegment.script_id == sid)
                    )
                ).scalars()
            )
        )
        for i, name in enumerate(speakers):
            s.add(ScriptCharacter(script_id=sid, sequence=i, name=name, tachie_pack="testpack"))
        await s.commit()
    job = await client.post("/characters/apply", json={"script_id": sid})
    done = await c.jobs.wait(job.json()["id"])
    assert done.status == "SUCCEEDED", done.error
    assert done.result["applied"] and not done.result["cached"]
    tl = (await client.get(f"/projects/{pid}/timeline")).json()
    track = next(t for t in tl["tracks"] if t["name"] == "立ち絵")
    clips = [x for x in tl["clips"] if x["track_id"] == track["id"]]
    assert len(clips) == 1 and clips[0]["transform"]["y"] > 0  # 画面の下に寄せる
    # 同じ内容なら作り直さない
    job = await client.post("/characters/apply", json={"script_id": sid})
    done = await c.jobs.wait(job.json()["id"])
    assert done.result["cached"] is True
    # クレジットに立ち絵の作者が入る
    assert await c.characters.project_credits(pid) == ["立ち絵：作者"]
    # 書き出し（プレビュー）で透過のまま重なる（下の黒い背景の上に体の灰色が出る）
    from io import BytesIO

    from PIL import Image

    r = await client.post(f"/projects/{pid}/preview", json={"frame": 20, "width": 320})
    assert r.status_code == 200, r.text
    im = Image.open(BytesIO(r.content)).convert("RGB")
    px = [im.getpixel((x, y)) for x in range(0, 320, 4) for y in range(90, 180, 4)]
    assert any(min(p) > 90 for p in px)  # 立ち絵がある
    assert im.getpixel((160, 5)) == (0, 0, 0)  # 立ち絵の無い所は透けて黒
