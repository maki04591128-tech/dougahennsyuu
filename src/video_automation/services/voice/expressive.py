"""抑揚づけ（平坦な読み上げの改善）。

エンジンが推定した音高（モーラごとの pitch、対数 F0）を、話し言葉らしい形に整える。

- 文の頭を少し高く、文末に向けて少しずつ下げる（自然下降）。区切り（、。）ごとにやり直す
- 強調する語（括弧で囲んだ語・数値と単位）は高く、少しゆっくり読む
- 「！」を含む発話は全体を少し高くし、抑揚を強める
- 疑問（？）の語尾上げはエンジンに任せる（is_interrogative）

strength は 0（変えない）〜 2（強め）。1 で控えめな解説調になるよう調整した。
pitch = 0 のモーラ（無声化）は変えない。
"""

from video_automation.schemas.voice import AccentPhrase

DECLINE = 0.10  # 区切り内の下降幅
HEAD = 0.05  # 区切りの最初の句を上げる量
EMPHASIS = 0.16  # 強調語を上げる量
EMPHASIS_STRETCH = 0.07  # 強調語の母音を伸ばす割合
EXCLAIM = 0.06  # 「！」の発話を上げる量


def _units(phrases: list[AccentPhrase]) -> list[list[int]]:
    """区切り（pause_mora の後）ごとのアクセント句の番号。"""
    units: list[list[int]] = [[]]
    for i, ap in enumerate(phrases):
        units[-1].append(i)
        if ap.pause_mora is not None and i < len(phrases) - 1:
            units.append([])
    return [u for u in units if u]


def find_spans(phrases: list[AccentPhrase], kana_terms: list[str]) -> set[tuple[int, int]]:
    """強調語のカナに当たるモーラ（句番号, モーラ番号）の集合。"""
    flat = [(i, j, m.text) for i, ap in enumerate(phrases) for j, m in enumerate(ap.moras)]
    text = "".join(t for _, _, t in flat)
    # 各モーラが text の何文字目から始まるか
    starts, pos = [], 0
    for _, _, t in flat:
        starts.append(pos)
        pos += len(t)
    hits: set[tuple[int, int]] = set()
    for term in kana_terms:
        if len(term) < 2:
            continue
        k = text.find(term)
        while k >= 0:
            for idx, st in enumerate(starts):
                if k <= st < k + len(term):
                    hits.add((flat[idx][0], flat[idx][1]))
            k = text.find(term, k + len(term))
    return hits


def shape(
    phrases: list[AccentPhrase],
    *,
    strength: float = 1.0,
    emphasis: set[tuple[int, int]] | None = None,
    exclaim: bool = False,
) -> list[AccentPhrase]:
    if strength <= 0 or not phrases:
        return phrases
    out = [ap.model_copy(deep=True) for ap in phrases]
    emphasis = emphasis or set()
    for unit in _units(out):
        voiced = [(i, j) for i in unit for j, m in enumerate(out[i].moras) if m.pitch > 0]
        n = len(voiced)
        for k, (i, j) in enumerate(voiced):
            m = out[i].moras[j]
            delta = 0.0
            if n > 1:
                delta -= DECLINE * strength * (k / (n - 1) - 0.5)
            if i == unit[0]:
                delta += HEAD * strength
            if (i, j) in emphasis:
                delta += EMPHASIS * strength
                m.vowel_length = round(m.vowel_length * (1 + EMPHASIS_STRETCH * strength), 4)
            if exclaim:
                delta += EXCLAIM * strength
            m.pitch = round(m.pitch + delta, 4)
    return out


def kana_of(phrases: list[AccentPhrase]) -> str:
    return "".join(m.text for ap in phrases for m in ap.moras)
