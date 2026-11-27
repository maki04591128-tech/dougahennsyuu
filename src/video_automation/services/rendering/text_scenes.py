"""文字だけの場面の型（統合版 段階5c: T30・T32・T33・T34）。Pillow で描く。

文字だけの場面: 映像・画像・カード・図表の素材が無い 3 秒以上の台詞の区間（解説は各章の最初の文の
区間だけ）。1 本の合計は動画の長さの最大 20%。この区間では字幕・強調テロップを出さず、下の型で
台詞の全文節を出す。

- T30 押しのける字組み（解説・商品紹介・イベント）: 中央揃え・1 行に 1 文節・行間は文字の 1.2 倍。
  行の幅が画面の幅の 80%（最大は画面の高さの 18%）。新しい文節が入るとき、既にある行は新しい位置へ
  8f（easeOutCubic）で動き、新しい文節は T10（下から 10f、easeOutExpo）で入る。4 行目が入るとき、
  一番上の行は上へ抜けて消える（8f、easeInCubic）
- T32 一語ずつ切り替える（PR・CM）: 中央に 1 文節だけを、文節の頭にくっきり出して、次の文節の
  頭で消す。
  幅が画面の幅の 70%（最大は画面の高さの 30%）。背景は base、文字は text、1 文の最後の文節は accent
- T33 塊を拍で叩きつける（縦型ショート）: 文をまたがずに 3 文節ずつの塊。塊の中は左揃え・1 行に
  1 文節。各文節は最も近い BGM の拍の頭（差が 4f を超えるなら文節の頭）に、下から y +100%→0・
  scale 1.25→1 を 6f（easeOutExpo）で入る。塊で最も字数の多い文節は accent の面に base の文字。
  次の塊が入るとき、前の塊は上へ画面の高さの 30%・不透明度 1→0 を 6f（easeInCubic）。塊を出して
  いる間、画面全体を 1.00→1.04（linear）
- T34 文字の面をゆっくり押し込む（予告編）: 台本の全文を前から繰り返した小さな文字（画面の高さの
  4%・行間 1.2・text・不透明度 35%）で画面を埋め、今の文節を中央に画面の高さの 12% で重ねる。
  区間の間、画面全体を 1.00→1.25 で一定の速さで寄せる
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

KIND = {
    "explainer": "T30",
    "product": "T30",
    "event": "T30",
    "promo": "T32",
    "short": "T33",
    "trailer": "T34",
}
MIN_SEC = 3.0
MAX_SHARE = 0.20
_PARTICLE = re.compile(r"(?<=[をがはにでとへもの、。！？])")


@dataclass
class Phrase:
    text: str
    start: int  # フレーム
    end: int
    last: bool = False  # 文の最後の文節


def _rgb(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def _font(path: str | None, size: int) -> Any:
    from PIL import ImageFont

    if path and Path(path).is_file():
        return ImageFont.truetype(path, max(1, size))
    return ImageFont.load_default(max(1, size))


def _cubic_out(u: float) -> float:
    u = min(max(u, 0.0), 1.0)
    return 1 - (1 - u) ** 3


def _cubic_in(u: float) -> float:
    u = min(max(u, 0.0), 1.0)
    return u**3


def _expo_out(u: float) -> float:
    u = min(max(u, 0.0), 1.0)
    return 1.0 if u >= 1 else 1 - 2 ** (-10 * u)


def chunks(text: str) -> list[str]:
    """文節（BudouX があればそれ、無ければ助詞・句読点の後で切る）。空白・空の文節は除く。"""
    from video_automation.services.subtitle.text import phrase_bounds

    t = text.strip()
    b = phrase_bounds(t)
    if b is not None:
        cuts = sorted(b)
        parts = [t[i:j] for i, j in zip([0, *cuts], [*cuts, len(t)], strict=True)]
    else:
        parts = _PARTICLE.split(t)
    return [p.strip() for p in parts if p.strip()]


def phrases(text: str, start: int, end: int, kana_starts: list[int] | None = None) -> list[Phrase]:
    """1 つの発話の文節と時刻。アクセント句の数と文節の数が同じならアクセント句の頭、
    違えば字数の割合で分ける。"""
    cs = chunks(text)
    if not cs:
        return []
    if kana_starts and len(kana_starts) == len(cs):
        starts = [max(start, min(end - 1, s)) for s in kana_starts]
    else:
        total = sum(len(c) for c in cs)
        acc, starts = 0, []
        for c in cs:
            starts.append(start + (end - start) * acc // max(1, total))
            acc += len(c)
    out = []
    for i, c in enumerate(cs):
        nxt = starts[i + 1] if i + 1 < len(cs) else end
        out.append(Phrase(c, starts[i], max(starts[i] + 1, nxt), last=i == len(cs) - 1))
    return out


def pick_sections(
    lines: list[tuple[int, int, str]],
    busy: list[tuple[int, int]],
    total: int,
    fps: float,
    purpose: str,
    chapter_starts: list[int] | None = None,
) -> list[tuple[int, int]]:
    """文字だけの場面の区間（発話の番号の [i, j)）。素材の無い 3 秒以上の連続した発話。
    解説は各章の最初の文だけ。合計は動画の長さの 20% まで（超える分は後ろから外す）。"""
    out: list[tuple[int, int]] = []
    i = 0
    n = len(lines)
    while i < n:
        a0, _b0, _t0 = lines[i]
        if any(x < lines[i][1] and a0 < y for x, y in busy):
            i += 1
            continue
        j = i + 1
        while j < n and not any(x < lines[j][1] and lines[j][0] < y for x, y in busy):
            j += 1
        if purpose == "explainer":
            starts = set(chapter_starts or [0])
            for k in range(i, j):
                is_first = k == 0 or any(lines[k - 1][1] <= s <= lines[k][0] for s in starts)
                if is_first and (lines[k][1] - lines[k][0]) >= MIN_SEC * fps:
                    out.append((k, k + 1))
        else:  # 続く発話を 3 秒以上になるまでまとめて 1 つの場面にする
            k = i
            while k < j:
                m = k + 1
                while m < j and lines[m - 1][1] - lines[k][0] < MIN_SEC * fps:
                    m += 1
                if lines[m - 1][1] - lines[k][0] >= MIN_SEC * fps:
                    out.append((k, m))
                k = m
        i = j
    budget = MAX_SHARE * total
    kept, used = [], 0.0
    for a, b in out:
        d = lines[b - 1][1] - lines[a][0]
        if used + d > budget:
            continue
        kept.append((a, b))
        used += d
    return kept


# ---------- 描く ----------


def _fit_size(text: str, font: str | None, width: float, cap: float) -> int:
    probe = 100
    tw = _font(font, probe).getlength(text) or 1.0
    return max(8, min(round(cap), round(probe * width / tw)))


def t32_frames(ph: list[Phrase], start: int, n: int, w: int, h: int, font: str | None,
               base: str, ink: str, accent: str) -> Iterator[Any]:  # fmt: skip
    from PIL import Image, ImageDraw

    for f in range(n):
        t = start + f
        im = Image.new("RGBA", (w, h), (*_rgb(base), 255))
        cur = next((p for p in ph if p.start <= t < p.end), None)
        if cur is not None:
            size = _fit_size(cur.text, font, w * 0.70, h * 0.30)
            col = _rgb(accent if cur.last else ink)
            ImageDraw.Draw(im).text((w / 2, h / 2), cur.text, font=_font(font, size), fill=col,
                                    anchor="mm")  # fmt: skip
        yield im


def t30_frames(ph: list[Phrase], start: int, n: int, w: int, h: int, font: str | None,
               ink: str) -> Iterator[Any]:  # fmt: skip
    from PIL import Image, ImageDraw

    sizes = [_fit_size(p.text, font, w * 0.80, h * 0.18) for p in ph]
    fonts = {s: _font(font, s) for s in set(sizes)}

    def layout(k: int) -> list[float]:
        """k 番目までが出ているとき（最大 3 行）の各行の中心の y。"""
        shown = list(range(max(0, k - 2), k + 1))
        hs = [sizes[i] * 1.2 for i in shown]
        y = h / 2 - sum(hs) / 2
        ys = []
        for hh in hs:
            ys.append(y + hh / 2)
            y += hh
        return ys

    for f in range(n):
        t = start + f
        k = max((i for i, p in enumerate(ph) if p.start <= t), default=-1)
        im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        if k < 0:
            yield im
            continue
        d = ImageDraw.Draw(im)
        u = _cubic_out((t - ph[k].start + 1) / 8)
        new_ys = layout(k)
        old_ys = layout(k - 1) if k > 0 else []
        shown = list(range(max(0, k - 2), k + 1))
        for idx, i in enumerate(shown):
            if i == k:
                continue
            prev_shown = list(range(max(0, k - 3), k))
            y0 = old_ys[prev_shown.index(i)] if i in prev_shown and old_ys else new_ys[idx]
            y = y0 + (new_ys[idx] - y0) * u
            d.text((w / 2, y), ph[i].text, font=fonts[sizes[i]], fill=_rgb(ink), anchor="mm")
        if k >= 3:  # 4 行目が入るとき、一番上の行は上へ抜けて消える
            gone = k - 3
            e = _cubic_in((t - ph[k].start + 1) / 8)
            if e < 1 and old_ys:
                lay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                y = old_ys[0] - sizes[gone] * 1.2 * e
                fill = (*_rgb(ink), round(255 * (1 - e)))
                ImageDraw.Draw(lay).text((w / 2, y), ph[gone].text, font=fonts[sizes[gone]],
                                         fill=fill, anchor="mm")  # fmt: skip
                im.alpha_composite(lay)
        # 新しい文節: T10（行の高さで切り抜いて下から 10f）
        e = _expo_out((t - ph[k].start + 1) / 10)
        lh = sizes[k] * 1.2
        y = new_ys[-1]
        lay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(lay).text((w / 2, y + (1 - e) * lh), ph[k].text, font=fonts[sizes[k]],
                                 fill=_rgb(ink), anchor="mm")  # fmt: skip
        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).rectangle((0, y - lh / 2, w, y + lh / 2), fill=255)
        from PIL import ImageChops

        lay.putalpha(ImageChops.multiply(lay.getchannel("A"), mask))
        im.alpha_composite(lay)
        yield im


def beat_snap(t: int, fps: float, bpm: float, tol: int = 4) -> int:
    """T33: 最も近い拍の頭（BGM の始まりを 0 拍目）。差が tol フレームを超えるなら t のまま。"""
    step = fps * 60 / bpm
    b = round(round(t / step) * step)
    return b if abs(b - t) <= tol else t


def t33_frames(ph: list[Phrase], start: int, n: int, w: int, h: int, font: str | None,
               ink: str, accent: str, base: str, fps: float,
               bpm: float) -> Iterator[Any]:  # fmt: skip
    from PIL import Image, ImageChops, ImageDraw

    # 文をまたがない 3 文節ずつの塊
    groups: list[list[int]] = []
    cur: list[int] = []
    for i, p in enumerate(ph):
        cur.append(i)
        if len(cur) == 3 or p.last:
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    enter = [beat_snap(p.start, fps, bpm) for p in ph]
    longest_all = max((p.text for p in ph), key=len, default="あ")
    size = _fit_size(longest_all, font, w * 0.80, h * 0.12)
    f_ = _font(font, size)
    lh = size * 1.35

    def draw_group(g: list[int], t: int) -> Any:
        im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        longest = max(g, key=lambda i: (len(ph[i].text), -i))
        x0 = w * 0.10
        y0 = h / 2 - lh * len(g) / 2
        for r, i in enumerate(g):
            dt = t - enter[i]
            if dt < 0:
                continue
            e = _expo_out((dt + 1) / 6)
            s = 1.25 - 0.25 * e
            y = y0 + r * lh
            lay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            d = ImageDraw.Draw(lay)
            tw = f_.getlength(ph[i].text)
            if i == longest:
                pad = size * 0.15
                d.rectangle((x0 - pad, y - pad, x0 + tw + pad, y + size + pad),
                            fill=_rgb(accent))  # fmt: skip
                d.text((x0, y), ph[i].text, font=f_, fill=_rgb(base))
            else:
                d.text((x0, y), ph[i].text, font=f_, fill=_rgb(ink))
            if s != 1:
                lay = lay.resize((round(w * s), round(h * s)), Image.Resampling.BICUBIC)
                ox, oy = round(x0 * s - x0), round((y + size / 2) * s - (y + size / 2))
                lay = lay.crop((ox, oy, ox + w, oy + h))
            moved = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            dy = round((1 - e) * lh)
            moved.alpha_composite(lay.crop((0, 0, w, h - dy)) if dy > 0 else lay, (0, dy))
            mask = Image.new("L", (w, h), 0)
            ImageDraw.Draw(mask).rectangle((0, y - size * 0.3, w, y + lh), fill=255)
            moved.putalpha(ImageChops.multiply(moved.getchannel("A"), mask))
            im.alpha_composite(moved)
        return im

    for f in range(n):
        t = start + f
        gi = max((k for k, g in enumerate(groups) if enter[g[0]] <= t), default=-1)
        im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        if gi >= 0:
            g = groups[gi]
            g_start = enter[g[0]]
            g_end = enter[groups[gi + 1][0]] if gi + 1 < len(groups) else start + n
            cur_im = draw_group(g, t)
            z = 1.0 + 0.04 * min(1.0, (t - g_start) / max(1, g_end - g_start))
            if z > 1.0:
                zw, zh = round(w * z), round(h * z)
                cur_im = cur_im.resize((zw, zh), Image.Resampling.BICUBIC).crop(
                    ((zw - w) // 2, (zh - h) // 2, (zw - w) // 2 + w, (zh - h) // 2 + h)
                )
            im.alpha_composite(cur_im)
            if gi > 0 and t - g_start < 6:  # 前の塊は上へ抜けて消える
                e = _cubic_in((t - g_start + 1) / 6)
                prev = draw_group(groups[gi - 1], g_start - 1)
                prev.putalpha(prev.getchannel("A").point(lambda v, e=e: round(v * (1 - e))))
                dy = round(h * 0.30 * e)
                shifted = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                shifted.alpha_composite(prev.crop((0, dy, w, h)), (0, 0))
                im.alpha_composite(shifted)
        yield im


def t34_frames(ph: list[Phrase], start: int, n: int, w: int, h: int, font: str | None,
               ink: str, script: str) -> Iterator[Any]:  # fmt: skip
    from PIL import Image, ImageDraw

    small = max(6, round(h * 0.04))
    fs = _font(font, small)
    wall = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(wall)
    text = re.sub(r"\s+", "", script) or "…"
    pos = 0
    y = 0.0
    while y < h:
        line = ""
        while fs.getlength(line) < w:
            line += text[pos % len(text)]
            pos += 1
        d.text((0, y), line, font=fs, fill=(*_rgb(ink), round(255 * 0.35)))
        y += small * 1.2
    big = max(8, round(h * 0.12))
    fb = _font(font, big)
    for f in range(n):
        t = start + f
        z = 1.0 + 0.25 * f / max(1, n - 1)
        im = wall.copy()
        cur = next((p for p in ph if p.start <= t < p.end), None)
        if cur is not None:
            ImageDraw.Draw(im).text((w / 2, h / 2), cur.text, font=fb, fill=_rgb(ink), anchor="mm")
        zw, zh = round(w * z), round(h * z)
        im = im.resize((zw, zh), Image.Resampling.BICUBIC).crop(
            ((zw - w) // 2, (zh - h) // 2, (zw - w) // 2 + w, (zh - h) // 2 + h)
        )
        yield im


def frames(kind: str, ph: list[Phrase], start: int, n: int, w: int, h: int, font: str | None,
           pal: Any, fps: float, bpm: float, script: str) -> Iterator[Any]:  # fmt: skip
    if kind == "T32":
        return t32_frames(ph, start, n, w, h, font, pal.base, pal.text, pal.accent)
    if kind == "T33":
        return t33_frames(ph, start, n, w, h, font, pal.text, pal.accent, pal.base, fps, bpm)
    if kind == "T34":
        return t34_frames(ph, start, n, w, h, font, pal.text, script)
    return t30_frames(ph, start, n, w, h, font, pal.text)
