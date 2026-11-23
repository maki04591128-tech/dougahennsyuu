"""Windows 配布用 zip を作る（P9-07）。

  uv run python tools/build_release.py            # dist/VideoAutomation-<version>-windows.zip

zip にはアプリ本体（src）・依存の固定版（uv.lock）・説明書（docs）と、展開したフォルダの直下に
install.bat / uninstall.bat を入れる。テストや開発用ファイルは入れない。
"""

import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INCLUDE_DIRS = ("src", "docs")
INCLUDE_FILES = ("pyproject.toml", "uv.lock", "README.md", ".env.example")
PACKAGING = ROOT / "packaging" / "windows"


def tracked_files() -> list[Path]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        files = [ROOT / line for line in out.splitlines() if line]
    except (OSError, subprocess.CalledProcessError):
        files = [p for p in ROOT.rglob("*") if p.is_file()]
    keep = []
    for p in files:
        rel = p.relative_to(ROOT)
        if "__pycache__" in rel.parts or not p.is_file():
            continue
        if rel.parts[0] in INCLUDE_DIRS or rel.as_posix() in INCLUDE_FILES:
            keep.append(p)
    return sorted(keep)


def build(out_dir: Path | None = None) -> Path:
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    out_dir = out_dir or ROOT / "dist"
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"VideoAutomation-{version}-windows"
    out = out_dir / f"{name}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in tracked_files():
            z.write(p, f"{name}/{p.relative_to(ROOT).as_posix()}")
        for p in sorted(PACKAGING.iterdir()):
            z.write(p, f"{name}/{p.name}")
    return out


if __name__ == "__main__":
    path = build(Path(sys.argv[1]) if len(sys.argv) > 1 else None)
    print(path)
