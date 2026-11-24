"""文字起こしの結果（単語ごとの時刻）から字幕・歌詞の行と時刻を作る。

- 歌詞（ユーザーが持っている正しい文）があれば、それを文字起こしに合わせて時刻を付ける
  （歌の文字起こしは誤りが多いので、文は歌詞を使い、時刻だけを借りる）
- 無ければ文字起こしの区切り（segment）をそのまま行にする
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass


@dataclass
class Word:
    text: str
    start: float
    end: float


@dataclass
class Line:
    text: str
    start: float
    end: float
    matched: float = 1.0  # 文字起こしと一致した文字の割合（歌詞を合わせたとき）


_DROP = re.compile(r"[\s、。，．,.!！?？「」『』（）()…・〜~ー―\-♪]")


def _norm_char(ch: str) -> str:
    """比べるための文字（全角半角をそろえ、カタカナはひらがなに）。"""
    ch = unicodedata.normalize("NFKC", ch).lower()
    if "ァ" <= ch <= "ヶ":
        ch = chr(ord(ch) - 0x60)
    return ch


def _chars(words: list[Word]) -> tuple[str, list[tuple[float, float]]]:
    """単語を 1 文字ずつに分け、各文字に時刻を割り当てる（単語の時間を等分）。"""
    out: list[str] = []
    times: list[tuple[float, float]] = []
    for w in words:
        cs = [c for c in w.text if not _DROP.match(c)]
        if not cs:
            continue
        step = max(0.0, w.end - w.start) / len(cs)
        for i, c in enumerate(cs):
            out.append(_norm_char(c))
            times.append((w.start + step * i, w.start + step * (i + 1)))
    return "".join(out), times


def align_lyrics(
    lyrics: list[str],
    words: list[Word],
    min_sec: float = 0.6,
    duration: float | None = None,
) -> list[Line]:
    """歌詞の各行に、文字起こしの時刻を付ける。

    歌詞全体と文字起こし全体を文字単位で照合し、行ごとに一致した文字の最初と最後の時刻を
    使う。一致が無い行は前後の行の間に割り当てる。歌声がほとんど聞き取れなかったときは、
    聞き取れた範囲（何も無ければ曲の長さ duration）に等分する（冒頭に詰め込まない）。"""
    lines = [x.strip() for x in lyrics if x.strip()]
    if not lines:
        return []
    hyp, times = _chars(words)
    ref_chars: list[str] = []
    owner: list[int] = []  # 歌詞の各文字がどの行か
    pos: list[int] = []  # 行の中で何文字目か
    for i, ln in enumerate(lines):
        k = 0
        for c in ln:
            if not _DROP.match(c):
                ref_chars.append(_norm_char(c))
                owner.append(i)
                pos.append(k)
                k += 1
    ref = "".join(ref_chars)
    sm = difflib.SequenceMatcher(None, ref, hyp, autojunk=False)
    hit: dict[int, list[tuple[float, float]]] = {}
    first: dict[int, int] = {}  # 行で最初に一致した文字の位置
    last: dict[int, int] = {}
    for a, b, n in sm.get_matching_blocks():
        for k in range(n):
            i = owner[a + k]
            hit.setdefault(i, []).append(times[b + k])
            first.setdefault(i, pos[a + k])
            last[i] = pos[a + k]
    count = [owner.count(i) for i in range(len(lines))]
    raw: list[tuple[float, float] | None] = []
    for i in range(len(lines)):
        t = hit.get(i)
        # 1〜2 文字の偶然の一致で行の位置が飛ばないよう、少なすぎる一致は使わない
        if t and len(t) >= max(2, count[i] * 0.25):
            # 行頭・行末の一致しなかった文字（漢字の読み違いなど）の分だけ時刻を広げる
            per = (t[-1][1] - t[0][0]) / max(1, last[i] - first[i] + 1)
            tail = count[i] - 1 - last[i]
            raw.append((max(0.0, t[0][0] - per * first[i]), t[-1][1] + per * tail))
        else:
            raw.append(None)
    if words:
        total_end = words[-1].end
    elif duration:
        total_end = duration
    else:
        total_end = min_sec * len(lines)
    start0 = words[0].start if words else 0.0
    out: list[Line] = []
    for i, text in enumerate(lines):
        r = raw[i]
        s, e = r if r is not None else (0.0, 0.0)  # 一致の無い行は後で割り当てる
        matched = len(hit.get(i, [])) / max(1, count[i])
        out.append(Line(text, s, e, round(min(1.0, matched), 2)))
    # 時刻が戻らないように整え、短すぎる行は伸ばす（次の一致した行とは重ねない）
    for i, line in enumerate(out):
        if raw[i] is None:
            continue
        prev = next((out[j].end for j in range(i - 1, -1, -1) if raw[j] is not None), 0.0)
        line.start = max(line.start, prev)
        line.end = max(line.end, line.start + min_sec)
        nxt = next((j for j in range(i + 1, len(out)) if raw[j] is not None), None)
        if nxt is not None and line.end > out[nxt].start:
            line.end = max(line.start + 0.1, out[nxt].start)
    _spread_unmatched(out, raw, total_end, min_sec, start0)
    return out


def _spread_unmatched(
    out: list[Line],
    raw: list[tuple[float, float] | None],
    total_end: float,
    min_sec: float,
    start0: float = 0.0,
) -> None:
    """一致の無い行（続くこともある）に、前後の一致した行の間を等分して割り当てる。"""
    i = 0
    while i < len(out):
        if raw[i] is not None:
            i += 1
            continue
        j = i
        while j < len(out) and raw[j] is None:
            j += 1
        a = out[i - 1].end if i else min(start0, max(0.0, total_end - min_sec * (j - i)))
        b = out[j].start if j < len(out) else max(total_end, a + min_sec * (j - i))
        step = max(0.1, (b - a) / (j - i))
        for k in range(i, j):
            out[k].start = a + step * (k - i)
            out[k].end = a + step * (k - i + 1)
        i = j


def lines_from_segments(segments: list[tuple[str, float, float]]) -> list[Line]:
    """文字起こしの区切りをそのまま行にする（空の区切りは捨てる）。"""
    return [Line(t.strip(), s, e) for t, s, e in segments if t.strip()]
