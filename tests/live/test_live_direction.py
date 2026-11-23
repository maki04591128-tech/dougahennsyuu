"""実機テスト（演出プラン）。実 Ollama・VOICEVOX Nemo・Blender・FFmpeg を使う。

台本 → 音声 → 字幕 → 演出プラン（実 LLM）→ 適用（カード=FFmpeg、図表・タイトル=Blender）→
数フレームの静止画と短い動画を書き出して、見た目を確認できるようにする。
VA_LIVE_TESTS=1 のときだけ実行。VA_LIVE_DIRECTION_OUT にレポートと画像・動画を出力する。
"""

import json
import os
import shutil
import time
from pathlib import Path

import httpx
import pytest
from asgi_lifespan import LifespanManager

from video_automation.api.app import create_app
from video_automation.app.settings import Settings
from video_automation.services.direction.plan import describe_plan

pytestmark = pytest.mark.skipif(os.environ.get("VA_LIVE_TESTS") != "1", reason="live only")

LINES = [
    ("ミナ", "ハカセ、ダイヤモンドより硬い物質って本当にあるんですか？"),
    ("ハカセ", "候補の一つが、ウルツァイト窒化ホウ素、略してw-BNです。"),
    ("ハカセ", "2009年の計算では、w-BNの押し込み強度は114ギガパスカル。"),
    ("ハカセ", "同じ条件のダイヤモンドは97ギガパスカルで、w-BNの方が上でした。"),
    ("ミナ", "すごい！じゃあ、w-BNが一番硬いんですね？"),
    ("ハカセ", "ところが、2019年の実験では、ダイヤモンドの方が硬いという結果でした。"),
    ("ハカセ", "予測と実験の両方を見ることが大切ですね。"),
]


async def _job(c: httpx.AsyncClient, app, r: httpx.Response) -> dict:
    assert r.status_code == 202, r.text
    j = await app.state.jobs.wait(r.json()["id"])
    assert j.status == "SUCCEEDED", j.error
    return j.result


async def test_live_direction_plan_apply_render(tmp_path):
    out = Path(os.environ.get("VA_LIVE_DIRECTION_OUT") or tmp_path)
    out.mkdir(parents=True, exist_ok=True)
    settings = Settings(data_dir=tmp_path / "data")
    app = create_app(settings)
    report: dict = {"timings": {}}

    def lap(name: str, t0: float) -> None:
        report["timings"][name] = round(time.perf_counter() - t0, 1)

    async with LifespanManager(app, startup_timeout=180):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t/api/v1", timeout=1800
        ) as c:
            await c.post("/providers/discover")
            speakers = (await c.get("/voice/speakers")).json()
            styles = [s["styles"][0]["id"] for s in speakers[:2]]
            pid = (await c.post("/projects", json={"name": "direction"})).json()["id"]
            sc = (
                await c.post(
                    "/scripts/studio",
                    json={
                        "project_id": pid,
                        "mode": "MANUAL",
                        "title": "w-BNはダイヤモンドより硬い？",
                        "characters": [
                            {"name": "ミナ", "voice": {"style_id": styles[0]}},
                            {"name": "ハカセ", "voice": {"style_id": styles[-1]}},
                        ],
                    },
                )
            ).json()
            sid = sc["id"]
            await c.post(
                f"/scripts/{sid}/dialogue",
                json={"lines": [{"speaker": s, "text": t} for s, t in LINES]},
            )
            t0 = time.perf_counter()
            await _job(c, app, await c.post(f"/scripts/{sid}/voice"))
            lap("voice", t0)
            await c.post("/subtitles/generate", json={"script_id": sid})
            await c.post("/subtitles/synchronize", json={"script_id": sid})
            r = await c.post(f"/projects/{pid}/timeline/from_script", json={"script_id": sid})
            assert r.status_code == 201, r.text

            t0 = time.perf_counter()
            r = await c.post("/direction/plan", json={"script_id": sid})
            lap("plan (LLM)", t0)
            assert r.status_code == 200, r.text
            res = r.json()
            plan = res["plan"]
            report["model"] = res["model_key"]
            report["plan_text"] = describe_plan(plan)
            report["plan"] = plan

            t0 = time.perf_counter()
            applied = await _job(
                c, app, await c.post("/direction/apply", json={"script_id": sid, "plan": plan})
            )
            lap("apply (cards/charts/title)", t0)
            report["apply"] = applied
            assert applied["status"] == "APPLIED" and applied["clips"] >= 3

            tl = (await c.get(f"/projects/{pid}/timeline")).json()
            segs = (await c.get(f"/scripts/{sid}")).json()["segments"]
            frames = []
            for i in (0, 2, 3, 5, len(segs) - 1):
                s = segs[i]
                f = (s["start_frame"] + s["end_frame"]) // 2
                png = await c.post(
                    f"/projects/{pid}/preview",
                    json={"frame": f, "script_id": sid, "width": 1280},
                )
                assert png.status_code == 200 and png.content[:4] == b"\x89PNG", png.text[:300]
                name = f"frame_line{i + 1}.png"
                (out / name).write_bytes(png.content)
                frames.append(name)
            report["frames"] = frames

            t0 = time.perf_counter()
            render = await _job(
                c,
                app,
                await c.post(
                    "/render",
                    json={"project_id": pid, "script_id": sid, "profile": "fhd_landscape"},
                ),
            )
            lap("render", t0)
            shutil.copy(settings.data_dir / render["output_path"], out / "direction_demo.mp4")
            report["render"] = {k: render[k] for k in ("duration_sec", "encoder")}
            report["tracks"] = [t["name"] for t in tl["tracks"]]
    (out / "direction_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    kinds = {s["kind"] for s in plan["shots"]}
    assert kinds & {"card", "chart"}, report["plan_text"]
