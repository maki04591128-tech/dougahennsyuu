"""多言語の字幕（統合版 W5）。

台本の字幕を LLM で訳し、別の字幕ファイル（SRT）に書き出す。焼き込みの字幕は変えない。
英語は 1 行 42 字・最大 2 行。2 行にするときは下の行が長い形（ピラミッド）で切る。
読む速さは 1 秒 20 字（子ども向けは 17 字）を超えたら知らせる。
"""

from __future__ import annotations

import json
from typing import Any

from video_automation.schemas.provider import ChatMessage, TaskType

LANGUAGES = {"en": "English"}
LINE_MAX = {"en": 42}
CPS_MAX, CPS_KIDS = 20.0, 17.0
BATCH = 40  # 1 回に LLM へ渡す字幕の数


def wrap(text: str, limit: int = 42) -> str:
    """1 行 limit 字。2 行は語の切れ目で、下の行が上と同じか長い所のうち差が最も小さい所で切る。"""
    t = " ".join(text.split())
    if len(t) <= limit:
        return t
    words = t.split(" ")
    best: tuple[int, str, str] | None = None
    for i in range(1, len(words)):
        top, bottom = " ".join(words[:i]), " ".join(words[i:])
        if len(top) > limit or len(bottom) > limit:
            continue
        diff = len(bottom) - len(top)
        if diff < 0:  # 下の行を長くする
            diff = 1000 - diff
        if best is None or diff < best[0]:
            best = (diff, top, bottom)
    if best is None:  # 2 行に収まらないときは、半分の所で切る（長すぎることは読む速さで知らせる）
        mid = max(1, len(words) // 2)
        return " ".join(words[:mid]) + "\n" + " ".join(words[mid:])
    return f"{best[1]}\n{best[2]}"


def reading_speed(text: str, sec: float) -> float:
    """1 秒あたりの字数（改行・前後の空白を数えない）。"""
    return len(text.replace("\n", "").strip()) / max(sec, 0.01)


SCHEMA = {
    "type": "object",
    "properties": {"lines": {"type": "array", "items": {"type": "string"}}},
    "required": ["lines"],
}


async def translate(llm: Any, texts: list[str], lang: str) -> list[str]:
    """字幕の文を訳す（同じ数・同じ順）。数が合わなければ LLMError と同じく例外にする。"""
    name = LANGUAGES[lang]
    out: list[str] = []
    for i in range(0, len(texts), BATCH):
        part = texts[i : i + BATCH]
        msgs = [
            ChatMessage(
                role="system",
                content=(
                    f"You translate Japanese video subtitles into natural, concise {name}. "
                    "Keep the same number of lines in the same order, one translation per line. "
                    'Return only JSON: {"lines": ["...", ...]}.'
                ),
            ),
            ChatMessage(
                role="user",
                content=json.dumps({"lines": part}, ensure_ascii=False),
            ),
        ]
        res = await llm.complete_json(TaskType.SUBTITLE, msgs, SCHEMA, temperature=0.2, retries=2)
        lines = [str(x) for x in res.data.get("lines", [])]
        if len(lines) != len(part):
            raise ValueError(f"訳の数が合いません（{len(lines)} / {len(part)}）")
        out += lines
    return out
