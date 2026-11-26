"""用途ごとの部品の絵（統合版 P4・P5・P8）。透過 PNG。

- 材料・STEP・日付の札（draw_labels）: 文字の大きさ = 画面の高さの 4.5%（P1 の 1 行目と同じ）
- 一覧のカード（draw_list_card）: 材料の一覧（P4）と、ここまでの順位のボード（P5）
- 順位の大きな数字（draw_rank_number）: 「第 N 位」。数字は画面の高さの 22%
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from video_automation.services.rendering.lower_third import LINE_PX, NAME_RATIO, _rgba

RANK_RATIO = 0.22  # 順位の数字の大きさ（画面の高さに対する比）
LIST_ITEM_RATIO = 0.04
LIST_MAX = 10  # 一覧に出す最大の行数（それより多ければ最後の行を「ほか n 件」）


def _font(path: str | None, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if path and Path(path).is_file():
        return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


def _save(img: Image.Image, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out


def draw_labels(
    out: Path,
    canvas_h: int,
    lines: list[str],
    font_path: str | None,
    text_color: str,
    line_color: str,
    panel_color: str,
    panel_opacity: float = 0.85,
) -> Path:
    """左に色の線がある札を縦に積む（1 行に 1 つ）。"""
    k = canvas_h / 1080
    size = round(canvas_h * NAME_RATIO)
    f = _font(font_path, size)
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    line = max(2, round(LINE_PX * k))
    pad = round(size * 0.4)
    gap = round(size * 0.25)
    row_h = round(pad * 2 + size * 1.2)
    w = round(line + pad * 2 + max(probe.textlength(x, font=f) for x in lines))
    h = row_h * len(lines) + gap * (len(lines) - 1)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for i, txt in enumerate(lines):
        y = i * (row_h + gap)
        tw = round(line + pad * 2 + probe.textlength(txt, font=f))
        d.rectangle((0, y, tw - 1, y + row_h - 1), fill=_rgba(panel_color, panel_opacity))
        d.rectangle((0, y, line - 1, y + row_h - 1), fill=_rgba(line_color))
        d.text((line + pad, y + pad), txt, font=f, fill=_rgba(text_color))
    return _save(img, out)


def draw_list_card(
    out: Path,
    width: int,
    height: int,
    heading: str,
    items: list[str],
    font_path: str | None,
    text_color: str,
    accent_color: str,
    panel_color: str,
    highlight: int = -1,
) -> Path:
    """見出しと行の一覧のカード（幅は画面の 46%）。highlight の行だけ強調の色。"""
    rows = list(items)
    if len(rows) > LIST_MAX:
        rows = rows[: LIST_MAX - 1] + [f"ほか {len(items) - LIST_MAX + 1} 件"]
    hs = round(height * NAME_RATIO)
    isz = round(height * LIST_ITEM_RATIO)
    fh, fi = _font(font_path, hs), _font(font_path, isz)
    pad = round(hs * 0.8)
    w = round(width * 0.46)
    row_h = round(isz * 1.45)
    h = round(pad * 2 + hs * 1.5 + row_h * len(rows))
    img = Image.new("RGBA", (w, h), _rgba(panel_color, 0.92))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, w, max(3, round(height * 0.006))), fill=_rgba(accent_color))
    d.text((pad, pad), heading, font=fh, fill=_rgba(accent_color))
    y = round(pad + hs * 1.5)
    for i, r in enumerate(rows):
        col = accent_color if i == highlight else text_color
        d.text((pad, y), r, font=fi, fill=_rgba(col))
        y += row_h
    return _save(img, out)


def draw_rank_number(
    out: Path,
    canvas_h: int,
    number: int,
    font_path: str | None,
    text_color: str,
    accent_color: str,
    outline_color: str,
) -> Path:
    """「第 N 位」の大きな数字。数字は強調の色、「第」「位」は数字の 45% の大きさ。"""
    ns = round(canvas_h * RANK_RATIO)
    ss = round(ns * 0.45)
    fn, fs = _font(font_path, ns), _font(font_path, ss)
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    num = str(number)
    stroke = max(2, round(ns * 0.05))
    parts = [("第", fs, text_color), (num, fn, accent_color), ("位", fs, text_color)]
    widths = [probe.textlength(t, font=f) for t, f, _ in parts]
    pad = stroke * 2
    w = round(sum(widths) + pad * 2 + ss * 0.2)
    h = round(ns * 1.3 + pad * 2)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    x = pad
    base = pad + ns * 1.05  # 数字の下の線に「第」「位」の下を揃える
    for (t, f, col), tw in zip(parts, widths, strict=True):
        size = ns if f is fn else ss
        d.text(
            (x, base - size),
            t,
            font=f,
            fill=_rgba(col),
            stroke_width=stroke if f is fn else max(2, stroke // 2),
            stroke_fill=_rgba(outline_color),
        )
        x += tw + (ss * 0.1)
    return _save(img, out)
