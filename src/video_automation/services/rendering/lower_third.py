"""名前帯（統合版 P1）。透過 PNG。

1 行目は見出しの書体の太字で文字の大きさ = 画面の高さの 4.5%、2 行目は 3.5%（T40 の最小の大きさ）。
左端に話者の「枠・線」の色の線（幅 6px。1080p の値で、解像度に比例）。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

NAME_RATIO, TITLE_RATIO = 0.045, 0.035
LINE_PX = 6  # 1080p での左の線の幅
SHOW_SEC, LONG_SHOW_SEC = 5.0, 7.0  # 名前が 8 字以上か、肩書きに改行があれば 7 秒
DELAY_SEC = 0.5  # その話者の最初の発話の 0.5 秒後に出す
REPEAT_GAP_SEC = 300.0  # 前に出してから 5 分以上空いたら、もう一度出す


def show_sec(name: str, title: str) -> float:
    return LONG_SHOW_SEC if len(name) >= 8 or "\n" in title else SHOW_SEC


def _rgba(c: str, a: float = 1.0) -> tuple[int, int, int, int]:
    h = c.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), round(255 * a)


def draw_lower_third(
    out: Path,
    canvas_h: int,
    name: str,
    title: str,
    font_path: str | None,
    text_color: str,
    line_color: str,
    panel_color: str,
    panel_opacity: float = 0.85,
) -> Path:
    """名前帯の透過 PNG（大きさは文字に合わせる）。"""
    k = canvas_h / 1080
    ns, ts = round(canvas_h * NAME_RATIO), round(canvas_h * TITLE_RATIO)

    def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        if font_path and Path(font_path).is_file():
            return ImageFont.truetype(font_path, size)
        return ImageFont.load_default(size)

    fn, ft = font(ns), font(ts)
    tlines = [x for x in title.split("\n") if x.strip()] if title else []
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    widths = [probe.textlength(name, font=fn)] + [probe.textlength(x, font=ft) for x in tlines]
    line = max(2, round(LINE_PX * k))
    pad = round(ns * 0.45)
    w = round(line + pad * 2 + max(widths))
    h = round(pad * 2 + ns * 1.2 + len(tlines) * ts * 1.35)
    img = Image.new("RGBA", (w, h), _rgba(panel_color, panel_opacity))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, line - 1, h), fill=_rgba(line_color))
    x, y = line + pad, pad
    d.text((x, y), name, font=fn, fill=_rgba(text_color), stroke_width=0)
    y += round(ns * 1.2)
    for ln in tlines:
        d.text((x, y), ln, font=ft, fill=_rgba(text_color, 0.9))
        y += round(ts * 1.35)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out


def schedule(lines: list[tuple[str, int, int]], fps: float) -> list[tuple[str, int]]:
    """各話者の名前帯を出すフレーム [(話者, 開始フレーム)]。lines は (話者, 開始, 終わり)。

    その話者の最初の発話の 0.5 秒後。前に出してから 5 分以上空いた後の最初の発話でも、
    もう一度出す。"""
    last: dict[str, int] = {}
    out: list[tuple[str, int]] = []
    for spk, start, _end in lines:
        if not spk:
            continue
        prev = last.get(spk)
        if prev is None or start - prev >= REPEAT_GAP_SEC * fps:
            at = start + round(DELAY_SEC * fps)
            out.append((spk, at))
            last[spk] = at
    return out
