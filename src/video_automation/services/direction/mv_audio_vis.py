"""音の視覚化（統合版 段階5d: M10・M11。MV・リリック）。透過の画の列を作る。

- M11 拍の輪: イントロ（最初の歌詞まで）の拍ごとに、中央から輪（太さ 3px・V12 の文字の色）が
  半径 0 → 画面の高さの 45% へ広がり、不透明度 60% → 0。1 つの輪は 20f
- M10 スペクトラム: 2 回目以降の間奏に、画面の下から 10% の位置に横幅 60% で棒 32 本。棒の高さは
  40〜8000Hz を対数で 32 に分けた各帯域の強さ（曲全体の 95 パーセンタイルで割る。最大は画面の
  高さの 20%）。下がるときは 150ms で戻す
- T29 文字で絵を作る: 1 曲で最初の間奏の最初の 1 小節。そのときのショットの明るいマスに直前の
  歌詞の文字を並べる（下の text_art_frames）
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

RING_FRAMES = 20
BARS = 32
LOW, HIGH = 40.0, 8000.0
RELEASE_SEC = 0.15


def _rgb(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def interludes(spans: list[tuple[float, float, str]]) -> list[tuple[float, float]]:
    """歌詞の区間の間で、歌詞の無い区間（line_spans で間奏として残った隙間）。"""
    out = []
    for (_a, b, _s), (a2, _b2, _s2) in zip(spans, spans[1:], strict=False):
        if a2 - b > 0.5:
            out.append((b, a2))
    return out


def ring_frames(
    beats: list[float], end: float, fps: float, w: int, h: int, color: str
) -> list[Any]:
    """M11（0 秒〜end 秒の画の列）。"""
    from PIL import Image, ImageDraw

    n = max(1, round(end * fps))
    starts = [round(b * fps) for b in beats if b < end]
    col = _rgb(color)
    lw = max(2, round(3 * h / 1080))
    out = []
    for f in range(n):
        im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        for s in starts:
            if 0 <= f - s < RING_FRAMES:
                u = (f - s) / RING_FRAMES
                r = 0.45 * h * (1 - (1 - u) ** 3)
                a = round(255 * 0.6 * (1 - u))
                if r > lw:
                    d.ellipse(
                        [w / 2 - r, h / 2 - r, w / 2 + r, h / 2 + r], outline=(*col, a), width=lw
                    )
        out.append(im)
    return out


def band_levels(wave: np.ndarray, sr: int, fps: float) -> np.ndarray:
    """フレームごとの 32 帯域の強さ（全体の 95 パーセンタイルで割り最大 1。下がるときは 150ms）。"""
    hop = sr / fps
    win = 2048
    nfr = max(1, int(len(wave) / hop))
    freqs = np.fft.rfftfreq(win, 1 / sr)
    edges = np.geomspace(LOW, HIGH, BARS + 1)
    idx = [np.where((freqs >= a) & (freqs < b))[0] for a, b in zip(edges, edges[1:], strict=False)]
    window = np.hanning(win).astype(np.float32)
    lev = np.zeros((nfr, BARS), np.float32)
    for f in range(nfr):
        c = int(f * hop)
        seg = wave[max(0, c - win // 2) : c + win // 2]
        if len(seg) < win:
            seg = np.pad(seg, (0, win - len(seg)))
        mag = np.abs(np.fft.rfft(seg * window))
        for k, ix in enumerate(idx):
            if len(ix):
                lev[f, k] = float(mag[ix].mean())
    ref = np.percentile(lev, 95, axis=0)
    lev = np.minimum(1.0, lev / np.maximum(ref, 1e-9))
    rel = math.exp(-1 / max(1e-6, RELEASE_SEC * fps))  # 下がるときは 150ms でなめらかに
    for f in range(1, nfr):
        lev[f] = np.maximum(lev[f], lev[f - 1] * rel)
    return lev


def spectrum_frames(levels: np.ndarray, a: float, b: float, fps: float, w: int, h: int,
                    color: str) -> list[Any]:  # fmt: skip
    """M10（a〜b 秒の画の列）。"""
    from PIL import Image, ImageDraw

    col = _rgb(color)
    f0, f1 = round(a * fps), round(b * fps)
    x0, span = w * 0.2, w * 0.6
    bw = span / BARS
    base = h * 0.9
    out = []
    for f in range(f0, f1):
        im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        lv = levels[min(f, len(levels) - 1)] if len(levels) else np.zeros(BARS)
        for k in range(BARS):
            hh = float(lv[k]) * h * 0.2
            if hh >= 1:
                x = x0 + k * bw
                d.rectangle([x + bw * 0.15, base - hh, x + bw * 0.85, base], fill=(*col, 255))
        out.append(im)
    return out


def key_char(texts: list[str]) -> str | None:
    """統合版 T26: サビの歌詞で最も多く出る漢字 1 字（無ければ最も多いかな 1 字）。"""
    from collections import Counter

    s = "".join(texts)
    kanji = Counter(c for c in s if "一" <= c <= "龯" or c in "々")
    if kanji:
        return kanji.most_common(1)[0][0]
    kana = Counter(c for c in s if "ぁ" <= c <= "ゖ" or "ァ" <= c <= "ヺ")
    return kana.most_common(1)[0][0] if kana else None


def key_char_image(ch: str, w: int, h: int, font: str | None, color: str) -> Any:
    """T26: 画面の高さの 90%・V12 の文字の色・不透明度 12% の 1 字（透過の全面の画）。"""
    from pathlib import Path

    from PIL import Image, ImageDraw, ImageFont

    size = round(h * 0.9)
    f = (
        ImageFont.truetype(font, size)
        if font and Path(font).is_file()
        else ImageFont.load_default(size)
    )
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((w / 2, h / 2), ch, font=f, fill=(*_rgb(color), round(255 * 0.12)),
                            anchor="mm")  # fmt: skip
    return im


# 統合版 T29: 文字で絵を作る（1 曲で最初の間奏の最初の 1 小節）
T29_COLS, T29_ROWS = 48, 27
T29_LUMA = 0.25
T29_MOVE = 18  # 各文字が自分のマスへ動く長さ（easeOutCubic）
T29_OUT = 6


def text_art_cells(
    img: Any, cols: int = T29_COLS, rows: int = T29_ROWS
) -> list[tuple[int, int, float]]:
    """画を cols×rows のマスに分け、Rec.709 の輝度の平均が T29_LUMA 以上のマス（列, 行, 輝度）。"""
    rgb = np.asarray(img.convert("RGB").resize((cols, rows), resample=2), dtype=np.float32) / 255
    # resize の BOX/BILINEAR の平均で各マスの平均の近似にする（輝度は線形の値ではなく画素値で取る）
    luma = rgb[..., 0] * 0.2126 + rgb[..., 1] * 0.7152 + rgb[..., 2] * 0.0722
    return [(c, r, float(luma[r, c])) for r in range(rows) for c in range(cols)
            if luma[r, c] >= T29_LUMA]  # fmt: skip


def text_art_frames(
    shot: Any,
    text: str,
    n: int,
    w: int,
    h: int,
    main: str,
    ink: str,
    font: str | None,
    origin: tuple[float, float] | None = None,
) -> list[Any]:
    """T29（n フレーム。不透明の画の列）。画面を 48×27 のマスに分け、明るいマスにだけ直前の歌詞の
    文字を前から繰り返して置く（大きさは 1 マスの 0.9 倍、不透明度はマスの輝度）。マスの外と文字の
    後ろは主色。文字は origin（直前の行の位置。既定は画面の下 20% の中央）から、下の段から 1 段 1f
    ずらして各 18f で動く。最後の 6f で不透明度 1→0（下の画が見えてくる）。"""
    from pathlib import Path

    from PIL import Image, ImageDraw, ImageFont

    chars = [ch for ch in text if not ch.isspace()] or ["・"]
    cells = text_art_cells(shot)
    cw, chh = w / T29_COLS, h / T29_ROWS
    size = max(8, round(min(cw, chh) * 0.9))
    f = (ImageFont.truetype(font, size) if font and Path(font).is_file()
         else ImageFont.load_default(size))  # fmt: skip
    glyphs: dict[str, Any] = {}
    for ch in set(chars):
        g = Image.new("L", (size * 2, size * 2), 0)
        ImageDraw.Draw(g).text((size, size), ch, font=f, fill=255, anchor="mm")
        glyphs[ch] = g
    ox, oy = origin or (w / 2, h * 0.8)
    col = _rgb(ink)
    bg = (*_rgb(main), 255)
    items = [(chars[i % len(chars)], (c + 0.5) * cw, (r + 0.5) * chh, lum, T29_ROWS - 1 - r)
             for i, (c, r, lum) in enumerate(cells)]  # fmt: skip
    tinted: dict[tuple[str, int], Any] = {}
    out = []
    for t in range(n):
        im = Image.new("RGBA", (w, h), bg)
        for ch, x, y, lum, delay in items:
            k = min(1.0, max(0.0, (t - delay) / T29_MOVE))
            e = 1 - (1 - k) ** 3
            px, py = ox + (x - ox) * e, oy + (y - oy) * e
            key = (ch, round(lum * 32))
            fill = tinted.get(key)
            if fill is None:
                g = glyphs[ch]
                fill = Image.new("RGBA", g.size, (*col, 255))
                fill.putalpha(g.point(lambda v, a_=key[1] / 32: round(v * min(1.0, a_))))
                tinted[key] = fill
            im.alpha_composite(fill, (round(px - size), round(py - size)))
        if t >= n - T29_OUT:
            k = (n - t) / (T29_OUT + 1)
            im.putalpha(im.getchannel("A").point(lambda v, k_=k: round(v * k_)))
        out.append(im)
    return out
