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


async def test_tutorial_from_screen_recording(bclient, fake, media_dir, tmp_path):
    """画面録画を渡すと操作説明になり、録画を中央の枠に置いて演出はそれを隠さない。"""
    c = bclient
    await c.post("/providers/discover")
    # 取り込みを許可していない場所の録画は断る
    other = tmp_path / "rec.mp4"
    other.write_bytes((media_dir / "clip.mp4").read_bytes())
    r = await c.post(
        "/build/plan", json={"instruction": "設定の変え方", "screen_recording": str(other)}
    )
    assert r.status_code == 400 and "フォルダ" in r.json()["message"]

    r = await c.post(
        "/build/plan",
        json={
            "instruction": "アプリの設定画面の開き方を説明して",
            "screen_recording": str(media_dir / "clip.mp4"),
        },
    )
    assert r.status_code == 201, r.text
    plan = r.json()
    assert plan["purpose"] == "tutorial" and "画面録画を置く" in plan["steps"]
    assert plan["duration_sec"] == 15  # 録画（3 秒）に合わせる。短すぎる値は 15 秒に
    assert any("操作の手順" in n for n in plan["notes"])
    names = [ch["name"] for ch in plan["characters"]]

    def responder(body):
        props = (body.get("format") or {}).get("properties", {})
        if "scenes" not in props:
            return None
        return {
            "scenes": [
                {
                    "purpose": "手順",
                    "turns": [
                        {"speaker": names[0], "text": "まず右上の歯車を押します。"},
                        {"speaker": names[-1], "text": "設定の画面が開いたね。"},
                        {"speaker": names[0], "text": "ここで表示の大きさを変えられます。"},
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
    job = await c.app.state.jobs.wait(r.json()["id"])
    assert job.status == "SUCCEEDED", job.error
    tl = (await c.get(f"/projects/{plan['project_id']}/timeline")).json()
    tracks = {t["name"]: t for t in tl["tracks"]}
    rec = [x for x in tl["clips"] if x["track_id"] == tracks["画面録画"]["id"]]
    assert rec and rec[0]["volume"] == 0.0 and rec[0]["start_frame"] == 0
    # 立ち絵が無いので枠は画面の 9 割まで。左右の真ん中・字幕より上
    assert abs(rec[0]["transform"]["x"]) < 1e-6 and rec[0]["transform"]["y"] < 0
    order = [t["name"] for t in sorted(tl["tracks"], key=lambda t: t["index"])]
    assert order.index("演出・背景") < order.index("画面録画") < order.index("演出・図")
    assert order.index("演出・図") < order.index("演出・文字")
    # 話が録画より長いので、最後の画面を止めて出す（操作箇所へのズームは作れている）
    assert len(rec) == 2 and rec[1]["start_frame"] == rec[0]["end_frame"]
    assert any("最後の画面" in n for n in job.result["notes"])
    assert not any("ズーム" in n for n in job.result["notes"])


async def test_music_video_from_song_and_lyrics(bclient, media_dir):
    """曲と歌詞を渡すとリリックビデオになり、歌の時刻を文字起こしで取って書き出す。"""
    from video_automation.services.transcribe.align import Word
    from video_automation.services.transcribe.service import Recognized

    c = bclient
    await c.post("/providers/discover")
    r = await c.post(
        "/build/plan",
        json={
            "instruction": "この曲の動画を作って",
            "music": str(media_dir / "tone.wav"),
            "lyrics": "夜をこえて\n光のほうへ",
        },
    )
    assert r.status_code == 201, r.text
    plan = r.json()
    assert plan["purpose"] == "lyric" and plan["characters"] == []
    assert plan["steps"][0] == "歌詞と時刻を合わせる" and plan["duration_sec"] == 15
    # 文字起こしのモデルが無いと作れない
    ready = {x["key"]: x for x in plan["readiness"]}
    assert not ready["transcribe"]["ok"] and not plan["can_run"]

    def fake(audio, language):
        return Recognized(
            words=[Word("よるを", 0.1, 0.8), Word("こえて", 0.8, 1.2), Word("ほうへ", 1.3, 1.9)],
            segments=[("夜を越えて", 0.1, 1.2), ("方へ", 1.3, 1.9)],
            language="ja",
            duration=2.0,
        )

    c.app.state.container.transcriber._recognizer = fake
    view = (
        await c.put(f"/build/{plan['project_id']}/plan", json={"script_id": plan["script_id"]})
    ).json()
    assert view["can_run"]
    r = await c.post(f"/build/{plan['project_id']}/run", json={"script_id": plan["script_id"]})
    job = await c.app.state.jobs.wait(r.json()["id"])
    assert job.status == "SUCCEEDED", job.error
    assert Path(job.result["video"]).is_file() and job.result["script_id"] != plan["script_id"]
    lines = (await c.get(f"/scripts/{job.result['script_id']}/detail")).json()["segments"]
    assert [x["text"] for x in lines] == ["夜をこえて", "光のほうへ"]
    tl = (await c.get(f"/projects/{plan['project_id']}/timeline")).json()
    song = next(t for t in tl["tracks"] if t["name"] == "曲")
    assert song["kind"] == "audio" and any(x["track_id"] == song["id"] for x in tl["clips"])

    # 見た目だけ作り直すときは、歌の時刻を取り直さない
    calls = []
    c.app.state.container.transcriber._recognizer = lambda a, lang: calls.append(1) or fake(a, lang)
    r = await c.post(
        f"/build/{plan['project_id']}/run",
        json={
            "script_id": plan["script_id"],
            "instruction": "落ち着いた感じに",
            "keep_script": True,
        },
    )
    job = await c.app.state.jobs.wait(r.json()["id"])
    assert job.status == "SUCCEEDED", job.error
    assert calls == []
