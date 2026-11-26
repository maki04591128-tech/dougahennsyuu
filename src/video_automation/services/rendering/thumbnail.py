"""サムネイル（統合版 S4）。

- 3840×2160（縦型は 2160×3840）、最大 50MB（JPEG で書く）
- 文字は最小 4 字・最大 9 字・最大 2 行。右下の幅 20%×高さ 15% に文字を置かない（再生時間の表示）
- 3 案作る。10% に縮小した画像で文字の高さが最小 8px（= 元の大きさで 80px）
- 太さは最小 Medium（統合版 Q6）。縁は二重（内側は暗い縁、外側は明るい縁。C1 と同じ考え方）
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

MIN_CHARS, MAX_CHARS, MAX_LINES = 4, 9, 2
NO_TEXT_W, NO_TEXT_H = 0.20, 0.15  # 右下の文字を置かない所
MIN_PX_AT_10 = 8  # 10% に縮小したときの文字の高さの最小（px）
MAX_BYTES = 50 * 1024 * 1024
_CUT = re.compile(r"[のをはがとでにへもや、。！？!?・\s]")


def size_for(vertical: bool) -> tuple[int, int]:
    return (2160, 3840) if vertical else (3840, 2160)


def shorten(text: str) -> str:
    """9 字以内に縮める（語の途中で切らないよう、助詞・句読点の所で切る）。"""
    t = re.sub(r"\s+", "", text).strip("「」『』。、")
    if len(t) <= MAX_CHARS:
        return t
    cuts = [m.start() for m in _CUT.finditer(t) if MIN_CHARS <= m.start() <= MAX_CHARS]
    if cuts:
        return t[: cuts[-1]]
    return ""


def thumb_texts(title: str, catch: str = "", headings: list[str] | None = None) -> list[str]:
    """3 案の文字（4〜9 字）。題名 → 一言 → 章の見出しの順。足りなければ同じ文字を使い回す。"""
    out: list[str] = []
    for src in [title, catch, *(headings or [])]:
        t = shorten(src or "")
        if MIN_CHARS <= len(t) <= MAX_CHARS and t not in out:
            out.append(t)
        if len(out) == 3:
            break
    if not out:
        t = re.sub(r"\s+", "", title or "動画")[:MAX_CHARS]
        out = [t if len(t) >= MIN_CHARS else (t + "とは？")[:MAX_CHARS]]
    while len(out) < 3:
        out.append(out[len(out) % max(1, len(out))])
    return out[:3]


def split_lines(text: str) -> list[str]:
    """2 行まで。6 字以上は、真ん中に近い助詞の後で分ける（無ければ真ん中）。"""
    if len(text) <= 5:
        return [text]
    mid = len(text) / 2
    cand = [m.end() for m in _CUT.finditer(text) if 2 <= m.end() <= len(text) - 2]
    at = min(cand, key=lambda i: abs(i - mid)) if cand else round(mid)
    return [text[:at], text[at:]][:MAX_LINES]


@dataclass(frozen=True)
class Layout:
    name: str
    anchor: str  # left / right / top
    text_w: float  # 文字の塊の最大の幅（画面の幅に対する比）


LAYOUTS = (
    Layout("左に大きく", "left", 0.55),
    Layout("右に大きく", "right", 0.55),
    Layout("上に横いっぱい", "top", 0.86),
)


def _rgb(c: str) -> tuple[int, int, int]:
    h = c.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


@dataclass
class ThumbResult:
    path: Path
    text: str
    layout: str
    char_px: int  # 文字の高さ（px）
    box: tuple[int, int, int, int]  # 文字の塊の外接矩形


def draw(
    background: bytes | Image.Image,
    out: Path,
    text: str,
    layout: Layout,
    font_path: str,
    text_color: str,
    accent: str,
    outline: str,
    vertical: bool = False,
) -> ThumbResult:
    """1 案を描いて JPEG で書く。"""
    w, h = size_for(vertical)
    bg = background if isinstance(background, Image.Image) else Image.open(io.BytesIO(background))
    bg = bg.convert("RGB")
    # 画面いっぱいに切り抜いて拡大する
    k = max(w / bg.width, h / bg.height)
    bg = bg.resize((round(bg.width * k), round(bg.height * k)), Image.Resampling.LANCZOS)
    left, top = (bg.width - w) // 2, (bg.height - h) // 2
    img = bg.crop((left, top, left + w, top + h))
    lines = split_lines(text)
    # 文字の大きさ: 塊の幅が layout.text_w に収まる最大（高さは画面の 40% まで）
    max_w = w * layout.text_w
    size = round(h * (0.22 if len(lines) == 1 else 0.19))
    while size > 80:
        font = ImageFont.truetype(font_path, size)
        widest = max(font.getlength(x) for x in lines)
        if widest <= max_w and size * 1.15 * len(lines) <= h * 0.42:
            break
        size -= 8
    font = ImageFont.truetype(font_path, size)
    widths = [font.getlength(x) for x in lines]
    block_w, line_h = max(widths), round(size * 1.15)
    block_h = line_h * len(lines)
    pad = round(min(w, h) * 0.06)
    if layout.anchor == "left":
        x0, y0 = pad, round((h - block_h) * 0.42)
    elif layout.anchor == "right":
        x0, y0 = round(w - pad - block_w), round(h * 0.08)
    else:
        x0, y0 = round((w - block_w) / 2), round(h * 0.07)
    # 文字の側を暗くぼかして読みやすくする
    shade = Image.new("L", (w, h), 0)
    sd = ImageDraw.Draw(shade)
    m = round(pad * 0.8)
    sd.rounded_rectangle(
        (max(0, x0 - m), max(0, y0 - m), min(w, x0 + block_w + m), min(h, y0 + block_h + m)),
        radius=m,
        fill=255,
    )
    shade = shade.filter(ImageFilter.GaussianBlur(pad))
    dark = img.filter(ImageFilter.GaussianBlur(round(min(w, h) * 0.006))).point(
        lambda v: round(v * 0.45)
    )
    img = Image.composite(dark, img, shade)
    d = ImageDraw.Draw(img)
    inner = max(6, round(size * 0.06))
    outer = inner + max(6, round(size * 0.05))
    for i, (ln, lw) in enumerate(zip(lines, widths, strict=True)):
        x = x0 + (block_w - lw) / 2 if layout.anchor == "top" else x0
        y = y0 + i * line_h
        fill = _rgb(accent) if i == len(lines) - 1 and len(lines) > 1 else _rgb(text_color)
        d.text(
            (x, y),
            ln,
            font=font,
            fill=_rgb(text_color),
            stroke_width=outer,
            stroke_fill=(255, 255, 255),
        )
        d.text((x, y), ln, font=font, fill=fill, stroke_width=inner, stroke_fill=_rgb(outline))
    box = (round(x0 - outer), round(y0), round(x0 + block_w + outer), round(y0 + block_h))
    out.parent.mkdir(parents=True, exist_ok=True)
    q = 92
    while True:
        img.save(out, "JPEG", quality=q, optimize=True)
        if out.stat().st_size <= MAX_BYTES or q <= 60:
            break
        q -= 8
    bbox = font.getbbox(lines[0])
    return ThumbResult(out, text, layout.name, round(bbox[3] - bbox[1]), box)


def checks(r: ThumbResult, vertical: bool = False) -> list[str]:
    """統合版 S4 の決まりに合わない所（空なら合格）。"""
    w, h = size_for(vertical)
    out = []
    n = len(re.sub(r"\s", "", r.text))
    if not MIN_CHARS <= n <= MAX_CHARS:
        out.append(f"文字が {n} 字です（{MIN_CHARS}〜{MAX_CHARS} 字）")
    if len(split_lines(r.text)) > MAX_LINES:
        out.append("文字が 3 行以上です")
    x0, y0, x1, y1 = r.box
    if x1 > w * (1 - NO_TEXT_W) and y1 > h * (1 - NO_TEXT_H):
        out.append("右下（再生時間の表示）に文字がかかっています")
    if r.char_px * 0.1 < MIN_PX_AT_10:
        out.append(f"10% に縮めると文字の高さが {r.char_px * 0.1:.1f}px です（8px 以上）")
    if r.path.stat().st_size > MAX_BYTES:
        out.append("50MB を超えています")
    return out
