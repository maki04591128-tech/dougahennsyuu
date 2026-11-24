"""スキルの API と MCP（ローカル LLM と同じスキルを読める）。"""

from pathlib import Path


async def test_skills_api(client, settings):
    r = await client.get("/skills")
    assert r.status_code == 200
    names = {s["name"] for s in r.json()}
    assert "video-design-by-purpose" in names
    one = (await client.get("/skills/video-design-by-purpose")).json()
    assert one["body"] and one["headings"]
    assert (await client.get("/skills/nope")).status_code == 404
    r = await client.post(
        "/skills/search", json={"query": "MVっぽく拍に合わせてカット、歌詞を出す"}
    )
    body = r.json()
    assert body["context"].startswith("参考（スキル）") and body["sections"]
    assert sum(s["chars"] for s in body["sections"]) <= 4000
    # ユーザーのフォルダに置いて読み直すと使われる（同じ名前なら上書き）
    d = Path(settings.data_dir) / "skills" / "my-rule"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        '---\nname: "my-rule"\ndescription: "うちのチャンネルの決まり"\n---\n# r\n## 締め\n'
        "最後は必ずチャンネル登録のお願いで締める。\n",
        encoding="utf-8",
    )
    listed = (await client.post("/skills/reload")).json()
    assert any(s["name"] == "my-rule" and s["source"] == "user" for s in listed)
    r = await client.post("/skills/search", json={"query": "最後の締め方", "names": ["my-rule"]})
    assert "チャンネル登録" in r.json()["context"]
