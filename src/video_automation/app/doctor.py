"""動作環境の確認（`video-automation --doctor`）。配布物のセットアップと問題の切り分けに使う。

外部エンジン（FFmpeg・Blender・Ollama/LM Studio・VOICEVOX Nemo・ACE-Step）の有無と状態、
データフォルダの書き込み可否を調べ、必須の欠落があれば終了コード 1 を返す。
"""

import json
import platform
import sys
from typing import Any

from video_automation.app.bootstrap import build_container
from video_automation.app.settings import APP_VERSION, Settings
from video_automation.integrations.ffmpeg.runner import FFmpegNotFoundError


async def diagnose(settings: Settings) -> dict[str, Any]:
    out: dict[str, Any] = {
        "app_version": APP_VERSION,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "data_dir": str(settings.data_dir.resolve()),
    }
    c = await build_container(settings, configure_logs=False)
    try:
        probe = settings.data_dir / ".write_test"
        try:
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            out["data_dir_writable"] = True
        except OSError:
            out["data_dir_writable"] = False
        try:
            out["ffmpeg"] = {
                "found": True,
                "version": await c.ffmpeg.version(),
                "nvenc": await c.ffmpeg.nvenc_usable(),
            }
        except FFmpegNotFoundError:
            out["ffmpeg"] = {"found": False}
        out["blender"] = (
            {"found": True, "version": await c.blender.version()}
            if c.blender.available
            else {"found": False}
        )
        d = await c.providers.discover()
        out["llm"] = {
            "providers": {p.provider_id: p.available for p in d.providers},
            "models": [m.model_id for m in d.models],
        }
        out["voice"] = {
            e.engine_id: {"available": e.available, "version": e.version}
            for e in await c.voice.engines()
        }
        m = await c.music.status()
        out["music"] = {"available": m.available, "installed": m.installed}
    finally:
        await c.aclose()
    problems: list[str] = []
    if not out["data_dir_writable"]:
        problems.append("データフォルダに書き込めません")
    if not out["ffmpeg"]["found"]:
        problems.append("FFmpeg が見つかりません（必須）")
    if not any(out["llm"]["providers"].values()):
        problems.append("ローカル LLM（Ollama / LM Studio）に接続できません")
    if not any(v["available"] for v in out["voice"].values()):
        problems.append("音声合成エンジン（VOICEVOX Nemo / VOICEVOX）に接続できません")
    out["problems"] = problems
    out["required_ok"] = out["data_dir_writable"] and out["ffmpeg"]["found"]
    return out


def format_text(r: dict[str, Any]) -> str:
    def yn(v: Any) -> str:
        return "OK" if v else "なし"

    lines = [
        f"VideoAutomation {r['app_version']} / Python {r['python']} / {r['platform']}",
        f"データフォルダ: {r['data_dir']}（書き込み {yn(r['data_dir_writable'])}）",
        f"FFmpeg: {r['ffmpeg'].get('version', 'なし')}"
        + (f"（NVENC {yn(r['ffmpeg'].get('nvenc'))}）" if r["ffmpeg"]["found"] else ""),
        f"Blender: {r['blender'].get('version', 'なし')}（文字アニメーション・図表に使用）",
        "LLM: "
        + ", ".join(f"{k}={yn(v)}" for k, v in r["llm"]["providers"].items())
        + f"（モデル {len(r['llm']['models'])} 個）",
        "音声: " + ", ".join(f"{k}={yn(v['available'])}" for k, v in r["voice"].items()),
        f"音楽生成 ACE-Step: {yn(r['music']['available'])}"
        + ("" if r["music"]["available"] else "（BGM 生成を使うときだけ必要）"),
    ]
    if r["problems"]:
        lines.append("確認が必要:")
        lines += [f"  - {p}" for p in r["problems"]]
    else:
        lines.append("問題は見つかりませんでした")
    return "\n".join(lines)


def run(settings: Settings, as_json: bool = False) -> int:
    import asyncio
    import logging

    import structlog

    # パイプ・ファイルへ出力するとき（インストーラーのログ等）は UTF-8 にする（既定は cp932）
    if not sys.stdout.isatty() and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    # 結果だけを出す（内部の info ログは出さない）
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING))
    r = asyncio.run(diagnose(settings))
    print(json.dumps(r, ensure_ascii=False, indent=2) if as_json else format_text(r))
    return 0 if r["required_ok"] else 1
