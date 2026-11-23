"""実機テスト（Phase 8）: 実 Ollama のモデルで台本作成（全自動・片側入力・再生成・検証）と
対話型編集（計画 → 検証 → 承認待ち → 承認）を行い、時間と結果を記録する。

VA_LIVE_TESTS=1 のときだけ実行。VA_LIVE_INTERACTIVE_OUT にフォルダを指定するとレポートを出力する。
VA_LIVE_MODELS（カンマ区切りの model_id の一部）で対象モデルを絞れる。

不変条件（必ず満たす）: ユーザー入力・固定セリフは一字一句変わらない / 承認前はタイムラインが
変わらない。モデルの出来（JSON が壊れる等）は記録し、少なくとも 1 モデルで全工程が通ることを確認する。
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

USER_LINES = [
    "今日は動画編集を自動化する方法を紹介するのだ！",
    "まずは台本から字幕を自動で作るところを見せるのだ。",
    "最後に、書き出しまで一気にやってみるのだ。",
]


def _ff(*args: str) -> None:
    subprocess.run([shutil.which("ffmpeg") or "ffmpeg", "-v", "error", "-y", *args], check=True)


async def _timed(coro):
    t0 = time.perf_counter()
    r = await coro
    return r, round(time.perf_counter() - t0, 1)


async def _model_run(c, key, pid, image_id, base_cmd) -> dict:
    rep: dict = {"model": key}
    # --- 全自動 ---
    sc = (
        await c.post(
            "/scripts/studio",
            json={
                "project_id": pid,
                "title": "自動化入門",
                "mode": "FULL_AI",
                "settings": {
                    "topic": "ローカルAIで動画編集を自動化する",
                    "audience": "動画編集の初心者",
                    "target_duration_sec": 40,
                    "must_keywords": ["字幕"],
                },
                "characters": [
                    {"name": "ずんだもん", "role": "解説役", "speaking_style": "語尾に「のだ」"},
                    {"name": "めたん", "role": "聞き役", "speaking_style": "丁寧語"},
                ],
            },
        )
    ).json()
    r, sec = await _timed(c.post(f"/scripts/{sc['id']}/generate", json={"model_key": key}))
    full = {"status": r.status_code, "sec": sec}
    if r.status_code == 200:
        d = r.json()
        full |= {
            "turns": len(d["segments"]),
            "scenes": [s["purpose"] for s in d["scenes"]],
            "estimated_total_sec": d["estimated_total_sec"],
            "speakers": sorted({t["speaker"] for t in d["segments"]}),
            "sample": [f"{t['speaker']}: {t['text']}" for t in d["segments"][:6]],
            "notes": d["notes"],
        }
        v = (await c.post(f"/scripts/{sc['id']}/validate", json={})).json()
        full["validation"] = [f"{i['level']}:{i['code']}" for i in v["issues"]]
        # 1発話を固定 → 再生成しても変わらない
        fixed = d["segments"][0] if d["segments"] else None
        if fixed:
            await c.put(f"/scripts/{sc['id']}/dialogue/{fixed['id']}/lock", json={"locked": True})
            r2, sec2 = await _timed(
                c.post(
                    f"/scripts/{sc['id']}/generate",
                    json={"model_key": key, "instruction": "もっと短く"},
                )
            )
            full["regenerate_all"] = {"status": r2.status_code, "sec": sec2}
            if r2.status_code == 200:
                after = {t["id"]: t for t in r2.json()["segments"]}
                assert after[fixed["id"]]["text"] == fixed["text"], "固定セリフが変わった"
                full["regenerate_all"]["turns"] = len(after)
    else:
        full["error"] = r.text[:300]
    rep["full_ai"] = full

    # --- 片側入力 ---
    sc2 = (
        await c.post(
            "/scripts/studio",
            json={
                "project_id": pid,
                "mode": "USER_ONE_SIDE",
                "settings": {"topic": "動画編集の自動化", "response_policy": "質問"},
                "characters": [
                    {"name": "ずんだもん", "is_user": True},
                    {"name": "めたん", "role": "聞き役", "speaking_style": "丁寧語"},
                ],
            },
        )
    ).json()
    await c.post(
        f"/scripts/{sc2['id']}/dialogue", json={"lines": [{"text": t} for t in USER_LINES]}
    )
    r, sec = await _timed(c.post(f"/scripts/{sc2['id']}/generate", json={"model_key": key}))
    one = {"status": r.status_code, "sec": sec}
    if r.status_code == 200:
        d = r.json()
        user_texts = [t["text"] for t in d["segments"] if t["speaker"] == "ずんだもん"]
        assert user_texts == USER_LINES, "ユーザー入力のセリフが変わった"
        ai = [t for t in d["segments"] if t["speaker"] == "めたん"]
        one |= {
            "ai_turns": len(ai),
            "dialogue": [f"{t['speaker']}: {t['text']}" for t in d["segments"]],
            "notes": d["notes"],
        }
        if ai:
            r3, sec3 = await _timed(
                c.post(
                    f"/scripts/{sc2['id']}/dialogue/{ai[0]['id']}/regenerate",
                    json={"candidates": 2, "model_key": key},
                )
            )
            one["candidates"] = {
                "status": r3.status_code,
                "sec": sec3,
                "texts": r3.json().get("candidates") if r3.status_code == 200 else r3.text[:200],
            }
        r4, sec4 = await _timed(
            c.post(f"/scripts/{sc2['id']}/validate", json={"use_llm": True, "model_key": key})
        )
        one["llm_review"] = {
            "status": r4.status_code,
            "sec": sec4,
            "issues": [
                i["message"] for i in r4.json().get("issues", []) if i["code"] == "llm_review"
            ]
            if r4.status_code == 200
            else r4.text[:200],
        }
    else:
        one["error"] = r.text[:300]
    rep["user_one_side"] = one

    # --- 対話型編集 ---
    conv = (await c.post("/conversations", json={"project_id": pid})).json()
    before = (await c.get(f"/projects/{pid}/timeline")).json()
    r, sec = await _timed(
        c.post(
            f"/conversations/{conv['id']}/messages",
            json={
                "text": "映像トラックに画像素材を冒頭から3秒間置いて、0.5秒でフェードインさせて",
                "model_key": key,
            },
        )
    )
    edit = {"status": r.status_code, "sec": sec}
    if r.status_code == 200:
        m = r.json()
        edit |= {
            "reply": m["content"][:300],
            "questions": m["questions"],
            "actions": [
                {"tool": a["tool"], "ok": a["ok"], "summary": a["summary"], "error": a.get("error")}
                for a in m["actions"]
            ],
        }
        after = (await c.get(f"/projects/{pid}/timeline")).json()
        assert after["clips"] == before["clips"], "承認前にタイムラインが変わった"
        approvals = [a["approval"] for a in m["actions"] if a.get("approval")]
        edit["approvals"] = [{"kind": a["kind"], "details": a["details"]} for a in approvals]
        applied = []
        for a in approvals:
            if a["kind"] == "command":
                res = (await c.post(f"/approvals/command/{a['id']}", json={"approve": True})).json()
                applied.append(res.get("status"))
        tl = (await c.get(f"/projects/{pid}/timeline")).json()
        edit["applied"] = applied
        edit["clips_after"] = [
            {
                "asset": cl["asset_id"] == image_id,
                "start": cl["start_frame"],
                "end": cl["end_frame"],
                "fade_in": cl["fade_in_frames"],
            }
            for cl in tl["clips"]
        ]
        # 次のモデルのために戻す
        await c.post(f"/projects/{pid}/history/revert", json={"to_command_id": base_cmd})
    else:
        edit["error"] = r.text[:300]
    rep["assistant"] = edit
    rep["ok"] = (
        full["status"] == 200
        and one["status"] == 200
        and edit["status"] == 200
        and any(s == "APPLIED" for s in edit.get("applied", []))
    )
    return rep


async def test_live_interactive(tmp_path):
    out_dir = Path(os.environ.get("VA_LIVE_INTERACTIVE_OUT") or tmp_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    media = tmp_path / "media"
    media.mkdir()
    _ff("-f", "lavfi", "-i", "testsrc2=s=1920x1080:d=1", "-frames:v", "1", str(media / "a.png"))
    app = create_app(Settings(data_dir=tmp_path / "data", import_dirs=[media]))
    report: dict = {"models": []}
    async with LifespanManager(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t/api/v1", timeout=1800
        ) as c:
            disc = (await c.post("/providers/discover")).json()
            models = [m for m in disc["models"] if m["provider_type"] in ("ollama", "lmstudio")]
            only = [x.strip() for x in os.environ.get("VA_LIVE_MODELS", "").split(",") if x.strip()]
            if only:
                models = [m for m in models if any(o in m["model_id"] for o in only)]
            assert models, "ローカルモデルが検出されませんでした"
            pid = (
                await c.post("/projects", json={"name": "P8", "fps_num": 30, "fps_den": 1})
            ).json()["id"]
            img = (
                await c.post(
                    "/assets/import", json={"project_id": pid, "path": str(media / "a.png")}
                )
            ).json()
            base_cmd = (
                await c.post(
                    f"/projects/{pid}/commands",
                    json={"command": {"type": "add_track", "kind": "video", "name": "映像"}},
                )
            ).json()["id"]
            for m in models:
                t0 = time.perf_counter()
                rep = await _model_run(c, m["key"], pid, img["id"], base_cmd)
                rep["total_sec"] = round(time.perf_counter() - t0, 1)
                report["models"].append(rep)
                (out_dir / "live_interactive_report.json").write_text(
                    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
                )
    assert any(r["ok"] for r in report["models"]), "全工程が通ったモデルがありません"
