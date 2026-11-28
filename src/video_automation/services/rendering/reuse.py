"""統合版 U23: 歌詞の画面の要素を、歌詞以外の用途で使う。

REUSE は統合版の「他の用途への流用」の表（流用先 → 要素の組み合わせ・役割の読み替え）。
この表は設計の初期の提案。描いているのは N11 の縦書きの引用（quote_vertical）と、解説の章の
終わりの 1 語のカード（H1 の「説明とまとめ」。direction の _chapter_words）の 2 行。
ほかの行は表として持ち、使うときに U22 の決まりと用途ごとの上限（2.1）をそのまま守る。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from video_automation.services.rendering import lyric_screen as ls


@dataclass(frozen=True)
class Reuse:
    target: str  # 流用先の既存の項目
    codes: tuple[str, ...]  # 使う要素（U21 の値）
    role: str  # 元の役割 → 流用先での役割
    drawn: bool = False  # この版で描いているか


REUSE: tuple[Reuse, ...] = (
    Reuse("H1", ("UC5", "UL1", "US5", "UF2", "UH8"), "鍵の語 → 章の題。2秒見せて絵に戻る"),
    Reuse("D1", ("UC2", "UL4", "US3", "UF4", "UT5", "UH6"), "歌詞の行 → 項目。1枚ずつ積む"),
    Reuse("P5", ("UL8", "US6", "UG5", "UC2", "US3"), "模様の壁 → 順位の数字"),
    Reuse("P9", ("UH5", "UC3"), "人物の顔 → 商品"),
    Reuse("T49", ("UL3", "US5", "UP4", "UG1", "UH4"), "サビの見出し → 最初の1文"),
    Reuse("N11", ("UH8", "UL4", "UF1", "UT5", "US4"), "積む縦書き → 引用文", drawn=True),
    Reuse("P1", ("UC1", "US2"), "歌詞 → 話者の字幕。絵に文字を重ねない"),
    Reuse("N15", ("UC6", "UF5", "UT5", "UH6", "UC2", "US2"), "書き足す紙 → 要点のメモ"),
    Reuse("N1", ("UH11", "UC2", "US3"), "コマ割り → 手順の3コマ"),
    Reuse("N11", ("UH12", "UL4", "US3", "UF1", "UT5"), "歌詞の縦書き → 引用文", drawn=True),
    Reuse(
        "H1",
        ("UC1", "US2", "UH6", "UC5", "UL1", "US5"),
        "A メロとサビ → 説明とまとめ",
        drawn=True,  # 解説の章の終わりの 1 語のカード（direction の _chapter_words）
    ),
    Reuse("紹介", ("UH14", "UP10"), "列の1枚だけ色を残して、話している人を示す"),
    Reuse("カラオケ", ("UT8",), "次の行を先に薄く見せる"),
    Reuse("和風の題名", ("UF7", "UD6"), "題名を2字の筆文字で見せる"),
    Reuse("見出し", ("UL14",), "難しい語にふりがなを付ける"),
)

QUOTE_US = 76  # US3（1080p の px）。引用の本文
KEY_US = 170  # US4。鍵の語（N11 の UH8 の行だけ）
QUOTE_COL_MAX = 10  # 1 列の字数の上限（決まり: 縦 1 列は 10 字まで）
QUOTE_STAGGER = 12  # UT5: 列を 12f ずつ積む（入りは 6f のフェード）
QUOTE_FADE = 6


def _columns(text: str, n: int = QUOTE_COL_MAX) -> list[str]:
    s = "".join(ch for ch in text if not ch.isspace())
    return [s[i : i + n] for i in range(0, len(s), n)]


def quote_vertical(w: int, h: int, text: str, speaker: str, fonts: Callable[[str], str | None],
                   ink: str, accent: str, frames: int, split: bool) -> Iterator[Any]:  # fmt: skip
    """N11 を U23 の要素で組む: 縦書き（UL4）を右から左へ 1 列ずつ積んで残す（UT5）。

    split=True（インタビュー・ドキュメンタリー。UH12）は左の 3 分の 1 を空けて、
    本文は残りの 3 分の 2 にだけ置く。split=False（朗読・物語。UH8 の余白）は、
    引用の最後の語（漢字・カタカナの続き）を US4 の列にして右端に先に出す。
    書体は UF1（Shippori Mincho Regular）。地は描かない（下の映像の上に重ねる）。"""
    from PIL import Image

    k = h / 1080
    size = round(QUOTE_US * k)
    f = ls._font(fonts, "UF1", size)
    rgb, acc = ls._rgb(ink), ls._rgb(accent)
    stroke = max(1, size // 16)
    cols = _columns(text)
    key = "" if split else ls.last_word(text)[-4:]  # 長い語は終わりの 4 字
    key_size = round(KEY_US * k)
    gap = round(size * 0.6)
    right = w - round(110 * k)  # UP2: 端から 110px
    left = round(w / 3) if split else round(110 * k)
    items: list[tuple[Any, int, int, int, int]] = []  # (画, x, y, 入りのフレーム, 大きさ)
    t = 0
    x = right
    if key:
        fk = ls._font(fonts, "UF1", key_size)
        x -= key_size
        y = round(h / 2 - key_size * len(key) / 2)
        for i, ch in enumerate(key):
            g = ls._glyph(ch, fk, key_size, acc, max(1, key_size // 16))
            g, dx, dy = ls._vertical(ch, g, key_size)
            items.append(
                (g, x + round(dx * key_size), y + i * key_size + round(dy * key_size), 0, key_size)
            )
        x -= gap
        t = QUOTE_STAGGER
    top = round(h / 2 - size * QUOTE_COL_MAX / 2)
    for col in cols:
        x -= size
        if x < left:  # 入りきらない列は出さない（呼び出し側で文の長さを抑える）
            break
        for i, ch in enumerate(col):
            g = ls._glyph(ch, f, size, rgb, stroke)
            g, dx, dy = ls._vertical(ch, g, size)
            items.append((g, x + round(dx * size), top + i * size + round(dy * size), t, size))
        x -= gap
        t += QUOTE_STAGGER
    sp = None
    if speaker:
        fs = ls._font(fonts, "UF1", round(38 * k))  # US1
        sp = Image.new("RGBA", (w, round(60 * k)), (0, 0, 0, 0))
        from PIL import ImageDraw

        ImageDraw.Draw(sp).text((max(x + gap, left), sp.height / 2), f"— {speaker}", font=fs,
                                fill=(*rgb, 230), anchor="lm")  # fmt: skip
    for fr in range(frames):
        im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        for g, gx, gy, t0, gs in items:
            if fr < t0:
                continue
            a = min(1.0, (fr - t0 + 1) / QUOTE_FADE)
            if a < 1.0:
                g = g.copy()
                g.putalpha(g.getchannel("A").point(lambda v, a=a: round(v * a)))
            im.alpha_composite(g, (gx - (g.width - gs) // 2, gy - (g.height - gs) // 2))
        if sp is not None and fr >= t:
            im.alpha_composite(sp, (0, round(top + size * QUOTE_COL_MAX + 30 * k)))
        yield im


def fits_vertical(text: str, w: int, h: int, split: bool) -> bool:
    """縦書きの列が全部入るか（入らなければ今の横組みの N11 を使う）。"""
    k = h / 1080
    size = QUOTE_US * k
    room = w - 110 * k - (w / 3 if split else 110 * k)
    if not split:
        room -= KEY_US * k + size * 0.6
    return len(_columns(text)) * size * 1.6 <= room


__all__ = ["REUSE", "Reuse", "quote_vertical", "fits_vertical"]
