"""台本分割 (P4-01) と字幕の日本語改行 (P4-02)。

改行候補:
- 強: 句読点・感嘆符・閉じ括弧の直後
- 中: 文節の切れ目（BudouX があればその区切り。無ければ、ひらがな → 漢字/カタカナ/英数 へ
  切り替わる位置で近似する）
- 弱: それ以外（文節の途中。どうしても収まらないときだけ）
行頭禁則: 句読点・閉じ括弧・長音・小書き仮名などを行頭に置かない。
1 枚の字幕には 1 文だけを入れる（1 行に収まる短い掛け合いは例外）。
"""

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

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


@lru_cache(maxsize=1)
def _budoux() -> Any:
    """BudouX の日本語の文節区切り（入っていなければ None で、規則だけで改行する）。"""
    try:
        import budoux  # type: ignore[import-untyped]

        return budoux.load_default_japanese_parser()
    except Exception:  # noqa: BLE001 - 無くても動く（規則ベース）
        return None


@lru_cache(maxsize=2048)
def phrase_bounds(text: str) -> frozenset[int] | None:
    """文節の切れ目の位置（text[:i] と text[i:] の間）。BudouX が無ければ None。"""
    parser = _budoux()
    if parser is None or not text:
        return None
    out, pos = set(), 0
    for chunk in parser.parse(text)[:-1]:
        pos += len(chunk)
        out.add(pos)
    return frozenset(out)


def _is_hira(ch: str) -> bool:
    return "ぁ" <= ch <= "ゟ"


def _is_kanji(ch: str) -> bool:
    return bool(ch) and ("\u4e00" <= ch <= "\u9fff" or ch in "々")


def _depth(text: str, i: int) -> int:
    """text[:i] の中で閉じていないかっこの数。"""
    d = 0
    for ch in text[:i]:
        if ch in "「『（(":
            d += 1
        elif ch in "」』）)":
            d = max(0, d - 1)
    return d


def _is_word(ch: str) -> bool:
    return ch.isascii() and (ch.isalnum() or ch in "_.-'")


def _break_score(text: str, i: int) -> int:
    """text[:i] と text[i:] の間で改行する適切さ。0=不可。"""
    if i <= 0 or i >= len(text) or text[i] in _NO_LINE_START:
        return 0
    prev, nxt = text[i - 1], text[i]
    if _is_word(prev) and _is_word(nxt):  # 英単語・数字の途中では切らない
        return 0
    if prev in "」』）)" and _is_hira(nxt):  # 「名前」の … の助詞を切り離さない
        return 1
    if _is_word(prev) and _is_hira(nxt):  # 「Ollama」や … の助詞を英単語から切り離さない
        return 1
    if _depth(text, i) > 0:  # かぎかっこ・かっこの中（「演出プラン（AI）」など）は切らない
        return 1
    if _is_hira(prev) and _is_kanji(text[i - 2] if i >= 2 else "") and _is_kanji(nxt):
        return 1  # 送り仮名 1 文字を挟む複合語（書き出し・取り込み）の途中
    if prev in _STRONG_AFTER or nxt in "「『（(":  # 句読点の後・かぎかっこの前
        return 3
    bounds = phrase_bounds(text)
    if bounds is not None:  # 文節の切れ目だけを良い位置とする（語の途中では切らない）
        return 2 if i in bounds else 1
    if prev == " ":
        return 2
    if _is_hira(prev) and not _is_hira(nxt):
        return 2
    return 1


def _is_word(ch: str) -> bool:
    """語の途中で切りたくない文字（英数字・カタカナの語。「コンセ / ント」を避ける）。"""
    return (ch.isascii() and (ch.isalnum() or ch in "_-.'")) or ("ァ" <= ch <= "ヺ" or ch == "ー")


def wrap_lines(text: str, max_chars: int) -> list[str]:
    """max_chars 以内の行に分割する。

    残りが 2 行に収まるときは、良い切れ目の中から 2 行の長さが揃う位置を選ぶ
    （1 行目だけ長く 2 行目が数文字、という偏りを避ける）。それ以外は後ろの良い位置で改行する。"""
    lines: list[str] = []
    rest = text.strip()
    while len(rest) > max_chars:
        n = len(rest)
        best_i, best_s = max_chars, 0
        if n <= max_chars * 2:
            best_key: tuple[int, float] = (0, 0.0)
            for i in range(max(1, n - max_chars, n // 4), max_chars + 1):
                s = _break_score(rest, i)
                key = (s, -abs(i - n / 2))
                if s > 0 and key > best_key:
                    best_key, best_i, best_s = key, i, s
        if best_s < 2:  # 揃えられる良い切れ目が無い: 後ろの良い位置で改行する
            best_i, best_s = max_chars, 0
            # 句読点（強）は行の1/3以降、それ以外は後ろほど優先
            for i in range(max(1, max_chars // 3), max_chars + 1):
                s = _break_score(rest, i)
                if s >= best_s and s > 0:
                    best_i, best_s = i, s
        if best_s == 0:  # 禁則や長い英単語で切れない場合
            best_i = max_chars - 1 if rest[max_chars] in _NO_LINE_START else max_chars
            # 英単語・数字の途中（「VideoAutomatio / n」）では切らない: 語の頭へ戻す。
            # 1 語だけで 1 行より長いときは切るしかない
            if _is_word(rest[best_i - 1]) and _is_word(rest[best_i]):
                a = best_i
                while a > 0 and _is_word(rest[a - 1]):
                    a -= 1
                if a > 0:
                    best_i = a
        lines.append(rest[:best_i].strip())
        rest = rest[best_i:].strip()
    if rest:
        lines.append(rest)
    return lines


def _fits(text: str, max_chars: int, max_lines: int) -> bool:
    return len(wrap_lines(text, max_chars)) <= max_lines


# キューの切れ目の良さ（_break_score）ごとの減点。句読点で切るのが最も自然
_CUE_BREAK_COST = {3: 0.0, 2: 4.0, 1: 12.0}


def make_cues(text: str, max_chars: int, max_lines: int, strip_period: bool) -> list[str]:
    """1セグメントの字幕キュー（各要素は改行 \\n を含む表示テキスト）。

    1 枚に収まる文はそのまま 1 枚にする。収まらない文は、句読点などの意味の切れ目で
    分け、短すぎる切れ端（「見つかるわ」だけの 1 枚など）や、語と助詞の間で切らないよう
    動的計画法で切れ目を選ぶ。
    """
    t = text.strip()
    if not t:
        return []
    sents = _sentences(t)
    if len(sents) > 1 and len(t) > max_chars:
        # 1 枚に 1 文（1 行に収まる短い掛け合い「えっ！本当？」は 1 枚のまま）
        out: list[str] = []
        for x in sents:
            out += make_cues(x, max_chars, max_lines, strip_period)
        return out
    if strip_period:
        t = t.rstrip("。.")
    if not t:
        return []
    if _fits(t, max_chars, max_lines):
        return ["\n".join(wrap_lines(t, max_chars))]
    n = len(t)
    cap = max_chars * max_lines
    cands = [i for i in range(1, n) if _break_score(t, i) > 0]
    pts = [0, *cands, n]
    best: dict[int, tuple[float, int]] = {0: (0.0, -1)}
    for b in pts[1:]:
        for a in pts:
            if a >= b or a not in best or b - a > cap:
                continue
            chunk = t[a:b].strip()
            if not chunk or not _fits(chunk, max_chars, max_lines):
                continue
            cost = best[a][0] + 3.0  # 枚数は少ないほど良い
            if b < n:
                cost += _CUE_BREAK_COST[_break_score(t, b)]
            if len(chunk) < max(6, max_chars // 3):  # 短すぎる切れ端
                cost += 10.0
            if b not in best or cost < best[b][0]:
                best[b] = (cost, a)
    if n not in best:  # 切れ目が見つからない（長い英単語など）: 行で詰める
        lines = wrap_lines(t, max_chars)
        return ["\n".join(lines[i : i + max_lines]) for i in range(0, len(lines), max_lines)]
    cuts, b = [], n
    while b > 0:
        a = best[b][1]
        cuts.append(t[a:b].strip())
        b = a
    return ["\n".join(wrap_lines(c, max_chars)) for c in reversed(cuts)]


def default_max_chars(width: int, height: int) -> int:
    return 14 if height > width else 22
