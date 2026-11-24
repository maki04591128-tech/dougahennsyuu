"""セリフとセリフの間（無音）の長さ。一律にせず、会話のリズムで決める。

数値は voice-tempo-intonation スキルの表に合わせる:
- 同じ人が続けて話す（読点で区切っただけ）0.10〜0.15 秒／次の文へ 0.20〜0.30 秒
- 話し手の交代: 相づち・即答 0.10〜0.20 秒／質問への答え 0.25〜0.40 秒
- 驚き・オチの前（ため）0.50〜0.80 秒／章・話題の切り替え 0.70〜1.00 秒
音声の前後の無音（prePhonemeLength / postPhonemeLength）とは別に足す。
"""

from __future__ import annotations

SAME_CONT = 0.12  # 同じ人が「、」で終わった続き
SAME_NEXT = 0.22  # 同じ人が次の文へ
SWAP_QUICK = 0.14  # 交代して相づち・即答・驚き
SWAP_ANSWER = 0.32  # 質問への答え
SWAP_DEFAULT = 0.24
BEFORE_REVEAL = 0.6  # 「実は」「なんと」の前のため
SCENE = 0.85  # 章・話題の切り替え
_SHORT = 7  # これ以下の文字数は相づち・即答（「なるほど！」「えっ？」）
_REVEAL = ("実は", "じつは", "なんと", "ところが", "結論から", "答えは", "正解は")


def _core(text: str) -> str:
    return text.strip().rstrip("。、！？!?…〜ー 　")


def line_gap(
    prev_speaker: str,
    prev_text: str,
    next_speaker: str,
    next_text: str,
    *,
    scene_changed: bool = False,
    scale: float = 1.0,
) -> float:
    """前のセリフの後、次のセリフの前に入れる無音（秒）。scale で全体を速く・遅くする。"""
    p, n = prev_text.strip(), next_text.strip()
    if scene_changed:
        g = SCENE
    elif any(n.startswith(w) for w in _REVEAL):
        g = BEFORE_REVEAL
    elif prev_speaker == next_speaker:
        g = SAME_CONT if p.endswith(("、", "，", ",")) else SAME_NEXT
    elif len(_core(n)) <= _SHORT:
        g = SWAP_QUICK  # 相づち・即答・驚きは間を空けない
    elif p.endswith(("？", "?")):
        g = SWAP_ANSWER
    elif p.endswith(("！", "!")):
        g = SWAP_QUICK + 0.04
    else:
        g = SWAP_DEFAULT
    return round(g * scale, 3)
