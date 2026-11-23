"""読み間違い対策と抑揚づけ: 読みのルール・AI による読みの確認・台本の音声生成への反映。"""

QWEN = "ollama-local::qwen3.5-9b-local:latest"


def _mora_text(q):
    return "".join(m["text"] for ap in q["accent_phrases"] for m in ap["moras"])


async def _studio_script(c, lines):
    await c.post("/providers/discover")
    pid = (await c.post("/projects", json={"name": "R"})).json()["id"]
    sc = (
        await c.post(
            "/scripts/studio",
            json={
                "project_id": pid,
                "mode": "MANUAL",
                "characters": [
                    {"name": "A", "voice": {"style_id": 10005}},
                    {"name": "B", "voice": {"style_id": 10007, "expressiveness": 0}},
                ],
            },
        )
    ).json()
    await c.post(f"/scripts/{sc['id']}/dialogue", json={"lines": lines})
    return pid, sc["id"]


async def test_reading_rules_and_voice_pipeline(client, fake):
    c = client
    pid, sid = await _studio_script(
        c,
        [
            {"speaker": "A", "text": "六方晶のw-BNは114GPa！"},
            {"speaker": "B", "text": "『窒化ホウ素』です。"},
        ],
    )
    r = await c.post("/voice/readings/preview", params={"text": "六方晶のw-BN", "project_id": pid})
    assert r.json()["speech"] == "ロッポウショウのダブリュービーエヌ"
    rule = (
        await c.post(
            "/voice/readings",
            json={"surface": "窒化ホウ素", "reading": "チッカホウソ", "project_id": pid},
        )
    ).json()
    assert rule["source"] == "user"
    bad = await c.post("/voice/readings", json={"surface": "x", "reading": "ひらがな"})
    assert bad.status_code == 422
    job = await c.app.state.jobs.wait((await c.post(f"/scripts/{sid}/voice")).json()["id"])
    assert job.status == "SUCCEEDED", job.error
    qa, qb = fake.nemo.synth_queries[-2:]
    # 読みのルール・略語・単位・括弧を整えた文で合成する（字幕の文は元のまま）
    assert _mora_text(qa) == "ロッポウショウのダブリュービーエヌは114ギガパスカル！"
    assert _mora_text(qb) == "チッカホウソです"
    # A は抑揚づけあり（音高が変わり、抑揚スケールの既定値）、B は強さ 0 でエンジンのまま
    pitches_a = [m["pitch"] for ap in qa["accent_phrases"] for m in ap["moras"]]
    assert len(set(pitches_a)) > 2 and qa["intonationScale"] == 1.15
    assert qb["intonationScale"] == 1.0
    segs = (await c.get(f"/scripts/{sid}")).json()["segments"]
    assert segs[1]["text"] == "『窒化ホウ素』です。"
    # 字幕の同期は読み上げた文のモーラ数で行う（エラーにならない）
    await c.post("/subtitles/generate", json={"script_id": sid})
    cues = (await c.post("/subtitles/synchronize", json={"script_id": sid})).json()
    assert all(x["start_frame"] is not None for x in cues)
    # ルールの更新・削除・一覧（共通ルールも含む）
    await c.post("/voice/readings", json={"surface": "相", "reading": "ソウ"})
    lst = (await c.get("/voice/readings", params={"project_id": pid})).json()
    assert {x["surface"] for x in lst} == {"窒化ホウ素", "相"}
    upd = await c.put(
        f"/voice/readings/{rule['id']}",
        json={
            "surface": "窒化ホウ素",
            "reading": "チッカホウソ",
            "enabled": False,
            "project_id": pid,
        },
    )
    assert upd.json()["enabled"] is False
    r = await c.post("/voice/readings/preview", params={"text": "窒化ホウ素", "project_id": pid})
    assert r.json()["speech"] == "窒化ホウ素"
    assert (await c.delete(f"/voice/readings/{rule['id']}")).status_code == 204
    assert (await c.delete(f"/voice/readings/{rule['id']}")).status_code == 404


async def test_ai_reading_check_registers_rules(client, fake):
    c = client
    pid, sid = await _studio_script(
        c,
        [
            {"speaker": "A", "text": "この相は準安定です。"},
            {"speaker": "B", "text": "爆薬の衝撃波で作ります。"},
        ],
    )
    seen = {}

    def responder(body):
        props = (body.get("format") or {}).get("properties", {})
        if "fixes" not in props:
            return None
        seen["prompt"] = body["messages"][-1]["content"]
        return {
            "fixes": [
                {"line": 1, "surface": "相", "reading": "そう"},  # ひらがなはカタカナにする
                {"line": 2, "surface": "存在しない語", "reading": "ソンザイ"},  # 文に無い → 捨てる
                {"line": 2, "surface": "衝撃波", "reading": "Shock"},  # カタカナでない → 捨てる
                {"line": 9, "surface": "相", "reading": "ソウ"},  # 行番号が範囲外 → 捨てる
            ]
        }

    fake.chat_responder = responder
    r = await c.post(f"/scripts/{sid}/readings/check", json={"model_key": QWEN})
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["checked"] == 2 and res["model_key"] == QWEN
    assert [(i["surface"], i["reading"]) for i in res["issues"]] == [("相", "ソウ")]
    assert res["issues"][0]["rule_id"]
    assert "[1] 文: この相は準安定です。" in seen["prompt"] and "読み:" in seen["prompt"]
    rules = (await c.get("/voice/readings", params={"project_id": pid})).json()
    assert [(x["surface"], x["source"]) for x in rules] == [("相", "llm")]
    # 登録後はすでにその読みになっているので、同じ指摘は出さない（apply=false も登録しない）
    r = await c.post(f"/scripts/{sid}/readings/check", json={"apply": False})
    assert r.json()["issues"] == []
    assert len((await c.get("/voice/readings", params={"project_id": pid})).json()) == 1
    assert (await c.post("/scripts/nope/readings/check", json={})).status_code == 404
