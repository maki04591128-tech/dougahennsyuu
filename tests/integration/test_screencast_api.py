"""画面録画の自動ズーム API: 録画の素材から寄った映像を作り、新しい素材として登録する。"""

import shutil
import subprocess

import pytest

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


async def test_zoom_creates_asset(client, settings):
    src = settings.data_dir / "rec.mp4"
    # 2 秒目から右下に白い四角が出る録画（音声つき）
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x202020:s=320x200:d=4:r=30",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=4",
            "-vf",
            "drawbox=x=220:y=130:w=60:h=40:color=white:t=fill:enable='gte(t,2)'",
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(src),
        ],
        check=True,
    )
    pid = (await client.post("/projects", json={"name": "S"})).json()["id"]
    a = (await client.post("/assets/import", json={"project_id": pid, "path": str(src)})).json()
    job = await client.post(
        f"/projects/{pid}/screencast/zoom",
        json={"asset_id": a["id"], "clicks": [[1.5, 40, 30]], "zoom": 1.6},
    )
    done = await client.app.state.jobs.wait(job.json()["id"])
    assert done.status == "SUCCEEDED", done.error
    out = done.result
    assert out["kind"] == "video" and out["width"] == 320 and out["has_audio"]
    assert abs(out["duration_sec"] - 4.0) < 0.3
    bad = await client.post(
        f"/projects/{pid}/screencast/zoom", json={"asset_id": "nope", "clicks": []}
    )
    done = await client.app.state.jobs.wait(bad.json()["id"])
    assert done.status == "FAILED"
