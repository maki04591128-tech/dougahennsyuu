"""情報カードの配置計算と FFmpeg フィルタ（透過 PNG の静止画）。

見た目: 半透明のパネル＋左端の色帯。見出しは大きく、本文は少し小さく。
文字の幅は等幅に近い推定（全角=1、半角=0.56）で折り返す。
"""

from dataclasses import dataclass, field
from pathlib import Path

from video_automation.integrations.ffmpeg.runner import filter_path
from video_automation.schemas.animation import CardRequest
from video_automation.services.rendering.pictogram import ICONS
from video_automation.services.subtitle.text import wrap_lines


@dataclass
class TextLine:
    text: str
    size: int
    color: str
    x: int
    y: int
    item: int = -1  # 本文の何番目か（見出しは -1）


@dataclass
class CardLayout:
    width: int
    height: int
    bar: int
    lines: list[TextLine] = field(default_factory=list)
    divider: tuple[int, int, int, int] | None = None  # x, y, w, h
    bullets: list[tuple[int, int, int]] = field(default_factory=list)  # x, y, 一辺
    bullet_items: list[int] = field(default_factory=list)  # bullets と同じ順の本文番号
    icon: tuple[int, int, int] | None = None  # 絵記号の x, y, 一辺


def text_width(text: str, size: int) -> float:
    return sum(size * (0.6 if ord(ch) < 0x2E80 else 1.0) for ch in text)


def _wrap(text: str, size: int, width: int) -> list[str]:
    """推定幅に収まるように折り返す（収まるなら折り返さない）。"""
    w = text_width(text, size)
    if w <= width + 1:  # 幅は丸めた整数なので 1px の誤差は収まるとみなす（「解／説」と折れた）
        return [text]
    # 半角を含む文は 1 文字あたりの幅が狭いので、平均の幅から文字数の上限を決める
    per_char = w / max(1, len(text))
    max_chars = max(4, int(width // per_char))
    return wrap_lines(text, max_chars) or [""]


def card_layout(req: CardRequest, canvas_w: int, canvas_h: int) -> CardLayout:
    k = req.scale
    unit = min(canvas_w, canvas_h)
    pad = round(unit * 0.034 * k)
    bar = max(6, round(unit * 0.009 * k))
    accent, white = req.accent_color, req.text_color

    if req.style == "keyword":
        text = req.heading or req.body[0]
        size = round(unit * 0.078 * k)
        width = min(round(canvas_w * 0.92), round(text_width(text, size) + 2 * pad + bar))
        lines = _wrap(text, size, width - bar - 2 * pad)
        layout = CardLayout(width, 0, bar)
        y = pad
        for ln in lines:
            layout.lines.append(TextLine(ln, size, white, bar + pad, y))
            y += round(size * 1.25)
        layout.height = y + pad - round(size * 0.25)
        return layout

    width = round(canvas_w * req.width_ratio)
    inner = width - bar - 2 * pad
    hs = round(unit * 0.058 * k)
    bs = round(unit * 0.043 * k)
    layout = CardLayout(width, 0, bar)
    x = bar + pad
    y = pad
    if req.icon and req.icon in ICONS:
        # 絵記号は左の列に置き、文字はその右から（列の幅 = 見出し 1.5 行分）
        size = round(hs * 1.5)
        layout.icon = (x, y, size)
        x += size + round(pad * 0.7)
        inner -= size + round(pad * 0.7)
    if req.heading:
        color = accent if req.style == "summary" else white
        for ln in _wrap(req.heading, hs, inner):
            layout.lines.append(TextLine(ln, hs, color, x, y))
            y += round(hs * 1.3)
        if req.body:
            y += round(bs * 0.2)
            layout.divider = (x, y, inner, max(2, round(unit * 0.003)))
            y += round(bs * 0.6)
    for idx, item in enumerate(req.body):
        bullet = req.style in ("point", "summary")
        text = item
        indent = 0
        if bullet:
            sq = max(6, round(bs * 0.26))
            layout.bullets.append((x + round(bs * 0.1), y + round(bs * 0.5) - sq // 2, sq))
            layout.bullet_items.append(idx)
            indent = round(bs * 0.75)
        for i, ln in enumerate(_wrap(text, bs, inner - indent)):
            if i:
                y += round(bs * 1.38)
            layout.lines.append(TextLine(ln, bs, white, x + indent, y, idx))
        y += round(bs * 1.5)
    if req.body:
        y -= round(bs * 0.12)
    else:
        y -= round(hs * 0.3)
    if layout.icon is not None:  # 本文が短くても絵記号が切れない高さにする
        y = max(y, layout.icon[1] + layout.icon[2])
    layout.height = y + pad
    # 文が短いときはカードの幅を内容に合わせて詰める（最小は画面幅の 30%）
    used = max(ln.x + text_width(ln.text, ln.size) for ln in layout.lines) + pad
    fit = max(round(canvas_w * 0.3), round(used))
    if fit < layout.width:
        layout.width = fit
        if layout.divider:
            dx, dy, _, dh = layout.divider
            layout.divider = (dx, dy, fit - dx - pad, dh)
    return layout


def _ff_color(hex_color: str, alpha: float = 1.0) -> str:
    return f"0x{hex_color.lstrip('#').upper()}@{alpha:g}"


def card_filter(req: CardRequest, layout: CardLayout, font: Path, tmp: Path) -> str:
    """パネル・色帯・区切り線・文字の順に描く。文字はファイル経由（エスケープ不要）。

    req.reveal があれば、本文はその数だけ描く（大きさは全部を出したときと同じなので、
    順に出しても位置がずれない）。直前までの項目は少し暗くし、新しい項目を目立たせる。"""
    shown = len(req.body) if req.reveal is None else req.reveal

    def alpha(item: int) -> float:
        if req.reveal is None or item < 0 or item == shown - 1:
            return 1.0
        return 0.62

    parts = [
        f"drawbox=x=0:y=0:w={layout.width}:h={layout.height}:"
        f"color={_ff_color(req.panel_color, req.panel_opacity)}:t=fill:replace=1",
        f"drawbox=x=0:y=0:w={layout.bar}:h={layout.height}:"
        f"color={_ff_color(req.accent_color)}:t=fill:replace=1",
    ]
    if layout.divider:
        dx, dy, dw, dh = layout.divider
        parts.append(
            f"drawbox=x={dx}:y={dy}:w={dw}:h={dh}:color={_ff_color(req.accent_color, 0.8)}:t=fill"
        )
    for (bx, by, sz), item in zip(layout.bullets, layout.bullet_items, strict=True):
        if item >= shown:
            continue
        parts.append(
            f"drawbox=x={bx}:y={by}:w={sz}:h={sz}:"
            f"color={_ff_color(req.accent_color, alpha(item))}:t=fill"
        )
    fontfile = filter_path(font)
    for i, ln in enumerate(layout.lines):
        if ln.item >= shown:
            continue
        f = tmp / f"line{i}.txt"
        f.write_text(ln.text, encoding="utf-8")
        parts.append(
            # expansion=none: 「18%」の % を書式として解釈させない
            f"drawtext=fontfile='{fontfile}':textfile='{filter_path(f)}':expansion=none:"
            f"fontsize={ln.size}:fontcolor={_ff_color(ln.color, alpha(ln.item))}:x={ln.x}:y={ln.y}:"
            "shadowcolor=0x000000@0.6:shadowx=2:shadowy=2"
        )
    return ",".join(parts)
