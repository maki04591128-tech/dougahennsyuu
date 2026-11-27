"""Natural Earth（パブリックドメイン）の 1:110m の国の境界と主要都市（統合版 J1〜J3）。

リポジトリに入れず、初回に GitHub の nvkelso/natural-earth-vector から取得して data_dir に置く。
地名は LLM が出した `place` を、日本語名（`NAME_JA`）と突き合わせる（一致しなければ出さない）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

BASE = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/"
FILES = {
    "countries": "ne_110m_admin_0_countries.geojson",
    "places": "ne_110m_populated_places.geojson",
}
# 台本でよく使う略称 → Natural Earth の日本語名（7 節: 表記ゆれの別名の表）
ALIASES = {
    "アメリカ": "アメリカ合衆国",
    "米国": "アメリカ合衆国",
    "英国": "イギリス",
    "中国": "中華人民共和国",
    "台湾": "中華民国",
    "韓国": "大韓民国",
    "北朝鮮": "朝鮮民主主義人民共和国",
    "ソウル": "ソウル特別市",
}

Ring = list[tuple[float, float]]  # (経度, 緯度)


@dataclass
class Country:
    name: str
    code: str
    polygons: list[Ring] = field(default_factory=list)  # 外周だけ（穴は描かない）

    def bbox(self) -> tuple[float, float, float, float]:
        xs = [x for r in self.polygons for x, _ in r]
        ys = [y for r in self.polygons for _, y in r]
        return min(xs), min(ys), max(xs), max(ys)


@dataclass
class Place:
    name: str
    code: str  # 国の ADM0_A3
    lon: float
    lat: float


@dataclass
class World:
    countries: list[Country]
    places: list[Place]

    def country(self, name: str) -> Country | None:
        n = ALIASES.get(name.strip(), name.strip())
        return next((c for c in self.countries if c.name == n), None)

    def place(self, name: str) -> Place | None:
        n = ALIASES.get(name.strip(), name.strip())
        for cand in (n, f"{n}市", f"{n}特別市", f"{n}都"):
            p = next((x for x in self.places if x.name == cand), None)
            if p is not None:
                return p
        return None

    def country_of(self, p: Place) -> Country | None:
        return next((c for c in self.countries if c.code == p.code), None)


def _rings(geom: dict[str, Any]) -> list[Ring]:
    if geom.get("type") == "Polygon":
        polys = [geom["coordinates"]]
    elif geom.get("type") == "MultiPolygon":
        polys = geom["coordinates"]
    else:
        return []
    return [[(float(x), float(y)) for x, y, *_ in poly[0]] for poly in polys if poly]


def parse(countries: dict[str, Any], places: dict[str, Any]) -> World:
    cs = [
        Country(
            str(f["properties"].get("NAME_JA") or ""),
            str(f["properties"].get("ADM0_A3") or ""),
            _rings(f.get("geometry") or {}),
        )
        for f in countries.get("features", [])
    ]
    ps = []
    for f in places.get("features", []):
        pr = f.get("properties") or {}
        lon, lat = (f.get("geometry") or {}).get("coordinates", [None, None])[:2]
        if pr.get("NAME_JA") and lon is not None:
            ps.append(Place(str(pr["NAME_JA"]), str(pr.get("ADM0_A3") or ""), lon, lat))
    return World([c for c in cs if c.name and c.polygons], ps)


def ensure(data_dir: Path, fetch: Any = None) -> World | None:
    """data_dir/maps に無ければ取得して読む。取得できなければ None（地図は出さない）。"""
    d = data_dir / "maps"
    d.mkdir(parents=True, exist_ok=True)
    raw: dict[str, Any] = {}
    for key, fn in FILES.items():
        p = d / fn
        if not p.is_file():
            try:
                body = fetch(BASE + fn) if fetch else _download(BASE + fn)
            except Exception:  # noqa: BLE001 - 取れないときは地図を出さないだけ
                return None
            p.write_bytes(body)
        try:
            raw[key] = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            p.unlink(missing_ok=True)
            return None
    return parse(raw["countries"], raw["places"])


def _download(url: str) -> bytes:
    import httpx

    r = httpx.get(url, timeout=60.0, follow_redirects=True)
    r.raise_for_status()
    return r.content
