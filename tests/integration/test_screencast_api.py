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


async def test_zoom_finish_blurs_pii_and_speeds_up_still_silence(client, settings):
    from video_automation.integrations.ocr.engine import FakeOcr, OcrLine, OcrResult, OcrWord

    src = settings.data_dir / "rec2.mp4"
    # 6 秒の無音の録画。左上に白い四角（メールの文字のつもり）、4 秒目から右下が変わる
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x202020:s=320x200:d=6:r=30",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=mono",
            "-vf",
            "drawbox=x=10:y=10:w=80:h=16:color=white:t=fill,"
            "drawbox=x=220:y=130:w=60:h=40:color=white:t=fill:enable='gte(t,4)'",
            "-t",
            "6",
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
    mail = OcrResult(320, 200, [OcrLine([OcrWord("me@example.com", 10, 10, 80, 16)])])
    # f000003 = 1.0 秒のサンプル（0.5 秒ごと、1 枚目は 0 秒）
    client.app.state.container.screencast.ocr = FakeOcr({"f000003": mail})
    pid = (await client.post("/projects", json={"name": "S"})).json()["id"]
    a = (await client.post("/assets/import", json={"project_id": pid, "path": str(src)})).json()
    job = await client.post(
        f"/projects/{pid}/screencast/zoom",
        json={"asset_id": a["id"], "keys": [[4.2, "Ctrl+S"]], "cursor": [[4.0, 250, 150]]},
    )
    done = await client.app.state.jobs.wait(job.json()["id"])
    assert done.status == "SUCCEEDED", done.error
    out = done.result
    fin = out["finish"]
    assert [p["kind"] for p in fin["pii"]] == ["email"]
    assert "example" not in fin["pii"][0]["text"]
    assert fin["fast"] and fin["fast"][0][1] - fin["fast"][0][0] >= 2.0
    assert out["duration_sec"] < 5.0  # 止まっていた所が 4 倍速
    assert out["has_audio"]
