"""文章の指示だけで動画を作る（/build）。LLM・音声エンジン・Blender は偽物、FFmpeg は本物。"""

import shutil
from pathlib import Path

import httpx
import pytest
from asgi_lifespan import LifespanManager

from fakes import FakeProtector, fake_blender
from video_automation.api.app import create_app

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg")


@pytest.fixture
async def bclient(tmp_path, media_settings, fake):
    s = media_settings.model_copy(update={"blender_path": fake_blender(tmp_path)})
    app = create_app(s, http_transport=fake.transport, protector=FakeProtector())
    async with LifespanManager(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t/api/v1", timeout=300
        ) as c:
            c.app = app  # type: ignore[attr-defined]
            yield c


async def test_build_from_one_sentence(bclient, fake, media_settings):
    c = bclient
    await c.post("/providers/discover")
    ready = {r["key"]: r for r in (await c.get("/build/readiness")).json()}
    assert ready["voice"]["ok"] and ready["llm"]["ok"] and ready["ffmpeg"]["ok"]

    r = await c.post(
        "/build/plan", json={"instruction": "ダイヤモンドより硬い物質の解説動画を作って"}
    )
    assert r.status_code == 201, r.text
    plan = r.json()
    assert plan["purpose"] == "explainer" and plan["can_run"]
    assert (plan["width"], plan["height"]) == (1920, 1080) and plan["duration_sec"] == 120
    # 掛け合いの 2 人。偽の音声エンジンには同じ名前の話者がいないので、使える声で代わりにする
    assert len(plan["characters"]) >= 1 and all(ch["voice_style_id"] for ch in plan["characters"])
    assert plan["steps"][0] == "台本を作る" and plan["steps"][-1] == "書き出す"
    # 構成を作っただけでは、タイムラインは空
    tl = (await c.get(f"/projects/{plan['project_id']}/timeline")).json()
    assert tl["clips"] == []

    names = [ch["name"] for ch in plan["characters"]]

    def responder(body):
        props = (body.get("format") or {}).get("properties", {})
        if "scenes" not in props:
            return None
        return {
            "scenes": [
                {
                    "purpose": "導入",
                    "turns": [
                        {"speaker": names[0], "text": "今日は硬い物質の話をします。"},
                        {"speaker": names[-1], "text": "ダイヤモンドより硬いものがあるの？"},
                        {
                            "speaker": names[0],
                            "text": "計算では114ギガパスカルという値が出ています。",
                        },
                    ],
                }
            ],
            "notes": [],
        }

    fake.chat_responder = responder
    r = await c.post(
        f"/build/{plan['project_id']}/run",
        json={"script_id": plan["script_id"], "use_llm_direction": False},
    )
    assert r.status_code == 202, r.text
    job = await c.app.state.jobs.wait(r.json()["id"])
    assert job.status == "SUCCEEDED", job.error
    assert job.message == "できあがりました" and job.progress == 1.0
    video = Path(job.result["video"])
    assert video.is_file() and video.stat().st_size > 0
    tl = (await c.get(f"/projects/{plan['project_id']}/timeline")).json()
    assert tl["clips"]


async def test_build_needs_music_for_mv(bclient):
    c = bclient
    await c.post("/providers/discover")
    plan = (
        await c.post("/build/plan", json={"instruction": "この曲のリリックビデオを作って"})
    ).json()
    assert plan["purpose"] in ("lyric", "mv") and not plan["can_run"]
    assert any("曲のファイル" in n for n in plan["notes"])


async def test_voice_engine_started_after_the_app_is_found(bclient, fake):
    """アプリより後に VOICEVOX を起動しても、確かめ直して使えるようになる。"""
    c = bclient
    fake.nemo.up = False
    ready = {r["key"]: r for r in (await c.get("/build/readiness")).json()}
    assert not ready["voice"]["ok"] and ready["voice"]["detail"] == "動いていません"
    assert (await c.get("/voice/speakers")).status_code != 200
    fake.nemo.up = True
    ready = {r["key"]: r for r in (await c.get("/build/readiness")).json()}
    assert ready["voice"]["ok"]
    assert (await c.get("/voice/speakers")).status_code == 200


async def test_change_plan_and_revise_after_build(bclient, fake):
    """構成の確認で用途・長さ・立ち絵を変え、できあがった後に直したいことで作り直す。"""
    c = bclient
    await c.post("/providers/discover")
    plan = (
        await c.post("/build/plan", json={"instruction": "電池を長持ちさせるコツの解説動画"})
    ).json()
    pid, sid = plan["project_id"], plan["script_id"]
    assert plan["packs"] == []  # 立ち絵のパックが無いときは選べない

    # 用途をショートに変えると縦長になる。長さも変えられる
    r = await c.put(
        f"/build/{pid}/plan", json={"script_id": sid, "purpose": "short", "duration_sec": 30}
    )
    assert r.status_code == 200, r.text
    ch = r.json()
    assert ch["purpose"] == "short" and (ch["width"], ch["height"]) == (1080, 1920)
    assert ch["duration_sec"] == 30 and any("用途を" in n for n in ch["notes"])
    # 解説に戻すと横長に戻る
    ch = (await c.put(f"/build/{pid}/plan", json={"script_id": sid, "purpose": "explainer"})).json()
    assert (ch["width"], ch["height"]) == (1920, 1080) and ch["duration_sec"] == 30
    # 無い立ち絵・知らない用途は断る
    name = ch["characters"][0]["name"]
    r = await c.put(f"/build/{pid}/plan", json={"script_id": sid, "tachie": {name: "nope"}})
    assert r.status_code == 400 and "見つかりません" in r.json()["message"]
    r = await c.put(f"/build/{pid}/plan", json={"script_id": sid, "purpose": "xx"})
    assert r.status_code == 400
    # 立ち絵なしにする
    r = await c.put(f"/build/{pid}/plan", json={"script_id": sid, "tachie": {name: ""}})
    assert r.status_code == 200 and r.json()["characters"][0]["tachie_pack"] is None

    names = [x["name"] for x in ch["characters"]]
    prompts: list[str] = []

    def responder(body):
        props = (body.get("format") or {}).get("properties", {})
        if "scenes" not in props:
            return None
        text = "\n".join(m["content"] for m in body["messages"])
        prompts.append(text)
        later = "直したいこと" in text
        return {
            "scenes": [
                {
                    "purpose": "導入",
                    "turns": [
                        {
                            "speaker": names[0],
                            "text": "電池の話です。" if not later else "短く話します。",
                        },
                        {"speaker": names[-1], "text": "どうすればいいの？"},
                    ],
                }
            ],
            "notes": [],
        }

    fake.chat_responder = responder

    async def run(**kw):
        r = await c.post(
            f"/build/{pid}/run", json={"script_id": sid, "use_llm_direction": False, **kw}
        )
        assert r.status_code == 202, r.text
        job = await c.app.state.jobs.wait(r.json()["id"])
        assert job.status == "SUCCEEDED", job.error
        return job

    await run()
    first = [s["text"] for s in (await c.get(f"/scripts/{sid}/detail")).json()["segments"]]
    assert first[0] == "電池の話です。"

    # 見た目だけ作り直す: 台本は変えない（台本の生成を呼ばない）
    n = len(prompts)
    job = await run(instruction="カードを減らして", keep_script=True)
    assert len(prompts) == n and Path(job.result["video"]).is_file()
    same = [s["text"] for s in (await c.get(f"/scripts/{sid}/detail")).json()["segments"]]
    assert same == first

    # 台本から作り直す: 前の台本と直したいことを渡す
    await run(instruction="もっと短くして")
    assert "前に作った台本" in prompts[-1] and "もっと短くして" in prompts[-1]
    new = [s["text"] for s in (await c.get(f"/scripts/{sid}/detail")).json()["segments"]]
    assert new[0] == "短く話します。"
