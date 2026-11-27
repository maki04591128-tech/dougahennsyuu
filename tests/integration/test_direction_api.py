"""演出プラン: LLM でプランを作り、確認後に素材を作ってタイムラインへ配置する。

Blender は偽の実行ファイル、LLM・音声エンジンは偽のサーバー。カードは実際に FFmpeg で作る。
"""

import json
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


async def _ready_script(c, lines=None):
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
    await c.post(f"/scripts/{sid}/dialogue", json={"lines": lines or LINES})
    await _job(c, await c.post(f"/scripts/{sid}/voice"))
    await c.post("/subtitles/generate", json={"script_id": sid})
    await c.post("/subtitles/synchronize", json={"script_id": sid})
    assert (
        await c.post(f"/projects/{pid}/timeline/from_script", json={"script_id": sid})
    ).status_code == 201
    return pid, sid


async def test_plan_apply_and_reapply(dclient, fake, tmp_path):
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
    # 統合版 W1: 台本を先頭、決まり・指示・例を後ろに置く
    pr = seen["prompt"]
    assert "参考（スキル）" in pr and "【video-design-by-purpose /" in pr
    assert pr.index("[2] B：") < pr.index("参考（スキル）") < pr.index("指示:") < pr.index("例1")
    assert "参考（スキル）" not in seen["system"]
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
    assert set(names.values()) == {
        "ナレーション",
        "演出・背景",
        "演出・図",
        "演出・文字",
        "演出・項目",
    }
    by_track: dict[str, list] = {}
    for cl in tl["clips"]:
        by_track.setdefault(names[cl["track_id"]], []).append(cl)
    fig = sorted(by_track["演出・図"], key=lambda x: x["start_frame"])
    # タイトルの下の一言・図表・まとめ（画面が空かないよう、次が出るまで表示）。
    # 1 発話目のカードはタイトルと重なり、表示時間が残らないので外れる。
    # まとめ（2 項目）は 1 項目ずつ出すので、同じ大きさの絵 2 枚を続けて置く
    assert len(fig) == 4
    assert fig[2]["end_frame"] == fig[3]["start_frame"]
    assert (fig[2]["fade_out_frames"], fig[3]["fade_in_frames"]) == (0, 0)
    assert any(x.startswith("1: 表示時間が短すぎる") for x in out["skipped"])
    for a, b in zip(fig[1:], fig[2:], strict=False):
        assert a["end_frame"] <= b["start_frame"]
    assert fig[-1]["end_frame"] == tl["duration_frames"]
    assert all(-0.1 < x["transform"]["y"] < 0 for x in fig[1:])  # 字幕の上の領域
    txt = by_track["演出・文字"]
    roles = await c.app.state.container.design.clip_roles(pid)
    assert sorted(roles[x["id"]] for x in txt) == ["keyword", "title_card"]  # タイトル + テロップ
    # 統合版 D1: まとめの 2 項目目は、別の絵で重ねて出す（重ねる用のトラック）
    (item,) = by_track["演出・項目"]
    assert (item["start_frame"], item["end_frame"]) == (fig[3]["start_frame"], fig[3]["end_frame"])
    keys = item["animation"]["keyframes"]
    assert keys[0]["opacity"] == 0 and abs(keys[0]["y"] - 16 / 1080) < 1e-6 and keys[1]["y"] == 0
    kw = next(x for x in txt if roles[x["id"]] == "keyword")
    # 右上（左上は書き出し時の章の見出しの場所なので重ねない）
    assert kw["transform"]["y"] < -0.3 and kw["transform"]["x"] > 0.2
    # 用途の型が無いときの背景は Blender の動く背景（8 秒で継ぎ目なく繰り返す）を並べて全体を覆う
    bgs = sorted(by_track["演出・背景"], key=lambda x: x["start_frame"])
    assert len(bgs) >= 2 and bgs[0]["start_frame"] == 0
    for a_, b_ in zip(bgs, bgs[1:], strict=False):
        assert a_["end_frame"] == b_["start_frame"]
    assert bgs[-1]["end_frame"] == tl["duration_frames"]
    calls = [json.loads(x) for x in (tmp_path / "calls.jsonl").read_text("utf-8").splitlines()]
    mb = [x for x in calls if x["script"] == "motion_bg"]
    assert mb and mb[0]["style"] == "iso_blocks" and mb[0]["frames"] == 8 * 30

    # 字幕つきで 1 フレーム描画できる（カードの PNG・帯・話者名）
    mid = fig[1]["start_frame"] + 20
    r = await c.post(
        f"/projects/{pid}/preview", json={"frame": mid, "script_id": sid, "width": 640}
    )
    assert r.status_code == 200 and r.content[:4] == b"\x89PNG"

    # もう一度適用すると以前の演出を置き換える（トラックは増えない）
    out2 = await _job(c, await c.post("/direction/apply", json={"script_id": sid, "plan": plan}))
    tl2 = (await c.get(f"/projects/{pid}/timeline")).json()
    assert len(tl2["tracks"]) == 5 and len(tl2["clips"]) == len(tl["clips"])
    assert out2["clips"] == out["clips"]
    # 元に戻すと 1 回分（演出全体）が戻る
    assert (await c.post(f"/projects/{pid}/undo")).status_code == 200
    assert len((await c.get(f"/projects/{pid}/timeline")).json()["clips"]) == len(tl["clips"])

    # 立ち絵があると、図・カードは左右の立ち絵の列に掛からない中央の列に収める
    from sqlalchemy import update

    from video_automation.database.models import ScriptCharacter

    async with c.app.state.db.session_factory() as ss:
        await ss.execute(
            update(ScriptCharacter)
            .where(ScriptCharacter.script_id == sid)
            .values(tachie_pack="dummy")
        )
        await ss.commit()
    await _job(c, await c.post("/direction/apply", json={"script_id": sid, "plan": plan}))
    tl3 = (await c.get(f"/projects/{pid}/timeline")).json()
    names3 = {t["id"]: t["name"] for t in tl3["tracks"]}
    fig3 = sorted(
        [x for x in tl3["clips"] if names3[x["track_id"]] == "演出・図"],
        key=lambda x: x["start_frame"],
    )
    assert len(fig3) == len(fig)
    for old, new in zip(fig, fig3, strict=True):
        assert new["transform"]["scale"] <= old["transform"]["scale"] + 1e-9
        assert new["transform"]["scale"] <= 0.56 + 1e-9 or old["transform"]["scale"] <= 0.56


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
    assert (await c.get(f"/projects/{pid}/design")).json() == {
        "purpose": None,
        "extras": [],
        "series_id": None,
        "genre": None,
        "hearing_aid": False,
    }
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
    # 冒頭の短い題名は 1 文字ずつ跳ねる（1 回）。数値のテロップはポンと飛び出す（2 回まで）
    assert chk["used"]["kinetic_text"] == 1 and chk["used"]["emphasis_text"] == 2
    tl = (await c.get(f"/projects/{pid}/timeline")).json()
    txt = next(t["id"] for t in tl["tracks"] if t["name"] == "演出・文字")
    presets = [
        (x.get("animation") or {}).get("preset") for x in tl["clips"] if x["track_id"] == txt
    ]
    assert presets.count("pop_in_soft") == 2, presets  # 解説の pop は控えめ（統合版 B2）
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
    # 出（不透明度 1→0）は最後の 1 つだけ（統合版 A2）
    assert fig[0]["animation"] is None or fig[0]["animation"]["keyframes"][-1]["opacity"] != 0
    assert fig[-1]["animation"]["keyframes"][-1]["opacity"] == 0.0
    rec = (await c.get(f"/projects/{pid}/design")).json()
    chk = (await c.get(f"/projects/{pid}/design/check", params={"script_id": sid})).json()
    assert not any(v["code"] == "empty_screen" for v in chk["violations"]), (rec, chk)


async def test_unplaceable_last_image_does_not_leave_the_end_empty(dclient, fake):
    """最後の図が素材の無い画像なら、その前の図・カードを終わりまで出し続ける（空白にしない）。"""
    c = dclient
    pid, sid = await _ready_script(c)

    def responder(body):
        if "shots" not in (body.get("format") or {}).get("properties", {}):
            return None
        return _full(
            {
                "title": "",
                "subtitle": "",
                "shots": [
                    {"kind": "card", "from_line": 2, "heading": "要点", "body": ["114GPa"]},
                    {"kind": "image", "from_line": 4, "query": "diamond"},  # 素材なし
                ],
            }
        )

    fake.chat_responder = responder
    plan = (await c.post("/direction/plan", json={"script_id": sid})).json()["plan"]
    out = await _job(c, await c.post("/direction/apply", json={"script_id": sid, "plan": plan}))
    assert any("diamond" in x for x in out["skipped"])
    tl = (await c.get(f"/projects/{pid}/timeline")).json()
    names = {t["id"]: t["name"] for t in tl["tracks"]}
    fig = [x for x in tl["clips"] if names[x["track_id"]] == "演出・図"]
    assert fig and max(x["end_frame"] for x in fig) == tl["duration_frames"]


async def test_too_few_visuals_are_asked_again_once(dclient, fake):
    """図・カードが少なすぎる案は、1 回だけ足すよう頼み直す（同じカードが出続けないように）。"""
    c = dclient
    _pid, sid = await _ready_script(c)
    calls = []

    def responder(body):
        if "shots" not in (body.get("format") or {}).get("properties", {}):
            return None
        last = body["messages"][-1]["content"]
        calls.append(last)
        card = {"kind": "card", "from_line": 2, "heading": "要点", "body": ["114GPa"]}
        if "しかありません" not in last:
            return _full({"title": "", "subtitle": "", "shots": [card]})
        return _full(
            {
                "title": "",
                "subtitle": "",
                "shots": [
                    card,
                    {"kind": "card", "from_line": 4, "heading": "実験", "body": ["ダイヤが上"]},
                    {
                        "kind": "card",
                        "from_line": 5,
                        "heading": "まとめ",
                        "style": "summary",
                        "body": ["計算と実験で違う"],
                    },
                ],
            }
        )

    fake.chat_responder = responder
    plan = (await c.post("/direction/plan", json={"script_id": sid})).json()["plan"]
    assert len(calls) == 2 and "少なくとも 2 個" in calls[1]
    assert [s["kind"] for s in plan["shots"]].count("card") == 3


async def test_tachie_placed_before_direction_stays_above_background(dclient):
    """立ち絵を先に置いてから演出を付けても、背景で隠れない（背景 → 図 → 立ち絵 → 文字）。"""
    c = dclient
    pid, sid = await _ready_script(c)
    r = await c.post(
        f"/projects/{pid}/commands",
        json={"command": {"type": "add_track", "kind": "video", "name": "立ち絵"}},
    )
    assert r.status_code == 201, r.text
    plan = (await c.post("/direction/plan", json={"script_id": sid, "use_llm": False})).json()
    for _ in range(2):  # 作り直しても順番は変わらない
        await _job(
            c, await c.post("/direction/apply", json={"script_id": sid, "plan": plan["plan"]})
        )
        tl = (await c.get(f"/projects/{pid}/timeline")).json()
        video = [t["name"] for t in sorted(tl["tracks"], key=lambda t: t["index"])]
        video = [n for n in video if n != "ナレーション"]
        assert video == ["演出・背景", "演出・図", "立ち絵", "演出・文字"]


async def test_user_edits_are_logged_and_accepted_defaults_applied(dclient, tmp_path):
    """統合版 W2: 利用者が直接直した演出の部品を記録し、承認した既定値を次の演出に足す。"""
    c = dclient
    pid, sid = await _ready_script(c)
    assert (
        await c.put(f"/projects/{pid}/design", json={"purpose": "explainer"})
    ).status_code == 200
    plan = (await c.post("/direction/plan", json={"script_id": sid, "use_llm": False})).json()[
        "plan"
    ]
    await _job(c, await c.post("/direction/apply", json={"script_id": sid, "plan": plan}))
    container = c.app.state.container
    log = container.design.edits
    made = [r for r in log.rows() if r["kind"] == "made"]
    assert made and made[-1]["purpose"] == "explainer" and "keyword" in made[-1]["components"]
    roles = await container.design.clip_roles(pid)
    tl = (await c.get(f"/projects/{pid}/timeline")).json()
    kw = next(x for x in tl["clips"] if roles.get(x["id"]) == "keyword")
    tf = {**kw["transform"], "y": kw["transform"]["y"] + 0.05}
    r = await c.post(
        f"/projects/{pid}/commands",
        json={"command": {"type": "set_clip_props", "clip_id": kw["id"], "transform": tf}},
    )
    assert r.status_code == 201 and r.json()["status"] == "APPLIED"
    edits = [r for r in log.rows() if r["kind"] == "edit"]
    assert [(e["component"], e["item"], e["amount"]) for e in edits] == [("keyword", "y", 0.05)]
    assert "w-BN" not in log.path.read_text("utf-8")  # 台本の文は記録しない

    # LLM からの命令（承認待ち）は記録しない
    r = await c.post(
        f"/projects/{pid}/commands",
        json={
            "command": {
                "type": "set_clip_props",
                "clip_id": kw["id"],
                "transform": kw["transform"],
            },
            "origin": "llm",
        },
    )
    assert r.status_code == 201
    assert len([r for r in log.rows() if r["kind"] == "edit"]) == 1

    # 承認した既定（テロップを下へ 5%）は、次の演出の適用から足す
    d = log.defaults()
    d.accepted = {"explainer": {"keyword": {"y": 0.05}}}
    log._save(d)
    await _job(c, await c.post("/direction/apply", json={"script_id": sid, "plan": plan}))
    roles = await container.design.clip_roles(pid)
    tl2 = (await c.get(f"/projects/{pid}/timeline")).json()
    kw2 = next(x for x in tl2["clips"] if roles.get(x["id"]) == "keyword")
    assert abs(kw2["transform"]["y"] - (kw["transform"]["y"] + 0.05)) < 1e-6
    assert (await c.get("/design/defaults")).json()["accepted"] == d.accepted
    assert (await c.get("/design/edits/proposals")).json() == []  # 5 本に足りない
    r = await c.post(
        "/design/edits/accept", json={"purpose": "explainer", "component": "card", "item": "x"}
    )
    assert r.status_code == 404


async def test_image_look_is_matched_to_the_purpose(dclient, media_dir):
    """統合版 Q3: 置く画像は用途の配色に揃えた複製にする（同じ画像は作り直さない）。"""
    import numpy as np
    from PIL import Image

    c = dclient
    pid, sid = await _ready_script(c)
    await c.put(f"/projects/{pid}/design", json={"purpose": "explainer"})
    yy, xx = np.mgrid[0:180, 0:320]
    dark = np.stack([0.15 + 0.1 * np.sin(xx / (8 + 3 * i) + yy / 11) for i in range(3)], -1)
    src = media_dir / "dark_photo_q3.png"
    Image.fromarray((np.clip(dark, 0, 1) * 255).astype(np.uint8)).save(src)
    a = (await c.post("/assets/import", json={"project_id": pid, "path": str(src)})).json()
    plan = {
        "title": "",
        "subtitle": "",
        "shots": [{"kind": "image", "from_line": 2, "to_line": 5, "asset_id": a["id"]}],
    }

    async def placed():
        tl = (await c.get(f"/projects/{pid}/timeline")).json()
        names = {t["id"]: t["name"] for t in tl["tracks"]}
        assets = {x["id"]: x for x in tl["assets"]}
        roles = await c.app.state.container.design.clip_roles(pid)
        return [
            assets[x["asset_id"]]
            for x in tl["clips"]
            if names[x["track_id"]] == "演出・図" and roles.get(x["id"]) == "image"
        ]

    await _job(c, await c.post("/direction/apply", json={"script_id": sid, "plan": plan}))
    (img,) = await placed()
    assert img["source"] == "look" and img["id"] != a["id"] and "見た目を揃えた" in img["name"]
    await _job(c, await c.post("/direction/apply", json={"script_id": sid, "plan": plan}))
    (img2,) = await placed()
    assert img2["id"] == img["id"]  # 前に作った複製を使う
    tl_assets = (await c.get(f"/projects/{pid}/timeline")).json()["assets"]
    assert sum(1 for x in tl_assets if x["source"] == "look") == 1
    body = {"script_id": sid, "plan": plan, "match_look": False}
    await _job(c, await c.post("/direction/apply", json=body))
    (img3,) = await placed()
    assert img3["id"] == a["id"]  # 揃えないときは元の画像


async def test_end_screen_fills_the_last_15_seconds(dclient):
    """統合版 S9: 終了画面を使う用途（解説）は、最後の発話の後に 15 秒の終了画面を置く。"""
    c = dclient
    pid, sid = await _ready_script(c)
    await c.put(f"/projects/{pid}/design", json={"purpose": "explainer"})
    before = (await c.get(f"/projects/{pid}/timeline")).json()["duration_frames"]
    plan = (await c.post("/direction/plan", json={"script_id": sid, "use_llm": False})).json()[
        "plan"
    ]
    await _job(c, await c.post("/direction/apply", json={"script_id": sid, "plan": plan}))
    tl = (await c.get(f"/projects/{pid}/timeline")).json()
    roles = await c.app.state.container.design.clip_roles(pid)
    (es,) = [x for x in tl["clips"] if roles.get(x["id"]) == "end_screen"]
    assert (es["start_frame"], es["end_frame"]) == (before, before + 15 * 30)
    assert tl["duration_frames"] == before + 15 * 30
    bg = [x for x in tl["clips"] if roles.get(x["id"]) == "background"]
    assert max(x["end_frame"] for x in bg) == before + 15 * 30
    chk = (await c.get(f"/projects/{pid}/design/check", params={"script_id": sid})).json()
    assert not any(
        v["code"] == "change_interval" and "15" in v["message"] for v in chk["violations"]
    )
    # 統合版 S4: サムネイルを 3 案（字幕の無いプレビューのフレームを背景に）
    out = await _job(c, await c.post(f"/projects/{pid}/design/thumbnails"))
    assert len(out["thumbnails"]) == 3
    from PIL import Image

    for p in out["thumbnails"]:
        assert Image.open(p).size == (3840, 2160)
    assert not [n for n in out["notes"] if "案目" in n]  # 決まりに合わない所が無い
    # 終了画面を使わない用途では置かない
    await c.put(f"/projects/{pid}/design", json={"purpose": "promo"})
    await _job(c, await c.post("/direction/apply", json={"script_id": sid, "plan": plan}))
    roles = await c.app.state.container.design.clip_roles(pid)
    assert "end_screen" not in roles.values()
    # 統合版 L7: PR・CM の題名には光沢のスイープを 1 回（解説には付けない）
    from pathlib import Path

    log_ = Path(c.app.state.container.settings.blender_path).parent / "calls.jsonl"
    titles = [json.loads(x) for x in log_.read_text("utf-8").splitlines()]
    titles = [x for x in titles if x["script"] == "title_card"]
    promo = [x for x in titles if x["animation"] == "tracking"]  # PR の題名（T7）
    assert promo[-1]["gloss_at"] > 0 and titles[0]["gloss_at"] == 0
    assert titles[-1]["animation"] == "slam"  # 統合版 H5: 最後の 3 秒の締め


async def test_lower_thirds_show_name_and_job_title(dclient):
    """統合版 P1: インタビューでは、各話者の最初の発話の 0.5 秒後に名前帯（肩書きは話者の設定）。"""
    from PIL import Image

    from video_automation.schemas.script_studio import CharacterIn, ScriptUpdate

    c = dclient
    pid, sid = await _ready_script(c)
    container = c.app.state.container
    detail = await container.studio.detail(sid)
    chars = [CharacterIn.model_validate(x.model_dump()) for x in detail.characters]
    chars[1].job_title = "物質・材料研究所 主任研究員"
    await container.studio.update(sid, ScriptUpdate(characters=chars))
    await c.put(f"/projects/{pid}/design", json={"purpose": "interview"})
    plan = (await c.post("/direction/plan", json={"script_id": sid, "use_llm": False})).json()[
        "plan"
    ]
    await _job(c, await c.post("/direction/apply", json={"script_id": sid, "plan": plan}))
    tl = (await c.get(f"/projects/{pid}/timeline")).json()
    roles = await container.design.clip_roles(pid)
    lts = sorted(
        (x for x in tl["clips"] if roles.get(x["id"]) == "lower_third"),
        key=lambda x: x["start_frame"],
    )
    segs = (await container.studio.detail(sid)).segments
    first = {}
    for x in segs:
        first.setdefault(x.speaker, x.start_frame)
    # A の最初の発話の間は題名が出ている。題名と同時に動かさないので、A の帯は題名の後に回し、
    # B の帯まで 1.5 秒も無ければ出さない（実機: 同時に 3 つ動いて検査で止まった）
    title = next(x for x in tl["clips"] if roles.get(x["id"]) == "title_card")
    assert all(x["start_frame"] >= title["end_frame"] for x in lts)
    assert lts[-1]["start_frame"] == first["B"] + 15
    assert all(x["end_frame"] - x["start_frame"] <= 5 * 30 for x in lts)
    assert all(a["end_frame"] <= b["start_frame"] for a, b in zip(lts, lts[1:], strict=False))
    assets = {a["id"]: a for a in tl["assets"]}
    b_img = Image.open(container.settings.data_dir / assets[lts[-1]["asset_id"]]["path"])
    assert b_img.height > 90 * 360 / 1080  # 肩書きの 2 行目がある
    assert lts[-1]["animation"]["preset"].startswith("slide_in_left")
    assert lts[-1]["transform"]["x"] < -0.3  # 左寄せ


async def _apply(c, pid, sid, purpose, **design):
    await c.put(f"/projects/{pid}/design", json={"purpose": purpose, **design})
    plan = (await c.post("/direction/plan", json={"script_id": sid, "use_llm": False})).json()[
        "plan"
    ]
    await _job(c, await c.post("/direction/apply", json={"script_id": sid, "plan": plan}))
    tl = (await c.get(f"/projects/{pid}/timeline")).json()
    roles = await c.app.state.container.design.clip_roles(pid)
    by: dict[str, list] = {}
    for x in sorted(tl["clips"], key=lambda x: x["start_frame"]):
        by.setdefault(roles.get(x["id"], ""), []).append(x)
    return tl, by


RECIPE = [
    {"speaker": "A", "text": "今日は2人分の野菜炒めを作ります。"},
    {"speaker": "A", "text": "まずキャベツを2枚と、にんじん1本を切ります。"},
    {"speaker": "A", "text": "次に塩少々を振って炒めます。"},
    {
        "speaker": "A",
        "text": "最後にお皿に盛り付けて、熱いうちにどうぞ召し上がってください。できあがりです。",
    },
]


async def test_recipe_ingredients_steps_and_list(dclient):
    """統合版 P4: 材料の発話の頭に「材料名 分量」2 秒、左上に STEP n、最後に材料の一覧のカード。"""
    c = dclient
    pid, sid = await _ready_script(c, RECIPE)
    tl, by = await _apply(c, pid, sid, "recipe")
    segs = (await c.app.state.container.studio.detail(sid)).segments
    ing = by["recipe_ingredient"]
    assert [x["start_frame"] for x in ing] == [segs[1].start_frame, segs[2].start_frame]
    assert all(x["end_frame"] - x["start_frame"] <= 60 for x in ing)
    steps = by["recipe_step"]
    assert [x["start_frame"] for x in steps] == [s.start_frame for s in segs[1:]]
    assert steps[0]["transform"]["x"] < -0.3 and steps[0]["transform"]["y"] < -0.3  # 左上
    (card,) = by["recipe_list"]
    assert card["end_frame"] == segs[-1].end_frame or card["end_frame"] >= segs[-1].end_frame - 1
    names = {t["name"] for t in tl["tracks"]}
    assert {"演出・部品", "演出・手順"} <= names
    # 作り直しても部品は重ならない（前の部品は消してから置く）
    _, by2 = await _apply(c, pid, sid, "recipe")
    assert len(by2["recipe_ingredient"]) == 2 and len(by2["recipe_list"]) == 1


RANKING = [
    {"speaker": "A", "text": "人気の街ランキングです。"},
    {"speaker": "A", "text": "第3位は京都です。古い町並みが人気です。"},
    {"speaker": "A", "text": "第2位は大阪です。食べ物がおいしいです。"},
    {"speaker": "A", "text": "第1位は東京です。何でもそろいます。"},
]


async def test_ranking_numbers_board_and_first_place_silence(dclient):
    """統合版 P5: 順位の大きな数字（ポン）→ ボード。1 位の前に 0.8 秒の無音。"""
    c = dclient
    pid, sid = await _ready_script(c, RANKING)
    container = c.app.state.container
    before = (await container.studio.detail(sid)).segments
    await c.put(f"/projects/{pid}/design", json={"purpose": "ranking"})
    await c.post("/subtitles/synchronize", json={"script_id": sid})
    after = (await container.studio.detail(sid)).segments
    gap_before = before[3].start_frame - before[2].end_frame
    gap_after = after[3].start_frame - after[2].end_frame
    assert gap_after - gap_before == 24  # 0.8 秒（30fps）
    assert after[2].start_frame - after[1].end_frame == before[2].start_frame - before[1].end_frame
    await c.post(f"/projects/{pid}/timeline/from_script", json={"script_id": sid})
    tl, by = await _apply(c, pid, sid, "ranking")
    nums = by["rank_number"]
    assert [x["start_frame"] for x in nums] == [s.start_frame for s in after[1:]]
    # 数字の間に始まるカードは、数字が消えてから出す（同じ所に重ねない）
    fig = next(t["id"] for t in tl["tracks"] if t["name"] == "演出・図")
    for n in nums:
        assert not any(
            x["track_id"] == fig and n["start_frame"] < x["start_frame"] < n["end_frame"]
            for x in tl["clips"]
        )
    assert all(x["animation"]["preset"] for x in nums)
    boards = by.get("rank_board", [])
    for b in boards:  # ボードの間はカードを出さない（同時に消えない）
        assert not any(
            x["track_id"] == fig and x["start_frame"] < b["start_frame"] < x["end_frame"]
            for x in tl["clips"]
        )
    for b in boards:
        assert not any(
            n["start_frame"] < b["end_frame"] and b["start_frame"] < n["end_frame"] for n in nums
        )


TRAILER = [
    {"speaker": "A", "text": "静かな町に、ある日。"},
    {"speaker": "A", "text": "一通の手紙が届いた。"},
    {"speaker": "A", "text": "それは、すべての始まりだった。"},
    {"speaker": "A", "text": "逃げろ。"},
    {"speaker": "A", "text": "2027年3月14日、公開。"},
]


async def test_trailer_title_after_act3_and_date_card(dclient):
    """統合版 P8: 予告編の題名は台詞の後、最後に台本の日付のカード。"""
    c = dclient
    pid, sid = await _ready_script(c, TRAILER)
    tl, by = await _apply(c, pid, sid, "trailer")
    segs = (await c.app.state.container.studio.detail(sid)).segments
    last = segs[-1].end_frame
    (title,) = [x for x in by["title_card"] if x["track_id"] != by["background"][0]["track_id"]][:1]
    assert title["start_frame"] >= last
    (date,) = by["trailer_date"]
    assert date["start_frame"] == title["start_frame"] + 90
    assert by["background"][-1]["end_frame"] >= date["end_frame"]
    # 統合版 L3・L7: 予告編の題名に光の筋と光沢
    from pathlib import Path

    log_ = Path(c.app.state.container.settings.blender_path).parent / "calls.jsonl"
    calls = [json.loads(x) for x in log_.read_text("utf-8").splitlines()]
    last_title = [x for x in calls if x["script"] == "title_card"][-1]
    assert last_title["sun_beams"] is True and last_title["gloss_at"] > 0


async def test_series_logo_op_and_check(dclient, media_dir):
    """統合版 Q4: シリーズのロゴ・OP（1 文目の直後に 3 秒）・色と書体の外れの warning。"""
    from PIL import Image

    c = dclient
    pid, sid = await _ready_script(c)
    logo = media_dir / "logo.png"
    Image.new("RGBA", (200, 100), (255, 0, 0, 255)).save(logo)
    op = media_dir / "op.png"
    Image.new("RGB", (640, 360), (0, 0, 255)).save(op)
    r = await c.post(
        "/design/series",
        json={
            "name": "週刊の解説",
            "colors": {"main": "#123456"},
            "logo_path": str(logo),
            "op_path": str(op),
            "end_screen": False,
        },
    )
    assert r.status_code == 201, r.text
    s = r.json()
    assert (await c.get("/design/series")).json()[0]["id"] == s["id"]
    bad = await c.put(
        f"/projects/{pid}/design", json={"purpose": "explainer", "series_id": "f" * 8}
    )
    assert bad.status_code == 404
    tl, by = await _apply(c, pid, sid, "explainer", series_id=s["id"])
    segs = (await c.app.state.container.studio.detail(sid)).segments
    (opc,) = by["series_op"]
    assert opc["start_frame"] == segs[0].end_frame and opc["end_frame"] - opc["start_frame"] == 90
    (lg,) = by["series_logo"]
    assert lg["transform"]["x"] > 0.3 and lg["transform"]["y"] < -0.3  # 右上
    assert "end_screen" not in by  # シリーズで終了画面を外した
    prof = await c.app.state.container.design.profile_for(pid)
    assert prof.palette.main == "#123456"
    d = (await c.get(f"/projects/{pid}/design")).json()
    assert d["series_id"] == s["id"]
    # 外す
    await c.put(f"/projects/{pid}/design", json={"purpose": "explainer", "series_id": ""})
    assert (await c.get(f"/projects/{pid}/design")).json()["series_id"] is None
    assert (await c.delete(f"/design/series/{s['id']}")).status_code == 204
    assert (await c.get(f"/design/series/{s['id']}")).status_code == 404


KIDS = [
    {"speaker": "A", "text": "今日は冒険のお話です。"},
    {"speaker": "A", "text": "山へ行きます。"},
]


async def test_kids_furigana_in_ass(dclient):
    """統合版 P7: 子ども向けは、3 年生までに習わない漢字にふりがな（読みは声のモーラ）。"""
    c = dclient
    pid, sid = await _ready_script(c, KIDS)
    await c.put(f"/projects/{pid}/design", json={"purpose": "kids"})
    ass = (await c.post("/subtitles/export", json={"script_id": sid, "format": "ass"})).json()
    rub = [x for x in ass["content"].split("\r\n") if x.startswith("Dialogue: 3,")]
    assert len(rub) == 1 and "\\pos(" in rub[0]  # 「冒険」だけ（今日・山・行は習う漢字）
    await c.put(f"/projects/{pid}/design", json={"purpose": "explainer"})
    ass2 = (await c.post("/subtitles/export", json={"script_id": sid, "format": "ass"})).json()
    assert "Dialogue: 3," not in ass2["content"]


async def test_music_genre_setting(dclient):
    """統合版 M7: 曲のジャンルを設定する（MV・リリックの歌詞の書体と質感）。"""
    c = dclient
    pid = (await c.post("/projects", json={"name": "G"})).json()["id"]
    r = await c.put(f"/projects/{pid}/design", json={"purpose": "mv", "genre": "edm"})
    assert r.status_code == 200 and r.json()["genre"] == "edm"
    assert (await c.put(f"/projects/{pid}/design", json={"genre": "jazz"})).status_code == 422
    r = await c.put(f"/projects/{pid}/design", json={"purpose": "mv"})
    assert r.json()["genre"] == "edm"  # 省くと前の設定のまま


async def test_hearing_aid_subtitles(dclient, tmp_path):
    """統合版 Q7: 選べる用途でオンにすると、BGM の頭と終わりに ♪ の字幕、話者が替わるたびに（名前）。"""
    from video_automation.schemas.timeline import (
        AddClip,
        AddTrack,
        Batch,
        CommandRequest,
        TrackKind,
    )

    c = dclient
    pid, sid = await _ready_script(c)
    container = c.app.state.container
    render = container.render
    # 解説（選べない用途）ではオンにしても付けない
    await c.put(f"/projects/{pid}/design", json={"purpose": "explainer", "hearing_aid": True})
    assert (await c.get(f"/projects/{pid}/design")).json()["hearing_aid"] is True
    ass = (await render._ass_for(sid, 640, 360, tmp_path / "a.ass")).read_text("utf-8-sig")
    assert "♪" not in ass and "（A）" not in ass
    # 字幕動画で、BGM を置いて書き出す
    await c.put(f"/projects/{pid}/design", json={"purpose": "subtitle"})
    tl = await container.timeline.get_timeline(pid)
    audio = {t.id for t in tl.tracks if t.kind == "audio"}
    voice = next(x for x in tl.clips if x.track_id in audio)
    import uuid

    tid = str(uuid.uuid4())
    await container.timeline.submit(
        pid,
        CommandRequest(
            command=Batch(
                commands=[
                    AddTrack(kind=TrackKind.AUDIO, name="BGM", track_id=tid),
                    *(
                        AddClip(
                            track_id=tid,
                            asset_id=voice.asset_id,
                            start_frame=start,
                            duration_frames=voice.end_frame - voice.start_frame,
                        )
                        for start in (0, 300)
                    ),
                ]
            )
        ),
    )
    ass = (await render._ass_for(sid, 640, 360, tmp_path / "b.ass")).read_text("utf-8-sig")
    assert ",♪～" in ass.replace("}", ",") and "～♪" in ass
    assert "（A）" in ass and "（B）" in ass
    # オフに戻すと付けない
    await c.put(f"/projects/{pid}/design", json={"hearing_aid": False})
    ass = (await render._ass_for(sid, 640, 360, tmp_path / "c.ass")).read_text("utf-8-sig")
    assert "♪" not in ass


async def test_emotion_subtitles_only_where_allowed(dclient, tmp_path):
    """統合版 K4: 台本の感情を保存し、感情の表現を使う用途（解説）の字幕だけ変える。"""
    from sqlalchemy import select

    from video_automation.database.models import ScriptSegment

    c = dclient
    pid, sid = await _ready_script(c)
    container = c.app.state.container
    async with container.db.session_factory() as s:
        segs = (
            (await s.execute(select(ScriptSegment).where(ScriptSegment.script_id == sid)))
            .scalars()
            .all()
        )
        segs = sorted(segs, key=lambda x: x.sequence)
        segs[0].emotion = "sad"
        await s.commit()
    await c.put(f"/projects/{pid}/design", json={"purpose": "explainer"})
    ass = (await container.render._ass_for(sid, 640, 360, tmp_path / "a.ass")).read_text(
        "utf-8-sig"
    )
    assert "\\fad(400," in ass
    await c.put(f"/projects/{pid}/design", json={"purpose": "news"})
    ass = (await container.render._ass_for(sid, 640, 360, tmp_path / "b.ass")).read_text(
        "utf-8-sig"
    )
    assert "\\fad(400," not in ass


async def test_podcast_waveform_per_speaker(dclient):
    """統合版 P2: ポッドキャストでは、話者ごとに話している声だけの波形を下寄りの中央に置く。"""
    import subprocess

    import numpy as np

    c = dclient
    pid, sid = await _ready_script(c)
    tl, by = await _apply(c, pid, sid, "podcast")
    waves = by.get("podcast_wave", [])
    assert len(waves) == 1  # 話者 A・B を色分けして 1 本
    # 台詞の終わりまで（終了画面の 15 秒には出さない）
    voice = max(
        x["end_frame"]
        for x in tl["clips"]
        if x["track_id"]
        in {t["id"] for t in tl["tracks"] if t["kind"] == "audio" and t["name"] != "BGM"}
    )
    assert all(x["start_frame"] == 0 and x["end_frame"] >= voice - 2 for x in waves)
    assert all(x["transform"]["y"] > 0.2 for x in waves)  # 画面の下寄り
    # 波形は透過で、話していない間は描かない。A と B で色が違う
    container = c.app.state.container
    from video_automation.database.models import Asset

    async with container.db.session_factory() as s:
        a_row = await s.get(Asset, waves[0]["asset_id"])
    path = container.settings.data_dir / a_row.path
    raw = subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgba",
            "-",
        ],
        capture_output=True,
        check=True,
    ).stdout
    w, h = a_row.width, a_row.height
    fr = np.frombuffer(raw, np.uint8).reshape(-1, h, w, 4)
    drawn = (fr[..., 3] > 0).reshape(len(fr), -1).sum(axis=1)
    assert drawn.max() > 0 and (drawn == 0).any()  # 話していない間は描かない
    lit = fr[fr[..., 3] > 0][:, :3]
    assert len({tuple(x) for x in lit[:: max(1, len(lit) // 500)]}) >= 2
    # 解説では波形を出さない
    _, by = await _apply(c, pid, sid, "explainer")
    assert not by.get("podcast_wave")


async def test_story_cutout_image_gets_parallax(dclient, media_dir):
    """統合版 M8: 朗読・物語の切り抜いた絵は、ケン・バーンズの代わりに横へ被写体の幅の 3% 動く。"""
    from PIL import Image

    c = dclient
    pid, sid = await _ready_script(c)
    img = Image.new("RGBA", (300, 400), (0, 0, 0, 0))
    img.paste((220, 120, 90, 255), (60, 40, 240, 380))
    src = media_dir / "cutout_m8.png"
    img.save(src)
    a = (await c.post("/assets/import", json={"project_id": pid, "path": str(src)})).json()
    plan = {
        "title": "",
        "subtitle": "",
        "shots": [{"kind": "image", "from_line": 2, "to_line": 4, "asset_id": a["id"]}],
    }

    async def image_clip(purpose):
        await c.put(f"/projects/{pid}/design", json={"purpose": purpose})
        await _job(c, await c.post("/direction/apply", json={"script_id": sid, "plan": plan}))
        tl = (await c.get(f"/projects/{pid}/timeline")).json()
        roles = await c.app.state.container.design.clip_roles(pid)
        return next(x for x in tl["clips"] if roles.get(x["id"]) == "image")

    clip = await image_clip("story")
    keys = clip["animation"]["keyframes"]
    xs = [k["x"] for k in keys if k.get("x") is not None]
    assert len(xs) == 2 and xs[0] < 0 < xs[1]
    assert abs((xs[1] - xs[0]) - 0.03 * 180 / 640 * (clip["transform"]["scale"] * 360 / 400)) < 0.02
    assert clip["animation"].get("preset") != "ken_burns_in"
    # 解説（2.5D を使わない用途）では今までどおりケン・バーンズ
    clip = await image_clip("explainer")
    assert not [k for k in clip["animation"]["keyframes"] if k.get("x")]


async def test_kids_background_is_memphis_then_liquid(dclient, monkeypatch):
    """統合版 R7: 子ども向けの背景はメンフィス柄。作れないときは M6 のメタボールの液体。"""
    from pathlib import Path

    c = dclient
    pid, sid = await _ready_script(c)
    await _apply(c, pid, sid, "kids")
    tl = (await c.get(f"/projects/{pid}/timeline")).json()
    assert any(a["name"] == "背景（R7 メンフィス柄）" for a in tl["assets"])

    async def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(type(c.app.state.container.direction), "_r_background", boom)
    await _apply(c, pid, sid, "kids")
    log_ = Path(c.app.state.container.settings.blender_path).parent / "calls.jsonl"
    calls = [json.loads(x) for x in log_.read_text("utf-8").splitlines()]
    assert [x["style"] for x in calls if x["script"] == "motion_bg"][-1] == "liquid"


async def test_translated_subtitles_written_separately(dclient):
    """統合版 W5: 訳した字幕は別の SRT に。速すぎる字幕は知らせる。"""
    from types import SimpleNamespace

    from video_automation.schemas.subtitle import SubtitleTranslateRequest

    c = dclient
    pid, sid = await _ready_script(c)
    container = c.app.state.container
    n = len(await container.subtitles.cues(sid))

    class Stub:
        async def complete_json(self, task, msgs, schema, **kw):
            import json as j

            lines = j.loads(msgs[-1].content)["lines"]
            text = "This sentence is quite long so that the reader may not keep up with it at all"
            return SimpleNamespace(data={"lines": ["Hi."] + [text] * (len(lines) - 1)})

    res = await container.subtitles.translate_export(
        SubtitleTranslateRequest(script_id=sid), Stub()
    )
    assert res.path.endswith(f"{sid}.en.srt") and res.cue_count == n
    assert "Hi." in res.content and res.warnings and "20" in res.warnings[0]
    assert all(len(x) <= 42 for x in res.content.splitlines())


async def test_cut_transitions_by_purpose(dclient):
    """統合版 E6（解説）・E7（Vlog）: 図のショットが替わる所に用途の切り替え。"""
    from video_automation.schemas.design import Purpose
    from video_automation.schemas.timeline import AddClip, SetClipAnimation
    from video_automation.services.design.profiles import PROFILES

    c = dclient
    pid, _ = await _ready_script(c)
    d = c.app.state.container.direction

    def shots():
        return [
            AddClip(track_id="fig", asset_id="a", start_frame=0, duration_frames=300, clip_id="x"),
            AddClip(
                track_id="fig", asset_id="a", start_frame=300, duration_frames=300, clip_id="y"
            ),
            AddClip(track_id="txt", asset_id="a", start_frame=600, duration_frames=30, clip_id="z"),
        ]

    cmds = shots()
    await d._cut_transitions(pid, PROFILES[Purpose.EXPLAINER], cmds, "fig", 900, 30.0, 640, 360,
                             lambda n: "cut")  # fmt: skip
    (ov,) = [x for x in cmds if isinstance(x, AddClip) and x.track_id == "cut"]
    assert (ov.start_frame, ov.duration_frames) == (300 - 9, 16)
    tl = (await c.get(f"/projects/{pid}/timeline")).json()
    assert any(a["name"] == "切り替え（E6）" and a["kind"] == "video" for a in tl["assets"])
    cmds = shots()
    await d._cut_transitions(pid, PROFILES[Purpose.VLOG], cmds, "fig", 900, 30.0, 640, 360,
                             lambda n: "cut")  # fmt: skip
    (an,) = [x for x in cmds if isinstance(x, SetClipAnimation)]
    assert an.clip_id == "y" and an.transition.type == "whip" and an.transition.duration_frames == 6


async def test_effects_by_purpose(dclient):
    """統合版 X1（イベントの題名の後の紙吹雪）・X2（商品紹介の題名のきらめき）・X5（予告編の煙）。"""
    c = dclient
    pid, sid = await _ready_script(c)
    for purpose, name in (
        ("event", "紙吹雪（X1）"),
        ("product", "きらめき（X2）"),
        ("trailer", "煙（X5）"),
    ):
        tl, _ = await _apply(c, pid, sid, purpose)
        tracks = {t["id"]: t["name"] for t in tl["tracks"]}
        names = {a["id"]: a["name"] for a in tl["assets"]}
        fx = [x for x in tl["clips"] if tracks[x["track_id"]] == "演出・効果"]
        assert fx and names[fx[0]["asset_id"]] == name, purpose


async def test_h_cards_story_and_news(dclient):
    """統合版 H6（その頃…の中間字幕）・H4（最後のクレジット）・H3（ニュースの帯とティッカー）。"""
    c = dclient
    lines = [
        {"speaker": "A", "text": "昔あるところに猫がいました。"},
        {"speaker": "B", "text": "その頃、町は雪でした。"},
        {"speaker": "A", "text": "猫は家に帰りました。"},
        {"speaker": "B", "text": "家ではおばあさんが待っていました。"},
        {"speaker": "A", "text": "二人は温かいスープを飲みました。"},
        {"speaker": "B", "text": "猫はゆっくり眠りました。おしまい。"},
    ]
    pid, sid = await _ready_script(c, lines)
    tl, _ = await _apply(c, pid, sid, "story")
    tracks = {t["id"]: t["name"] for t in tl["tracks"]}
    names = {a["id"]: a["name"] for a in tl["assets"]}
    close = [x for x in tl["clips"] if tracks[x["track_id"]] == "演出・締め"]
    kinds = sorted(names[x["asset_id"]] for x in close)
    assert kinds == ["クレジットロール（H4）", "中間字幕（H6）"]
    roll = next(x for x in close if names[x["asset_id"]].startswith("クレジット"))
    assert roll["end_frame"] == tl["duration_frames"] or roll["end_frame"] <= tl["duration_frames"]
    ys = [k["y"] for k in roll["animation"]["keyframes"]]
    assert ys[0] > 0.5 and ys[-1] < 0  # 下から上へ
    tl, _ = await _apply(c, pid, sid, "news")
    tracks = {t["id"]: t["name"] for t in tl["tracks"]}
    band = [x for x in tl["clips"] if tracks[x["track_id"]] == "演出・ニュースの帯"]
    tick = [x for x in tl["clips"] if tracks[x["track_id"]] == "演出・ティッカー"]
    assert band and tick and tick[0]["animation"]["keyframes"][-1]["x"] < 0


async def test_h1_chapter_cards_and_h5_closing(dclient):
    """統合版 H1（2 章目からの章の題名カード）・H5（PR の最後の 3 秒の締め）。"""
    from video_automation.schemas.design import Purpose
    from video_automation.schemas.direction import DirectionPlan
    from video_automation.schemas.timeline import AddClip, SetClipAnimation
    from video_automation.services.design.profiles import PROFILES

    c = dclient
    pid, _ = await _ready_script(c)
    d = c.app.state.container.direction
    cmds: list = []
    await d._h_cards(pid, PROFILES[Purpose.EXPLAINER], DirectionPlan(), [], [(0, "はじめに"),
                     (300, "仕組み"), (600, "まとめ")], cmds, lambda n: n, 900, 30.0, 640, 360,
                     None, "#FFFFFF")  # fmt: skip
    cards = [x for x in cmds if isinstance(x, AddClip) and x.track_id == "演出・章"]
    # 章が 3 つ以上なので、目次（N6・2 秒）の後に章の題名カード（H1・2.5 秒）
    assert [(x.start_frame, x.duration_frames) for x in cards] == [
        (300, 60), (600, 60), (360, 75), (660, 75),
    ]  # fmt: skip
    anims = [x for x in cmds if isinstance(x, SetClipAnimation)]
    assert anims[0].animation.keyframes[-1].opacity == 0.0
    cmds = []
    await d._h_cards(pid, PROFILES[Purpose.PROMO], DirectionPlan(title="新しい靴"), [], [], cmds,
                     lambda n: n, 900, 30.0, 640, 360, None, "#FFFFFF")  # fmt: skip
    (close,) = [x for x in cmds if isinstance(x, AddClip) and x.track_id == "演出・締め"]
    assert close.start_frame == 810 and close.duration_frames == 90


async def test_n_cards_from_script_words(dclient):
    """統合版 N1（まず・次に・最後に）・N7（〜とは）・N11（「」の引用）を台本の語だけで出す。"""
    c = dclient
    lines = [
        {"speaker": "A", "text": "光合成とは、植物が光で養分を作るはたらきです。"},
        {"speaker": "B", "text": "まず葉が光を受け取ります。"},
        {"speaker": "B", "text": "次に水を分けます。"},
        {"speaker": "B", "text": "最後に糖を作ります。"},
    ]
    pid, sid = await _ready_script(c, lines)
    tl, _ = await _apply(c, pid, sid, "explainer")
    tracks = {t["id"]: t["name"] for t in tl["tracks"]}
    names = {a["id"]: a["name"] for a in tl["assets"]}
    dg = sorted(
        (x for x in tl["clips"] if tracks[x["track_id"]] == "演出・図解"),
        key=lambda x: x["start_frame"],
    )
    got = [names[x["asset_id"]] for x in dg]
    assert got[0] == "用語の定義（N7）「光合成」"
    assert got[1:] == ["手順の流れ（N1）"] * 3
    for a, b in zip(dg, dg[1:], strict=False):
        assert a["end_frame"] <= b["start_frame"]
    q = [
        {
            "speaker": "A",
            "text": "彼は「失敗したことのない人は何も新しいことに挑戦していない」と言った。",
        }
    ]
    pid, sid = await _ready_script(c, q + lines[1:2])
    tl, _ = await _apply(c, pid, sid, "interview")
    names = {a["id"]: a["name"] for a in tl["assets"]}
    assert "引用カード（N11）" in names.values()


async def test_year_roll_in_documentary(dclient):
    """統合版 T43: 1 回目の年はフェード、2 回目からは前の年から回して右上に出す。"""
    c = dclient
    lines = [
        {"speaker": "A", "text": "1964年に東京で大会が開かれました。"},
        {"speaker": "B", "text": "そして2021年に再び開かれました。"},
    ]
    pid, sid = await _ready_script(c, lines)
    tl, _ = await _apply(c, pid, sid, "documentary")
    tracks = {t["id"]: t["name"] for t in tl["tracks"]}
    names = {a["id"]: a["name"] for a in tl["assets"]}
    ys = sorted(
        (x for x in tl["clips"] if tracks[x["track_id"]] == "演出・年号"),
        key=lambda x: x["start_frame"],
    )
    got = [names[x["asset_id"]] for x in ys]
    assert got == ["年号（T43）1964", "年号が回る（T43）2021", "年号（T43）2021"]
    assert ys[0]["transform"]["x"] > 0.3 and ys[0]["transform"]["y"] < -0.3  # 右上
    # 統合版 N4: 年が 3 つ以上なら、最後の文で年表
    lines.append({"speaker": "A", "text": "2030年にはまた新しい計画があります。"})
    pid, sid = await _ready_script(c, lines)
    tl, _ = await _apply(c, pid, sid, "documentary")
    assert "年表（N4）" in {a["name"] for a in tl["assets"]}
