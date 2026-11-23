"""動作環境の確認（--doctor）。外部エンジンは偽物で、FFmpeg は実物。"""

import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))


async def test_doctor_reports_engines(tmp_path, monkeypatch):
    from fakes import FakeProtector, FakeServers
    from video_automation.app import bootstrap, doctor
    from video_automation.app.settings import Settings

    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    fake = FakeServers()
    orig = bootstrap.build_container

    async def patched(settings, **kw):
        kw.update(http_transport=fake.transport, protector=FakeProtector())
        return await orig(settings, **kw)

    monkeypatch.setattr(doctor, "build_container", patched)
    s = Settings(data_dir=tmp_path, acestep_path=tmp_path / "no", blender_path=str(tmp_path / "nb"))
    r = await doctor.diagnose(s)
    assert r["required_ok"] and r["data_dir_writable"] and r["ffmpeg"]["found"]
    assert r["llm"]["providers"]["ollama-local"] is True and len(r["llm"]["models"]) == 3
    assert r["voice"]["voicevox-nemo"]["available"] is True
    assert r["blender"] == {"found": False} and r["music"]["available"] is False
    text = doctor.format_text(r)
    assert "FFmpeg:" in text and "Blender: なし" in text
    fake.ollama_up = False
    fake.nemo.up = False
    r = await doctor.diagnose(s)
    assert any("LLM" in p for p in r["problems"]) and any("音声" in p for p in r["problems"])
    assert r["required_ok"]  # LLM・音声は任意（使う機能だけ止まる）
