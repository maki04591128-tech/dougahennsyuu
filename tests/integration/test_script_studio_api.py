"""対話型台本作成（Phase 8 §17）: 全自動・ユーザー片側入力・固定セリフ・再生成・版管理。"""

import json

QWEN = "ollama-local::qwen3.5-9b-local:latest"
CHARS = [
    {
        "name": "ずんだもん",
        "role": "解説役",
        "personality": "元気",
        "speaking_style": "語尾に「のだ」",
        "voice": {"style_id": 3, "speed_scale": 1.1},
    },
    {
        "name": "めたん",
        "role": "聞き役",
        "personality": "落ち着き",
        "speaking_style": "丁寧語",
        "voice": {"style_id": 2},
    },
]


def kind_of(body):
    props = (body.get("format") or {}).get("properties", {})
    for k in ("scenes", "replies", "candidates", "issues"):
        if k in props:
            return k
    return None


def prompt_of(body):
    return "\n".join(m["content"] for m in body["messages"])


async def _setup(client, mode="FULL_AI", chars=CHARS, settings=None):
    await client.post("/providers/discover")
    await client.put("/models/active", json={"model_key": QWEN})
    pid = (
        await client.post(
            "/projects",
            json={"name": "S", "width": 1920, "height": 1080, "fps_num": 30, "fps_den": 1},
        )
    ).json()["id"]
    r = await client.post(
        "/scripts/studio",
        json={
            "project_id": pid,
            "title": "AI入門",
            "mode": mode,
            "characters": chars,
            "settings": settings
            or {"topic": "AI動画編集", "target_duration_sec": 20, "must_keywords": ["自動化"]},
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def full_reply(extra_turns=()):
    return {
        "scenes": [
            {
                "purpose": "導入",
                "estimated_duration_sec": 8,
                "turns": [
                    {"speaker": "ずんだもん", "text": "今日は動画編集の自動化を紹介するのだ。"},
                    {"speaker": "めたん", "text": "どんなことができるのですか？"},
                    *extra_turns,
                ],
            },
            {
                "purpose": "まとめ",
                "estimated_duration_sec": 6,
                "turns": [
                    {"speaker": "ずんだもん", "text": "作業時間が半分になるのだ。"},
                    {"speaker": "謎の人", "text": "こんにちは"},
                ],
            },
        ],
        "notes": ["視聴者層が未指定のため初心者向けと仮定しました"],
    }


async def test_full_ai_generation_records_scenes_runs_and_versions(client, fake):
    sc = await _setup(client)
    assert sc["version"] == 1 and len(sc["characters"]) == 2
    assert sc["characters"][0]["voice"]["style_id"] == 3
    fake.chat_responder = lambda b: full_reply() if kind_of(b) == "scenes" else None
    r = await client.post(f"/scripts/{sc['id']}/generate", json={"instruction": "明るく"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert [s["purpose"] for s in d["scenes"]] == ["導入", "まとめ"]
    assert [t["speaker"] for t in d["segments"]] == ["ずんだもん", "めたん", "ずんだもん"]
    assert all(t["generation_source"] == "AI" for t in d["segments"])
    assert d["segments"][0]["voice_style_id"] == 3 and d["segments"][1]["voice_style_id"] == 2
    assert d["segments"][2]["scene_index"] == 1
    assert any("謎の人" in n for n in d["notes"]) and any("初心者" in n for n in d["notes"])
    assert d["estimated_total_sec"] > 0 and d["version"] == 2
    # 台本を作る呼び出し（後に続く推敲の呼び出しではなく）
    p = prompt_of(next(b for b in reversed(fake.chat_bodies) if kind_of(b) == "scenes"))
    assert "AI動画編集" in p and "語尾に「のだ」" in p and "明るく" in p
    runs = (await client.get(f"/scripts/{sc['id']}/runs")).json()
    assert runs[-1]["status"] == "SUCCEEDED" and runs[-1]["prompt_version"].startswith(
        "script.full"
    )
    assert runs[-1]["model_key"].startswith("ollama-local::")


async def test_locked_and_edited_lines_survive_full_regeneration(client, fake):
    sc = await _setup(client)
    sid = sc["id"]
    fake.chat_responder = lambda b: full_reply() if kind_of(b) == "scenes" else None
    d = (await client.post(f"/scripts/{sid}/generate", json={})).json()
    first, second, third = d["segments"]
    # 1行目を固定、2行目を手修正（EDITED）
    await client.put(f"/scripts/{sid}/dialogue/{first['id']}/lock", json={"locked": True})
    d = (
        await client.put(
            f"/scripts/{sid}/dialogue/{second['id']}", json={"text": "具体的に何が変わるのですか？"}
        )
    ).json()
    assert d["segments"][1]["generation_source"] == "EDITED"

    # モデルは固定行を落とし、手修正行は一字一句同じに含めてくる
    def regen(b):
        if kind_of(b) != "scenes":
            return None
        assert "具体的に何が変わるのですか？" in prompt_of(b)  # 固定セリフをプロンプトに含める
        return {
            "scenes": [
                {
                    "purpose": "全体",
                    "turns": [
                        {"speaker": "めたん", "text": "具体的に何が変わるのですか？"},
                        {"speaker": "ずんだもん", "text": "新しい説明なのだ。"},
                    ],
                }
            ],
            "notes": [],
        }

    fake.chat_responder = regen
    d = (await client.post(f"/scripts/{sid}/generate", json={})).json()
    by_id = {t["id"]: t for t in d["segments"]}
    assert by_id[first["id"]]["text"] == first["text"] and by_id[first["id"]]["locked"]
    assert by_id[second["id"]]["text"] == "具体的に何が変わるのですか？"
    assert third["id"] not in by_id  # 固定でない AI 行は作り直し
    assert any(t["text"] == "新しい説明なのだ。" for t in d["segments"])
    assert any("固定セリフ" in n for n in d["notes"])
    # 再生成は固定行を拒否
    r = await client.post(f"/scripts/{sid}/dialogue/{first['id']}/regenerate", json={})
    assert r.status_code == 409 and r.json()["code"] == "turn_is_fixed"


async def test_user_one_side_counterpart_keeps_user_lines_verbatim(client, fake):
    chars = [{**CHARS[0], "is_user": True}, CHARS[1]]
    sc = await _setup(
        client,
        "USER_ONE_SIDE",
        chars,
        {"topic": "自動化", "response_policy": "質問", "ai_ratio": 0.5},
    )
    sid = sc["id"]
    lines = ["今日は自動化の話をするのだ！", "まずは字幕から説明するのだ。", "最後にまとめるのだ。"]
    d = (
        await client.post(f"/scripts/{sid}/dialogue", json={"lines": [{"text": t} for t in lines]})
    ).json()
    assert [t["generation_source"] for t in d["segments"]] == ["USER"] * 3
    assert all(t["locked"] for t in d["segments"])

    def counterpart(b):
        if kind_of(b) != "replies":
            return None
        p = prompt_of(b)
        assert "[L1] ずんだもん: 今日は自動化の話をするのだ！" in p and "質問" in p
        return {
            "replies": [
                {"after": "L1", "turns": [{"text": "楽しみです。"}]},
                {"after": "L2", "turns": [{"text": "字幕は自動ですか？"}]},
                {"after": "L9", "turns": [{"text": "範囲外"}]},
            ],
            "notes": ["L3 への応答は不要と判断"],
        }

    fake.chat_responder = counterpart
    d = (await client.post(f"/scripts/{sid}/generate", json={})).json()
    texts = [(t["speaker"], t["text"]) for t in d["segments"]]
    assert texts == [
        ("ずんだもん", lines[0]),
        ("めたん", "楽しみです。"),
        ("ずんだもん", lines[1]),
        ("めたん", "字幕は自動ですか？"),
        ("ずんだもん", lines[2]),
    ]
    user_ids = [t["id"] for t in d["segments"] if t["speaker"] == "ずんだもん"]
    assert d["segments"][1]["reply_to_segment_id"] == user_ids[0]
    assert any("L9" in n for n in d["notes"])
    v = (await client.post(f"/scripts/{sid}/validate", json={})).json()
    assert not any(i["code"] == "user_line_changed" for i in v["issues"])
    assert v["ratio_ai_to_user"] is not None

    # 応答を手修正して固定 → 次の生成ではその入力への新しい応答を求めない
    reply = d["segments"][1]
    await client.put(f"/scripts/{sid}/dialogue/{reply['id']}", json={"text": "わくわくします。"})
    fake.chat_responder = lambda b: (
        {"replies": [{"after": "L2", "turns": [{"text": "新しい質問？"}]}], "notes": []}
        if kind_of(b) == "replies"
        and "L1" not in prompt_of(b).split("応答が必要な入力セリフ:")[1].split("。")[0]
        else None
    )
    d = (await client.post(f"/scripts/{sid}/generate", json={})).json()
    assert [t["text"] for t in d["segments"]] == [
        lines[0],
        "わくわくします。",
        lines[1],
        "新しい質問？",
        lines[2],
    ]


async def test_regenerate_candidates_apply_versions_diff_restore(client, fake):
    sc = await _setup(client)
    sid = sc["id"]
    fake.chat_responder = lambda b: full_reply() if kind_of(b) == "scenes" else None
    d = (await client.post(f"/scripts/{sid}/generate", json={})).json()
    target = d["segments"][1]

    fake.chat_responder = lambda b: (
        {"candidates": ["案A", "案B", "案C"]} if kind_of(b) == "candidates" else None
    )
    r = await client.post(
        f"/scripts/{sid}/dialogue/{target['id']}/regenerate",
        json={"candidates": 2, "instruction": "短く"},
    )
    c = r.json()
    assert c["candidates"] == ["案A", "案B"]  # 求めた数だけ
    p = prompt_of(fake.chat_bodies[-1])
    assert "今日は動画編集の自動化を紹介するのだ。" in p and "短く" in p  # 前後の文脈
    d = (
        await client.post(f"/scripts/{sid}/dialogue/{target['id']}/apply", json={"text": "案B"})
    ).json()
    assert d["segments"][1]["text"] == "案B" and d["segments"][1]["generation_source"] == "AI"

    vers = (await client.get(f"/scripts/{sid}/versions")).json()
    assert [v["version"] for v in vers] == [1, 2, 3]
    diff = (
        await client.get(
            f"/scripts/{sid}/versions/diff", params={"from_version": 2, "to_version": 3}
        )
    ).json()
    assert diff["changes"] == [
        {
            "kind": "changed",
            "turn_id": target["id"],
            "speaker": "めたん",
            "before": "どんなことができるのですか？",
            "after": "案B",
        }
    ]
    d = (await client.post(f"/scripts/{sid}/versions/2/restore")).json()
    assert d["segments"][1]["text"] == "どんなことができるのですか？" and d["version"] == 4


async def test_validation_rules_and_llm_review(client, fake):
    sc = await _setup(
        client,
        settings={
            "topic": "x",
            "target_duration_sec": 60,
            "must_keywords": ["字幕"],
            "banned": ["絶対"],
        },
    )
    sid = sc["id"]
    await client.post(
        f"/scripts/{sid}/dialogue",
        json={
            "lines": [
                {"speaker": "ずんだもん", "text": "絶対に便利なのだ。"},
                {"speaker": "めたん", "text": "絶対に便利なのだ。"},
            ]
        },
    )
    fake.chat_responder = lambda b: (
        {"issues": [{"turn": "T2", "level": "warning", "message": "めたんの口調が丁寧語でない"}]}
        if kind_of(b) == "issues"
        else None
    )
    v = (await client.post(f"/scripts/{sid}/validate", json={"use_llm": True})).json()
    codes = {i["code"] for i in v["issues"]}
    assert {"banned_word", "duplicate", "missing_keyword", "duration", "llm_review"} <= codes
    assert v["valid"] is False
    llm = next(i for i in v["issues"] if i["code"] == "llm_review")
    seg2 = (await client.get(f"/scripts/{sid}/detail")).json()["segments"][1]
    assert llm["turn_id"] == seg2["id"]


async def test_invalid_llm_output_is_retried_then_reported(client, fake):
    sc = await _setup(client)
    calls = []

    def bad(b):
        calls.append(1)
        return "<think>考え中</think>これはJSONではありません"

    fake.chat_responder = bad
    r = await client.post(f"/scripts/{sc['id']}/generate", json={})
    assert r.status_code == 502 and r.json()["code"] == "llm_output_invalid"
    assert len(calls) == 2  # 1回だけ再試行
    assert "スキーマに合いません" in prompt_of(fake.chat_bodies[-1])  # 誤りを伝えて再試行
    runs = (await client.get(f"/scripts/{sc['id']}/runs")).json()
    assert runs[-1]["status"] == "FAILED"
    # 台本は変わらない
    d = (await client.get(f"/scripts/{sc['id']}/detail")).json()
    assert d["segments"] == [] and d["version"] == 1

    # 2回目で直れば成功
    seq = iter(['{"scenes": 1}', json.dumps(full_reply(), ensure_ascii=False)])
    # 台本の生成だけ順に答える（推敲などの後の呼び出しは既定の応答）
    fake.chat_responder = lambda b: next(seq, None) if kind_of(b) == "scenes" else None
    r = await client.post(f"/scripts/{sc['id']}/generate", json={})
    assert r.status_code == 200 and len(r.json()["segments"]) == 3


async def test_mode_validation_and_character_voice_propagation(client, fake):
    await client.post("/providers/discover")
    pid = (
        await client.post(
            "/projects",
            json={"name": "S", "width": 1920, "height": 1080, "fps_num": 30, "fps_den": 1},
        )
    ).json()["id"]
    r = await client.post(
        "/scripts/studio", json={"project_id": pid, "mode": "USER_ONE_SIDE", "characters": CHARS}
    )
    assert r.status_code == 422  # is_user が無い
    sc = await _setup(client)
    await client.post(
        f"/scripts/{sc['id']}/dialogue",
        json={"lines": [{"speaker": "めたん", "text": "こんにちは"}]},
    )
    r = await client.post(
        f"/scripts/{sc['id']}/dialogue", json={"lines": [{"speaker": "誰か", "text": "x"}]}
    )
    assert r.status_code == 422
    chars = [CHARS[0], {**CHARS[1], "voice": {"style_id": 8, "pitch_scale": 0.05}}]
    d = (await client.put(f"/scripts/{sc['id']}", json={"characters": chars})).json()
    assert d["segments"][0]["voice_style_id"] == 8
    assert d["characters"][1]["voice"]["pitch_scale"] == 0.05
    # 旧形式の台本 API は互換のまま
    old = await client.post("/scripts", json={"project_id": pid, "text": "A：こんにちは"})
    assert old.status_code == 201


async def test_generation_enforces_each_characters_persona(client, fake):
    chars = [
        {**CHARS[0], "speaking_style": "一人称は「僕」。相手は「オマエ」と呼ぶ。語尾は「〜のだ」"},
        {**CHARS[1], "speaking_style": "一人称は「わたくし」。相手は「アンタ」と呼ぶ"},
    ]
    sc = await _setup(client, chars=chars)
    reply = {
        "scenes": [
            {
                "purpose": "導入",
                "turns": [
                    {"speaker": "ずんだもん", "text": "これは便利だよ。本当にすごいでしょ。"},
                    {"speaker": "めたん", "text": "僕もそう思うわ。あなたはどう？"},
                ],
            }
        ],
        "notes": [],
    }
    fixes = {"fixes": [{"id": "T1", "text": "これは便利なのだ。本当にすごいのだ。"}]}

    def responder(b):
        props = (b.get("format") or {}).get("properties", {})
        if "fixes" in props:
            return fixes
        return reply if kind_of(b) == "scenes" else None

    fake.chat_responder = responder
    r = await client.post(f"/scripts/{sc['id']}/generate", json={})
    assert r.status_code == 200, r.text
    d = r.json()
    texts = [t["text"] for t in d["segments"]]
    assert texts == ["これは便利なのだ。本当にすごいのだ。", "わたくしもそう思うわ。アンタはどう？"]
    assert any("取り違え" in n for n in d["notes"]) and any("書き直し" in n for n in d["notes"])
    # 生成の指示に口調の規則が入る
    first = next(b for b in fake.chat_bodies if kind_of(b) == "scenes")
    assert "一人称は「わたくし」だけ" in prompt_of(first)
    # 確認でも口調の問題を指摘する（手で戻した場合）
    tid = d["segments"][1]["id"]
    r = await client.put(f"/scripts/{sc['id']}/dialogue/{tid}", json={"text": "僕はそう思うわ"})
    assert r.status_code == 200, r.text
    v = (await client.post(f"/scripts/{sc['id']}/validate", json={})).json()
    assert any(i["code"] == "persona_first_person" and i["turn_id"] == tid for i in v["issues"])


async def test_far_too_long_script_is_rewritten_shorter_once(client, fake):
    sc = await _setup(client, settings={"topic": "x", "target_duration_sec": 10})
    long_turn = {"speaker": "めたん", "text": "とても長い説明をします。" * 12}
    asked = []

    def responder(b):
        if kind_of(b) != "scenes":
            return None
        p = prompt_of(b)
        asked.append("長すぎます" in p)
        if "長すぎます" in p:
            assert "実際の項目の数に合わせて" in p
            return json.dumps(full_reply(), ensure_ascii=False)
        return json.dumps(full_reply([long_turn] * 3), ensure_ascii=False)

    fake.chat_responder = responder
    r = await client.post(f"/scripts/{sc['id']}/generate", json={})
    assert r.status_code == 200, r.text
    assert asked == [False, True]  # 1 回だけ書き直させる
    texts = [s["text"] for s in r.json()["segments"]]
    assert long_turn["text"] not in texts and len(texts) == 3


async def test_fact_check_fixes_wrong_lines_keeping_persona(client, fake):
    chars = [
        {**CHARS[0], "speaking_style": "一人称は「僕」。語尾は「〜のだ」"},
        {**CHARS[1], "speaking_style": "語尾は「です」・「ます」"},
    ]
    sc = await _setup(client, chars=chars)
    sid = sc["id"]
    await client.post(
        f"/scripts/{sid}/dialogue",
        json={
            "lines": [
                {"speaker": "ずんだもん", "text": "コンセントを抜くと電気が貯まるのだ。"},
                {"speaker": "めたん", "text": "なるほど、そうなのです。"},
            ]
        },
    )
    seen = []

    def responder(b):
        props = (b.get("format") or {}).get("properties", {})
        if "fixes" not in props or "problem" not in str(b.get("format")):
            return None
        seen.append(prompt_of(b))
        return {
            "fixes": [
                {
                    "id": "T1",
                    "problem": "抜いても電気は貯まらない",
                    "text": "コンセントを抜くと待機電力を減らせるのだ。",
                },
                # 口調を外した直しは採らない
                {"id": "T2", "problem": "x", "text": "なるほどなのだ。"},
            ]
        }

    fake.chat_responder = responder
    c = client.app.state.container
    # 手で入れたセリフは固定（直さない）なので、AI 由来にしてから確かめる
    from sqlalchemy import update

    from video_automation.database.models import ScriptSegment

    async with c.db.session_factory() as s:
        await s.execute(
            update(ScriptSegment)
            .where(ScriptSegment.script_id == sid)
            .values(generation_source="AI", locked=False)
        )
        await s.commit()
    notes = await c.studio.fact_check(sid)
    assert notes and "1 か所" in notes[0] and "貯まらない" in notes[0]
    assert "[T1] ずんだもん: コンセントを抜くと電気が貯まるのだ。" in seen[0]
    texts = [t.text for t in (await c.studio.detail(sid)).segments]
    assert texts[0] == "コンセントを抜くと待機電力を減らせるのだ。"
    assert texts[1] == "なるほど、そうなのです。"


async def test_too_short_script_is_lengthened_once(client, fake):
    """目標の尺の 8 割に届かない台本は、1 回だけ内容を書き足させる（実機: 60 秒に 42 秒）。"""
    sc = await _setup(client, settings={"topic": "節電", "target_duration_sec": 60})
    asks = []

    def responder(b):
        if kind_of(b) != "scenes":
            return None
        text = prompt_of(b)
        asks.append("短すぎます" in text)
        n = 12 if "短すぎます" in text else 3
        turns = [
            {"speaker": ["ずんだもん", "めたん"][i % 2], "text": f"節電のコツその{i}を話すのだ。"}
            for i in range(n)
        ]
        return {"scenes": [{"purpose": "本題", "turns": turns}], "notes": []}

    fake.chat_responder = responder
    d = (await client.post(f"/scripts/{sc['id']}/generate", json={})).json()
    assert asks == [False, True]
    assert len(d["segments"]) == 12
    assert any("書き足させました" in n for n in d["notes"])
