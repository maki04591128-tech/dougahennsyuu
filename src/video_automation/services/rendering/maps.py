"""地図（統合版 段階5d: J1 国の塗り・J2 地点へ寄る・J3 経路の線）。Pillow で描く。

- 陸は型の surface、海は型の base、境界は text_muted の 1px。経度・緯度をそのまま平面に置く
  （正距円筒。1:110m の地図なので細部は粗い）
- J1: その国を accent で塗り（10f）、国が画面の高さの 40% になるまで寄る（24f、easeInOutCubic）
- J2: 都市を含む国が画面の高さの 60% になる所から始め、都市にピンを落とし（8f、easeOutBack）、
  都市が中央に来るよう 1.00→1.40 に 30f（easeInOutCubic）で寄る
- J3: 2 地点が画面の幅の 70% に収まる範囲を映し、1 地点目から 2 地点目へ上に膨らむ弧
  （高さは 2 地点の距離の 20%・accent・3px）を 24f（easeInOutSine）で描く。先頭に直径 10px の点
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from video_automation.integrations.maps.natural_earth import Country, Place, World

J1_FILL, J1_ZOOM = 10, 24
J2_PIN, J2_ZOOM = 8, 30
J3_DRAW = 24


def _rgb(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def _mix(a: str, b: str, t: float) -> tuple[int, int, int]:
    ca, cb = _rgb(a), _rgb(b)
    return tuple(round(x + (y - x) * t) for x, y in zip(ca, cb, strict=True))  # type: ignore[return-value]


def _cubic_io(u: float) -> float:
    u = min(max(u, 0.0), 1.0)
    return 4 * u**3 if u < 0.5 else 1 - (-2 * u + 2) ** 3 / 2


def _sine_io(u: float) -> float:
    u = min(max(u, 0.0), 1.0)
    return -(math.cos(math.pi * u) - 1) / 2


def _back_out(u: float, s: float = 1.70158) -> float:
    u = min(max(u, 0.0), 1.0) - 1
    return u * u * ((s + 1) * u + s) + 1


@dataclass
class View:
    """画面の中心の経度・緯度と、画面の高さに入る緯度の幅（度）。"""

    lon: float
    lat: float
    span: float

    def lerp(self, o: View, t: float) -> View:
        # 大きさは対数で補間する（寄る速さが一定に見える）
        s = math.exp(math.log(self.span) + (math.log(o.span) - math.log(self.span)) * t)
        return View(self.lon + (o.lon - self.lon) * t, self.lat + (o.lat - self.lat) * t, s)


@dataclass
class Palette:
    sea: str
    land: str
    border: str
    accent: str


WORLD = View(0.0, 10.0, 200.0)


def fit_view(bbox: tuple[float, float, float, float], w: int, h: int, height: float,
             width: float = 0.9) -> View:  # fmt: skip
    """bbox（経度・緯度）が画面の高さの height（幅は width まで）になる眺め。"""
    x0, y0, x1, y1 = bbox
    bw, bh = max(x1 - x0, 0.5), max(y1 - y0, 0.5)
    span = max(bh / height, bw / width * h / w)
    return View((x0 + x1) / 2, (y0 + y1) / 2, span)


def main_bbox(c: Country) -> tuple[float, float, float, float]:
    """国の範囲（離れた島を除くため、面積が最大の輪の範囲。180 度をまたぐ国も崩れない）。"""

    def area(r: list[tuple[float, float]]) -> float:
        pairs = zip(r, r[1:] + r[:1], strict=True)
        return abs(sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in pairs)) / 2

    big = max(c.polygons, key=area)
    xs, ys = [x for x, _ in big], [y for _, y in big]
    return min(xs), min(ys), max(xs), max(ys)


def _project(v: View, w: int, h: int) -> Any:
    k = h / v.span

    def p(lon: float, lat: float) -> tuple[float, float]:
        return w / 2 + (lon - v.lon) * k, h / 2 - (lat - v.lat) * k

    return p


def draw(world: World, v: View, w: int, h: int, pal: Palette,
         fill: dict[str, tuple[int, int, int]] | None = None) -> Any:  # fmt: skip
    """眺め v の地図（RGB）。fill は国の符号 → 塗る色。"""
    from PIL import Image, ImageDraw

    im = Image.new("RGB", (w, h), _rgb(pal.sea))
    d = ImageDraw.Draw(im)
    p = _project(v, w, h)
    k = h / v.span
    land, border = _rgb(pal.land), _rgb(pal.border)
    lon0, lon1 = v.lon - w / 2 / k, v.lon + w / 2 / k
    lat0, lat1 = v.lat - h / 2 / k, v.lat + h / 2 / k
    for c in world.countries:
        col = (fill or {}).get(c.code, land)
        for ring in c.polygons:
            xs = [x for x, _ in ring]
            ys = [y for _, y in ring]
            if max(xs) < lon0 or min(xs) > lon1 or max(ys) < lat0 or min(ys) > lat1:
                continue
            pts = [p(x, y) for x, y in ring]
            if len(pts) >= 3:
                d.polygon(pts, fill=col, outline=border)
    return im


def _pin(d: Any, x: float, y: float, h: int, accent: str) -> None:
    """H2 と同じピン（高さは画面の高さの 6%）。先端が (x, y)。"""
    ph = h * 0.06
    r = ph * 0.38
    cx, cy = x, y - ph + r
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=_rgb(accent))
    d.polygon([(cx - r * 0.7, cy + r * 0.5), (cx + r * 0.7, cy + r * 0.5), (x, y)],
              fill=_rgb(accent))  # fmt: skip
    d.ellipse([cx - r * 0.4, cy - r * 0.4, cx + r * 0.4, cy + r * 0.4], fill=(255, 255, 255))


def country_frames(world: World, c: Country, w: int, h: int, pal: Palette
                   ) -> tuple[list[Any], Any]:  # fmt: skip
    """J1: 塗り（10f）→ 寄る（24f）。動く画の列と、最後の止め絵。"""
    target = fit_view(main_bbox(c), w, h, 0.40)
    out = []
    for i in range(J1_FILL + J1_ZOOM):
        t_fill = min(1.0, (i + 1) / J1_FILL)
        t_zoom = _cubic_io((i + 1 - J1_FILL) / J1_ZOOM) if i >= J1_FILL else 0.0
        col = _mix(pal.land, pal.accent, t_fill)
        out.append(draw(world, WORLD.lerp(target, t_zoom), w, h, pal, {c.code: col}))
    return out, out[-1]


def city_frames(world: World, place: Place, w: int, h: int, pal: Palette
                ) -> tuple[list[Any], Any]:  # fmt: skip
    """J2: 国が画面の高さの 60% の眺めにピンを落とし（8f）、都市へ 1.40 倍に寄る（30f）。"""
    from PIL import ImageDraw

    c = world.country_of(place)
    start = fit_view(main_bbox(c), w, h, 0.60) if c else View(place.lon, place.lat, 30.0)
    end = View(place.lon, place.lat, start.span / 1.40)
    out = []
    for i in range(J2_PIN + J2_ZOOM):
        t = _cubic_io((i + 1 - J2_PIN) / J2_ZOOM) if i >= J2_PIN else 0.0
        v = start.lerp(end, t)
        im = draw(world, v, w, h, pal)
        x, y = _project(v, w, h)(place.lon, place.lat)
        drop = (1 - _back_out((i + 1) / J2_PIN)) * 20 * h / 1080 if i < J2_PIN else 0.0
        _pin(ImageDraw.Draw(im), x, y - drop, h, pal.accent)
        out.append(im)
    return out, out[-1]


def route_frames(world: World, a: Place, b: Place, w: int, h: int, pal: Palette
                 ) -> tuple[list[Any], Any]:  # fmt: skip
    """J3: 2 地点が画面の幅の 70% に収まる眺めに、上に膨らむ弧を 24f で描く。"""
    from PIL import ImageDraw

    bbox = (min(a.lon, b.lon), min(a.lat, b.lat), max(a.lon, b.lon), max(a.lat, b.lat))
    v = fit_view(bbox, w, h, 0.55, 0.70)
    base = draw(world, v, w, h, pal)
    p = _project(v, w, h)
    (x0, y0), (x1, y1) = p(a.lon, a.lat), p(b.lon, b.lat)
    dist = math.hypot(x1 - x0, y1 - y0)
    # 2 次のベジェの頂点の高さ = 制御点の高さの半分（距離の 20% にする）
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2 - 0.4 * dist
    curve = [((1 - t) ** 2 * x0 + 2 * (1 - t) * t * mx + t * t * x1,
              (1 - t) ** 2 * y0 + 2 * (1 - t) * t * my + t * t * y1)
             for t in (k / 96 for k in range(97))]  # fmt: skip
    lw = max(1, round(3 * h / 1080))
    dot = 10 * h / 1080 / 2
    out = []
    for i in range(J3_DRAW):
        n = max(2, round(len(curve) * _sine_io((i + 1) / J3_DRAW)))
        im = base.copy()
        d = ImageDraw.Draw(im)
        d.line(curve[:n], fill=_rgb(pal.accent), width=lw, joint="curve")
        hx, hy = curve[n - 1]
        d.ellipse([hx - dot, hy - dot, hx + dot, hy + dot], fill=_rgb(pal.accent))
        for x, y in ((x0, y0), (x1, y1)):
            d.ellipse([x - dot * 0.8, y - dot * 0.8, x + dot * 0.8, y + dot * 0.8],
                      outline=_rgb(pal.accent), width=lw)  # fmt: skip
        out.append(im)
    return out, out[-1]


def places_in(world: World, text: str, first: str) -> list[str]:
    """J3: LLM の place と、文の中に出てくる別の地名（国・都市。出てくる順）。"""
    from video_automation.integrations.maps.natural_earth import ALIASES

    names = {c.name for c in world.countries} | {p.name for p in world.places} | set(ALIASES)
    # 「東京都」「北京市」は「東京」「北京」でも探す
    names |= {
        p.name[:-1] for p in world.places if p.name.endswith(("市", "都")) and len(p.name) > 2
    }
    found = sorted(
        ((text.find(n), n) for n in names if len(n) >= 2 and n != first and n in text),
        key=lambda x: x[0],
    )
    out = [first]
    for _pos, n in found:
        same = world.country(n) or world.place(n)
        first_obj = world.country(first) or world.place(first)
        if n in out or any(n in o or o in n for o in out) or (same and same is first_obj):
            continue
        out.append(n)
    return out


def frames_for(world: World, place: str, text: str, w: int, h: int, pal: Palette
               ) -> tuple[str, list[Any], Any] | None:  # fmt: skip
    """place（と文）から J1・J2・J3 のどれかの画の列。地名が地図に無ければ None。"""
    names = places_in(world, text, place)
    if len(names) >= 2:
        pts = [world.place(n) or _center(world, n) for n in names[:2]]
        if pts[0] is not None and pts[1] is not None:
            fr, still = route_frames(world, pts[0], pts[1], w, h, pal)
            return "経路の線（J3）", fr, still
    c = world.country(place)
    if c is not None:
        fr, still = country_frames(world, c, w, h, pal)
        return "国の塗り（J1）", fr, still
    pl = world.place(place)
    if pl is not None:
        fr, still = city_frames(world, pl, w, h, pal)
        return "地点へ寄る（J2）", fr, still
    return None


def _center(world: World, name: str) -> Place | None:
    c = world.country(name)
    if c is None:
        return None
    x0, y0, x1, y1 = main_bbox(c)
    return Place(c.name, c.code, (x0 + x1) / 2, (y0 + y1) / 2)
