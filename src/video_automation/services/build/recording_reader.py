"""画面録画の中身を画像の分かる AI（ローカル LLM の画像入力）に読ませ、台本の参考にする。

- 場面が大きく変わった所（と最初）から最大 MAX_SHOTS 枚を抜き出し、1 枚ずつ「何をしている画面か」
  を短く書かせる。時刻つきの手順として台本の参考資料に足す
- 画像入力のモデルが無ければ何もしない（台本は文章の指示だけで作る）
- 画面に個人情報が映っていても、説明には書かせない（指示で禁じる）
"""

from __future__ import annotations

import base64
import re
import tempfile
from pathlib import Path

from video_automation.integrations.ffmpeg.runner import FFmpeg, filter_path
from video_automation.schemas.provider import ChatMessage, TaskType
from video_automation.services.llm_tasks.gateway import LLMGateway

MAX_SHOTS = 6
SCENE_THRESHOLD = 0.25
PROMPT = (
    "これは操作説明の動画にする画面録画の 1 場面です。画面で何が表示され、"
    "利用者が何をしている（しようとしている）かを、日本語で 1〜2 文にまとめてください。"
    "アプリ名・ボタン名・メニュー名など画面に書かれた語はそのまま使います。"
    "メールアドレス・名前・パスワード・キーなど個人の情報は書きません。"
)


async def extract_shots(
    ffmpeg: FFmpeg, video: Path, out_dir: Path, duration: float
) -> list[tuple[float, Path]]:
    """場面の変わり目（無ければ等間隔）の画像（時刻, パス）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    await ffmpeg.run(
        [
            "-i",
            str(video),
            "-vf",
            f"select='eq(n,0)+gt(scene,{SCENE_THRESHOLD})',"
            f"metadata=print:file='{filter_path(out_dir / 'times.txt')}',scale=768:-2",
            "-fps_mode",
            "vfr",
            "-frames:v",
            str(MAX_SHOTS * 3),
            str(out_dir / "s_%03d.jpg"),
        ],
        timeout=600,
    )
    files = sorted(out_dir.glob("s_*.jpg"))
    if len(files) < 2 and duration > 4:
        for f in files:
            f.unlink()
        step = duration / MAX_SHOTS
        files = []
        for i in range(MAX_SHOTS):
            t = round(step * (i + 0.5), 2)
            p = out_dir / f"e_{i:03d}.jpg"
            await ffmpeg.run(
                ["-ss", str(t), "-i", str(video), "-frames:v", "1", "-vf", "scale=768:-2", str(p)]
            )
            files.append(p)
        return [(round(step * (i + 0.5), 1), p) for i, p in enumerate(files)]
    times = [
        float(m.group(1))
        for m in re.finditer(r"pts_time:([0-9.]+)", (out_dir / "times.txt").read_text("utf-8"))
    ]
    shots = [(round(times[i], 1) if i < len(times) else 0.0, f) for i, f in enumerate(files)]
    if len(shots) > MAX_SHOTS:  # 多すぎるときは均等に間引く
        shots = [shots[round(i * (len(shots) - 1) / (MAX_SHOTS - 1))] for i in range(MAX_SHOTS)]
    return shots


async def describe(
    llm: LLMGateway, ffmpeg: FFmpeg, video: Path, duration: float, model_key: str | None = None
) -> str:
    """録画の場面ごとの説明（「[0:12] 〜」の行）。読めなければ空文字。"""
    await llm.resolve(TaskType.IMAGE_UNDERSTANDING, model_key)  # 画像入力のモデルが無ければ例外
    lines: list[str] = []
    with tempfile.TemporaryDirectory(prefix="va_rec_") as tmp:
        shots = await extract_shots(ffmpeg, video, Path(tmp), duration)
        for t, path in shots:
            img = base64.b64encode(path.read_bytes()).decode("ascii")
            res = await llm.complete_text(
                TaskType.IMAGE_UNDERSTANDING,
                [ChatMessage(role="user", content=PROMPT, images=[img])],
                model_key=model_key,
                temperature=0.2,
            )
            text = " ".join(res.text.split())[:200]
            if text:
                lines.append(f"[{int(t // 60)}:{int(t % 60):02d}] {text}")
    return "\n".join(lines)
