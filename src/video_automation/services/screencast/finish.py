"""画面録画の仕上げ（W3）の計画: 個人情報のぼかし・早送り・キー操作・カーソルの光。

- 個人情報: 0.5 秒ごとの OCR の結果から、メール・`sk-` / `AKIA` で始まる 20 字以上の英数字・
  電話番号を正規表現で探し、半径 9px のガウスぼかしを前後のフレームで追う（同じ文字の検出は
  サンプルの間で位置を補間し、最初の前・最後の後は 1 サンプル分だけ延ばす）
- 早送り: 画面の変化も声も無い 2 秒以上の区間を 4 倍速にし「⏩ ×4」を出す
- キー操作: 画面下の中央に 1.8 秒。1 秒に 4 回以上の連続入力（文字を打っている所）は出さない
- カーソルの光: 半径 30px・不透明度 0.28
- スポットライト（統合版 N14。操作説明）: クリックの 0.3 秒前から 1.2 秒間、クリックの位置の外を暗く
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from video_automation.integrations.ocr.engine import OcrResult, union

OCR_STEP = 0.5
BLUR_RADIUS = 9
BLUR_PAD = 4.0
FAST_MIN = 2.0
FAST_SPEED = 4
KEY_SEC = 1.8
KEY_BURST = 4  # 1 秒にこの回数以上なら連続入力
CURSOR_RADIUS = 30
CURSOR_OPACITY = 0.28

PII_PATTERNS: dict[str, re.Pattern[str]] = {
    "email": re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}"),
    "secret": re.compile(r"(?:sk-[A-Za-z0-9_\-]{17,}|AKIA[A-Za-z0-9]{16,})"),
    # 国内（0 から始まる 10〜11 桁、区切りは - か空白）と国際（+ から始まる）
    "phone": re.compile(r"(?<![\d+])(?:\+\d{1,3}[\- ]?|0)\d{1,4}[\- ]?\d{1,4}[\- ]?\d{3,4}(?!\d)"),
}


_DASH = set("ー－‐‑‒–—―−ｰ")


def _norm(c: str) -> str:
    """1 字ずつそろえる（全角の英数字 → 半角、長音・ダッシュ → '-'）。長さは変えない。

    日本語の OCR は電話番号の '-' を「ー」と読むことがある（実機で確認）。"""
    if c in _DASH:
        return "-"
    n = unicodedata.normalize("NFKC", c)
    return n if len(n) == 1 else c


def _digits(s: str) -> int:
    return sum(c.isdigit() for c in s)


@dataclass
class Hit:
    kind: str
    text: str
    box: tuple[float, float, float, float]


def find_pii(res: OcrResult) -> list[Hit]:
    """1 枚の OCR の結果から個人情報の文字と位置を探す（行の中の語をつないで探す）。"""
    out: list[Hit] = []
    for ln in res.lines:
        # 語を空白なしでつなぎ、文字ごとに元の語の番号を覚える（日本語の OCR は語を細かく分ける）
        text, owner = "", []
        for i, w in enumerate(ln.words):
            text += "".join(_norm(ch) for ch in w.text)
            owner += [i] * len(w.text)
        taken: list[tuple[int, int]] = []
        for kind, pat in PII_PATTERNS.items():
            for m in pat.finditer(text):
                a, b = m.span()
                if any(a < y and x < b for x, y in taken):
                    continue  # 先に見つけたもの（メールの中の数字など）と重なる
                s = m.group()
                if kind == "phone":
                    n = _digits(s)
                    if not 10 <= n <= 13:
                        continue
                taken.append((a, b))
                idx = sorted(set(owner[a:b]))
                box = union(
                    [(ln.words[j].x, ln.words[j].y, ln.words[j].w, ln.words[j].h) for j in idx]
                )
                out.append(Hit(kind, s, box))
    return out


def mask(text: str) -> str:
    """一覧に出す文字（全部は出さない）。"""
    if len(text) <= 4:
        return "*" * len(text)
    return text[:2] + "*" * (len(text) - 4) + text[-2:]


@dataclass
class Track:
    kind: str
    text: str
    keys: list[tuple[float, tuple[float, float, float, float]]] = field(default_factory=list)

    @property
    def t0(self) -> float:
        return self.keys[0][0] - OCR_STEP

    @property
    def t1(self) -> float:
        return self.keys[-1][0] + OCR_STEP


def track(samples: list[tuple[float, list[Hit]]], step: float = OCR_STEP) -> list[Track]:
    """同じ文字の検出を、続くサンプルの間でつなぐ。"""
    done: list[Track] = []
    live: dict[tuple[str, str], Track] = {}
    for t, hits in sorted(samples, key=lambda x: x[0]):
        seen: set[tuple[str, str]] = set()
        for h in hits:
            k = (h.kind, h.text)
            if k in seen:  # 同じ画面に同じ文字が 2 つ: 2 本目は別の追跡にする
                done.append(Track(h.kind, h.text, [(t, h.box)]))
                continue
            seen.add(k)
            tr = live.get(k)
            if tr is not None and t - tr.keys[-1][0] <= step * 1.5:
                tr.keys.append((t, h.box))
            else:
                if tr is not None:
                    done.append(tr)
                live[k] = Track(h.kind, h.text, [(t, h.box)])
        for k in [k for k, tr in live.items() if k not in seen and t - tr.keys[-1][0] > step * 1.5]:
            done.append(live.pop(k))
    done += live.values()
    return sorted(done, key=lambda x: x.t0)


def boxes_at(tracks: list[Track], t: float) -> list[tuple[float, float, float, float]]:
    """時刻 t にぼかす範囲（録画の画素・少し広げる）。"""
    out = []
    for tr in tracks:
        if not (tr.t0 <= t <= tr.t1):
            continue
        ks = tr.keys
        if t <= ks[0][0]:
            b = ks[0][1]
        elif t >= ks[-1][0]:
            b = ks[-1][1]
        else:
            j = next(i for i in range(1, len(ks)) if ks[i][0] >= t)
            (ta, ba), (tb, bb) = ks[j - 1], ks[j]
            u = (t - ta) / max(1e-9, tb - ta)
            b = tuple(p + (q - p) * u for p, q in zip(ba, bb, strict=True))  # type: ignore[assignment]
        x, y, w, h = b
        out.append((x - BLUR_PAD, y - BLUR_PAD, w + 2 * BLUR_PAD, h + 2 * BLUR_PAD))
    return out


def fast_ranges(
    motion: list[tuple[float, float]],
    silences: list[tuple[float, float]],
    duration: float,
    min_len: float = FAST_MIN,
    thr: float = 0.0005,
) -> list[tuple[float, float]]:
    """画面の変化（秒・変わった画素の割合）も声も無い min_len 秒以上の区間。

    motion はサンプルの列（隣のサンプルとの差）。silences は声の無い区間。"""
    still: list[tuple[float, float]] = []
    if motion:
        start: float | None = None
        prev_t = 0.0
        for t, frac in motion:
            if frac <= thr:
                if start is None:
                    start = prev_t
            elif start is not None:
                still.append((start, prev_t))
                start = None
            prev_t = t
        if start is not None:
            still.append((start, duration))
    out = []
    for a, b in still:
        for s0, s1 in silences:
            x, y = max(a, s0), min(b, s1)
            if y - x >= min_len:
                out.append((x, y))
    out.sort()
    return out


def visible_keys(keys: list[tuple[float, str]]) -> list[tuple[float, str]]:
    """出すキー操作（1 秒に KEY_BURST 回以上続く入力の中のものは除く）。"""
    ks = sorted(keys)
    ts = [t for t, _ in ks]
    out = []
    for t, label in ks:
        # t を含むどの 1 秒の窓でも KEY_BURST 回未満なら出す
        busy = False
        for s in ts:
            if s <= t <= s + 1.0 and sum(1 for u in ts if s <= u <= s + 1.0) >= KEY_BURST:
                busy = True
                break
        if not busy:
            out.append((t, label))
    return out


def key_at(keys: list[tuple[float, str]], t: float) -> str | None:
    """時刻 t に出すキー（最後に押したもの、1.8 秒まで）。"""
    cur = None
    for k, label in keys:
        if k <= t < k + KEY_SEC:
            cur = label
    return cur


def cursor_at(cursor: list[tuple[float, float, float]], t: float) -> tuple[float, float] | None:
    """時刻 t のカーソルの位置（記録の間を補間。記録の前後 0.5 秒より外は無し）。"""
    if not cursor:
        return None
    if t < cursor[0][0] - 0.5 or t > cursor[-1][0] + 0.5:
        return None
    if t <= cursor[0][0]:
        return cursor[0][1], cursor[0][2]
    for (ta, xa, ya), (tb, xb, yb) in zip(cursor, cursor[1:], strict=False):
        if ta <= t <= tb:
            u = (t - ta) / max(1e-9, tb - ta)
            return xa + (xb - xa) * u, ya + (yb - ya) * u
    return cursor[-1][1], cursor[-1][2]


def speed_segments(
    fast: list[tuple[float, float]], duration: float
) -> list[tuple[float, float, int]]:
    """録画の全体を (始め, 終わり, 倍速) の区間に分ける。"""
    segs: list[tuple[float, float, int]] = []
    t = 0.0
    for a, b in fast:
        if a > t:
            segs.append((t, a, 1))
        segs.append((a, b, FAST_SPEED))
        t = b
    if t < duration:
        segs.append((t, duration, 1))
    return [s for s in segs if s[1] - s[0] > 1e-6]


def audio_filter(segs: list[tuple[float, float, int]]) -> str:
    """音声を区間ごとに切り、早送りの区間は 4 倍（atempo は 2 倍まで → 2 回）にしてつなぐ。"""
    parts, labels = [], []
    for i, (a, b, sp) in enumerate(segs):
        f = f"[1:a]atrim={a:.4f}:{b:.4f},asetpts=PTS-STARTPTS"
        if sp == FAST_SPEED:
            f += ",atempo=2,atempo=2"
        parts.append(f + f"[a{i}]")
        labels.append(f"[a{i}]")
    parts.append("".join(labels) + f"concat=n={len(segs)}:v=0:a=1[aout]")
    return ";".join(parts)


# 統合版 N14 スポットライト: クリックの 0.3 秒前から 1.2 秒間、半径は画面の高さの 18% の外を
# 黒・不透明度 50% にする。入り・出は 6f
SPOT_BEFORE, SPOT_SEC, SPOT_RADIUS, SPOT_DIM, SPOT_FADE = 0.3, 1.2, 0.18, 0.5, 6


def spotlight_level(dt: float, fps: float) -> float:
    """クリックからの秒 dt での暗さの割合（0〜1。入り・出の 6f は直線で）。"""
    a = dt + SPOT_BEFORE
    if a < 0 or a > SPOT_SEC:
        return 0.0
    fade = SPOT_FADE / fps
    return min(1.0, a / fade, (SPOT_SEC - a) / fade)


def spotlight(im: Any, x: float, y: float, level: float) -> Any:
    """(x, y) を中心に半径 画面の高さ × 18% の外を暗くした画（RGB）。"""
    from PIL import Image, ImageDraw

    w, h = im.size
    r = SPOT_RADIUS * h
    mask = Image.new("L", (w, h), round(255 * SPOT_DIM * level))
    ImageDraw.Draw(mask).ellipse([x - r, y - r, x + r, y + r], fill=0)
    black = Image.new("RGB", (w, h), (0, 0, 0))
    return Image.composite(black, im, mask)
