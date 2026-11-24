"""台本分割 (P4-01) と字幕の日本語改行 (P4-02)。形態素解析器に依存しない規則ベース。

改行候補:
- 強: 句読点・感嘆符・閉じ括弧の直後
- 弱: ひらがな → 漢字/カタカナ/英数 へ切り替わる位置（助詞の後で語が始まる位置にほぼ一致）
行頭禁則: 句読点・閉じ括弧・長音・小書き仮名などを行頭に置かない。
"""

import re
from dataclasses import dataclass

_SPEAKER_LINE = re.compile(r"^\s*([^\s：:「」]{1,20})\s*[：:]\s*(.+)$")
_SENTENCE_END = "。！？!?"
_STRONG_AFTER = set("、。，．！？!?」』）)】〕…")
_NO_LINE_START = set("、。，．！？!?」』）)】〕ーぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮヵヶ・…")


@dataclass
class SegmentDraft:
    scene_index: int
    speaker: str
    text: str


def split_script(text: str) -> list[SegmentDraft]:
    """台本テキストを発話セグメントに分割する。

    - 「話者：セリフ」行は話者付き。話者の無い行は直前の話者を引き継ぐ
    - 空行はシーン区切り
    - 1行が長い場合は文末（。！？）で分割する
    """
    out: list[SegmentDraft] = []
    scene, speaker, blank = 0, "", False
    for raw in text.replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if not line:
            blank = True
            continue
        if blank and out:
            scene += 1
        blank = False
        m = _SPEAKER_LINE.match(line)
        if m:
            speaker, line = m.group(1), m.group(2).strip()
        for sent in _sentences(line):
            out.append(SegmentDraft(scene_index=scene, speaker=speaker, text=sent))
    return out


def _sentences(line: str) -> list[str]:
    parts, buf, depth = [], "", 0
    for ch in line:
        buf += ch
        if ch in "「『（(":
            depth += 1
        elif ch in "」』）)":
            depth = max(0, depth - 1)
        elif ch in _SENTENCE_END and depth == 0:
            parts.append(buf.strip())
            buf = ""
    if buf.strip():
        if parts and len(buf.strip()) <= 1 and buf.strip() in "」』）)":
            parts[-1] += buf.strip()
        else:
            parts.append(buf.strip())
    return [p for p in parts if p]


def _is_hira(ch: str) -> bool:
    return "ぁ" <= ch <= "ゟ"


def _is_word(ch: str) -> bool:
    return ch.isascii() and (ch.isalnum() or ch in "_.-'")


def _break_score(text: str, i: int) -> int:
    """text[:i] と text[i:] の間で改行する適切さ。0=不可。"""
    if i <= 0 or i >= len(text) or text[i] in _NO_LINE_START:
        return 0
    prev, nxt = text[i - 1], text[i]
    if _is_word(prev) and _is_word(nxt):  # 英単語・数字の途中では切らない
        return 0
    if prev in _STRONG_AFTER or nxt in "「『（(":  # 句読点の後・かぎかっこの前
        return 3
    if _is_hira(prev) and not _is_hira(nxt):
        return 2
    if prev == " ":
        return 2
    return 1


def wrap_lines(text: str, max_chars: int) -> list[str]:
    """max_chars 以内の行に分割する（候補の中で最も後ろの良い位置で改行）。"""
    lines: list[str] = []
    rest = text.strip()
    while len(rest) > max_chars:
        # 句読点（強）は行の1/3以降、それ以外は後ろほど優先
        window = range(max(1, max_chars // 3), max_chars + 1)
        best_i, best_s = max_chars, 0
        for i in window:
            s = _break_score(rest, i)
            if s >= best_s and s > 0:
                best_i, best_s = i, s
        if best_s == 0:  # 禁則や長い英単語で切れない場合
            best_i = max_chars - 1 if rest[max_chars] in _NO_LINE_START else max_chars
        lines.append(rest[:best_i].strip())
        rest = rest[best_i:].strip()
    if rest:
        lines.append(rest)
    return lines


def make_cues(text: str, max_chars: int, max_lines: int, strip_period: bool) -> list[str]:
    """1セグメントの字幕キュー（各要素は改行 \\n を含む表示テキスト）。"""
    t = text.strip()
    if strip_period:
        t = t.rstrip("。.")
    lines = wrap_lines(t, max_chars)
    return ["\n".join(lines[i : i + max_lines]) for i in range(0, len(lines), max_lines)]


def default_max_chars(width: int, height: int) -> int:
    return 14 if height > width else 22
