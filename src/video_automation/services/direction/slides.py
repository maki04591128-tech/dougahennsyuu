"""スライドの取り込み（統合版 P3）。

- PDF を 2 倍の解像度で画像にする（integrations/ocr/pdf.py）
- 台本の各文を、話す順を崩さずにスライドへ割り当てる（語の一致の数が最大になるよう）
- 寄る先: Windows.Media.Ocr で読んだ行のうち、発話の語（2 字以上の漢字・カタカナ・英数字）と
  一致する数が最大の行。0 なら寄らない
- 箇条書きは話す順に 1 項目ずつ出す（まだ話していない項目は地の色で隠す）
- 立ち絵は画面の面積の 8% で右下に常に出す。6 分を超える講義は分割を提案する
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from video_automation.integrations.ocr.engine import OcrLine, OcrResult

SLIDE_NAME = "スライド:"
SLIDE_TRACK = "スライド"
ZOOM = 1.25  # 統合版 N16 と同じ寄り（24f・easeInOutCubic）
ZOOM_FRAMES = 24
TACHIE_AREA = 0.08
SPLIT_SEC = 6 * 60
SKIP_COST = 0.5  # スライドを飛ばすときの減点（1 枚あたり）

_TOKEN = re.compile(r"[一-龯々〆]{2,}|[ァ-ヶー]{2,}|[A-Za-z0-9][A-Za-z0-9.\-]*[A-Za-z0-9]")
_BULLET = re.compile(r"^\s*(?:[・•●○◦■□◆◇▪▫‐\-–—*✓✔]|\d{1,2}[.)．）]|[①-⑳])")


def tokens(text: str) -> set[str]:
    """発話の語（2 字以上の漢字・カタカナ・英数字）。英字は小文字にそろえる。"""
    return {m.group().lower() for m in _TOKEN.finditer(text)}


def match_count(line: str, toks: set[str]) -> int:
    low = line.lower()
    return sum(1 for t in toks if t in low)


def best_line(res: OcrResult, toks: set[str]) -> int | None:
    """一致する語の数が最大の行（同じなら上の行）。0 なら None（寄らない）。"""
    best, idx = 0, None
    for i, ln in enumerate(res.lines):
        n = match_count(ln.text, toks)
        if n > best:
            best, idx = n, i
    return idx


def assign(seg_tokens: list[set[str]], pages: list[OcrResult]) -> list[int]:
    """各文のスライドの番号。順は戻らず、飛ばすと減点（一致が全く無ければ文の数で等分）。"""
    n, m = len(seg_tokens), len(pages)
    if not n or not m:
        return [0] * n
    texts = [p.text for p in pages]
    score = [[match_count(t, toks) for t in texts] for toks in seg_tokens]
    if not any(any(r) for r in score):
        return [min(m - 1, i * m // n) for i in range(n)]
    neg = float("-inf")
    dp = [[neg] * m for _ in range(n)]
    back = [[0] * m for _ in range(n)]
    for j in range(m):
        dp[0][j] = score[0][j] - SKIP_COST * j
    for i in range(1, n):
        for j in range(m):
            k = max(range(j + 1), key=lambda k: dp[i - 1][k] - SKIP_COST * max(0, j - k - 1))
            dp[i][j] = dp[i - 1][k] - SKIP_COST * max(0, j - k - 1) + score[i][j]
            back[i][j] = k
    # 最後のスライドまで行かなくてもよい（残りは減点しない）
    j = max(range(m), key=lambda j: dp[n - 1][j])
    out = [0] * n
    for i in range(n - 1, -1, -1):
        out[i] = j
        j = back[i][j]
    return out


def bullet_lines(res: OcrResult) -> list[int]:
    return [i for i, ln in enumerate(res.lines) if _BULLET.match(ln.text)]


def reveal_frames(res: OcrResult, segs: list[tuple[int, set[str]]], start: int) -> dict[int, int]:
    """箇条書きの行 → 出すフレーム（その項目と一致する文の頭。一致しなければスライドの頭）。

    segs はそのスライドの (文の始まりのフレーム, 語)。"""
    out: dict[int, int] = {}
    for i in bullet_lines(res):
        line = res.lines[i].text
        hit = [f for f, toks in segs if match_count(line, toks) > 0]
        out[i] = min(hit) if hit else start
    return out


def background(img: Any, box: tuple[float, float, float, float]) -> tuple[int, int, int]:
    """行の外側の縁の色（中央値）。隠す四角をこの色で塗る。"""
    import numpy as np

    a = np.asarray(img.convert("RGB"))
    h, w = a.shape[:2]
    x, y, bw, bh = box
    x0, y0 = max(0, int(x) - 6), max(0, int(y) - 6)
    x1, y1 = min(w, int(x + bw) + 6), min(h, int(y + bh) + 6)
    ring = np.concatenate(
        [a[y0:y0 + 3, x0:x1].reshape(-1, 3), a[max(y0, y1 - 3):y1, x0:x1].reshape(-1, 3),
         a[y0:y1, x0:x0 + 3].reshape(-1, 3), a[y0:y1, max(x0, x1 - 3):x1].reshape(-1, 3)]
    )  # fmt: skip
    if not len(ring):
        return (255, 255, 255)
    med = np.median(ring, axis=0)
    return (int(med[0]), int(med[1]), int(med[2]))


def cover(src: Path, out: Path, lines: list[OcrLine], hide: list[int]) -> Path:
    """hide の行を地の色で隠した画像。"""
    from PIL import Image, ImageDraw

    img = Image.open(src).convert("RGB")
    d = ImageDraw.Draw(img)
    for i in hide:
        box = lines[i].box
        x, y, w, h = box
        d.rectangle([x - 3, y - 3, x + w + 3, y + h + 3], fill=background(img, box))
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out


def fit_aspect(src: Path, aspect: float) -> None:
    """ページを画面の縦横比に合わせる（足りない側を地の色で足す。寄る位置の計算を合わせるため）。"""
    from PIL import Image

    img = Image.open(src).convert("RGB")
    w, h = img.size
    if abs(w / h - aspect) < 0.01:
        return
    col = background(img, (6, 6, w - 12, h - 12))
    if w / h < aspect:
        nw, nh = round(h * aspect), h
    else:
        nw, nh = w, round(w / aspect)
    canvas = Image.new("RGB", (nw, nh), col)
    canvas.paste(img, ((nw - w) // 2, (nh - h) // 2))
    canvas.save(src)


def pan_for(cx: float, cy: float, zoom: float = ZOOM) -> tuple[float, float]:
    """画像の (cx, cy)（0〜1）を中央に置く pan_x・pan_y（render_anim のカメラの式の逆）。"""

    def one(c: float) -> float:
        v = 1 - 1 / zoom
        return max(-1.0, min(1.0, 2 * (c - 1 / (2 * zoom)) / v - 1)) if v > 0 else 0.0

    return one(cx), one(cy)


@dataclass
class Shot:
    """1 つの画像のクリップ（スライドの n 枚目の、ある項目まで出した状態）。"""

    page: int
    start: int
    end: int
    hide: list[int]


def plan_shots(
    page_of: list[int],
    seg_frames: list[tuple[int, int]],
    pages: list[OcrResult],
    seg_tokens: list[set[str]],
    total: int,
) -> list[Shot]:
    """スライドと箇条書きの状態ごとのクリップ（頭から最後まで隙間なく）。"""
    shots: list[Shot] = []
    order = sorted(set(page_of))
    for k, pg in enumerate(order):
        idx = [i for i, p in enumerate(page_of) if p == pg]
        start = 0 if k == 0 else seg_frames[idx[0]][0]
        nxt = [i for i, p in enumerate(page_of) if p == order[k + 1]] if k + 1 < len(order) else []
        end = seg_frames[nxt[0]][0] if nxt else total
        if end <= start:
            continue
        rev = reveal_frames(pages[pg], [(seg_frames[i][0], seg_tokens[i]) for i in idx], start)
        cuts = sorted({start, *(max(start, f) for f in rev.values() if f < end)})
        for a, b in zip(cuts, [*cuts[1:], end], strict=True):
            if b > a:
                shots.append(Shot(pg, a, b, sorted(i for i, f in rev.items() if f > a)))
    return shots


def tachie_transform(w: int, h: int, band_h: float, char_h: float, aspect: float = 0.55) -> dict:
    """立ち絵の帯（右に 1 人）を縮めて右下に置く（キャラクターが画面の面積の 8% になる倍率）。

    帯の中のキャラクターの幅は高さの aspect 倍とみなす（部品ごとの幅は帯からは分からない）。"""
    area = aspect * char_h * char_h
    k = min(2.0, (TACHIE_AREA * w * h / area) ** 0.5) if area > 0 else 1.0
    return {
        "scale": round(k, 4),
        "x": round((1 - k) / 2, 6),
        "y": round((1 - band_h * k / h) / 2, 6),
    }
