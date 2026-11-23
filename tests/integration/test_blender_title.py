"""Blender Adapter（偽の blender 実行ファイル）とタイトルカード生成。実 Blender は live テストで確認。"""

import os
import shutil
import stat
import sys
import textwrap

import httpx
import pytest
from asgi_lifespan import LifespanManager

from fakes import FakeProtector
from video_automation.api.app import create_app
from video_automation.integrations.blender.runner import Blender, BlenderError


def _fake_blender(tmp_path):
    ff = shutil.which("ffmpeg")
    script = tmp_path / "blender"
    script.write_text(
        textwrap.dedent(f"""\
        #!{sys.executable}
        import json, subprocess, sys
        from pathlib import Path
        if "--version" in sys.argv:
            print("Blender 5.3.0"); sys.exit(0)
        a = json.loads(Path(sys.argv[sys.argv.index("--") + 1]).read_text(encoding="utf-8"))
        assert sys.argv[sys.argv.index("--python") + 1].endswith("title_card.py")
        out = Path(a["output_dir"])
        subprocess.run(["{ff}", "-v", "error", "-y", "-f", "lavfi", "-i",
            f"color=c=white@0.5:s={{a['width']}}x{{a['height']}}:r={{a['fps']}}",
            "-frames:v", str(a["frames"]), "-pix_fmt", "rgba", str(out / "frame_%04d.png")],
            check=True)
        Path(a["result_path"]).write_text(json.dumps({{"frames": a["frames"],
            "engine": "FAKE", "font_loaded": bool(a.get("font_path"))}}))
    """),
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


async def test_blender_runner_rejects_unknown_scripts(tmp_path):
    b = Blender(_fake_blender(tmp_path))
    assert b.available and await b.version() == "5.3.0"
    with pytest.raises(BlenderError):
        await b.run_script("rm_rf", {})


async def test_title_card_creates_alpha_video_asset(tmp_path, media_settings, fake):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg")
    s = media_settings.model_copy(update={"blender_path": _fake_blender(tmp_path)})
    app = create_app(s, http_transport=fake.transport, protector=FakeProtector())
    async with LifespanManager(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t/api/v1"
        ) as c:
            pid = (
                await c.post("/projects", json={"name": "T", "width": 320, "height": 180})
            ).json()["id"]
            r = await c.post(
                "/assets/title_card",
                json={"project_id": pid, "text": "タイトル", "duration_sec": 1},
            )
            job = await app.state.jobs.wait(r.json()["id"])
            assert job.status == "SUCCEEDED", job.error
            a = job.result
            assert a["kind"] == "video" and a["source"] == "blender"
            assert (a["width"], a["height"]) == (320, 180) and abs(a["duration_sec"] - 1) < 0.1
            assert (await c.get("/render/engines")).json()["blender"]["version"] == "5.3.0"
            bad = await c.post(
                "/assets/title_card", json={"project_id": pid, "text": "x", "text_color": "red"}
            )
            assert bad.status_code == 422
    assert os.path.exists(s.data_dir / a["path"])
