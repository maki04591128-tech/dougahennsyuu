"""要点カードに添えるピクトグラム（線の絵記号）。素材をダウンロードせず、図形から描く。

- 形は単純な線と面だけ（太さは大きさの 1/12・角は丸く）。色は型の強調色 1 色
- どの絵を使うかは、カードの見出し・本文の語から決める（pick_icon）。合う語が無ければ付けない
"""

from __future__ import annotations

import math
from collections.abc import Callable

from PIL import Image, ImageDraw

# 語 → 絵（上から順に照合。見出しを本文より優先）
KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("battery", ("バッテリー", "電池", "充電", "電力", "省電力")),
    ("wifi", ("Wi-Fi", "Wi‑Fi", "WiFi", "通信", "電波", "Bluetooth", "ネット", "回線")),
    ("phone", ("スマホ", "スマートフォン", "携帯", "アプリ", "端末")),
    ("warning", ("注意", "危険", "NG", "やめ", "禁止", "失敗", "落とし穴", "リスク")),
    ("clock", ("時間", "時計", "待ち", "期限", "毎日", "早く", "スケジュール")),
    ("yen", ("お金", "円", "節約", "料金", "費用", "価格", "コスト", "安く")),
    ("gear", ("設定", "調整", "仕組み", "機能", "カスタム")),
    ("lock", ("安全", "セキュリティ", "パスワード", "保護", "個人情報")),
    ("chart", ("増え", "伸び", "上昇", "成長", "効果", "改善", "比較", "データ")),
    ("search", ("検索", "調べ", "探す", "確認", "チェック")),
    ("bulb", ("コツ", "ポイント", "アイデア", "ヒント", "工夫", "理由", "なぜ")),
    ("check", ("まとめ", "完了", "OK", "準備", "手順")),
    ("star", ("おすすめ", "人気", "最強", "一番", "ベスト")),
    ("person", ("ユーザー", "利用者", "自分", "家族", "人")),
    ("heart", ("健康", "好き", "長持ち", "寿命", "大切")),
]


def pick_icon(heading: str, body: list[str]) -> str | None:
    """見出し（なければ本文）の語に合う絵の名前。合わなければ None。"""
    for text in (heading or "", " ".join(body or [])):
        for name, words in KEYWORDS:
            if any(w in text for w in words):
                return name
    return None


def _hex(c: str, alpha: int = 255) -> tuple[int, int, int, int]:
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), alpha


Draw = Callable[[ImageDraw.ImageDraw, float, int, tuple[int, int, int, int]], None]


def _battery(d: ImageDraw.ImageDraw, s: float, w: int, col: tuple[int, ...]) -> None:
    d.rounded_rectangle((0.14 * s, 0.30 * s, 0.80 * s, 0.70 * s), 0.07 * s, outline=col, width=w)
    d.rounded_rectangle((0.80 * s, 0.42 * s, 0.88 * s, 0.58 * s), 0.02 * s, fill=col)
    d.rectangle((0.22 * s, 0.38 * s, 0.58 * s, 0.62 * s), fill=col)


def _wifi(d: ImageDraw.ImageDraw, s: float, w: int, col: tuple[int, ...]) -> None:
    cx, cy = 0.5 * s, 0.78 * s
    for r in (0.18, 0.36, 0.54):
        d.arc((cx - r * s, cy - r * s, cx + r * s, cy + r * s), 225, 315, fill=col, width=w)
    d.ellipse((cx - 0.06 * s, cy - 0.06 * s, cx + 0.06 * s, cy + 0.06 * s), fill=col)


def _phone(d: ImageDraw.ImageDraw, s: float, w: int, col: tuple[int, ...]) -> None:
    d.rounded_rectangle((0.30 * s, 0.10 * s, 0.70 * s, 0.90 * s), 0.08 * s, outline=col, width=w)
    d.line((0.44 * s, 0.80 * s, 0.56 * s, 0.80 * s), fill=col, width=w)


def _warning(d: ImageDraw.ImageDraw, s: float, w: int, col: tuple[int, ...]) -> None:
    d.polygon(
        [(0.5 * s, 0.12 * s), (0.90 * s, 0.84 * s), (0.10 * s, 0.84 * s)], outline=col, width=w
    )
    d.line((0.5 * s, 0.38 * s, 0.5 * s, 0.62 * s), fill=col, width=w)
    d.ellipse((0.46 * s, 0.70 * s, 0.54 * s, 0.78 * s), fill=col)


def _clock(d: ImageDraw.ImageDraw, s: float, w: int, col: tuple[int, ...]) -> None:
    d.ellipse((0.14 * s, 0.14 * s, 0.86 * s, 0.86 * s), outline=col, width=w)
    d.line((0.5 * s, 0.5 * s, 0.5 * s, 0.28 * s), fill=col, width=w)
    d.line((0.5 * s, 0.5 * s, 0.66 * s, 0.6 * s), fill=col, width=w)


def _yen(d: ImageDraw.ImageDraw, s: float, w: int, col: tuple[int, ...]) -> None:
    d.ellipse((0.14 * s, 0.14 * s, 0.86 * s, 0.86 * s), outline=col, width=w)
    d.line((0.36 * s, 0.30 * s, 0.5 * s, 0.50 * s, 0.64 * s, 0.30 * s), fill=col, width=w)
    d.line((0.5 * s, 0.50 * s, 0.5 * s, 0.72 * s), fill=col, width=w)
    for y in (0.52, 0.62):
        d.line((0.38 * s, y * s, 0.62 * s, y * s), fill=col, width=max(1, w * 3 // 4))


def _gear(d: ImageDraw.ImageDraw, s: float, w: int, col: tuple[int, ...]) -> None:
    cx = cy = 0.5 * s
    for k in range(8):
        a = math.pi * 2 * k / 8
        x0, y0 = cx + math.cos(a) * 0.26 * s, cy + math.sin(a) * 0.26 * s
        x1, y1 = cx + math.cos(a) * 0.40 * s, cy + math.sin(a) * 0.40 * s
        d.line((x0, y0, x1, y1), fill=col, width=int(w * 1.6))
    d.ellipse((0.24 * s, 0.24 * s, 0.76 * s, 0.76 * s), outline=col, width=w)
    d.ellipse((0.40 * s, 0.40 * s, 0.60 * s, 0.60 * s), outline=col, width=w)


def _lock(d: ImageDraw.ImageDraw, s: float, w: int, col: tuple[int, ...]) -> None:
    d.arc((0.30 * s, 0.14 * s, 0.70 * s, 0.56 * s), 180, 360, fill=col, width=w)
    d.line((0.30 * s, 0.35 * s, 0.30 * s, 0.46 * s), fill=col, width=w)
    d.line((0.70 * s, 0.35 * s, 0.70 * s, 0.46 * s), fill=col, width=w)
    d.rounded_rectangle((0.20 * s, 0.44 * s, 0.80 * s, 0.86 * s), 0.06 * s, fill=col)


def _chart(d: ImageDraw.ImageDraw, s: float, w: int, col: tuple[int, ...]) -> None:
    for i, h in enumerate((0.25, 0.42, 0.60)):
        x = (0.18 + i * 0.24) * s
        d.rectangle((x, (0.86 - h) * s, x + 0.16 * s, 0.86 * s), fill=col)
    d.line((0.14 * s, 0.50 * s, 0.46 * s, 0.30 * s, 0.84 * s, 0.14 * s), fill=col, width=w)


def _search(d: ImageDraw.ImageDraw, s: float, w: int, col: tuple[int, ...]) -> None:
    d.ellipse((0.14 * s, 0.14 * s, 0.62 * s, 0.62 * s), outline=col, width=w)
    d.line((0.56 * s, 0.56 * s, 0.86 * s, 0.86 * s), fill=col, width=int(w * 1.5))


def _bulb(d: ImageDraw.ImageDraw, s: float, w: int, col: tuple[int, ...]) -> None:
    d.ellipse((0.24 * s, 0.10 * s, 0.76 * s, 0.62 * s), outline=col, width=w)
    d.rectangle((0.38 * s, 0.62 * s, 0.62 * s, 0.74 * s), outline=col, width=w)
    d.line((0.40 * s, 0.84 * s, 0.60 * s, 0.84 * s), fill=col, width=w)


def _check(d: ImageDraw.ImageDraw, s: float, w: int, col: tuple[int, ...]) -> None:
    d.ellipse((0.12 * s, 0.12 * s, 0.88 * s, 0.88 * s), outline=col, width=w)
    d.line(
        (0.30 * s, 0.52 * s, 0.45 * s, 0.66 * s, 0.70 * s, 0.38 * s), fill=col, width=int(w * 1.3)
    )


def _person(d: ImageDraw.ImageDraw, s: float, w: int, col: tuple[int, ...]) -> None:
    d.ellipse((0.36 * s, 0.12 * s, 0.64 * s, 0.40 * s), fill=col)
    d.pieslice((0.20 * s, 0.46 * s, 0.80 * s, 1.06 * s), 180, 360, fill=col)


def _heart(d: ImageDraw.ImageDraw, s: float, w: int, col: tuple[int, ...]) -> None:
    d.ellipse((0.14 * s, 0.20 * s, 0.52 * s, 0.56 * s), fill=col)
    d.ellipse((0.48 * s, 0.20 * s, 0.86 * s, 0.56 * s), fill=col)
    d.polygon([(0.15 * s, 0.44 * s), (0.85 * s, 0.44 * s), (0.5 * s, 0.86 * s)], fill=col)


def _star(d: ImageDraw.ImageDraw, s: float, w: int, col: tuple[int, ...]) -> None:
    pts = []
    for k in range(10):
        r = 0.40 if k % 2 == 0 else 0.17
        a = -math.pi / 2 + math.pi * k / 5
        pts.append((0.5 * s + math.cos(a) * r * s, 0.54 * s + math.sin(a) * r * s))
    d.polygon(pts, fill=col)


ICONS: dict[str, Draw] = {
    "battery": _battery,
    "wifi": _wifi,
    "phone": _phone,
    "warning": _warning,
    "clock": _clock,
    "yen": _yen,
    "gear": _gear,
    "lock": _lock,
    "chart": _chart,
    "search": _search,
    "bulb": _bulb,
    "check": _check,
    "person": _person,
    "heart": _heart,
    "star": _star,
}


def render_icon(name: str, size: int, color: str) -> Image.Image:
    """透過の絵記号（size×size）。4 倍で描いて縮め、線の縁を滑らかにする。"""
    k = 4
    big = Image.new("RGBA", (size * k, size * k), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    ICONS[name](d, float(size * k), max(2, round(size * k / 12)), _hex(color))
    return big.resize((size, size), Image.Resampling.LANCZOS)
