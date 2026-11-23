"""ツール呼び出しフォールバック解析。

tool_calls を返さず、本文に {"name": ..., "arguments": {...}} を書くモデル
（実機では Qwen3-Swallow-8B で確認）向け。要求したツール名に一致するものだけ採用する。
"""

import json
import re
from typing import Any

from video_automation.schemas.provider import ToolCall

_TAGGED = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.S)
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def _json_objects(text: str) -> list[Any]:
    """本文中の JSON 値（オブジェクト/配列）を順に取り出す。"""
    out: list[Any] = []
    candidates = _TAGGED.findall(text) + _FENCE.findall(text)
    if not candidates:
        candidates = [text]
    decoder = json.JSONDecoder()
    for chunk in candidates:
        i = 0
        while i < len(chunk):
            ch = chunk[i]
            if ch in "{[":
                try:
                    obj, end = decoder.raw_decode(chunk, i)
                except json.JSONDecodeError:
                    i += 1
                    continue
                out.append(obj)
                i = end
            else:
                i += 1
    return out


def _to_call(obj: Any, allowed: set[str]) -> ToolCall | None:
    if not isinstance(obj, dict):
        return None
    if "function" in obj and isinstance(obj["function"], dict):  # OpenAI形式
        obj = obj["function"]
    name = obj.get("name")
    if not isinstance(name, str) or name not in allowed:
        return None
    args = obj.get("arguments", obj.get("parameters", {}))
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            return None
    if not isinstance(args, dict):
        return None
    return ToolCall(name=name, arguments=args)


def parse_tool_calls(text: str, allowed_names: set[str]) -> list[ToolCall]:
    if not text or not allowed_names:
        return []
    calls: list[ToolCall] = []
    for obj in _json_objects(text):
        items = obj if isinstance(obj, list) else [obj]
        for item in items:
            call = _to_call(item, allowed_names)
            if call:
                calls.append(call)
    return calls
