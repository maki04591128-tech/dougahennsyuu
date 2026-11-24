"""導入の補助: アプリだけでは入らないもの（声・FFmpeg・ローカル LLM）を探し、起動し、
無ければ利用者の同意を得て公式の配布元から入れる。bpy に依存しない（テストできるように分ける）。

- ダウンロード先は下の SOURCES に書いた公式の配布元だけ（ほかの URL からは入れない）
- 入れる前に、配布元・大きさ・入れる場所を画面に出して同意を得る（呼び出し側で行う）
- Blender の「オンラインアクセスを許可」（bpy.app.online_access）がオフなら入れない
- 入れる場所はアプリのフォルダの tools/（ほかの場所には書かない）
"""

from __future__ import annotations

import os
import subprocess
import sys
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .client import BridgeError

CREATE_NO_WINDOW = 0x08000000


@dataclass(frozen=True)
class Source:
    key: str
    label: str
    url: str
    size: int  # バイト（配布元の値。取り直せたらそちらを使う）
    origin: str  # 画面に出す配布元の名前
    terms: str  # 利用規約などの案内


SOURCES: dict[str, Source] = {
    "voice": Source(
        "voice",
        "VOICEVOX ENGINE 0.26.0（CPU 版）",
        "https://github.com/VOICEVOX/voicevox_engine/releases/download/0.26.0/"
        "voicevox_engine-windows-cpu-0.26.0.vvpp",
        1_925_791_376,
        "VOICEVOX 公式（GitHub: VOICEVOX/voicevox_engine）",
        "音声の利用規約はキャラクターごとに違います: https://voicevox.hiroshiba.jp/term/",
    ),
    "ffmpeg": Source(
        "ffmpeg",
        "FFmpeg（release essentials）",
        "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
        119_467_219,
        "gyan.dev（ffmpeg.org が案内している Windows 版の配布元）",
        "GPL v3 のソフトウェアです: https://ffmpeg.org/legal.html",
    ),
    "ollama": Source(
        "ollama",
        "Ollama（ローカル LLM を動かすアプリ）",
        "https://ollama.com/download/OllamaSetup.exe",
        1_953_587_080,
        "Ollama 公式（ollama.com）",
        "MIT ライセンス: https://github.com/ollama/ollama/blob/main/LICENSE",
    ),
}
# Ollama で入れる文章生成のモデル（公式ライブラリ）。大きさは ollama.com/library/qwen3 の値
LLM_MODEL = "qwen3:8b"
LLM_MODEL_SIZE = 5_200_000_000
VOICEVOX_PORT = 50021


def human_size(n: int) -> str:
    return f"{n / 1e9:.1f} GB" if n >= 1e9 else f"{n / 1e6:.0f} MB"


def tools_dir(app_dir: str) -> str:
    return os.path.join(app_dir, "tools")


# ---------- 探す ----------
def _walk_find(root: str, name: str, depth: int = 4) -> str | None:
    if not os.path.isdir(root):
        return None
    base = root.rstrip("\\/").count(os.sep)
    for d, dirs, files in os.walk(root):
        if d.count(os.sep) - base >= depth:
            dirs[:] = []
        if name in files:
            return os.path.join(d, name)
    return None


def find_voicevox(app_dir: str | None, localappdata: str = "", extra: str = "") -> str | None:
    """VOICEVOX ENGINE の run.exe。設定した場所 → アプリの tools → 隣の voicevox_engine →
    公式のエディタ（VOICEVOX アプリ）に入っているエンジンの順に探す。"""
    roots = [extra] if extra else []
    if app_dir:
        roots += [
            os.path.join(tools_dir(app_dir), "voicevox"),
            os.path.join(os.path.dirname(app_dir), "voicevox_engine"),
        ]
    if localappdata:
        roots.append(os.path.join(localappdata, "Programs", "VOICEVOX", "vv-engine"))
    for r in roots:
        hit = _walk_find(r, "run.exe")
        if hit:
            return hit
    return None


def voicevox_command(run_exe: str) -> list[str]:
    return [run_exe, "--host", "127.0.0.1", "--port", str(VOICEVOX_PORT)]


def find_ffmpeg(app_dir: str | None) -> str | None:
    if not app_dir:
        return None
    return _walk_find(os.path.join(tools_dir(app_dir), "ffmpeg"), "ffmpeg.exe")


def find_ollama(localappdata: str = "") -> str | None:
    if localappdata:
        p = os.path.join(localappdata, "Programs", "Ollama", "ollama.exe")
        if os.path.isfile(p):
            return p
    return None


# ---------- 起動する ----------
def start_process(cmd: list[str], cwd: str | None = None, log_path: str | None = None) -> Any:
    flags = CREATE_NO_WINDOW if sys.platform == "win32" else 0
    log = open(log_path, "ab") if log_path else subprocess.DEVNULL  # noqa: SIM115
    return subprocess.Popen(  # noqa: S603 - 固定の実行ファイルを固定の引数で起動
        cmd,
        cwd=cwd,
        stdout=log,
        stderr=log,
        stdin=subprocess.DEVNULL,
        creationflags=flags,
    )


# ---------- 入れる ----------
Opener = Callable[[urllib.request.Request, float], Any]


def remote_size(url: str, opener: Opener | None = None) -> int | None:
    """配布元の大きさ（HEAD）。取れなければ None。"""
    req = urllib.request.Request(url, method="HEAD")
    try:
        with (opener or _open)(req, 20) as r:
            n = r.headers.get("Content-Length")
            return int(n) if n else None
    except Exception:  # noqa: BLE001
        return None


def _open(req: urllib.request.Request, timeout: float) -> Any:
    return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 - SOURCES の https だけ


def download(
    src: Source,
    dest: str,
    progress: Callable[[float], None] = lambda _f: None,
    opener: Opener | None = None,
    cancelled: Callable[[], bool] = lambda: False,
) -> str:
    """公式の配布元からファイルを落とす。途中のファイルは .part に書き、終わったら名前を変える。"""
    if src.key not in SOURCES or SOURCES[src.key].url != src.url:
        raise BridgeError("決められた配布元以外からは入れません")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    part = dest + ".part"
    req = urllib.request.Request(src.url, headers={"User-Agent": "VideoAutomation-Blender"})
    got = 0
    with (opener or _open)(req, 60) as r, open(part, "wb") as f:
        total = int(r.headers.get("Content-Length") or src.size or 0)
        while True:
            if cancelled():
                raise BridgeError("ダウンロードを止めました")
            buf = r.read(1 << 20)
            if not buf:
                break
            f.write(buf)
            got += len(buf)
            if total:
                progress(min(1.0, got / total))
    if total and got != total:
        os.remove(part)
        raise BridgeError("ダウンロードが途中で切れました。もう一度試してください")
    os.replace(part, dest)
    return dest


def extract_zip(path: str, dest: str, progress: Callable[[float], None] = lambda _f: None) -> str:
    """zip（VOICEVOX の .vvpp も zip）を展開する。展開先の外へ出るパスは拒否する。"""
    os.makedirs(dest, exist_ok=True)
    root = os.path.realpath(dest)
    with zipfile.ZipFile(path) as z:
        names = z.infolist()
        for i, info in enumerate(names):
            target = os.path.realpath(os.path.join(dest, info.filename))
            if not target.startswith(root + os.sep) and target != root:
                raise BridgeError("展開できないファイルが含まれています")
            z.extract(info, dest)
            progress((i + 1) / max(1, len(names)))
    return dest
