"""実機 E2E と性能測定（P9-01 / P9-06）。

実 Ollama・VOICEVOX Nemo・FFmpeg・Blender で、台本の AI 生成 → 音声 → 字幕同期 → 配置 →
タイトル（Blender）→ AI アシスタントの編集（承認）→ 書き出し → 品質検査 を通し、各工程の時間と
GPU メモリ・RAM を記録する。

VA_LIVE_TESTS=1 のときだけ実行。VA_LIVE_E2E_OUT に出力フォルダ（性能レポート・動画・品質検査結果）。
VA_LIVE_MODELS で使うモデル（model_id の一部）を指定できる（既定はタスク割当）。
"""

import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
from asgi_lifespan import LifespanManager

from video_automation.api.app import create_app
from video_automation.app.settings import APP_VERSION, Settings

pytestmark = pytest.mark.skipif(os.environ.get("VA_LIVE_TESTS") != "1", reason="live only")


def _ff(*args: str) -> None:
    subprocess.run([shutil.which("ffmpeg") or "ffmpeg", "-v", "error", "-y", *args], check=True)


def _gpu() -> dict | None:
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.used,memory.total,driver_version",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None
    if not out:
        return None
    name, used, total, drv = [x.strip() for x in out.splitlines()[0].split(",")]
    return {"name": name, "used_mb": int(used), "total_mb": int(total), "driver": drv}


def _ram() -> dict | None:
    if sys.platform != "win32":
        return None
    import ctypes

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    m = MEMORYSTATUSEX()
    m.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))  # type: ignore[attr-defined]
    return {"total_gb": round(m.ullTotalPhys / 2**30, 1), "used_pct": int(m.dwMemoryLoad)}


class Sampler:
    """工程ごとの GPU メモリ・RAM 使用率の最大値を 0.5 秒間隔で記録する。"""

    def __init__(self) -> None:
        self.peak_gpu = 0
        self.peak_ram = 0
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.is_set():
            g, r = _gpu(), _ram()
            if g:
                self.peak_gpu = max(self.peak_gpu, g["used_mb"])
            if r:
                self.peak_ram = max(self.peak_ram, r["used_pct"])
            self._stop.wait(0.5)

    def __enter__(self) -> "Sampler":
        self._t.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        self._t.join(timeout=15)


async def test_live_end_to_end_and_performance(tmp_path):
    out_dir = Path(os.environ.get("VA_LIVE_E2E_OUT") or tmp_path / "out")
    out_dir.mkdir(parents=True, exist_ok=True)
    media = tmp_path / "media"
    media.mkdir()
    _ff(
        "-f",
        "lavfi",
        "-i",
        "gradients=s=1920x1080:c0=0x203060:c1=0x6080c0:d=1",
        "-frames:v",
        "1",
        str(media / "bg.png"),
    )
    settings = Settings(data_dir=tmp_path / "data", import_dirs=[media])
    app = create_app(settings)
    steps: list[dict] = []
    report: dict = {
        "app_version": APP_VERSION,
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "gpu": _gpu(),
        "ram": _ram(),
        "steps": steps,
    }

    async def step(name: str, coro):
        t0 = time.perf_counter()
        with Sampler() as smp:
            result = await coro
        steps.append(
            {
                "step": name,
                "sec": round(time.perf_counter() - t0, 1),
                "peak_gpu_mb": smp.peak_gpu or None,
                "peak_ram_pct": smp.peak_ram or None,
            }
        )
        (out_dir / "e2e_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return result

    async with LifespanManager(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t/api/v1", timeout=1800
        ) as c:
            jobs = app.state.jobs

            async def job(r):
                assert r.status_code == 202, r.text
                body = r.json()
                j = await jobs.wait(body.get("job_id") or body["id"])
                assert j.status == "SUCCEEDED", j.error
                return j.result

            disc = await step("モデル検出", c.post("/providers/discover"))
            models = [m for m in disc.json()["models"] if m["provider_type"] == "ollama"]
            only = [x for x in os.environ.get("VA_LIVE_MODELS", "").split(",") if x]
            key = next((m["key"] for m in models if any(o in m["model_id"] for o in only)), None)
            report["model"] = key or "（タスク割当）"
            speakers = (await c.get("/voice/speakers")).json()
            styles = [st["id"] for sp in speakers for st in sp["styles"]][:2]
            assert len(styles) >= 1, "Nemo の話者が取得できません"
            pid = (
                await c.post("/projects", json={"name": "E2E", "fps_num": 30, "fps_den": 1})
            ).json()["id"]

            sc = (
                await c.post(
                    "/scripts/studio",
                    json={
                        "project_id": pid,
                        "title": "ローカルAIで動画編集",
                        "mode": "FULL_AI",
                        "settings": {
                            "topic": "ローカルAIで動画編集を自動化するメリット",
                            "audience": "動画編集の初心者",
                            "target_duration_sec": 30,
                        },
                        "characters": [
                            {
                                "name": "解説",
                                "role": "解説役",
                                "speaking_style": "丁寧語",
                                "voice": {"style_id": styles[0]},
                            },
                            {
                                "name": "聞き手",
                                "role": "質問役",
                                "speaking_style": "くだけた口調",
                                "voice": {"style_id": styles[-1]},
                            },
                        ],
                    },
                )
            ).json()
            sid = sc["id"]
            r = await step(
                "台本の AI 生成", c.post(f"/scripts/{sid}/generate", json={"model_key": key})
            )
            assert r.status_code == 200, r.text
            d = r.json()
            report["script"] = {
                "turns": len(d["segments"]),
                "estimated_sec": d["estimated_total_sec"],
                "lines": [f"{t['speaker']}: {t['text']}" for t in d["segments"]],
            }
            await step("音声生成（Nemo）", job(await c.post(f"/scripts/{sid}/voice")))
            await step(
                "字幕の生成・同期",
                c.post("/subtitles/generate", json={"script_id": sid}),
            )
            await c.post("/subtitles/synchronize", json={"script_id": sid})
            r = await c.post(f"/projects/{pid}/timeline/from_script", json={"script_id": sid})
            assert r.status_code == 201, r.text
            title = await step(
                "タイトル（Blender）",
                job(
                    await c.post(
                        "/assets/title_card",
                        json={
                            "project_id": pid,
                            "text": "ローカルAIで動画編集",
                            "duration_sec": 3,
                            "animation": "pop",
                        },
                    )
                ),
            )
            bg = (
                await c.post(
                    "/assets/import", json={"project_id": pid, "path": str(media / "bg.png")}
                )
            ).json()
            tl = (await c.get(f"/projects/{pid}/timeline")).json()
            total = tl["duration_frames"]
            report["timeline_sec"] = round(total / 30, 1)
            conv = (await c.post("/conversations", json={"project_id": pid})).json()
            m = await step(
                "AI アシスタントの編集計画",
                c.post(
                    f"/conversations/{conv['id']}/messages",
                    json={
                        "text": "新しい映像トラック「背景」を作り、画像素材 bg.png を 0 フレームから "
                        f"{total} フレームの長さで置いて、ken_burns_in の動きを付けて",
                        "model_key": key,
                    },
                ),
            )
            assert m.status_code == 200, m.text
            msg = m.json()
            report["assistant"] = {
                "reply": msg["content"],
                "actions": [
                    {
                        "tool": a["tool"],
                        "ok": a["ok"],
                        "error": a.get("error"),
                        "details": (a.get("approval") or {}).get("details"),
                    }
                    for a in msg["actions"]
                ],
            }
            # 承認前は変わらない
            assert (await c.get(f"/projects/{pid}/timeline")).json()["clips"] == tl["clips"]
            applied = 0
            for a in (await c.get(f"/projects/{pid}/approvals")).json():
                if a["kind"] == "command":
                    res = await c.post(f"/approvals/command/{a['id']}", json={"approve": True})
                    applied += res.json().get("status") == "APPLIED"
            report["assistant"]["applied"] = applied
            if not applied:  # モデルの計画が使えなかったときは手で背景を敷いて続ける
                await c.post(
                    f"/projects/{pid}/commands",
                    json={
                        "command": {
                            "type": "batch",
                            "commands": [
                                {"type": "add_track", "kind": "video", "track_id": "bg"},
                                {
                                    "type": "add_clip",
                                    "track_id": "bg",
                                    "asset_id": bg["id"],
                                    "start_frame": 0,
                                    "duration_frames": total,
                                },
                            ],
                        }
                    },
                )
            # タイトルは背景より上のトラックに置く
            await c.post(
                f"/projects/{pid}/commands",
                json={
                    "command": {
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
                                "asset_id": title["id"],
                                "start_frame": 0,
                            },
                        ],
                    }
                },
            )
            render = await step(
                "書き出し（FHD・字幕焼き込み）",
                job(await c.post("/render", json={"project_id": pid, "script_id": sid})),
            )
            report["render"] = render
            qa = await step("品質検査", job(await c.post(f"/projects/{pid}/qa", json={})))
            rep = (await c.get(f"/qa/{qa['report_id']}")).json()
            report["qa"] = {
                "status": rep["status"],
                "sections": {
                    s["name"]: {
                        "status": s["status"],
                        "metrics": s["metrics"],
                        "checks": [f"{x['level']}:{x['code']}:{x['message']}" for x in s["checks"]],
                    }
                    for s in rep["sections"]
                },
            }
            src = settings.data_dir / render["output_path"]
            shutil.copy(src, out_dir / "e2e_render.mp4")
            for sub in render.get("subtitle_paths", []):
                shutil.copy(settings.data_dir / sub, out_dir / Path(sub).name)

    (out_dir / "e2e_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "performance_report.md").write_text(_markdown(report), encoding="utf-8")
    errors = [
        (n, x)
        for n, s in report["qa"]["sections"].items()
        for x in s["checks"]
        if x.startswith("error:")
    ]
    assert errors == [], errors


def _markdown(r: dict) -> str:
    gpu = r.get("gpu") or {}
    ram = r.get("ram") or {}
    lines = [
        f"# 性能レポート（VideoAutomation {r['app_version']}）",
        "",
        f"- 環境: {r['platform']} / Python {r['python']}",
        f"- GPU: {gpu.get('name', '不明')}（VRAM {gpu.get('total_mb', '?')} MB、ドライバ {gpu.get('driver', '?')}）",
        f"- RAM: {ram.get('total_gb', '?')} GB",
        f"- LLM: {r.get('model')}",
        f"- 台本: {r.get('script', {}).get('turns')} 発話・推定 {r.get('script', {}).get('estimated_sec')} 秒"
        f" / 動画 {r.get('timeline_sec')} 秒",
        f"- 書き出し: {r.get('render', {}).get('encoder')}、{r.get('render', {}).get('width')}x{r.get('render', {}).get('height')}",
        f"- 品質検査: {r.get('qa', {}).get('status')}",
        "",
        "| 工程 | 時間(秒) | GPU メモリ最大(MB) | RAM 使用率最大(%) |",
        "|---|---:|---:|---:|",
    ]
    for s in r["steps"]:
        lines.append(
            f"| {s['step']} | {s['sec']} | {s['peak_gpu_mb'] or '-'} | {s['peak_ram_pct'] or '-'} |"
        )
    lines.append(f"| **合計** | **{round(sum(s['sec'] for s in r['steps']), 1)}** | | |")
    return "\n".join(lines) + "\n"
