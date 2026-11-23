"""実機テスト（Phase 7）: 実 Blender の文字アニメーション・図表、FFmpeg の演出・切替を書き出す。

VA_LIVE_TESTS=1 のときだけ実行。VA_LIVE_ANIM_OUT にフォルダを指定すると、動画・確認用の
コマ並べ画像・レポートを出力する。
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


def _ff(*args: str) -> None:
    subprocess.run([shutil.which("ffmpeg") or "ffmpeg", "-v", "error", "-y", *args], check=True)


async def test_live_animation(tmp_path):
    out_dir = Path(os.environ.get("VA_LIVE_ANIM_OUT") or tmp_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    media = tmp_path / "media"
    media.mkdir()
    _ff("-f", "lavfi", "-i", "testsrc2=s=1920x1080:d=1", "-frames:v", "1", str(media / "a.png"))
    _ff("-f", "lavfi", "-i", "smptehdbars=s=1920x1080:d=1", "-frames:v", "1", str(media / "b.png"))
    _ff(
        "-f",
        "lavfi",
        "-i",
        "mandelbrot=s=1280x720:r=30",
        "-t",
        "4",
        "-pix_fmt",
        "yuv420p",
        str(media / "c.mp4"),
    )
    settings = Settings(data_dir=tmp_path / "data", import_dirs=[media])
    app = create_app(settings)
    report: dict = {"titles": {}, "infographics": {}, "renders": {}}
    try:
        async with LifespanManager(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://t/api/v1", timeout=1800
            ) as c:
                report["engines"] = (await c.get("/render/engines")).json()
                pid = (await c.post("/projects", json={"name": "live-anim"})).json()["id"]

                async def job(r):
                    assert r.status_code == 202, r.text
                    j = await app.state.jobs.wait(r.json()["id"])
                    assert j.status == "SUCCEEDED", j.error
                    return j.result

                # 文字アニメーション（実 Blender）
                for anim, pos in (
                    ("pop", "center"),
                    ("typewriter", "lower_third"),
                    ("bounce", "center"),
                    ("zoom", "top"),
                    ("fade", "lower_third"),
                ):
                    t0 = time.perf_counter()
                    a = await job(
                        await c.post(
                            "/assets/title_card",
                            json={
                                "project_id": pid,
                                "text": "動画編集の自動化",
                                "duration_sec": 2.5,
                                "animation": anim,
                                "position": pos,
                                "box": pos == "lower_third",
                            },
                        )
                    )
                    report["titles"][anim] = {
                        "sec": round(time.perf_counter() - t0, 1),
                        "asset": a["id"],
                        "duration": a["duration_sec"],
                    }
                for kind in ("bar", "counter"):
                    t0 = time.perf_counter()
                    a = await job(
                        await c.post(
                            "/assets/infographic",
                            json={
                                "project_id": pid,
                                "kind": kind,
                                "title": "月間再生数",
                                "labels": ["1月", "2月", "3月"],
                                "values": [12000, 34000, 25500],
                                "unit": "回",
                                "duration_sec": 3,
                            },
                        )
                    )
                    report["infographics"][kind] = {
                        "sec": round(time.perf_counter() - t0, 1),
                        "asset": a["id"],
                    }

                imp = {}
                for n in ("a.png", "b.png", "c.mp4"):
                    imp[n] = (
                        await c.post(
                            "/assets/import", json={"project_id": pid, "path": str(media / n)}
                        )
                    ).json()["id"]
                # シーン: 色背景＋タイトル(bounce)＋ケンバーンズ → 画像(パン・ディゾルブ) → 動画(ワイプ)
                await job(
                    await c.post(
                        f"/projects/{pid}/scenes",
                        json={
                            "start_frame": 0,
                            "duration_sec": 2.5,
                            "title": "第1章 はじめに",
                            "title_animation": "bounce",
                            "camera_preset": "ken_burns_in",
                        },
                    )
                )
                await job(
                    await c.post(
                        f"/projects/{pid}/scenes",
                        json={
                            "start_frame": 75,
                            "duration_sec": 2.5,
                            "background_asset_id": imp["a.png"],
                            "camera_preset": "pan_left",
                            "transition": {"type": "dissolve", "duration_frames": 15},
                        },
                    )
                )
                await job(
                    await c.post(
                        f"/projects/{pid}/scenes",
                        json={
                            "start_frame": 150,
                            "duration_sec": 3,
                            "background_asset_id": imp["c.mp4"],
                            "camera_preset": "ken_burns_out",
                            "transition": {"type": "wipe_left", "duration_frames": 15},
                        },
                    )
                )
                tl = (await c.get(f"/projects/{pid}/timeline")).json()
                tracks = {t["name"]: t["id"] for t in tl["tracks"]}
                # テロップ（typewriter）と図表を重ねる。画像に要素アニメーション
                cmds = [
                    {
                        "type": "add_clip",
                        "track_id": tracks["テロップ"],
                        "asset_id": report["titles"]["typewriter"]["asset"],
                        "start_frame": 80,
                    },
                    {"type": "add_track", "kind": "video", "name": "図表", "track_id": "chart"},
                    {
                        "type": "add_clip",
                        "track_id": "chart",
                        "clip_id": "chart1",
                        "asset_id": report["infographics"]["bar"]["asset"],
                        "start_frame": 160,
                    },
                    {"type": "add_track", "kind": "video", "name": "画像", "track_id": "img"},
                    {
                        "type": "add_clip",
                        "track_id": "img",
                        "clip_id": "pic",
                        "asset_id": imp["b.png"],
                        "start_frame": 0,
                        "duration_frames": 60,
                        "transform": {"fit": "fit", "scale": 0.3, "x": 0.3, "y": -0.25},
                    },
                    {"type": "set_clip_animation", "clip_id": "pic", "preset": "spin_in"},
                    {"type": "set_clip_animation", "clip_id": "pic", "preset": "fade_out"},
                    {"type": "set_clip_animation", "clip_id": "chart1", "preset": "slide_in_right"},
                ]
                r = await c.post(
                    f"/projects/{pid}/commands",
                    json={"command": {"type": "batch", "commands": cmds}},
                )
                assert r.status_code == 201, r.text
                v = (await c.get(f"/projects/{pid}/validate")).json()
                report["validate"] = v
                assert v["valid"], v
                total = (await c.get(f"/projects/{pid}/timeline")).json()["duration_frames"]
                report["duration_frames"] = total

                for name, prof in (("fhd", "fhd_landscape"), ("4k_portrait", "4k_portrait")):
                    t0 = time.perf_counter()
                    r = (
                        await c.post(
                            "/render",
                            json={"project_id": pid, "profile": prof, "external_subtitles": False},
                        )
                    ).json()
                    j = await app.state.jobs.wait(r["job_id"])
                    assert j.status == "SUCCEEDED", j.error
                    src = settings.data_dir / j.result["output_path"]
                    dst = out_dir / f"live_anim_{name}.mp4"
                    shutil.copy(src, dst)
                    report["renders"][name] = {
                        "sec": round(time.perf_counter() - t0, 1),
                        "encoder": j.result["encoder"],
                        "size": f"{j.result['width']}x{j.result['height']}",
                        "duration": j.result["duration_sec"],
                    }
                    assert abs(j.result["duration_sec"] - total / 30) < 0.1
                # 確認用のコマ並べ（0.5秒ごと）
                fhd = out_dir / "live_anim_fhd.mp4"
                _ff(
                    "-i",
                    str(fhd),
                    "-vf",
                    "fps=2,scale=480:-2,tile=4x4",
                    "-frames:v",
                    "1",
                    str(out_dir / "live_anim_sheet.png"),
                )
                for anim, info in report["titles"].items():
                    p = settings.data_dir / next(
                        a["path"]
                        for a in (await c.get(f"/projects/{pid}/timeline")).json()["assets"]
                        if a["id"] == info["asset"]
                    )
                    _ff(
                        "-f",
                        "lavfi",
                        "-i",
                        "color=c=0x335577:s=1920x1080",
                        "-i",
                        str(p),
                        "-filter_complex",
                        "[0][1]overlay=shortest=1,fps=4,scale=480:-2,tile=5x2",
                        "-frames:v",
                        "1",
                        str(out_dir / f"title_{anim}.png"),
                    )
    finally:
        (out_dir / "live_anim_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
