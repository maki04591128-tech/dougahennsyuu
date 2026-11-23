"""実機テスト（Nemo 音声 → 字幕同期 → FFmpeg/libass で描画）。VA_LIVE_TESTS=1 のときだけ実行。

VA_LIVE_SUB_OUT にフォルダを指定すると、字幕焼き込み＋音声入りの確認用 MP4 と ASS/SRT を出力する。
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest
from asgi_lifespan import LifespanManager

from video_automation.api.app import create_app
from video_automation.app.settings import Settings

pytestmark = pytest.mark.skipif(os.environ.get("VA_LIVE_TESTS") != "1", reason="live only")

SCRIPT = (
    "ナレーター：こんにちは。動画編集の自動化システムで、字幕を音声に同期させるテストです。\n"
    "字幕は話している内容に合わせて、自動で改行され、表示のタイミングも音声の長さから計算されます。\n\n"
    "アシスタント：本当にずれずに表示されるのか、確認してみましょう。"
)


def _ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


async def test_live_subtitle_sync_and_render(tmp_path):
    out_dir = Path(os.environ.get("VA_LIVE_SUB_OUT") or tmp_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    settings = Settings(data_dir=tmp_path / "data")
    app = create_app(settings)
    report: dict = {}
    async with LifespanManager(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t/api/v1", timeout=600
        ) as c:
            pid = (await c.post("/projects", json={"name": "live-sub"})).json()["id"]
            sc = (
                await c.post(
                    "/scripts",
                    json={
                        "project_id": pid,
                        "text": SCRIPT,
                        "default_style_id": 10005,
                        "speaker_styles": {"アシスタント": 10001},
                    },
                )
            ).json()
            job = (await c.post(f"/scripts/{sc['id']}/voice")).json()
            done = await app.state.jobs.wait(job["id"])
            assert done.status == "SUCCEEDED", done.error
            segs = done.result["segments"]
            await c.post("/subtitles/generate", json={"script_id": sc["id"]})
            cues = (await c.post("/subtitles/synchronize", json={"script_id": sc["id"]})).json()
            segs = (await c.get(f"/scripts/{sc['id']}")).json()["segments"]
            report["segments"] = [
                {k: s[k] for k in ("speaker", "text", "start_frame", "end_frame")} for s in segs
            ]
            report["cues"] = cues
            for a, b in zip(cues, cues[1:], strict=False):
                assert a["start_frame"] < a["end_frame"] <= b["start_frame"]
            ass = (
                await c.post(
                    "/subtitles/export",
                    json={"script_id": sc["id"], "speaker_colors": {"アシスタント": "#FFE066"}},
                )
            ).json()
            srt = (
                await c.post("/subtitles/export", json={"script_id": sc["id"], "format": "srt"})
            ).json()
            audio = (await c.get("/voice/audio", params={"project_id": pid})).json()

    data = settings.data_dir
    shutil.copy(data / ass["path"], out_dir / "live_subtitle.ass")
    shutil.copy(data / srt["path"], out_dir / "live_subtitle.srt")
    ff = _ffmpeg()
    report["ffmpeg"] = ff
    if ff:
        total = segs[-1]["end_frame"] / 30 + 0.5
        audio_by_id = {a["id"]: a for a in audio}
        inputs, filters = [], []
        for i, s in enumerate(segs):
            inputs += ["-i", str(data / audio_by_id[s["audio_file_id"]]["path"])]
            ms = round(s["start_frame"] / 30 * 1000)
            filters.append(f"[{i + 1}:a]adelay={ms}|{ms}[a{i}]")
        mix = "".join(f"[a{i}]" for i in range(len(segs)))
        filters.append(f"{mix}amix=inputs={len(segs)}:normalize=0[aout]")
        ass_path = (out_dir / "live_subtitle.ass").as_posix().replace(":", "\\:")
        filters.append(f"[0:v]ass='{ass_path}'[vout]")
        mp4 = out_dir / "live_subtitle_preview.mp4"
        cmd = [
            ff,
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x203040:s=1920x1080:r=30:d={total:.2f}",
            *inputs,
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(mp4),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        report["ffmpeg_rc"], report["ffmpeg_err"] = r.returncode, r.stderr[-800:]
        assert r.returncode == 0, r.stderr
        # 1つ目の字幕の表示中と、字幕が出ていない冒頭フレームの差（字幕が描画されたか）
        mid = (cues[0]["start_frame"] + cues[0]["end_frame"]) / 2 / 30

        def grab(t: float) -> bytes:
            return subprocess.run(
                [
                    ff,
                    "-v",
                    "error",
                    "-ss",
                    f"{t:.3f}",
                    "-i",
                    str(mp4),
                    "-frames:v",
                    "1",
                    "-f",
                    "rawvideo",
                    "-pix_fmt",
                    "gray",
                    "-",
                ],
                capture_output=True,
                timeout=60,
            ).stdout

        with_sub, without = grab(mid), grab(0.0)
        diff = sum(1 for x, y in zip(with_sub, without, strict=False) if abs(x - y) > 40)
        report["changed_pixels"] = diff
        assert diff > 1000, diff
    (out_dir / "live_subtitle_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
