"""Blender アドオンの導入の補助（bpy を使わない部分）。"""

import importlib.util
import io
import sys
import types
import zipfile
from pathlib import Path

import pytest

_dir = Path(__file__).parents[2] / "tools/blender_addon/video_automation_bridge"


def _load():
    # パッケージの __init__ は bpy を読むので、client と setup_assist だけを読む
    pkg = types.ModuleType("va_addon_pkg")
    pkg.__path__ = [str(_dir)]
    sys.modules["va_addon_pkg"] = pkg
    for name in ("client", "setup_assist"):
        spec = importlib.util.spec_from_file_location(f"va_addon_pkg.{name}", _dir / f"{name}.py")
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"va_addon_pkg.{name}"] = mod
        spec.loader.exec_module(mod)
    return sys.modules["va_addon_pkg.setup_assist"], sys.modules["va_addon_pkg.client"]


sa, client = _load()


class _Resp(io.BytesIO):
    def __init__(self, data: bytes, length: int | None = None):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data) if length is None else length)}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def test_sources_are_official_https():
    for s in sa.SOURCES.values():
        assert s.url.startswith("https://") and s.size > 0 and s.origin and s.terms
    hosts = {s.url.split("/")[2] for s in sa.SOURCES.values()}
    assert hosts == {"github.com", "www.gyan.dev", "ollama.com"}


def test_download_writes_file_and_reports_progress(tmp_path):
    data = b"x" * (3 << 20)
    seen = []
    out = sa.download(
        sa.SOURCES["ffmpeg"],
        str(tmp_path / "dl" / "f.zip"),
        seen.append,
        opener=lambda req, t: _Resp(data),
    )
    assert Path(out).read_bytes() == data and seen[-1] == 1.0
    assert not (tmp_path / "dl" / "f.zip.part").exists()
    # 途中で切れたら残さない
    with pytest.raises(client.BridgeError, match="途中で切れ"):
        sa.download(
            sa.SOURCES["ffmpeg"], str(tmp_path / "g.zip"), opener=lambda r, t: _Resp(data, 9 << 20)
        )
    assert not (tmp_path / "g.zip").exists() and not (tmp_path / "g.zip.part").exists()
    # 決められた配布元以外は拒否
    bad = sa.Source("ffmpeg", "x", "https://evil.example/x.zip", 1, "x", "x")
    with pytest.raises(client.BridgeError, match="配布元"):
        sa.download(bad, str(tmp_path / "h.zip"), opener=lambda r, t: _Resp(b"1"))


def test_extract_zip_and_find_tools(tmp_path):
    app = tmp_path / "VideoAutomation"
    z = tmp_path / "e.vvpp"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("run.exe", b"")
        f.writestr("model/a.bin", b"1")
    sa.extract_zip(str(z), str(app / "tools" / "voicevox"))
    exe = sa.find_voicevox(str(app))
    assert exe and exe.endswith("run.exe")
    assert sa.voicevox_command(exe)[1:] == ["--host", "127.0.0.1", "--port", "50021"]
    # 隣の voicevox_engine（手で入れた場合）も見つける
    other = tmp_path / "voicevox_engine" / "engine-0.26.0" / "windows-cpu"
    other.mkdir(parents=True)
    (other / "run.exe").write_bytes(b"")
    assert sa.find_voicevox(str(tmp_path / "VideoAutomation")) == exe  # tools/ が先
    found = sa.find_voicevox(str(tmp_path / "other_app"))
    assert found and "voicevox_engine" in found
    assert sa.find_voicevox(str(tmp_path / "a" / "b")) is None
    # 展開先の外へ出るパスは拒否
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as f:
        f.writestr("../outside.txt", b"x")
    with pytest.raises(client.BridgeError):
        sa.extract_zip(str(evil), str(tmp_path / "ex"))
    assert not (tmp_path / "outside.txt").exists()
    assert sa.human_size(1_925_791_376) == "1.9 GB" and sa.human_size(119_467_219) == "119 MB"
