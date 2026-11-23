"""実機テスト（Phase 5）: 台本 → Nemo音声 → 字幕同期 → タイムライン → Blenderタイトル → MP4書き出し。

VA_LIVE_TESTS=1 のときだけ実行。VA_LIVE_RENDER_OUT にフォルダを指定すると成果物とレポートを出力する。
NVENC（RTX）・4K縦・Blender タイトルカードを実機で確認する。
"""

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import httpx
import pytest
from asgi_lifespan import LifespanManager

from video_automation.api.app import create_app
from video_automation.app.settings import Settings

pytestmark = pytest.mark.skipif(os.environ.get("VA_LIVE_TESTS") != "1", reason="live only")

SCRIPT = (
    "ナレーター：動画編集の自動化システム、フェーズ5の確認です。\n"
    "音声と字幕をタイムラインに並べて、一本の動画に書き出します。\n\n"
    "アシスタント：縦長の4K動画にも書き出してみます。"
)


def _probe(path: Path) -> dict:
    ffprobe = shutil.which("ffprobe") or "ffprobe"
    r = subprocess.run(
        [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return json.loads(r.stdout)


async def _render(c, app, body: dict) -> tuple[dict, float]:
    t0 = time.perf_counter()
    r = (await c.post("/render", json=body)).json()
    assert r["status"] in ("QUEUED", "RENDERING"), r
    job = await app.state.jobs.wait(r["job_id"])
    assert job.status == "SUCCEEDED", job.error
    return (await c.get(f"/render/{r['id']}")).json(), time.perf_counter() - t0


async def test_live_full_pipeline(tmp_path):
    out_dir = Path(os.environ.get("VA_LIVE_RENDER_OUT") or tmp_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    settings = Settings(data_dir=tmp_path / "data")
    app = create_app(settings)
    report: dict = {}
    try:
        async with LifespanManager(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://t/api/v1", timeout=1800
            ) as c:
                report["engines"] = (await c.get("/render/engines")).json()
                pid = (await c.post("/projects", json={"name": "live-render"})).json()["id"]
                sc = (
                    await c.post(
                        "/scripts",
                        json={
                            "project_id": pid,
                            "text": SCRIPT,
                            "default_style_id": 10005,
                            "speaker_styles": {"アシスタント": 10001},
                        },
                    )
                ).json()
                job = (await c.post(f"/scripts/{sc['id']}/voice")).json()
                done = await app.state.jobs.wait(job["id"])
                assert done.status == "SUCCEEDED", done.error
                await c.post("/subtitles/generate", json={"script_id": sc["id"]})
                await c.post("/subtitles/synchronize", json={"script_id": sc["id"]})
                r = await c.post(
                    f"/projects/{pid}/timeline/from_script", json={"script_id": sc["id"]}
                )
                assert r.status_code == 201, r.text

                # Blender タイトルカード（実機の Steam Blender）
                if report["engines"].get("blender"):
                    t0 = time.perf_counter()
                    tj = (
                        await c.post(
                            "/assets/title_card",
                            json={"project_id": pid, "text": "フェーズ5 確認", "duration_sec": 2},
                        )
                    ).json()
                    tdone = await app.state.jobs.wait(tj["id"])
                    report["title_card"] = {
                        "status": tdone.status,
                        "error": tdone.error,
                        "sec": round(time.perf_counter() - t0, 1),
                    }
                    assert tdone.status == "SUCCEEDED", tdone.error
                    asset = tdone.result
                    batch = {
                        "type": "batch",
                        "commands": [
                            {
                                "type": "add_track",
                                "kind": "video",
                                "name": "タイトル",
                                "track_id": "title",
                            },
                            {
                                "type": "add_clip",
                                "track_id": "title",
                                "asset_id": asset["id"],
                                "start_frame": 0,
                            },
                        ],
                    }
                    r = await c.post(f"/projects/{pid}/commands", json={"command": batch})
                    assert r.status_code == 201, r.text
                    shutil.copy(settings.data_dir / asset["path"], out_dir / "live_title_card.mov")

                v = (await c.get(f"/projects/{pid}/validate")).json()
                report["validate"] = v
                assert v["valid"], v
                tl = (await c.get(f"/projects/{pid}/timeline")).json()
                report["duration_frames"] = tl["duration_frames"]

                png = await c.post(
                    f"/projects/{pid}/preview",
                    json={"frame": 30, "script_id": sc["id"], "width": 960},
                )
                assert png.headers["content-type"] == "image/png"
                (out_dir / "live_preview_frame30.png").write_bytes(png.content)

                renders = {}
                for name, body in {
                    "fhd_landscape": {"profile": "fhd_landscape"},
                    "4k_portrait": {"profile": "4k_portrait"},
                    "fhd_x264": {"profile": "fhd_landscape", "encoder": "libx264"},
                }.items():
                    rr, sec = await _render(
                        c, app, {"project_id": pid, "script_id": sc["id"], **body}
                    )
                    src = settings.data_dir / rr["output_path"]
                    dst = out_dir / f"live_render_{name}.mp4"
                    shutil.copy(src, dst)
                    for sp in rr["subtitle_paths"]:
                        shutil.copy(
                            settings.data_dir / sp, out_dir / f"live_render_{name}{Path(sp).suffix}"
                        )
                    info = _probe(dst)
                    vs = next(s for s in info["streams"] if s["codec_type"] == "video")
                    au = [s for s in info["streams"] if s["codec_type"] == "audio"]
                    renders[name] = {
                        "encoder": rr["encoder"],
                        "sec": round(sec, 1),
                        "size": f"{vs['width']}x{vs['height']}",
                        "codec": vs["codec_name"],
                        "duration": float(info["format"]["duration"]),
                        "audio": bool(au),
                        "bytes": dst.stat().st_size,
                    }
                report["renders"] = renders
                exp = tl["duration_frames"] / 30
                assert renders["fhd_landscape"]["size"] == "1920x1080"
                assert renders["4k_portrait"]["size"] == "2160x3840"
                for x in renders.values():
                    assert x["audio"] and abs(x["duration"] - exp) < 0.2, x
                if report["engines"]["ffmpeg"]["nvenc"]:
                    assert renders["fhd_landscape"]["encoder"] == "h264_nvenc"
    finally:
        (out_dir / "live_render_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
