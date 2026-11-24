"""演出プラン: LLM でプランを作り、確認後に素材を作ってタイムラインへ配置する。

Blender は偽の実行ファイル、LLM・音声エンジンは偽のサーバー。カードは実際に FFmpeg で作る。
"""

import shutil

import httpx
import pytest
from asgi_lifespan import LifespanManager

from fakes import FakeProtector, fake_blender
from video_automation.api.app import create_app

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg")
QWEN = "ollama-local::qwen3.5-9b-local:latest"
LINES = [
    {"speaker": "A", "text": "ダイヤモンドより硬い物質って本当にあるんですか？"},
    {"speaker": "B", "text": "w-BNの押し込み強度は114ギガパスカルと計算されました。"},
    {"speaker": "B", "text": "ダイヤモンドは97ギガパスカルです。"},
    {"speaker": "A", "text": "すごい！じゃあ一番硬いんですね？"},
    {"speaker": "B", "text": "実験ではダイヤモンドの方が硬いという結果でした。"},
]


@pytest.fixture
async def dclient(tmp_path, media_settings, fake):
    s = media_settings.model_copy(update={"blender_path": fake_blender(tmp_path)})
    app = create_app(s, http_transport=fake.transport, protector=FakeProtector())
    async with LifespanManager(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t/api/v1", timeout=120
        ) as c:
            c.app = app  # type: ignore[attr-defined]
            yield c


def _full(plan):
    """実際のモデルは構造化出力で全項目を書く（使わない項目は空）。"""
    empty = {
        "to_line": 0,
        "heading": "",
        "body": [],
        "labels": [],
        "values": [],
        "unit": "",
        "text": "",
    }
    plan["shots"] = [{**empty, **x} for x in plan["shots"]]
    return plan


async def _job(c, r):
    assert r.status_code == 202, r.text
    job = await c.app.state.jobs.wait(r.json()["id"])
    assert job.status == "SUCCEEDED", job.error
    return job.result


async def _ready_script(c):
    await c.post("/providers/discover")
    pid = (await c.post("/projects", json={"name": "D", "width": 640, "height": 360})).json()["id"]
    sc = (
        await c.post(
            "/scripts/studio",
            json={
                "project_id": pid,
                "mode": "MANUAL",
                "title": "w-BNの硬さ",
                "characters": [
                    {"name": "A", "voice": {"style_id": 10005}, "color": "#FF8800"},
                    {"name": "B", "voice": {"style_id": 10001}},
                ],
            },
        )
    ).json()
    sid = sc["id"]
    await c.post(f"/scripts/{sid}/dialogue", json={"lines": LINES})
    await _job(c, await c.post(f"/scripts/{sid}/voice"))
    await c.post("/subtitles/generate", json={"script_id": sid})
    await c.post("/subtitles/synchronize", json={"script_id": sid})
    assert (
        await c.post(f"/projects/{pid}/timeline/from_script", json={"script_id": sid})
    ).status_code == 201
    return pid, sid


async def test_plan_apply_and_reapply(dclient, fake):
    c = dclient
    pid, sid = await _ready_script(c)
    seen = {}

    def responder(body):
        props = (body.get("format") or {}).get("properties", {})
        if "shots" not in props:
            return None
        seen["prompt"] = body["messages"][-1]["content"]
        seen["system"] = body["messages"][0]["content"]
        seen["num_ctx"] = (body.get("options") or {}).get("num_ctx")
        return _full(
            {
                "title": "w-BNは最強？",
                "subtitle": "計算と実験の違い",
                "shots": [
                    {"kind": "card", "from_line": 1, "heading": "疑問", "body": ["硬さの比べ方"]},
                    {
                        "kind": "chart",
                        "from_line": 2,
                        "to_line": 3,
                        "heading": "押し込み強度",
                        "labels": ["w-BN", "ダイヤモンド"],
                        "values": [114, 97],
                        "unit": "GPa",
                        "highlight": 0,
                    },
                    {"kind": "keyword", "from_line": 2, "text": "114GPa"},
                    {"kind": "keyword", "from_line": 3, "text": "500GPa"},  # 台本に無い → 外す
                    {"kind": "image", "from_line": 4, "query": "diamond crystal"},  # 素材なし
                    {
                        "kind": "card",
                        "from_line": 5,
                        "heading": "まとめ",
                        "style": "summary",
                        "body": ["計算: w-BN が 18% 上", "実験: ダイヤモンドが上"],
                    },
                ],
            }
        )

    fake.chat_responder = responder
    r = await c.post("/direction/plan", json={"script_id": sid, "model_key": QWEN})
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["lines"] == 5 and res["model_key"] == QWEN
    assert "[2] B：w-BNの押し込み強度" in seen["prompt"] and "w-BNの硬さ" in seen["prompt"]
    # 演出の決まり（スキル）の関係する節がプロンプトに入り、どの節を使ったかが返る
    assert "参考（スキル）" in seen["system"] and "【video-design-by-purpose /" in seen["system"]
    assert res["skills"] and all(" / " in x for x in res["skills"])
    assert seen["num_ctx"] and seen["num_ctx"] % 4096 == 0  # 長いプロンプトは num_ctx を広げる
    plan = res["plan"]
    assert [s["kind"] for s in plan["shots"]] == ["card", "chart", "keyword", "image", "card"]
    assert any("500GPa" in n for n in plan["notes"])

    # プランを作っただけではタイムラインは変わらない
    before = (await c.get(f"/projects/{pid}/timeline")).json()
    assert [t["name"] for t in before["tracks"]] == ["ナレーション"]

    out = await _job(c, await c.post("/direction/apply", json={"script_id": sid, "plan": plan}))
    assert out["status"] == "APPLIED"
    assert any("diamond crystal" in x for x in out["skipped"])  # 画像は素材を選ぶまで外す
    tl = (await c.get(f"/projects/{pid}/timeline")).json()
    names = {t["id"]: t["name"] for t in tl["tracks"]}
    assert set(names.values()) == {"ナレーション", "演出・背景", "演出・図", "演出・文字"}
    by_track: dict[str, list] = {}
    for cl in tl["clips"]:
        by_track.setdefault(names[cl["track_id"]], []).append(cl)
    fig = sorted(by_track["演出・図"], key=lambda x: x["start_frame"])
    # タイトルの下の一言・図表・まとめ（画面が空かないよう、次が出るまで表示）。
    # 1 発話目のカードはタイトルと重なり、表示時間が残らないので外れる
    assert len(fig) == 3
    assert any(x.startswith("1: 表示時間が短すぎる") for x in out["skipped"])
    for a, b in zip(fig[1:], fig[2:], strict=False):
        assert a["end_frame"] <= b["start_frame"]
    assert fig[-1]["end_frame"] == tl["duration_frames"]
    assert all(-0.1 < x["transform"]["y"] < 0 for x in fig[1:])  # 字幕の上の領域
    txt = by_track["演出・文字"]
    assert len(txt) == 2  # タイトル + テロップ 1 つ
    kw = max(txt, key=lambda x: x["start_frame"])
    assert kw["transform"]["y"] < -0.3 and kw["transform"]["x"] < -0.2  # 左上
    assert len(by_track["演出・背景"]) == 1
    assert by_track["演出・背景"][0]["end_frame"] == tl["duration_frames"]

    # 字幕つきで 1 フレーム描画できる（カードの PNG・帯・話者名）
    mid = fig[1]["start_frame"] + 20
    r = await c.post(
        f"/projects/{pid}/preview", json={"frame": mid, "script_id": sid, "width": 640}
    )
    assert r.status_code == 200 and r.content[:4] == b"\x89PNG"

    # もう一度適用すると以前の演出を置き換える（トラックは増えない）
    out2 = await _job(c, await c.post("/direction/apply", json={"script_id": sid, "plan": plan}))
    tl2 = (await c.get(f"/projects/{pid}/timeline")).json()
    assert len(tl2["tracks"]) == 4 and len(tl2["clips"]) == len(tl["clips"])
    assert out2["clips"] == out["clips"]
    # 元に戻すと 1 回分（演出全体）が戻る
    assert (await c.post(f"/projects/{pid}/undo")).status_code == 200
    assert len((await c.get(f"/projects/{pid}/timeline")).json()["clips"]) == len(tl["clips"])


async def test_rule_plan_without_llm_and_errors(dclient):
    c = dclient
    pid, sid = await _ready_script(c)
    r = await c.post("/direction/plan", json={"script_id": sid, "use_llm": False})
    plan = r.json()["plan"]
    assert plan["title"] == "w-BNの硬さ"
    assert [(s["kind"], s["from_line"]) for s in plan["shots"]] == [
        ("keyword", 2),
        ("keyword", 3),
    ]
    assert (await c.post("/direction/plan", json={"script_id": "nope"})).status_code == 404


async def test_design_purpose_shapes_direction_and_guards_render(dclient):
    c = dclient
    pid, sid = await _ready_script(c)
    profiles = (await c.get("/design/profiles")).json()
    assert len(profiles) >= 20 and {p["purpose"] for p in profiles} >= {"subtitle", "mv"}
    r = (await c.post("/design/classify", json={"text": "解説動画だけどMVっぽく"})).json()
    assert r["purpose"] == "explainer" and r["mixed_from"] == ["mv"]
    assert (await c.get(f"/projects/{pid}/design")).json() == {"purpose": None, "extras": []}
    plan = (await c.post("/direction/plan", json={"script_id": sid, "use_llm": False})).json()[
        "plan"
    ]
    plan["shots"].append({"kind": "card", "from_line": 4, "heading": "まとめ", "body": ["要点"]})

    # 字幕動画: カード・テロップは使わない（外したことを返す）。題名と背景だけ
    r = await c.put(f"/projects/{pid}/design", json={"purpose": "subtitle"})
    assert r.status_code == 200
    out = await _job(c, await c.post("/direction/apply", json={"script_id": sid, "plan": plan}))
    assert sum("「字幕動画」では" in x for x in out["skipped"]) == 3
    chk = (await c.get(f"/projects/{pid}/design/check", params={"script_id": sid})).json()
    assert chk["ok"], chk
    assert chk["used"].get("card") is None and chk["used"]["title_card"] == 1
    assert chk["used"]["subtitle"] >= 5
    # 字幕の見た目は型から（帯は文字の幅だけ・型の大きさ）
    style = (await c.get(f"/subtitles/style/{pid}")).json()
    assert style["band_mode"] == "fitted" and style["font_size_ratio"] == 0.058

    # 別の用途の演出が混ざったら書き出す前に止める
    await c.app.state.container.design.record(pid, "rich", {"used": {"shake_punch": 2}})
    chk = (await c.get(f"/projects/{pid}/design/check", params={"script_id": sid})).json()
    assert not chk["ok"] and chk["violations"][0]["code"] == "denied_component"
    body = {"project_id": pid, "script_id": sid, "quality": 35}
    r = await c.post("/render", json=body)
    assert r.status_code == 422 and r.json()["code"] == "design_violation"
    assert "揺れ" in r.json()["message"]
    # LLM 経由では例外にできない
    r = await c.post("/render", json={**body, "allow_design_violations": True, "origin": "llm"})
    assert r.status_code == 422
    # 混ぜる指示が明示されたとき（extras）は通る
    await c.put(f"/projects/{pid}/design", json={"purpose": "subtitle", "extras": ["shake_punch"]})
    assert (await c.get(f"/projects/{pid}/design/check", params={"script_id": sid})).json()["ok"]
    await c.app.state.container.design.record(pid, "rich", None)

    # 解説: カード・テロップを使い、色は型の配色
    await c.put(f"/projects/{pid}/design", json={"purpose": "explainer"})
    out = await _job(c, await c.post("/direction/apply", json={"script_id": sid, "plan": plan}))
    chk = (await c.get(f"/projects/{pid}/design/check", params={"script_id": sid})).json()
    assert chk["ok"], chk
    assert chk["used"]["keyword"] == 2 and chk["used"]["card"] == 1
    rec = (await c.app.state.container.design.manifest(pid)).colors
    assert "#4CB3E6" in rec and "#F2B544" in rec


async def test_long_chart_is_extended_by_repeating_its_still_part(dclient, monkeypatch):
    """図表の素材は長さに上限がある。区間がそれより長いと、止まった後半を繰り返して
    最後まで出し続ける（背景だけの時間を作らない）。"""
    from video_automation.services.direction import service as dsvc

    monkeypatch.setattr(dsvc, "CHART_MAX_SEC", 3.0)
    c = dclient
    pid, sid = await _ready_script(c)
    plan = {
        "title": "",
        "shots": [
            {
                "kind": "chart",
                "from_line": 1,
                "to_line": 5,
                "heading": "押し込み強度",
                "labels": ["w-BN", "ダイヤ"],
                "values": [114, 97],
                "unit": "GPa",
            }
        ],
    }
    out = await _job(c, await c.post("/direction/apply", json={"script_id": sid, "plan": plan}))
    assert out["status"] == "APPLIED", out
    tl = (await c.get(f"/projects/{pid}/timeline")).json()
    names = {t["id"]: t["name"] for t in tl["tracks"]}
    fig = sorted(
        (x for x in tl["clips"] if names[x["track_id"]] == "演出・図"),
        key=lambda x: x["start_frame"],
    )
    assert len(fig) >= 3 and len({x["asset_id"] for x in fig}) == 1
    for a, b in zip(fig, fig[1:], strict=False):
        assert a["end_frame"] == b["start_frame"]  # 隙間なくつながる
    assert fig[0]["source_in_frame"] == 0 and all(x["source_in_frame"] > 0 for x in fig[1:])
    assert fig[-1]["end_frame"] == tl["duration_frames"]
    assert fig[0]["fade_out_frames"] == 0 and fig[-1]["fade_out_frames"] > 0
    rec = (await c.get(f"/projects/{pid}/design")).json()
    chk = (await c.get(f"/projects/{pid}/design/check", params={"script_id": sid})).json()
    assert not any(v["code"] == "empty_screen" for v in chk["violations"]), (rec, chk)
