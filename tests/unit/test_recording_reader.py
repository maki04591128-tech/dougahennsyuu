"""画面録画の場面を画像の分かる AI に読ませる（AI は差し替え・FFmpeg は本物）。"""

import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from video_automation.integrations.ffmpeg.runner import FFmpeg
from video_automation.services.build.recording_reader import describe, extract_shots

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg")


def _rec(path: Path) -> Path:
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", "color=red:s=320x180:d=2,format=yuv420p",
            "-f", "lavfi", "-i", "color=blue:s=320x180:d=2,format=yuv420p",
            "-filter_complex", "[0][1]concat=n=2:v=1[v]", "-map", "[v]", "-r", "30", str(path),
        ],
        check=True,
    )  # fmt: skip
    return path


async def test_extract_shots_at_scene_changes(tmp_path):
    shots = await extract_shots(FFmpeg(), _rec(tmp_path / "r.mp4"), tmp_path / "s", 4.0)
    assert [t for t, _ in shots] == [0.0, 2.0] and all(p.is_file() for _, p in shots)


async def test_describe_sends_each_shot_as_image(tmp_path):
    calls = []

    class FakeLLM:
        async def resolve(self, task, model_key=None):
            return "vision-model"

        async def complete_text(self, task, messages, model_key=None, temperature=None):
            calls.append(messages[0])
            return SimpleNamespace(text=f"場面 {len(calls)}: 設定画面を開いている")

    text = await describe(FakeLLM(), FFmpeg(), _rec(tmp_path / "r.mp4"), 4.0)
    assert text.splitlines() == [
        "[0:00] 場面 1: 設定画面を開いている",
        "[0:02] 場面 2: 設定画面を開いている",
    ]
    assert all(m.images and "個人の情報は書きません" in m.content for m in calls)
