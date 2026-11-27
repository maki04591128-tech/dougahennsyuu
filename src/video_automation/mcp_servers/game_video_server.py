"""mcp_game_video サーバー: AI が遊んだゲームを実況動画にして YouTube に上げるまでの道具。

遊ぶ AI（Claude）が使う順:
1. capture_start → 遊びながら capture_mark（出来事・見どころ・感想）→ capture_stop
2. make_highlights（長いときは見どころだけで 15 分以内に）→ job_status で待つ
3. 返ってきた時刻つきの記録を見て、自分（実況・解説役）のセリフを時刻つきで書き、
   build_game_video に渡す（相手役のセリフは Qwen などのローカル LLM が作る）→ job_status で待つ
4. youtube_upload（既定は非公開。confirm=True で本当に上げる）

時間のかかる処理は裏で動かし、job id を返す（MCP の呼び出しが時間切れにならないように）。
VOICEVOX・Ollama が止まっていれば起こす。
"""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from video_automation.mcp_servers._shared import get_container as _get_container
from video_automation.schemas.build import BuildRequest, BuildRunRequest, PlayLine
from video_automation.services.capture import highlights as hl
from video_automation.services.capture import session as cap

mcp = FastMCP("mcp_game_video")
_jobs: dict[str, dict[str, Any]] = {}
_tasks: set[asyncio.Task[Any]] = set()
APP_DIR = Path(__file__).resolve().parents[3]  # リポジトリ（アプリ）のフォルダ


def _job(kind: str, make: Any) -> dict[str, Any]:
    """make(rec) が返す処理を裏で動かす（rec に進み具合を書ける）。"""
    jid = uuid.uuid4().hex[:12]
    rec: dict[str, Any] = {
        "id": jid,
        "kind": kind,
        "status": "running",
        "progress": 0.0,
        "message": "",
        "result": None,
        "error": None,
        "started": time.time(),
    }
    _jobs[jid] = rec

    async def run() -> None:
        try:
            rec["result"] = await make(rec)
            rec["status"] = "succeeded"
        except Exception as exc:  # noqa: BLE001 - 失敗の中身を返す
            rec["status"] = "failed"
            rec["error"] = f"{type(exc).__name__}: {str(exc)[:800]}"

    t = asyncio.get_running_loop().create_task(run())
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)
    return {"job_id": jid, "status": "running"}


def _port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _flags() -> int:
    return 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW


def _find(root: Path, name: str) -> Path | None:
    if not root.is_dir():
        return None
    for p in root.rglob(name):
        return p
    return None


async def _ensure_engines() -> list[str]:
    """VOICEVOX と Ollama が止まっていれば起こす。起こしたものの説明を返す。"""
    notes: list[str] = []
    if not (_port_open(50021) or _port_open(50121)):
        exe = _find(APP_DIR.parent / "voicevox_engine", "run.exe") or _find(
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "VOICEVOX" / "vv-engine",
            "run.exe",
        )
        if exe is None:
            raise RuntimeError("VOICEVOX ENGINE（run.exe）が見つかりません")
        subprocess.Popen(  # noqa: S603 - 固定の実行ファイル
            [str(exe), "--host", "127.0.0.1", "--port", "50021"],
            cwd=str(exe.parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=_flags(),
        )
        notes.append("VOICEVOX を起動しました")
    if not _port_open(11434):
        ollama = shutil.which("ollama") or str(
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
        )
        if Path(ollama).is_file() or shutil.which("ollama"):
            subprocess.Popen(  # noqa: S603
                [ollama, "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=_flags(),
            )
            notes.append("Ollama を起動しました")
    for _ in range(90):
        if (_port_open(50021) or _port_open(50121)) and _port_open(11434):
            break
        await asyncio.sleep(1.0)
    return notes


# ---------- 1. 録画とプレイ記録 ----------
async def capture_start(window_title: str = "") -> dict[str, Any]:
    """OBS を起こし、ゲームの窓とゲームの音だけを録る録画を始める。"""
    wid = cap.obs_window_id(window_title) if window_title else None
    s = await asyncio.to_thread(cap.start, wid)
    return {"folder": s.folder, "id": s.id, "status": "recording"}


async def capture_mark(text: str, kind: str = "event", importance: int = 2) -> dict[str, Any]:
    """今の録画時刻で記録を書く。kind: event 出来事 / highlight 見どころ / comment 感想 /
    scene 場面の変わり目。importance: 1〜3（見どころの切り出しで重く扱う）。"""
    m = await asyncio.to_thread(cap.mark, text, kind, importance)
    return {"t": m.t, "text": m.text, "kind": m.kind, "importance": m.importance}


async def capture_stop() -> dict[str, Any]:
    """録画を止めて mp4 と記録をそろえる（この道具が起こした OBS は閉じる）。"""
    s = await asyncio.to_thread(cap.stop)
    return {"folder": s.folder, "video": s.video, "duration_sec": s.duration_sec,
            "marks": len(s.marks)}  # fmt: skip


def capture_status() -> dict[str, Any]:
    s = cap.current()
    if s is None:
        return {"status": "idle"}
    return {"status": s.state, "folder": s.folder, "marks": len(s.marks)}


# ---------- 2. 見どころ ----------
async def make_highlights(folder: str = "", limit_sec: int = hl.LIMIT_SEC) -> dict[str, Any]:
    """録画が limit_sec を超えるときは見どころだけを集めて詰める（既定 15 分）。裏で動く。"""
    f = Path(folder) if folder else _latest_folder()
    sess = cap.Session.load(f)

    async def work(_rec: dict[str, Any]) -> dict[str, Any]:
        spans = hl.plan_spans(sess.marks, sess.duration_sec, float(limit_sec))
        marks = hl.retime(sess.marks, spans)
        src = Path(sess.video)
        if len(spans) == 1 and spans[0].start == 0 and spans[0].end >= sess.duration_sec:
            dst = src  # 短いのでそのまま
        else:
            dst = src.with_name(src.stem + "_highlights.mp4")
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", *hl.ffmpeg_args(src, dst, spans, has_audio=True),
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
            )  # fmt: skip
            _, err_b = await proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError(f"ffmpeg: {err_b.decode(errors='replace')[-600:]}")
        data = hl.summary(spans, marks, sess.duration_sec)
        data["video"] = str(dst)
        hl.write_summary(f / "highlights.json", data)
        return data

    return _job("highlights", work)


def _latest_folder() -> Path:
    root = cap.capture_root()
    done = [p for p in root.iterdir() if (p / "session.json").is_file()] if root.is_dir() else []
    if not done:
        raise RuntimeError("録画がまだありません")
    return max(done, key=lambda p: p.name)


# ---------- 3. 動画にする ----------
async def build_game_video(
    video: str,
    play_lines: list[dict[str, Any]],
    instruction: str = "AIが自作ゲームを遊んだゲーム実況のプレイ動画",
    reference: str = "",
    script_model: str = "",
    voices: list[str] | None = None,
    cast: list[str] | None = None,
) -> dict[str, Any]:
    """ゲーム実況の動画を作って書き出す（裏で動く）。

    play_lines は [{at_sec, text}]: 1 人目（実況・解説役）のセリフと言う時刻。
    2 人目（質問・ガヤ役）のセリフは script_model が作る。"""
    c = await _get_container()

    async def work(rec: dict[str, Any]) -> dict[str, Any]:
        notes = await _ensure_engines()
        await c.providers.discover()
        req = BuildRequest(
            instruction=instruction,
            screen_recording=video,
            reference=reference[:20000],
            play_lines=[PlayLine(**x) for x in play_lines],
            characters=voices or [],
            cast=cast or [],
        )
        plan = await c.build.plan(req)
        if not plan.can_run:
            miss = [x.label for x in plan.readiness if x.required and not x.ok]
            raise RuntimeError("準備が足りません: " + "、".join(miss))
        rec["project_id"] = plan.project_id

        async def progress(v: float, msg: str = "") -> None:
            rec["progress"], rec["message"] = round(v, 3), msg

        res = await c.build.run(
            plan.project_id,
            BuildRunRequest(script_id=plan.script_id, script_model=script_model or None),
            progress,
        )
        res["notes"] = notes + plan.notes + list(res.get("notes") or [])
        res["project_id"] = plan.project_id
        return res

    return _job("build", work)


# ---------- 4. YouTube ----------
async def youtube_upload(
    video: str,
    title: str,
    description: str = "",
    tags: list[str] | None = None,
    thumbnail: str = "",
    privacy: str = "private",
    confirm: bool = False,
) -> dict[str, Any]:
    """YouTube に上げる。confirm=False は確認だけ（上げない）。既定は非公開。"""
    from video_automation.integrations.youtube import oauth, uploader

    meta = uploader.VideoMeta(title=title, description=description, tags=tags or [],
                              privacy=privacy)  # type: ignore[arg-type]  # fmt: skip
    errs = meta.validate()
    if errs or not confirm:
        return {"ok": not errs, "errors": errs, "dry_run": True, "title": title,
                "privacy": privacy, "video": video}  # fmt: skip
    folder = Path((await _get_container()).settings.data_dir).resolve() / "youtube"

    def work_sync() -> dict[str, Any]:
        import httpx

        with httpx.Client(timeout=httpx.Timeout(60, write=600)) as http:
            token = oauth.access_token(folder, http)
            res = uploader.upload(http, token, Path(video), meta)
            note = ""
            if thumbnail:
                try:
                    uploader.set_thumbnail(http, token, res["id"], Path(thumbnail))
                except uploader.YouTubeUploadError as exc:
                    note = str(exc)
        vid = res["id"]
        return {
            "id": vid,
            "url": f"https://youtu.be/{vid}",
            "studio": f"https://studio.youtube.com/video/{vid}/edit",
            "privacy": res.get("status", {}).get("privacyStatus"),
            "thumbnail_note": note,
        }

    return _job("youtube", lambda _rec: asyncio.to_thread(work_sync))


async def youtube_auth() -> dict[str, Any]:
    """YouTube への投稿の許可を取り始める。返る auth_url をブラウザで開いて許可すると、
    job が succeeded になり鍵が保存される（5 分で時間切れ）。"""
    from video_automation.integrations.youtube import oauth

    folder = Path((await _get_container()).settings.data_dir).resolve() / "youtube"
    oauth.load_client(folder)  # client_secret.json が無ければここで分かる
    got: dict[str, str] = {}

    def work_sync() -> dict[str, Any]:
        oauth.authorize(folder, open_browser=False, on_url=lambda u: got.update(url=u))
        return {"token": str(folder / oauth.TOKEN_FILE)}

    job = _job("youtube_auth", lambda _rec: asyncio.to_thread(work_sync))
    for _ in range(50):
        if "url" in got:
            break
        await asyncio.sleep(0.1)
    return {**job, "auth_url": got.get("url", "")}


def job_status(job_id: str) -> dict[str, Any]:
    """裏で動いている仕事の様子（status: running / succeeded / failed）。"""
    rec = _jobs.get(job_id)
    if rec is None:
        return {"status": "unknown"}
    return {k: v for k, v in rec.items() if not k.startswith("_")}


mcp.tool(name="capture_start", description="ゲームの録画を始める（OBS・ゲームの窓と音だけ）")(
    capture_start
)
mcp.tool(name="capture_mark", description="今の録画時刻でプレイ記録を書く（出来事・感想など）")(
    capture_mark
)
mcp.tool(name="capture_stop", description="録画を止めて mp4 とプレイ記録をそろえる")(capture_stop)
mcp.tool(name="capture_status", description="録画中かどうか")(capture_status)
mcp.tool(name="make_highlights", description="見どころだけを集めて 15 分以内に詰める（裏で動く）")(
    make_highlights
)
mcp.tool(name="build_game_video", description="ゲーム実況の動画を作って書き出す（裏で動く）")(
    build_game_video
)
mcp.tool(name="youtube_upload", description="YouTube に上げる（既定は非公開・confirm で実行）")(
    youtube_upload
)
mcp.tool(name="youtube_auth", description="YouTube への投稿の許可を取る（URL を返す）")(
    youtube_auth
)
mcp.tool(name="job_status", description="裏で動いている仕事の様子")(job_status)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
