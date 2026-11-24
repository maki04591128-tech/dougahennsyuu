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

    def timeline_for_blender(self, project_id: str, script_id: str | None) -> dict[str, Any]:
        q = f"?script_id={quote(script_id)}" if script_id else ""
        return self.call("GET", f"/projects/{quote(project_id)}/timeline/blender{q}")

    # ---------- 文章だけで作る（/build） ----------
    def readiness(self) -> list[dict[str, Any]]:
        return self.call("GET", "/build/readiness", timeout=15)

    def build_plan(
        self,
        instruction: str,
        duration_sec: int | None = None,
        orientation: str = "auto",
        reference: str = "",
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"instruction": instruction, "orientation": orientation}
        if duration_sec:
            body["duration_sec"] = duration_sec
        if reference.strip():
            body["reference"] = reference[:20000]
        return self.call("POST", "/build/plan", body, timeout=60)

    def build_change(
        self,
        project_id: str,
        script_id: str,
        purpose: str | None = None,
        duration_sec: int | None = None,
        tachie: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """構成の確認で、用途・長さ・立ち絵（"" は立ち絵なし）を変える。"""
        body: dict[str, Any] = {"script_id": script_id}
        if purpose:
            body["purpose"] = purpose
        if duration_sec:
            body["duration_sec"] = duration_sec
        if tachie is not None:
            body["tachie"] = tachie
        return self.call("PUT", f"/build/{quote(project_id)}/plan", body, timeout=30)

    def build_run(
        self, project_id: str, script_id: str, instruction: str = "", keep_script: bool = False
    ) -> dict[str, Any]:
        """作る。できあがった後の手直しは instruction（直したいこと）と
        keep_script（見た目だけ作り直す）。"""
        body: dict[str, Any] = {"script_id": script_id}
        if instruction.strip():
            body["instruction"] = instruction.strip()[:2000]
        if keep_script:
            body["keep_script"] = True
        return self.call("POST", f"/build/{quote(project_id)}/run", body)

    def job(self, job_id: str) -> dict[str, Any]:
        return self.call("GET", f"/jobs/{quote(job_id)}", timeout=10)

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        return self.call("POST", f"/jobs/{quote(job_id)}/cancel", timeout=10)

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


# ---------- 素材ごとの読み込み（タイムライン → VSE のストリップ） ----------
def plan_strips(
    tl: dict[str, Any], base_channel: int = 1, subtitles: bool = True
) -> dict[str, Any]:
    """アプリのタイムラインを、VSE に置くストリップの一覧にする（bpy を使わない部分）。

    チャンネルは下から: 音声トラック → 映像トラック（index の小さい＝奥から）→ 字幕。
    映像トラックの動画素材に音があれば、その音は音声の段に置く。
    フレームは 0 始まり（Blender は 1 始まりなので、置くときに +1 する）。
    再現しないもの（アニメーション・入りの切り替え・ぼかし背景）は notes に書く。
    """
    assets = {a["id"]: a for a in tl.get("assets", [])}
    tracks = sorted(tl.get("tracks", []), key=lambda t: (t["kind"] != "audio", t["index"]))
    clips = tl.get("clips", [])
    ch: dict[str, int] = {}
    n = base_channel
    audio_ch = None
    for t in tracks:
        if t["kind"] == "audio":
            ch[t["id"]] = n
            n += 1
    # 映像の音（映像トラックの動画）を置く段
    if any(
        assets.get(c["asset_id"], {}).get("kind") == "video"
        and assets.get(c["asset_id"], {}).get("has_audio")
        and ch.get(c["track_id"]) is None
        for c in clips
    ):
        audio_ch = n
        n += 1
    for t in tracks:
        if t["kind"] != "audio":
            ch[t["id"]] = n
            n += 1
    sub_ch = n
    W, H = int(tl["width"]), int(tl["height"])
    strips: list[dict[str, Any]] = []
    notes: list[str] = []
    missing: set[str] = set()
    muted = {t["id"] for t in tracks if t.get("muted")}
    kinds = {t["id"]: t["kind"] for t in tracks}
    anim = 0
    for c in sorted(clips, key=lambda c: (ch.get(c["track_id"], 0), c["start_frame"])):
        a = assets.get(c["asset_id"])
        if a is None or not a.get("abs_path"):
            missing.add(c["asset_id"])
            continue
        tr = c.get("transform") or {}
        base = {
            "name": a["name"][:60],
            "path": a["abs_path"],
            "start": int(c["start_frame"]),
            "end": int(c["end_frame"]),
            "source_in": int(c.get("source_in_frame") or 0),
            "fade_in": int(c.get("fade_in_frames") or 0),
            "fade_out": int(c.get("fade_out_frames") or 0),
            "mute": c["track_id"] in muted,
        }
        if kinds.get(c["track_id"]) == "audio" or a["kind"] == "audio":
            strips.append(
                {**base, "type": "sound", "channel": ch[c["track_id"]], "volume": c["volume"]}
            )
            continue
        if c.get("animation"):
            anim += 1
        fit = tr.get("fit", "fill")
        if fit == "blur":
            fit = "fit"
        strips.append(
            {
                **base,
                "type": "movie" if a["kind"] == "video" else "image",
                "channel": ch[c["track_id"]],
                "fit": fit.upper(),
                "scale": float(tr.get("scale", 1.0)),
                "offset_x": float(tr.get("x", 0.0)) * W,
                "offset_y": -float(tr.get("y", 0.0)) * H,  # アプリは下が +、Blender は上が +
                "opacity": float(tr.get("opacity", 1.0)),
            }
        )
        if a["kind"] == "video" and a.get("has_audio") and c["volume"] > 0 and audio_ch:
            strips.append({**base, "type": "sound", "channel": audio_ch, "volume": c["volume"]})
    if subtitles:
        for q in tl.get("cues", []):
            strips.append(
                {
                    "type": "text",
                    "name": q["text"][:30],
                    "text": q["text"],
                    "channel": sub_ch,
                    "start": int(q["start_frame"]),
                    "end": int(q["end_frame"]),
                    "font_size": round(H * 0.05),
                }
            )
    if anim:
        notes.append(f"演出の動き・切り替え（{anim} 個）は再現せず、止まった状態で置きました")
    if missing:
        notes.append(f"見つからない素材が {len(missing)} 個ありました（置いていません）")
    top = max([s["channel"] for s in strips], default=base_channel)
    return {
        "strips": strips,
        "notes": notes,
        "channels": (base_channel, top),
        "width": W,
        "height": H,
        "fps_num": int(tl["fps_num"]),
        "fps_den": int(tl["fps_den"]),
        "frames": int(tl.get("duration_frames") or 0),
    }


# ---------- アプリ（エンジン）の場所と起動 ----------
def app_candidates(home: str, localappdata: str, addon_dir: str) -> list[str]:
    """アプリが入っていそうな場所（上から順に調べる）。"""
    import os

    out = []
    if localappdata:
        out.append(os.path.join(localappdata, "VideoAutomation"))
    # 開発用: アドオンを tools/blender_addon から入れたとき（リポジトリの直下がアプリ）
    out.append(os.path.normpath(os.path.join(addon_dir, "..", "..", "..")))
    if home:
        out.append(os.path.join(home, "VideoAutomation"))
    return out


def find_python(app_dir: str) -> str | None:
    """アプリの Python（uv で作った .venv）。無ければ None。"""
    import os

    for rel in ((".venv", "Scripts", "python.exe"), (".venv", "bin", "python")):
        p = os.path.join(app_dir, *rel)
        if os.path.isfile(p):
            return p
    return None


def find_app_dir(candidates: list[str]) -> str | None:
    import os

    for d in candidates:
        if d and os.path.isfile(os.path.join(d, "pyproject.toml")) and find_python(d):
            return d
    return None


def engine_command(app_dir: str) -> list[str]:
    """画面なし（API だけ）でアプリを起動するコマンド。"""
    py = find_python(app_dir)
    if py is None:
        raise BridgeError("アプリの Python が見つかりません（インストールし直してください）")
    return [py, "-m", "video_automation.app.main", "--headless"]
