"""図表の追加（統合版 段階5d: D5〜D12）。LLM が発話に付けた `chart` から Pillow で描く。

chart: {kind, labels, values, unit, series（race だけ: 時点ごとの values）, title}
- number → D6 大きな数字（T35 で回す。下に単位と一言）
- share → D7 ドーナツの切り出し（最大の項目を外へずらし、中央にその割合）
- progress → D12 進み具合の輪（半径は高さの 22%・太さ 3%、中央の数字）
- bars → D3 の棒（既存の棒グラフと同じ見た目の静止画）
- race → D5 棒グラフの競争（最後の時点の順位の横棒。1 位だけ accent）
- pair → D8 左右の比較の棒
- radar → D9 レーダー（2 者。面は不透明度 30%・線 2px）
- flow → D10 流れの図（最大の流れだけ accent、他は 35%）
- funnel → D11 ファネル（幅 = 量の比 × 画面の幅の 70%、最も減った段の間に accent の矢印）
動き（各図の伸びる・回る）は、D6 の数字が回る以外は、入りのフェードだけにした（静止画）。
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

KINDS = ("number", "share", "progress", "bars", "race", "pair", "radar", "flow", "funnel")


def _font(path: str | None, size: int) -> Any:
    from PIL import ImageFont

    if path and Path(path).is_file():
        return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


def _rgb(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def _num(v: Any) -> float | None:
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def valid(chart: dict[str, Any]) -> bool:
    """形に合うデータか（統合版 5d の「データの形 → 図」の数の決まり）。"""
    k = chart.get("kind")
    vals = [x for x in (_num(v) for v in chart.get("values") or []) if x is not None]
    labels = chart.get("labels") or []
    if k == "number":
        return len(vals) == 1
    if k == "progress":
        return len(vals) == 1 and 0 <= vals[0] <= 100
    if k == "share":
        return 2 <= len(vals) <= 5 and len(labels) >= len(vals)
    if k in ("bars", "funnel"):
        lo = 3
        return lo <= len(vals) <= (5 if k == "funnel" else 8) and len(labels) >= len(vals)
    if k == "race":
        series = chart.get("series") or []
        return len(series) >= 3 and 3 <= len(labels) <= 8
    if k in ("pair", "radar"):
        return 3 <= len(labels) <= 6 and len(chart.get("series") or []) == 2
    if k == "flow":
        return 2 <= len(labels) and len(vals) == len(labels)
    return False


def _canvas(w: int, h: int) -> Any:
    from PIL import Image

    return Image.new("RGBA", (w, h), (0, 0, 0, 0))


def draw(chart: dict[str, Any], w: int, h: int, font: str | None, pal: dict[str, Any],
         ink: str) -> Any:  # fmt: skip
    """静止の図（number 以外）。"""
    from PIL import ImageDraw

    k = chart["kind"]
    im = _canvas(w, h)
    d = ImageDraw.Draw(im)
    acc, cols = _rgb(pal["accent"]), [_rgb(c) for c in pal["chart"]]
    labels = [str(x)[:10] for x in chart.get("labels") or []]
    vals = [_num(v) or 0.0 for v in chart.get("values") or []]
    unit = str(chart.get("unit") or "")[:6]
    fs = _font(font, round(h * 0.04))
    fb = _font(font, round(h * 0.12))
    st: dict[str, Any] = {"stroke_width": max(1, round(h / 400)), "stroke_fill": (0, 0, 0, 180)}
    txt = (*_rgb(ink), 255)
    cx, cy = w / 2, h * 0.5
    if k in ("share", "progress"):
        r = h * 0.22 if k == "progress" else h * 0.26
        thick = round(h * 0.03) if k == "progress" else round(r * 0.45)
        if k == "progress":
            d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(*cols[-1], 90), width=thick)
            d.arc([cx - r, cy - r, cx + r, cy + r], -90, -90 + 3.6 * vals[0], fill=(*acc, 255),
                  width=thick)  # fmt: skip
            d.text((cx, cy), f"{vals[0]:g}%", font=fb, fill=txt, anchor="mm", **st)
        else:
            tot = sum(vals) or 1
            big = max(range(len(vals)), key=lambda i: vals[i])
            a0 = -90.0
            for i, v in enumerate(vals):
                a1 = a0 + 360 * v / tot
                mid = math.radians((a0 + a1) / 2)
                off = r * 0.04 if i == big else 0
                ox, oy = math.cos(mid) * off, math.sin(mid) * off
                box = [cx - r + ox, cy - r + oy, cx + r + ox, cy + r + oy]
                col = acc if i == big else cols[i % len(cols)]
                d.arc(box, a0, a1, fill=(*col, 255), width=thick)
                lx, ly = cx + math.cos(mid) * r * 1.35, cy + math.sin(mid) * r * 1.35
                d.text((lx, ly), labels[i] if i < len(labels) else "", font=fs, fill=txt,
                       anchor="mm", **st)  # fmt: skip
                a0 = a1
            d.text((cx, cy), f"{100 * vals[big] / tot:.0f}%", font=_font(font, round(h * 0.12)),
                   fill=txt, anchor="mm", **st)  # fmt: skip
    elif k in ("bars", "race", "funnel"):
        if k == "race":
            last = chart["series"][-1]
            raw = (last.get("values") if isinstance(last, dict) else last) or []
            vals = [_num(v) or 0.0 for v in raw]
            order = sorted(range(len(vals)), key=lambda i: -vals[i])[:6]
            labels = [labels[i] for i in order if i < len(labels)]
            vals = [vals[i] for i in order]
        n = len(vals)
        mx = max(vals) or 1
        bh = h * 0.08 if k != "funnel" else h * 0.1
        gap = h * 0.02
        top = cy - (bh + gap) * n / 2
        for i, v in enumerate(vals):
            y = top + i * (bh + gap)
            if k == "funnel":
                bw = v / mx * w * 0.7
                x0 = cx - bw / 2
                col = cols[i % len(cols)]
            else:
                bw = v / mx * w * 0.55
                x0 = w * 0.28
                col = (
                    acc
                    if (k == "race" and i == 0)
                    else cols[1 % len(cols)]
                    if k == "race"
                    else cols[i % len(cols)]
                )
            d.rectangle([x0, y, x0 + max(2, bw), y + bh], fill=(*col, 255))
            lab = labels[i] if i < len(labels) else ""
            if k == "funnel":
                pct = f"（{100 * v / vals[i - 1]:.0f}%）" if i and vals[i - 1] else ""
                d.text((x0 + bw + 12, y + bh / 2), f"{lab} {v:g}{unit}{pct}", font=fs, fill=txt,
                       anchor="lm", **st)  # fmt: skip
            else:
                d.text((x0 - 12, y + bh / 2), lab, font=fs, fill=txt, anchor="rm", **st)
                d.text((x0 + bw - 8, y + bh / 2), f"{v:g}{unit}", font=fs, fill=txt, anchor="rm",
                       **st)  # fmt: skip
        if k == "funnel" and n >= 2:
            drops = [vals[i] / vals[i - 1] if vals[i - 1] else 1 for i in range(1, n)]
            j = min(range(len(drops)), key=lambda i: drops[i]) + 1
            y = top + j * (bh + gap) - gap / 2
            d.polygon([(cx - 14, y - 8), (cx + 14, y - 8), (cx, y + 8)], fill=(*acc, 255))
    elif k == "pair":
        a, b = chart["series"][0], chart["series"][1]
        va = [_num(v) or 0.0 for v in (a.get("values") or [])]
        vb = [_num(v) or 0.0 for v in (b.get("values") or [])]
        mx = max([*va, *vb, 1])
        n = len(labels)
        bh = h * 0.06
        top = cy - (bh * 1.8) * n / 2
        for i, lab in enumerate(labels):
            y = top + i * bh * 1.8
            d.text((cx, y - bh * 0.15), lab, font=fs, fill=txt, anchor="mb", **st)
            la = (va[i] if i < len(va) else 0) / mx * w * 0.38
            lb = (vb[i] if i < len(vb) else 0) / mx * w * 0.38
            d.rectangle([cx - la, y, cx - 2, y + bh], fill=(*cols[0], 255))
            d.rectangle([cx + 2, y, cx + lb, y + bh], fill=(*cols[1 % len(cols)], 255))
        for x, nm in ((w * 0.15, a.get("name", "")), (w * 0.85, b.get("name", ""))):
            d.text((x, top - h * 0.08), str(nm)[:10], font=fs, fill=txt, anchor="mm", **st)
    elif k == "radar":
        n = len(labels)
        r = h * 0.3
        angs = [-math.pi / 2 + 2 * math.pi * i / n for i in range(n)]
        pts = [(cx + r * math.cos(a), cy + r * math.sin(a)) for a in angs]
        for (px, py), lab in zip(pts, labels, strict=False):
            d.line([(cx, cy), (px, py)], fill=(*_rgb(ink), 120), width=1)
            d.text((cx + (px - cx) * 1.15, cy + (py - cy) * 1.15), lab, font=fs, fill=txt,
                   anchor="mm", **st)  # fmt: skip
        allv = [_num(v) or 0.0 for s in chart["series"] for v in (s.get("values") or [])]
        mx = max([*allv, 1])
        lay = _canvas(w, h)
        dl = ImageDraw.Draw(lay)
        for j, s in enumerate(chart["series"][:2]):
            vs = [_num(v) or 0.0 for v in (s.get("values") or [])]
            poly = [(cx + (px - cx) * (vs[i] / mx if i < len(vs) else 0),
                     cy + (py - cy) * (vs[i] / mx if i < len(vs) else 0))
                    for i, (px, py) in enumerate(pts)]  # fmt: skip
            col = cols[j % len(cols)]
            dl.polygon(poly, fill=(*col, 77), outline=(*col, 255), width=2)
        im.alpha_composite(lay)
    elif k == "flow":
        n = len(labels)
        half = max(1, n // 2)
        src, dst = list(range(half)), list(range(half, n))
        tot = sum(vals) or 1
        big = max(range(n), key=lambda i: vals[i])
        y = h * 0.2
        for i in src:
            hh = vals[i] / tot * h * 0.6
            d.rectangle([w * 0.15, y, w * 0.2, y + hh], fill=(*cols[i % len(cols)], 255))
            d.text((w * 0.14, y + hh / 2), labels[i], font=fs, fill=txt, anchor="rm", **st)
            j = dst[i % len(dst)] if dst else i
            a = 255 if i == big or j == big else round(255 * 0.35)
            col = acc if (i == big or j == big) else cols[i % len(cols)]
            d.polygon([(w * 0.2, y), (w * 0.8, y), (w * 0.8, y + hh), (w * 0.2, y + hh)],
                      fill=(*col, a))  # fmt: skip
            d.text((w * 0.81, y + hh / 2), labels[j] if j < n else "", font=fs, fill=txt,
                   anchor="lm", **st)  # fmt: skip
            y += hh + h * 0.02
    title = str(chart.get("title") or "")[:20]
    if title:
        d.text((cx, h * 0.12), title, font=_font(font, round(h * 0.05)), fill=txt, anchor="mm",
               **st)  # fmt: skip
    return im


def number_frames(chart: dict[str, Any], w: int, h: int, font: str | None, accent: str,
                  ink: str, caption: str) -> list[Any]:  # fmt: skip
    """D6 大きな数字（画面の高さの 30%・accent・T35 で回す。下に単位と一言を 6%）。"""
    from PIL import ImageDraw

    from video_automation.services.rendering.numbers import roll_frames_count, roll_text

    v = str((chart.get("values") or ["0"])[0])
    unit = str(chart.get("unit") or "")[:6]
    fb, fs = _font(font, round(h * 0.30)), _font(font, round(h * 0.06))
    st: dict[str, Any] = {"stroke_width": max(2, round(h / 200)), "stroke_fill": (0, 0, 0, 200)}
    out = []
    for i in range(roll_frames_count(v)):
        im = _canvas(w, h)
        d = ImageDraw.Draw(im)
        d.text((w / 2, h * 0.45), roll_text(v, i), font=fb, fill=(*_rgb(accent), 255),
               anchor="mm", **st)  # fmt: skip
        d.text((w / 2, h * 0.68), f"{unit} {caption}".strip()[:24], font=fs,
               fill=(*_rgb(ink), 255), anchor="mm", **st)  # fmt: skip
        out.append(im)
    return out
