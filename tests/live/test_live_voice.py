"""実機テスト（VOICEVOX Nemo）。VA_LIVE_TESTS=1 のときだけ実行。"""

import json
import os

import httpx
import pytest
from asgi_lifespan import LifespanManager

from video_automation.api.app import create_app
from video_automation.app.settings import Settings
from video_automation.schemas.voice import AudioQuery
from video_automation.services.voice.timing import estimated_duration

pytestmark = pytest.mark.skipif(os.environ.get("VA_LIVE_TESTS") != "1", reason="live only")
TEXTS = [
    "こんにちは。動画編集の自動化システムです。",
    "字幕と音声のタイミングを、正確に合わせます。",
    "本日は晴天なり、テスト、テスト。",
]


async def test_live_nemo(tmp_path):
    report: dict = {}
    app = create_app(Settings(data_dir=tmp_path))
    async with LifespanManager(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t/api/v1", timeout=600
        ) as c:
            engines = (await c.get("/voice/engines")).json()
            report["engines"] = engines
            nemo = next(e for e in engines if e["engine_id"] == "voicevox-nemo")
            assert nemo["available"], nemo
            speakers = (await c.get("/voice/speakers")).json()
            report["speakers"] = [(s["name"], [st["id"] for st in s["styles"]]) for s in speakers]
            style = speakers[0]["styles"][0]["id"]

            # 生成 + 区間時刻と実WAV長の整合
            r = await c.post(
                "/voice/generate",
                json={
                    "items": [{"text": t, "style_id": style} for t in TEXTS]
                    + [
                        {
                            "text": TEXTS[0],
                            "style_id": style,
                            "prosody": {"speed_scale": 1.5, "pause_length_scale": 0.5},
                        }
                    ]
                },
            )
            job = await app.state.jobs.wait(r.json()["id"])
            assert job.status == "SUCCEEDED", job.error
            report["audio"] = []
            for a in job.result:
                est = estimated_duration(AudioQuery.model_validate(await _row_query(app, a["id"])))
                diff = round(a["duration_sec"] - est, 4)
                report["audio"].append(
                    {
                        "text": a["text"],
                        "duration": a["duration_sec"],
                        "estimated": est,
                        "diff": diff,
                        "timings": a["timings"],
                    }
                )
                assert abs(diff) < 0.05, (a["text"], a["duration_sec"], est)

            # アクセント変更 → 再計算 → 生成
            aps = (
                await c.post("/voice/accent_phrases", json={"text": "動画", "style_id": style})
            ).json()
            before = [m["pitch"] for m in aps[0]["moras"]]
            aps[0]["accent"] = 1 if aps[0]["accent"] != 1 else len(aps[0]["moras"])
            re = (
                await c.post(
                    "/voice/accent_phrases/recompute",
                    json={"style_id": style, "accent_phrases": aps},
                )
            ).json()
            report["accent"] = {"before": before, "after": [m["pitch"] for m in re[0]["moras"]]}
            assert report["accent"]["before"] != report["accent"]["after"]

            # 辞書：登録 → 読みが変わる → 削除
            q1 = (
                await c.post("/voice/accent_phrases", json={"text": "鯖缶工房", "style_id": style})
            ).json()
            w = (
                await c.post(
                    "/voice/dictionary",
                    json={
                        "surface": "鯖缶工房",
                        "pronunciation": "サバカンコウボウ",
                        "accent_type": 0,
                        "priority": 9,
                    },
                )
            ).json()
            q2 = (
                await c.post("/voice/accent_phrases", json={"text": "鯖缶工房", "style_id": style})
            ).json()
            reading = lambda aps: "".join(m["text"] for a in aps for m in a["moras"])  # noqa: E731
            report["dictionary"] = {"before": reading(q1), "after": reading(q2)}
            assert reading(q2) == "サバカンコウボウ"
            assert (await c.delete(f"/voice/dictionary/{w['id']}")).status_code == 204
    out = os.environ.get("VA_LIVE_VOICE_REPORT")
    if out:
        with open(out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)


async def _row_query(app, audio_id):
    from video_automation.database.models import AudioFile

    async with app.state.db.session_factory() as s:
        return (await s.get(AudioFile, audio_id)).query
