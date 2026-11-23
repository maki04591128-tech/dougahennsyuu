"""実機テスト（読み間違い対策・抑揚づけ）。実 VOICEVOX Nemo と Ollama を使う。

解説動画の台本で見つかった読み誤り（六方晶→ロッポオ/アキラ、化合物→カゴオモノ、
w-BN のハイフンで間が空く等）が直ること、抑揚づけで音高の幅が広がることを確かめる。
VA_LIVE_TESTS=1 のときだけ実行。VA_LIVE_VOICEQ_OUT にレポート（JSON）と試聴用 WAV を出力する。
"""

import json
import os
import statistics
from pathlib import Path

import httpx
import pytest
from asgi_lifespan import LifespanManager

from video_automation.api.app import create_app
from video_automation.app.settings import Settings

pytestmark = pytest.mark.skipif(os.environ.get("VA_LIVE_TESTS") != "1", reason="live only")

LINES = [
    "例えば六方晶のh-BNは、黒鉛のような層状で柔らかいです。",
    "窒化ホウ素は、ホウ素と窒素が1対1で結合した化合物です。",
    "w-BNの押し込み強度は114GPa、ダイヤモンドの97GPaを約18%上回ります。",
    "『ダイヤモンドより硬い』は今のところ理論上の話です。",
    "すごい！じゃあ、本当に強かったんですね？",
]


def _kana(q: dict) -> str:
    return "".join(
        "".join(m["text"] for m in ap["moras"]) + ("、" if ap.get("pause_mora") else "/")
        for ap in q["accent_phrases"]
    )


def _spread(q: dict) -> float:
    p = [m["pitch"] for ap in q["accent_phrases"] for m in ap["moras"] if m["pitch"] > 0]
    return round(statistics.pstdev(p), 4) if len(p) > 1 else 0.0


async def test_live_reading_and_intonation(tmp_path):
    out = Path(os.environ.get("VA_LIVE_VOICEQ_OUT") or tmp_path)
    out.mkdir(parents=True, exist_ok=True)
    app = create_app(Settings(data_dir=tmp_path / "data"))
    report: dict = {"lines": []}
    async with LifespanManager(app, startup_timeout=120):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t/api/v1", timeout=900
        ) as c:
            await c.post("/providers/discover")
            speakers = (await c.get("/voice/speakers")).json()
            style = speakers[0]["styles"][0]["id"]
            for i, text in enumerate(LINES):
                speech = (await c.post("/voice/readings/preview", params={"text": text})).json()[
                    "speech"
                ]
                before = (
                    await c.post("/voice/accent_phrases", json={"text": text, "style_id": style})
                ).json()
                after = (
                    await c.post("/voice/accent_phrases", json={"text": speech, "style_id": style})
                ).json()
                plain = await c.post("/voice/preview", json={"text": text, "style_id": style})
                natural = await c.post(
                    "/voice/preview",
                    json={
                        "text": text,
                        "reading_text": speech,
                        "style_id": style,
                        "expressiveness": 1.0,
                    },
                )
                assert plain.status_code == 200 and natural.status_code == 200
                (out / f"line{i + 1}_before.wav").write_bytes(plain.content)
                (out / f"line{i + 1}_after.wav").write_bytes(natural.content)
                report["lines"].append(
                    {
                        "text": text,
                        "speech": speech,
                        "kana_before": _kana({"accent_phrases": before}),
                        "kana_after": _kana({"accent_phrases": after}),
                    }
                )
            # 抑揚の幅（音高の標準偏差）を、実際に合成に使うクエリで比べる
            sc_pid = (await c.post("/projects", json={"name": "vq"})).json()["id"]
            sc = (
                await c.post(
                    "/scripts/studio",
                    json={
                        "project_id": sc_pid,
                        "mode": "MANUAL",
                        "characters": [
                            {"name": "A", "voice": {"style_id": style}},
                            {"name": "B", "voice": {"style_id": style, "expressiveness": 0}},
                        ],
                    },
                )
            ).json()
            await c.post(
                f"/scripts/{sc['id']}/dialogue",
                json={
                    "lines": [
                        {"speaker": "A", "text": LINES[2]},
                        {"speaker": "B", "text": LINES[2]},
                        {"speaker": "A", "text": LINES[1]},
                    ]
                },
            )
            job = (await c.post(f"/scripts/{sc['id']}/voice")).json()
            j = await app.state.jobs.wait(job["id"])
            assert j.status == "SUCCEEDED", j.error
            audio = (await c.get("/voice/audio", params={"project_id": sc_pid})).json()
            by_text: dict = {}
            for a in audio:
                by_text.setdefault(a["text"], []).append(a)
            files = by_text[LINES[2]]
            assert len(files) == 2
            db = app.state.container.db
            from video_automation.database.models import AudioFile

            async with db.session_factory() as s:
                qs = [(await s.get(AudioFile, f["id"])).query for f in files]
            plain_q = next(q for q in qs if q.get("intonationScale") == 1.0)
            expr_q = next(q for q in qs if q is not plain_q)
            report["pitch_spread"] = {"plain": _spread(plain_q), "expressive": _spread(expr_q)}
            report["intonation_scale"] = expr_q.get("intonationScale")
            # 実際の合成クエリ（読み替え語をつないだ後）の句の区切り
            async with db.session_factory() as s:
                q2 = (await s.get(AudioFile, by_text[LINES[1]][0]["id"])).query
            report["synth_kana"] = {"line2": _kana(q2), "line3": _kana(expr_q)}
            # AI による読みの確認（実際の LLM。登録はしない）
            r = await c.post(f"/scripts/{sc['id']}/readings/check", json={"apply": False})
            report["ai_check"] = r.json() if r.status_code == 200 else {"error": r.text[:300]}
    (out / "voice_quality_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # 句の区切り（/）と息継ぎ（、）を除いた読みで判定する
    raw = [x["kana_after"] for x in report["lines"]]
    kana = [k.replace("/", "").replace("、", "") for k in raw]
    assert "アキラ" not in kana[0] and "ロッポ" in kana[0]
    assert "カゴオモノ" not in kana[1]
    assert "ダブリュウ、" not in raw[2] and "ギガパスカル" in kana[2]
    assert "カタイ、ワ" not in raw[3]
    # 読み替えた語が句の途中で切れていない
    synth = report["synth_kana"]
    assert "カゴ/ウブ" not in synth["line2"] and "ダブ/リュウ" not in synth["line3"]
    assert report["pitch_spread"]["expressive"] > report["pitch_spread"]["plain"]
