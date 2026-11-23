"""Blender Adapter (P5-02)。

Blender は別プロセス（--background --factory-startup）で起動し、本パッケージ同梱の
固定スクリプトに JSON 引数を渡して実行する。LLM が生成したコードは実行しない（仕様 §9）。
Python API のバージョン差は同梱スクリプト側で吸収する。
"""

import asyncio
import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).parent / "scripts"
ALLOWED_SCRIPTS = {"title_card", "infographic"}
CANDIDATES = [
    r"D:\Steam\steamapps\common\Blender\blender.exe",
    r"C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe",
    r"C:\Program Files\Blender Foundation\Blender\blender.exe",
]


class BlenderError(Exception):
    pass


class BlenderNotFoundError(BlenderError):
    pass


class Blender:
    def __init__(self, executable: str | None = None) -> None:
        self.executable = executable or self._find()
        self._version: str | None = None

    @staticmethod
    def _find() -> str | None:
        found = shutil.which("blender")
        if found:
            return found
        for c in CANDIDATES:
            if Path(c).is_file():
                return c
        base = Path(r"C:\Program Files\Blender Foundation")
        if base.is_dir():
            for exe in sorted(base.glob("Blender*/blender.exe"), reverse=True):
                return str(exe)
        return None

    @property
    def available(self) -> bool:
        return bool(self.executable and Path(self.executable).is_file())

    async def _exec(self, *args: str, timeout: float) -> tuple[int, str]:
        if not self.available:
            raise BlenderNotFoundError("blender executable not found")
        proc = await asyncio.create_subprocess_exec(
            self.executable or "",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout)
        except (TimeoutError, asyncio.CancelledError):
            proc.kill()
            await proc.wait()
            raise
        return proc.returncode or 0, out.decode("utf-8", "replace")

    async def version(self) -> str:
        if self._version is None:
            _, out = await self._exec("--factory-startup", "--version", timeout=60)
            m = re.search(r"Blender (\d+\.\d+(?:\.\d+)?)", out)
            self._version = m.group(1) if m else "unknown"
        return self._version

    async def run_script(self, name: str, args: dict[str, Any], timeout: float = 900) -> dict:
        """同梱スクリプト name を実行し、スクリプトが出力した結果JSONを返す。"""
        if name not in ALLOWED_SCRIPTS:
            raise BlenderError(f"script not allowed: {name}")
        with tempfile.TemporaryDirectory(prefix="va_blender_") as tmp:
            args_path = Path(tmp) / "args.json"
            result_path = Path(tmp) / "result.json"
            args_path.write_text(
                json.dumps({**args, "result_path": str(result_path)}, ensure_ascii=False),
                encoding="utf-8",
            )
            rc, out = await self._exec(
                "--background",
                "--factory-startup",
                "--python-exit-code",
                "1",
                "--python",
                str(SCRIPTS_DIR / f"{name}.py"),
                "--",
                str(args_path),
                timeout=timeout,
            )
            if rc != 0 or not result_path.is_file():
                raise BlenderError(f"blender script failed (rc={rc}): {out.strip()[-500:]}")
            return json.loads(result_path.read_text(encoding="utf-8"))
