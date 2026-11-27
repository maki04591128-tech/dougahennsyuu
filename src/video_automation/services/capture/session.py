"""プレイの録画とプレイ記録（遊ぶ AI が使う）。

流れ: start（OBS を起こし、ゲームの窓と音だけを録る場面を作って録画開始）→ mark（出来事・感想を、
OBS の録画時間で時刻を付けて書く）→ stop（録画を止め、mp4 と記録 session.json をそろえる）。

- 録画の保存先: %USERPROFILE%\\Videos\\va_capture\\<開始日時>\\
  （動画編集ツールが取り込めるフォルダ）
- デスクトップ音声・マイクは消音にし、ゲームの音だけ録る（Discord の通話などを入れない）
- 状態は va_capture\\current.json に置く（別のプロセスの呼び出しからも続きを扱える）
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from video_automation.integrations.obs.client import SECRET_DIR, ObsClient, ObsError

SCENE = "VA_Game"
VIDEO_INPUT = "VA_GameWindow"
AUDIO_INPUT = "VA_GameAudio"
GAME_EXE = "wakuwaku.exe"
GAME_TITLE = "わくわく☆生活シミュレーター♪"
UNITY_CLASS = "UnityWndClass"
KINDS = ("event", "highlight", "comment", "scene")


def capture_root() -> Path:
    return Path.home() / "Videos" / "va_capture"


def obs_window_id(title: str = GAME_TITLE, cls: str = UNITY_CLASS, exe: str = GAME_EXE) -> str:
    """OBS の窓の指定（題:クラス:exe、題の ':' は '#3A'）。"""
    return f"{title.replace(':', '#3A')}:{cls}:{exe}"


@dataclass
class Mark:
    t: float  # 録画の頭からの秒
    text: str
    kind: str = "event"  # event 出来事 / highlight 見どころ / comment 感想 / scene 場面の変わり目
    importance: int = 2  # 1 低い〜3 高い（見どころの切り出しで使う）


@dataclass
class Session:
    id: str
    folder: str
    started_at: str
    game_title: str = GAME_TITLE
    video: str = ""
    duration_sec: float = 0.0
    marks: list[Mark] = field(default_factory=list)
    obs_pid: int | None = None  # この道具が起こした OBS（止めるときに閉じる）
    state: str = "recording"  # recording / stopped

    def save(self) -> None:
        d = asdict(self)
        Path(self.folder, "session.json").write_text(
            json.dumps(d, ensure_ascii=False, indent=2), "utf-8"
        )

    @classmethod
    def load(cls, folder: Path) -> Session:
        d = json.loads((folder / "session.json").read_text("utf-8"))
        d["marks"] = [Mark(**m) for m in d.get("marks", [])]
        return cls(**d)


def _current_file() -> Path:
    return capture_root() / "current.json"


def current() -> Session | None:
    f = _current_file()
    if not f.is_file():
        return None
    folder = Path(json.loads(f.read_text("utf-8"))["folder"])
    return Session.load(folder) if (folder / "session.json").is_file() else None


def obs_exe() -> Path | None:
    p = SECRET_DIR / "obs_path.txt"
    if p.is_file():
        exe = Path(p.read_text("utf-8").strip())
        return exe if exe.is_file() else None
    return None


def _launch_obs(wait: float = 40.0) -> int | None:
    """OBS が動いていなければ起こす（トレイに最小化）。起こしたときはその pid。"""
    try:
        with ObsClient(timeout=3):
            return None  # 既に動いている
    except Exception:  # noqa: BLE001
        pass
    exe = obs_exe()
    if exe is None:
        raise ObsError("OBS が見つかりません（導入の bat を先に実行してください）")
    flags = 0x00000008 if sys.platform == "win32" else 0  # DETACHED_PROCESS
    proc = subprocess.Popen(
        [
            str(exe),
            "--portable",
            "--minimize-to-tray",
            "--disable-shutdown-check",
            "--disable-updater",
            "--disable-missing-files-check",
        ],
        cwd=str(exe.parent),  # OBS は bin のフォルダで起こす決まり
        creationflags=flags,
    )
    end = time.monotonic() + wait
    while time.monotonic() < end:
        try:
            with ObsClient(timeout=3):
                return proc.pid
        except Exception:  # noqa: BLE001
            time.sleep(1.0)
    raise ObsError("OBS の WebSocket につながりませんでした")


def setup_scene(obs: ObsClient, window_id: str, width: int = 1920, height: int = 1080) -> None:
    """ゲームの窓と音だけを録る場面を作る（何度呼んでもよい）。"""
    obs.call(
        "SetVideoSettings",
        {
            "baseWidth": width,
            "baseHeight": height,
            "outputWidth": width,
            "outputHeight": height,
            "fpsNumerator": 30,
            "fpsDenominator": 1,
        },
    )
    scenes = [s["sceneName"] for s in obs.call("GetSceneList").get("scenes", [])]
    if SCENE not in scenes:
        obs.call("CreateScene", {"sceneName": SCENE})
    obs.call("SetCurrentProgramScene", {"sceneName": SCENE})
    inputs = {i["inputName"] for i in obs.call("GetInputList").get("inputs", [])}
    # priority 2 = 同じ exe の窓（題が変わってもつかむ）。method 2 = Windows 10 の画面取り込み
    vset = {"window": window_id, "priority": 2, "capture_cursor": False, "method": 2}
    if VIDEO_INPUT in inputs:
        obs.call("SetInputSettings", {"inputName": VIDEO_INPUT, "inputSettings": vset})
    else:
        obs.call(
            "CreateInput",
            {
                "sceneName": SCENE,
                "inputName": VIDEO_INPUT,
                "inputKind": "window_capture",
                "inputSettings": vset,
            },
        )
    aset = {"window": window_id, "priority": 2}
    if AUDIO_INPUT in inputs:
        obs.call("SetInputSettings", {"inputName": AUDIO_INPUT, "inputSettings": aset})
    else:
        obs.call(
            "CreateInput",
            {
                "sceneName": SCENE,
                "inputName": AUDIO_INPUT,
                "inputKind": "wasapi_process_output_capture",
                "inputSettings": aset,
            },
        )
    # 窓を画面いっぱいに合わせる
    item = obs.call("GetSceneItemId", {"sceneName": SCENE, "sourceName": VIDEO_INPUT})
    obs.call(
        "SetSceneItemTransform",
        {
            "sceneName": SCENE,
            "sceneItemId": item["sceneItemId"],
            "sceneItemTransform": {
                "boundsType": "OBS_BOUNDS_SCALE_INNER",
                "boundsWidth": width,
                "boundsHeight": height,
                "boundsAlignment": 0,
                "positionX": 0,
                "positionY": 0,
            },
        },
    )
    # デスクトップ音声・マイクは録らない（通話や別のアプリの音を入れない）
    special = obs.call("GetSpecialInputs")
    for k in ("desktop1", "desktop2", "mic1", "mic2", "mic3", "mic4"):
        name = special.get(k)
        if name:
            obs.call("SetInputMute", {"inputName": name, "inputMuted": True})
    # 録画の形式: 落ちても読める mp4（OBS 30.2 以降の hybrid_mp4）・画質は高め
    for cat, key, val in (
        ("Output", "Mode", "Simple"),
        ("SimpleOutput", "RecFormat2", "hybrid_mp4"),
        ("SimpleOutput", "RecQuality", "Small"),
    ):
        try:
            obs.call(
                "SetProfileParameter",
                {"parameterCategory": cat, "parameterName": key, "parameterValue": val},
            )
        except ObsError:
            pass


def start(
    window_id: str | None = None, obs_factory: Callable[[], ObsClient] = ObsClient
) -> Session:
    cur = current()
    if cur is not None and cur.state == "recording":
        raise ObsError(f"録画中です（{cur.folder}）。先に止めてください")
    pid = _launch_obs() if obs_factory is ObsClient else None
    sid = datetime.now().strftime("%Y%m%d_%H%M%S")
    folder = capture_root() / sid
    folder.mkdir(parents=True, exist_ok=True)
    with obs_factory() as obs:
        setup_scene(obs, window_id or obs_window_id())
        obs.call("SetRecordDirectory", {"recordDirectory": str(folder)})
        obs.call("StartRecord")
        for _ in range(50):  # 録画が始まるまで待つ
            if obs.call("GetRecordStatus").get("outputActive"):
                break
            time.sleep(0.1)
        else:
            raise ObsError("録画が始まりませんでした")
    sess = Session(id=sid, folder=str(folder), started_at=datetime.now().isoformat(), obs_pid=pid)
    sess.save()
    capture_root().mkdir(parents=True, exist_ok=True)
    _current_file().write_text(json.dumps({"folder": str(folder)}), "utf-8")
    return sess


def mark(
    text: str,
    kind: str = "event",
    importance: int = 2,
    obs_factory: Callable[[], ObsClient] = ObsClient,
) -> Mark:
    """今の録画時刻で記録を 1 つ書く。"""
    sess = current()
    if sess is None or sess.state != "recording":
        raise ObsError("録画していません（先に start）")
    if kind not in KINDS:
        raise ValueError(f"kind は {', '.join(KINDS)} のどれか")
    with obs_factory() as obs:
        ms = obs.call("GetRecordStatus").get("outputDuration", 0)
    imp = max(1, min(3, importance))
    m = Mark(t=round(ms / 1000, 2), text=text.strip()[:500], kind=kind, importance=imp)
    sess.marks.append(m)
    sess.save()
    return m


def stop(
    obs_factory: Callable[[], ObsClient] = ObsClient,
    probe: Callable[[Path], float] | None = None,
    close_obs: bool = True,
) -> Session:
    """録画を止め、mp4 と記録をそろえる。この道具が起こした OBS は閉じる。"""
    sess = current()
    if sess is None or sess.state != "recording":
        raise ObsError("録画していません")
    with obs_factory() as obs:
        res = obs.call("StopRecord")
    out = Path(res.get("outputPath") or "")
    for _ in range(100):  # 書き終わるまで待つ
        if out.is_file() and out.stat().st_size > 0:
            break
        time.sleep(0.2)
    video = out
    if out.suffix.lower() != ".mp4" and shutil.which("ffmpeg"):
        video = out.with_suffix(".mp4")
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-i", str(out), "-c", "copy", str(video)], check=True
        )
    sess.video = str(video)
    sess.duration_sec = (probe or _probe)(video) if video.is_file() else 0.0
    sess.state = "stopped"
    sess.save()
    _current_file().unlink(missing_ok=True)
    if close_obs and sess.obs_pid and sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(sess.obs_pid)], capture_output=True)
    return sess


def _probe(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
    )
    try:
        return round(float(r.stdout.strip()), 2)
    except ValueError:
        return 0.0


def to_dict(sess: Session) -> dict[str, Any]:
    return asdict(sess)
