"""終了画面（統合版 S9）。動画の最後の 15 秒に置く透過 PNG。

枠は 1920×1080 での値（解像度に比例させる）。YouTube の終了画面の要素（動画 2 つ・登録の丸）は
YouTube 側で重ねるので、ここでは置く場所の目安の枠だけを薄く描き、まとめの文字を描く。
- 動画: (1180,180)–(1820,540) と (1180,580)–(1820,940)
- 登録の丸: 中心 (300,800)・半径 120
- まとめの文字: (120,160)–(1060,560)
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

END_SCREEN_SEC = 15.0
VIDEO_BOXES = ((1180, 180, 1820, 540), (1180, 580, 1820, 940))
SUBSCRIBE = (300, 800, 120)
SUMMARY = (120, 160, 1060, 560)
GUIDE_ALPHA = 0.35  # 目安の枠の不透明度


def _rgba(hex_color: str, a: float = 1.0) -> tuple[int, int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), round(255 * a)


def _font(path: str | None, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if path and Path(path).is_file():
        return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> list[str]:  # noqa: ANN001
    lines, cur = [], ""
    for ch in text:
        if draw.textlength(cur + ch, font=font) > width and cur:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    return [*lines, cur] if cur else lines


def draw_end_screen(
    out: Path,
    width: int,
    height: int,
    title: str,
    catch: str,
    font_path: str | None,
    text_color: str,
    line_color: str,
) -> Path:
    """終了画面の透過 PNG を書く。"""
    k = width / 1920
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    guide = _rgba(line_color, GUIDE_ALPHA)
    lw = max(2, round(4 * k))
    for x0, y0, x1, y1 in VIDEO_BOXES:
        d.rounded_rectangle(
            (round(x0 * k), round(y0 * k), round(x1 * k), round(y1 * k)),
            radius=round(16 * k),
            outline=guide,
            width=lw,
        )
    cx, cy, r = (round(v * k) for v in SUBSCRIBE)
    d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=guide, width=lw)
    # まとめの文字: 題名（大）と一言（小）を、枠の中に収まる大きさで
    x0, y0, x1, y1 = (round(v * k) for v in SUMMARY)
    box_w, box_h = x1 - x0, y1 - y0
    size = round(84 * k)
    while size > round(36 * k):
        f_big, f_small = _font(font_path, size), _font(font_path, round(size * 0.55))
        big = _wrap(d, title, f_big, box_w) if title else []
        small = _wrap(d, catch, f_small, box_w) if catch else []
        need = (
            len(big) * size * 1.25 + len(small) * size * 0.55 * 1.4 + (size * 0.4 if small else 0)
        )
        if need <= box_h and len(big) <= 3:
            break
        size -= round(4 * k) or 1
    y = y0
    ink = _rgba(text_color)
    shadow = (0, 0, 0, 150)
    for ln in big:
        d.text((x0 + 3, y + 3), ln, font=f_big, fill=shadow)
        d.text((x0, y), ln, font=f_big, fill=ink)
        y += round(size * 1.25)
    if small:
        y += round(size * 0.4)
        bar = max(4, round(8 * k))
        d.rectangle(
            (x0, y, x0 + bar, y + round(size * 0.55 * 1.4 * len(small))), fill=_rgba(line_color)
        )
        for ln in small:
            d.text((x0 + bar * 3, y), ln, font=f_small, fill=ink)
            y += round(size * 0.55 * 1.4)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out
