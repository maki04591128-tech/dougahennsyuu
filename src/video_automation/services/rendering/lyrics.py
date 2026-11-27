"""歌詞の置き方（統合版 M3）と、ジャンルごとの書体・質感（M7）。

M3 のセクションの推定（歌詞だけから）
- サビ: 歌詞の中で 2 回以上出る行を含む、4 行以上のまとまり（同じ行の並びが繰り返す所）
- サビの 1 行目: 1 曲で最初のサビの最初の行（円形は 1 曲に 1 回）
- 落ちサビ: 2 回目以降のサビのうち、行の平均の音量（rms）がサビの音量の中央値の 60% 未満のもの
- B メロ: サビの直前の 2 行（サビ・間奏を除く）
- A メロ: それ以外
音量（rms）が無いときは落ちサビを推定しない。サビの行が無い曲は、全部 A メロにする。

置き方（M3）: A メロ 中央、B メロ サイズ違い（助詞を 68%）、サビ 斜め（−8°）、
サビの 1 行目 円形、落ちサビ 縦書き、間奏 歌詞なし。
"""

from __future__ import annotations

import re
import statistics

GENRES = ("ballad", "citypop", "rock", "edm", "vocaloid", "idol", "pop")
DEFAULT_GENRE = "pop"
# ジャンル → (書体, 太さ, 文字の質感)。M3・M7 の表
GENRE_STYLE: dict[str, tuple[str, str, str]] = {
    "ballad": ("Zen Old Mincho", "Bold", "shadow"),
    "citypop": ("Zen Old Mincho", "Bold", "halftone"),
    "rock": ("Noto Sans CJK JP", "Black", "grunge"),
    "edm": ("Noto Sans CJK JP", "Black", "glow"),
    "vocaloid": ("Noto Sans CJK JP", "Black", "noise"),
    "idol": ("M PLUS Rounded 1c", "Bold", "outline3"),
    "pop": ("M PLUS Rounded 1c", "Bold", "matte"),
}
GENRE_LABEL = {
    "ballad": "バラード",
    "citypop": "シティポップ",
    "rock": "ロック",
    "edm": "EDM",
    "vocaloid": "ボカロ",
    "idol": "アイドル",
    "pop": "ポップ",
}
CHORUS_TILT_DEG = -8.0
PARTICLE_SCALE = 0.68
DROP_RATIO = 0.6
_PARTICLE = re.compile(r"(?<=[一-龥ァ-ヶー])(?:は|が|を|に|へ|と|で|の|も|や|から|まで|より)")


def _norm(t: str) -> str:
    return re.sub(r"[\s、。！？!?…〜ー・「」]", "", t)


def sections(texts: list[str], rms: list[float] | None = None) -> list[str]:
    """各行のセクション。

    verse（A メロ）・bridge（B メロ）・chorus・chorus_first・drop（落ちサビ）。"""
    n = len(texts)
    keys = [_norm(t) for t in texts]
    # 同じ行の並びが 4 行以上くり返す所（2 行だけのくり返しはサビにしない）
    inc = [False] * n
    for i in range(n):
        for j in range(i + 1, n):
            m = 0
            while j + m < n and i + m < j and keys[i + m] and keys[i + m] == keys[j + m]:
                m += 1
            if m >= 4:
                for k in range(m):
                    inc[i + k] = inc[j + k] = True
    out = ["verse"] * n
    blocks: list[tuple[int, int]] = []
    k = 0
    while k < n:
        if inc[k]:
            b = k
            while b < n and inc[b]:
                b += 1
            blocks.append((k, b))
            k = b
        else:
            k += 1
    if not blocks:
        return out
    for b0, b1 in blocks:
        for k in range(b0, b1):
            out[k] = "chorus"
        for k in range(max(0, b0 - 2), b0):
            if out[k] == "verse":
                out[k] = "bridge"
    out[blocks[0][0]] = "chorus_first"
    if rms and len(rms) == n:
        loud = [statistics.mean(rms[b0:b1]) for b0, b1 in blocks]
        med = statistics.median(loud)
        for (b0, b1), v in zip(blocks[1:], loud[1:], strict=True):
            if med > 0 and v < med * DROP_RATIO:
                for k in range(b0, b1):
                    out[k] = "drop"
    return out


def particle_spans(text: str) -> list[tuple[int, int]]:
    """B メロで小さくする助詞の位置（漢字・カタカナの直後の助詞）。"""
    return [(m.start(), m.end()) for m in _PARTICLE.finditer(text)]


def genre_style(genre: str | None) -> tuple[str, str, str]:
    return GENRE_STYLE.get(genre or DEFAULT_GENRE, GENRE_STYLE[DEFAULT_GENRE])
