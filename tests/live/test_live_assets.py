"""実機テスト（Phase 6）: 実在の素材サービスと ACE-Step 1.5。VA_LIVE_TESTS=1 のときだけ実行。

- Openverse / Wikimedia Commons（キー不要）は常に実行
- Pexels / Pixabay / Freesound は VA_PEXELS_API_KEY 等が設定されているときだけ実行
- ACE-Step は http://127.0.0.1:8001 が応答するときだけ BGM 生成まで実行
VA_LIVE_ASSET_OUT にフォルダを指定すると取得物とレポートを出力する。
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

pytestmark = pytest.mark.skipif(os.environ.get("VA_LIVE_TESTS") != "1", reason="live only")

CASES = [
    ("wikimedia", "image", "Mount Fuji"),
    ("openverse", "image", "mount fuji"),
    ("openverse", "audio", "bell"),
    ("wikimedia", "audio", "bell"),
    ("pexels", "image", "東京 夜景"),
    ("pexels", "video", "ocean waves"),
    ("pixabay", "image", "桜"),
    ("pixabay", "video", "clouds"),
    ("freesound", "audio", "door knock"),
]


async def test_live_assets_and_music(tmp_path):
    out_dir = Path(os.environ.get("VA_LIVE_ASSET_OUT") or tmp_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    settings = Settings(data_dir=tmp_path / "data")
    app = create_app(settings)
    report: dict = {"sources": {}, "cases": [], "music": None}
    try:
        async with LifespanManager(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://t/api/v1", timeout=1800
            ) as c:
                report["sources"] = {
                    s["name"]: s["configured"] for s in (await c.get("/asset_sources")).json()
                }
                pid = (await c.post("/projects", json={"name": "live-assets"})).json()["id"]
                ok_free = 0
                for source, mt, q in CASES:
                    case: dict = {"source": source, "media_type": mt, "query": q}
                    report["cases"].append(case)
                    if not report["sources"].get(source):
                        case["skipped"] = "APIキー未設定"
                        continue
                    t0 = time.perf_counter()
                    r = (
                        await c.post(
                            "/assets/search",
                            json={
                                "query": q,
                                "media_type": mt,
                                "sources": [source],
                                "per_page": 10,
                                "allowed_only": True,
                                "max_duration_sec": 60,
                            },
                        )
                    ).json()
                    case["search_sec"] = round(time.perf_counter() - t0, 2)
                    case["errors"] = r["errors"]
                    case["count"] = len(r["results"])
                    case["licenses"] = sorted({x["license"]["code"] for x in r["results"]})
                    if not r["results"]:
                        continue
                    x = r["results"][0]
                    case["first"] = {
                        k: x.get(k)
                        for k in (
                            "source_id",
                            "title",
                            "creator",
                            "width",
                            "height",
                            "duration_sec",
                            "attribution",
                        )
                    }
                    th = await c.post(
                        "/assets/thumbnail",
                        json={"source": source, "source_id": x["source_id"], "media_type": mt},
                    )
                    case["thumbnail"] = th.status_code
                    d = await c.post(
                        "/assets/download",
                        json={
                            "project_id": pid,
                            "source": source,
                            "source_id": x["source_id"],
                            "media_type": mt,
                        },
                    )
                    if d.status_code != 202:
                        case["download"] = {"http": d.status_code, "body": d.text[:300]}
                        continue
                    job = await app.state.jobs.wait(d.json()["job_id"])
                    case["download"] = {"status": job.status, "error": job.error}
                    if job.status == "SUCCEEDED":
                        a = job.result
                        case["asset"] = {
                            k: a.get(k)
                            for k in ("kind", "width", "height", "duration_sec", "has_audio")
                        }
                        src = settings.data_dir / a["path"]
                        shutil.copy(src, out_dir / f"asset_{source}_{mt}{src.suffix}")
                        if source in ("openverse", "wikimedia"):
                            ok_free += 1
                credits = (await c.get(f"/projects/{pid}/credits")).json()
                (out_dir / "credits.txt").write_text(credits["text"], encoding="utf-8")
                report["credits_entries"] = len(credits["entries"])

                eng = (await c.get("/music/engines")).json()
                report["music_engine"] = eng
                if eng["available"]:
                    t0 = time.perf_counter()
                    r = await c.post(
                        "/music/generate",
                        json={
                            "project_id": pid,
                            "duration_sec": 20,
                            "prompt": "calm lo-fi hip hop, soft piano, warm, relaxing, instrumental",
                        },
                    )
                    job = await app.state.jobs.wait(r.json()["id"])
                    report["music"] = {
                        "status": job.status,
                        "error": job.error,
                        "sec": round(time.perf_counter() - t0, 1),
                    }
                    assert job.status == "SUCCEEDED", job.error
                    a = job.result["assets"][0]
                    report["music"]["duration_sec"] = a["duration_sec"]
                    shutil.copy(settings.data_dir / a["path"], out_dir / "bgm_acestep.wav")
                    gens = (await c.get("/music/generations", params={"project_id": pid})).json()
                    report["music"]["dit_model"] = gens[0]["dit_model"]
                    report["music"]["lm_model"] = gens[0]["lm_model"]
                    assert abs(a["duration_sec"] - 20) < 2
                # キー不要の取得元で少なくとも1件は取得できること
                assert ok_free >= 1, report["cases"]
    finally:
        (out_dir / "live_assets_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
