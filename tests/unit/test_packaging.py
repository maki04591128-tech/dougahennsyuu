"""Windows 配布物（P9-07）: zip の中身と、スクリプトの文字コード・改行。"""

import importlib.util
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location("build_release", ROOT / "tools/build_release.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_release_zip_contents(tmp_path):
    out = _load().build(tmp_path)
    assert out.name.startswith("VideoAutomation-") and out.name.endswith("-windows.zip")
    names = zipfile.ZipFile(out).namelist()
    top = out.stem
    for must in (
        "install.bat",
        "install.ps1",
        "uninstall.bat",
        "uninstall.ps1",
        "pyproject.toml",
        "uv.lock",
        "docs/USER_MANUAL.md",
        "src/video_automation/app/main.py",
        "src/video_automation/integrations/blender/scripts/title_card.py",
    ):
        assert f"{top}/{must}" in names, must
    assert not any("/tests/" in n or n.startswith(f"{top}/tests") for n in names)
    assert not any("__pycache__" in n or n.endswith(".pyc") for n in names)
    assert not any(n.startswith(f"{top}/data/") for n in names)


def test_windows_scripts_encoding():
    d = ROOT / "packaging" / "windows"
    for ps1 in d.glob("*.ps1"):
        raw = ps1.read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf"), f"{ps1.name}: PowerShell 5.1 は BOM が必要"
        assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b""), ps1.name
    for bat in d.glob("*.bat"):
        raw = bat.read_bytes()
        raw.decode("ascii")  # cmd.exe の文字化けを避けるため ASCII のみ
        assert b"\n" not in raw.replace(b"\r\n", b""), bat.name
