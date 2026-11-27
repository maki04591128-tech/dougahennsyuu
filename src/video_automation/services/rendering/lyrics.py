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
# 統合版 U1・U7: 曲の方向性 → (名前, 読み替える M3 のジャンル, 書体, 太さ, 主色, 文字の色, 待機)。
# 方向性を選んだときは、書体・色・待機をこの表から取り、M3・M7・V12・ジャンルの待機は使わない。
# ジャンルで決まるほかの項目（X7 など）は、読み替えたジャンルで決める。
# 色は 5e の「値の詳細1」の表（背景と色）の主な色。書体が入っていなければジャンルの書体
DIRECTIONS: dict[str, tuple[str, str, str, str, str, str, str]] = {
    "vocaloid_cool": ("ボカロ（かっこいい系）", "vocaloid", "Shippori Mincho", "SemiBold",
                      "#8E1024", "#F4EDEA", "static"),
    "vocaloid_cute": ("ボカロ（かわいい系）", "idol", "Hachi Maru Pop", "Regular",
                      "#FFD9E8", "#E8336D", "sway"),
    "vocaloid_calm": ("ボカロ（しっとり系）", "ballad", "Shippori Mincho", "Regular",
                      "#F4F4F2", "#2B2D33", "static"),
    "anison": ("アニソン", "rock", "Zen Maru Gothic", "Medium", "#E4212B", "#FFFFFF", "drift"),
    "ballad": ("バラード", "ballad", "Zen Kurenaido", "Regular", "#140A1E", "#FFFFFF", "static"),
    "rap": ("ラップ", "rock", "Dela Gothic One", "Regular", "#EDE7DA", "#111111", "static"),
    "pop": ("ポップソング", "pop", "RocknRoll One", "Regular", "#FFC82E", "#111111", "sway"),
    "idol": ("アイドルソング", "idol", "Mochiy Pop One", "Regular", "#FFD3EA", "#55C6F2", "wave"),
}  # fmt: skip


def direction_genre(direction: str | None, genre: str | None) -> str | None:
    """U1: 方向性を選んでいれば、読み替えた M3 のジャンル。選んでいなければ genre のまま。"""
    d = DIRECTIONS.get(direction or "")
    return d[1] if d else genre


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


# ---------------- V5 の改訂（歌詞の行 → 入り・待機・出。上の行ほど優先） ----------------

GENRE_HOLD = {  # ジャンルの待機
    "ballad": "breathe",  # 呼吸: scale 1.000→1.015→1.000、周期 3.2 秒
    "citypop": "drift",  # 漂う: 右へ画面の幅の 1%／秒
    "rock": "static",
    "edm": "pulse",  # 拍で脈打つ: 拍ごとに 1.00→1.08→1.00、6f
    "vocaloid": "glitch",  # 2 秒ごとに 2f、x ±4px
    "idol": "wave",  # T8 波打つ
    "pop": "sway",  # 揺れ: 周期 2 秒・±8px
}


def _dup_word(text: str) -> bool:
    words = [w for w in re.split(r"[\s、。！？!?…〜ー・「」]+", text) if len(w) >= 2]
    return any(a == b for a, b in zip(words, words[1:], strict=False)) or bool(
        re.search(r"(.{2,})\1", _norm(text))
    )


def lyric_motion(
    texts: list[str], secs: list[str], genre: str | None, hold_override: str | None = None
) -> list[tuple[str, str, str]]:
    """各行の (入り, 待機, 出)。段階5c の「V5 の改訂」の表（上の行ほど優先）。
    hold_override は U1 の方向性の待機（ジャンルの待機の代わり）。"""
    hold = hold_override or GENRE_HOLD.get(genre or DEFAULT_GENRE, "sway")
    n = len(texts)
    chorus_idx = [i for i, s in enumerate(secs) if s in ("chorus", "chorus_first")]
    blocks: list[list[int]] = []
    for i in chorus_idx:
        if blocks and i == blocks[-1][-1] + 1:
            blocks[-1].append(i)
        else:
            blocks.append([i])
    last_chorus = blocks[-1] if blocks else []
    second_first = blocks[1][0] if len(blocks) >= 2 else -1
    first_first = blocks[0][0] if blocks else -1
    same_len_pair = next(
        (i for i in range(1, n) if len(_norm(texts[i])) == len(_norm(texts[i - 1])) > 0), -1
    )
    out = []
    for i, (t, s) in enumerate(zip(texts, secs, strict=True)):
        tt = t.strip()
        prev_s = secs[i - 1] if i else ""
        nxt_s = secs[i + 1] if i + 1 < n else ""
        if last_chorus and i == last_chorus[0] and len(blocks) >= 2:
            m = ("slam", hold, "scatter")  # ラストサビの 1 行目: T17 → T18
        elif i == n - 1 and s not in ("chorus", "chorus_first"):
            m = ("track", "outline_light", "fall")  # アウトロの最後の行: T7 → T19 → T21
        elif i == same_len_pair:
            m = ("morph", hold, "shrink")  # 前の行と字数が同じ（最初の 1 組）
        elif _dup_word(tt):
            m = ("stack", hold, "rise_out")
        elif tt.endswith(("！", "!")):
            m = ("shake", "static", "flicker")
        elif tt.endswith(("〜", "ー", "～")):
            m = ("fade_up", "wave", "fade")
        elif last_chorus and i == last_chorus[-1] and len(blocks) >= 2:
            m = ("neon", "outline_light", "flicker")
        elif i == second_first:
            m = ("glitch_in", hold, "glitch_out")
        elif i == first_first:
            m = ("circle", hold, "spin_out")
        elif s in ("chorus", "chorus_first") and nxt_s not in ("chorus", "chorus_first"):
            m = ("gather", hold, "dissolve")
        elif s in ("chorus", "chorus_first"):
            m = ("bounce", hold, "shrink")
        elif s == "verse" and prev_s == "interlude":
            m = ("warp_in", hold, "warp_out")
        elif s == "bridge" and nxt_s != "bridge":
            m = ("curve", hold, "curve_out")
        elif s == "bridge":
            m = ("stretch", hold, "stretch_out")
        elif s == "drop":
            m = ("tilt3d", "static", "fall_back")
        else:
            m = ("zoom", hold, "blur_out")  # A メロ
        out.append(m)
    return out
