"""動画自動化アプリ（ローカル API）との通信。bpy に依存しない（テストできるように分ける）。

- 接続先は同じ PC（127.0.0.1 / localhost / ::1）だけ。ほかのホストは拒否する。
- 標準ライブラリ（urllib）だけを使う。Blender に追加のパッケージを入れない。
- API キーなどの秘密は扱わない（アプリ側が保存・使用する）。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any
from urllib.parse import quote, urlsplit

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
DEFAULT_SERVER = "http://127.0.0.1:8765"


class BridgeError(Exception):
    """アプリとの通信の失敗。メッセージはそのまま画面に出す。"""


def check_server(url: str) -> str:
    """接続先を検査して API の基底 URL を返す（同じ PC の http だけを許す）。"""
    parts = urlsplit(url.strip())
    if parts.scheme != "http" or parts.hostname not in LOCAL_HOSTS:
        raise BridgeError("接続先は同じ PC（http://127.0.0.1:ポート）だけにできます")
    if parts.port is None:
        raise BridgeError("ポート番号を指定してください（例: http://127.0.0.1:8765）")
    host = f"[{parts.hostname}]" if ":" in (parts.hostname or "") else parts.hostname
    return f"http://{host}:{parts.port}/api/v1"


Opener = Callable[[urllib.request.Request, float], Any]


def _default_open(req: urllib.request.Request, timeout: float) -> Any:
    # プロキシを経由させない（環境変数のプロキシ設定で localhost が外へ出るのを防ぐ）
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return opener.open(req, timeout=timeout)


class Client:
    def __init__(self, server: str, opener: Opener | None = None, timeout: float = 30.0) -> None:
        self.base = check_server(server)
        self.open = opener or _default_open
        self.timeout = timeout

    def call(self, method: str, path: str, body: Any = None, timeout: float | None = None) -> Any:
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with self.open(req, timeout or self.timeout) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read().decode("utf-8"))
                msg = detail.get("message") or detail.get("detail") or detail
            except Exception:  # noqa: BLE001
                msg = e.reason
            raise BridgeError(f"{e.code}: {msg}") from None
        except (urllib.error.URLError, OSError) as e:
            raise BridgeError(f"アプリに接続できません（起動していますか？）: {e}") from None
        return json.loads(raw.decode("utf-8")) if raw else None

    # ---------- よく使う呼び出し ----------
    def health(self) -> dict[str, Any]:
        return self.call("GET", "/health", timeout=5)

    def projects(self) -> list[dict[str, Any]]:
        return self.call("GET", "/projects")

    def scripts(self, project_id: str) -> list[dict[str, Any]]:
        return self.call("GET", f"/scripts?project_id={quote(project_id)}")

    def profiles(self) -> list[dict[str, Any]]:
        return self.call("GET", "/design/profiles")

    def classify(self, text: str, has_script: bool) -> dict[str, Any]:
        return self.call("POST", "/design/classify", {"text": text, "has_script": has_script})

    def set_purpose(self, project_id: str, purpose: str) -> dict[str, Any]:
        return self.call("PUT", f"/projects/{quote(project_id)}/design", {"purpose": purpose})

    def design_check(self, project_id: str, script_id: str | None) -> dict[str, Any]:
        q = f"?script_id={quote(script_id)}" if script_id else ""
        return self.call("GET", f"/projects/{quote(project_id)}/design/check{q}")

    def wait_job(
        self,
        job_id: str,
        progress: Callable[[float], None] | None = None,
        limit_sec: float = 3600,
        interval: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> Any:
        """ジョブの終了を待って結果を返す。失敗したら BridgeError。"""
        waited = 0.0
        while True:
            j = self.call("GET", f"/jobs/{quote(job_id)}")
            if progress:
                progress(float(j.get("progress") or 0.0))
            st = j.get("status")
            if st == "SUCCEEDED":
                return j.get("result")
            if st in ("FAILED", "CANCELLED"):
                raise BridgeError(f"ジョブが止まりました: {j.get('error') or st}")
            if waited >= limit_sec:
                raise BridgeError("時間内に終わりませんでした")
            sleep(interval)
            waited += interval


# ---------- 手順（1 つずつ、または続けて実行する） ----------
STEPS = ("voice", "subtitles", "direction", "check", "render")
STEP_LABELS = {
    "voice": "音声を作る",
    "subtitles": "字幕を作る",
    "direction": "画面の演出を付ける",
    "check": "デザインを検査する",
    "render": "書き出す",
}


def run_step(
    c: Client,
    step: str,
    project_id: str,
    script_id: str,
    use_llm: bool = True,
    report: Callable[[str], None] = lambda _m: None,
) -> dict[str, Any]:
    """1 つの手順を実行して、画面に出す情報を返す。"""
    sid = script_id
    if step == "voice":
        j = c.call("POST", f"/scripts/{quote(sid)}/voice", {})
        c.wait_job(j["id"], lambda p: report(f"音声 {p:.0%}"))
        return {"message": "音声を作りました"}
    if step == "subtitles":
        cues = c.call("POST", "/subtitles/generate", {"script_id": sid})
        c.call("POST", "/subtitles/synchronize", {"script_id": sid})
        return {"message": f"字幕を {len(cues)} 件作りました"}
    if step == "direction":
        report("演出の案を作っています…")
        plan = c.call(
            "POST", "/direction/plan", {"script_id": sid, "use_llm": use_llm}, timeout=600
        )["plan"]
        j = c.call("POST", "/direction/apply", {"script_id": sid, "plan": plan})
        r = c.wait_job(j["id"], lambda p: report(f"演出 {p:.0%}")) or {}
        skipped = r.get("skipped") or []
        msg = f"演出を付けました（{r.get('clips', 0)} 個）"
        return {
            "message": msg + (f"・外したもの {len(skipped)} 件" if skipped else ""),
            "notes": skipped,
        }
    if step == "check":
        chk = c.design_check(project_id, sid)
        notes = [f"[{v['severity']}] {v['message']}" for v in chk.get("violations", [])]
        if not chk.get("ok"):
            ok = "書き出せません"
        else:
            ok = "書き出せます" if notes else "問題なし"
        return {"message": f"検査: {ok}（{chk.get('purpose') or '用途未設定'}）", "notes": notes}
    if step == "render":
        r = c.call("POST", "/render", {"project_id": project_id, "script_id": sid})
        if r.get("status") == "PENDING_APPROVAL":
            return {
                "message": "書き出しは承認待ちです（アプリで承認してください）",
                "render_id": r["id"],
            }
        c.wait_job(r["job_id"], lambda p: report(f"書き出し {p:.0%}"))
        files = c.call("GET", f"/render/{quote(r['id'])}/files")
        return {"message": "書き出しました", "render_id": r["id"], "video": files.get("video")}
    raise BridgeError(f"unknown step: {step}")
