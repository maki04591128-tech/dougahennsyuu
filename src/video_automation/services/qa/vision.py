"""画像を読める LLM で、書き出した画面を確かめる（統合版 Q1）。

- TaskType.IMAGE_UNDERSTANDING に割り当てたモデルで、1 場面 1 枚（題名・カード・図表・画像の
  場面の、出そろった所の 1 フレーム）
- 質問は 4 つ。はい／いいえと、はいのときの場所で答えさせる（はい = 直す所がある）
- 2 案の比較は順番を入れ替えて 2 回聞き、2 回とも同じ案を選んだときだけ採る（compare）
- 美しさの点数は聞かない
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

from video_automation.schemas.provider import ChatMessage, TaskType

QUESTIONS: dict[str, str] = {
    "face_overlap": "文字が、人の顔やキャラクター（立ち絵）に重なっていますか",
    "cut_off": "文字が、画面の端や枠の外で切れていますか",
    "unreadable": "つぶれ・にじみ・背景と同じ色などで、読めない文字がありますか",
    "chart_mismatch": (
        "図表の数値が、下の台本の数値と合わない所がありますか（図表が無ければ「いいえ」）"
    ),
}
LABEL = {
    "face_overlap": "文字が顔・キャラクターに重なっています",
    "cut_off": "文字が見切れています",
    "unreadable": "読めない文字があります",
    "chart_mismatch": "図表の数値が台本と合いません",
}
_ANSWER = {
    "type": "object",
    "properties": {
        "answer": {"type": "string", "enum": ["はい", "いいえ"]},
        "where": {"type": "string"},  # はいのときの場所（「右上のテロップ」など）
    },
    "required": ["answer", "where"],
}
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {k: _ANSWER for k in QUESTIONS},
    "required": list(QUESTIONS),
}
COMPARE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"choice": {"type": "string", "enum": ["1", "2"]}},
    "required": ["choice"],
}
SCENE_ROLES = ("title_card", "card", "chart", "image")
MAX_SCENES = 24


@dataclass(frozen=True)
class Scene:
    role: str
    start: int  # フレーム
    end: int
    at: int  # 確かめるフレーム（出そろった所）


def scenes(clips: list[Any], roles: dict[str, str], max_n: int = MAX_SCENES) -> list[Scene]:
    """演出が作った場面（題名・カード・図表・画像）。続けて置いた同じ部品のクリップ
    （順に出すカードの段・繰り返した図表）は 1 つの場面にまとめる。"""
    items = sorted(
        (c for c in clips if roles.get(c.id) in SCENE_ROLES), key=lambda c: c.start_frame
    )
    out: list[list[Any]] = []
    for c in items:
        last = out[-1] if out else None
        if (
            last is not None
            and roles[last[-1].id] == roles[c.id]
            and abs(c.start_frame - last[-1].end_frame) <= 1
            and roles[c.id] != "title_card"
        ):
            last.append(c)
        else:
            out.append([c])
    res = []
    for run in out:
        a, b = run[0].start_frame, run[-1].end_frame
        tail = run[-1]  # 出そろった最後の段の、入りが終わった後（70% の所）
        at = tail.start_frame + round((tail.end_frame - tail.start_frame) * 0.7)
        res.append(Scene(roles[run[0].id], a, b, min(max(a, at), b - 1)))
    if len(res) > max_n:  # 多すぎるときは均等に間引く
        res = [res[round(i * (len(res) - 1) / (max_n - 1))] for i in range(max_n)]
    return res


def prompt(script: str) -> str:
    qs = "\n".join(f"- {k}: {q}" for k, q in QUESTIONS.items())
    return (
        "動画の 1 場面の画像です。次の 4 つの質問に、画像だけを見て"
        "「はい」か「いいえ」で答えてください。"
        "「はい」のときは where に場所（例: 右上のテロップ、中央のカードの 2 行目）を書き、"
        "「いいえ」のときは where を空にします。見た目の良さの評価はしません。\n"
        f"{qs}\n台本（この場面で話している所）:\n{script or '（なし）'}"
    )


async def ask(
    llm: Any, png: bytes, script: str, model_key: str | None = None
) -> dict[str, tuple[bool, str]]:
    """4 つの質問の答え（はいなら True と場所）。"""
    img = base64.b64encode(png).decode("ascii")
    res = await llm.complete_json(
        TaskType.IMAGE_UNDERSTANDING,
        [ChatMessage(role="user", content=prompt(script), images=[img])],
        SCHEMA,
        model_key=model_key,
        temperature=0.0,
        retries=1,
    )
    out: dict[str, tuple[bool, str]] = {}
    for k in QUESTIONS:
        v = res.data.get(k) or {}
        yes = str(v.get("answer", "")).strip() in ("はい", "yes", "Yes")
        out[k] = (yes, str(v.get("where", "")).strip()[:80] if yes else "")
    return out


async def compare(
    llm: Any, a: bytes, b: bytes, question: str, model_key: str | None = None
) -> str | None:
    """2 案（画像 a・b）のどちらが question に合うか。順番を入れ替えて 2 回聞き、
    2 回とも同じ案を選んだときだけ "a" か "b" を返す（位置の偏りで選ばれた答えは採らない）。"""

    async def pick(first: bytes, second: bytes) -> str:
        imgs = [base64.b64encode(x).decode("ascii") for x in (first, second)]
        res = await llm.complete_json(
            TaskType.IMAGE_UNDERSTANDING,
            [
                ChatMessage(
                    role="user",
                    content=(
                        f"1 枚目と 2 枚目の画像のうち、次の問いに合う方を選んでください: {question}"
                        "。見た目の良さの点数は付けず、choice に 1 か 2 だけを書きます。"
                    ),
                    images=imgs,
                )
            ],
            COMPARE_SCHEMA,
            model_key=model_key,
            temperature=0.0,
            retries=1,
        )
        return str(res.data.get("choice", "")).strip()

    one = await pick(a, b)
    two = await pick(b, a)
    if one == "1" and two == "2":
        return "a"
    if one == "2" and two == "1":
        return "b"
    return None
