"""統合版 Q4: シリーズの設定の保存と、用途の型への当てはめ・外れの検査。

設定は PC の data/series/<id>.json に置く（Git には入れない）。
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from video_automation.schemas.design import (
    DesignProfile,
    SeriesSettings,
    Violation,
)

OP_SEC, ED_SEC = 3.0, 10.0
COLOR_NEAR = 40.0  # RGB の距離がこれ以下なら同じ色とみなす


class SeriesNotFoundError(LookupError):
    pass


class SeriesStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, sid: str) -> Path:
        if not sid or any(c not in "0123456789abcdef-" for c in sid):
            raise SeriesNotFoundError(sid)
        return self.root / f"{sid}.json"

    def list(self) -> list[SeriesSettings]:
        if not self.root.is_dir():
            return []
        out = []
        for f in sorted(self.root.glob("*.json")):
            try:
                out.append(SeriesSettings.model_validate_json(f.read_text(encoding="utf-8")))
            except ValueError:
                continue  # 壊れたファイルは一覧に出さない
        return sorted(out, key=lambda x: x.name)

    def get(self, sid: str) -> SeriesSettings:
        f = self._path(sid)
        if not f.is_file():
            raise SeriesNotFoundError(sid)
        return SeriesSettings.model_validate_json(f.read_text(encoding="utf-8"))

    def save(self, s: SeriesSettings) -> SeriesSettings:
        s = s.model_copy(update={"id": s.id or str(uuid4())})
        f = self._path(s.id)
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(s.model_dump(), ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(f)
        return s

    def delete(self, sid: str) -> None:
        f = self._path(sid)
        if not f.is_file():
            raise SeriesNotFoundError(sid)
        f.unlink()


def apply_series(p: DesignProfile, s: SeriesSettings) -> DesignProfile:
    """シリーズの色・書体で用途の型を上書きする（安全・点滅などの基準は型のまま）。"""
    pal = p.palette.model_copy(update=dict(s.colors))
    upd: dict[str, str] = {}
    if s.heading_font:
        upd["heading_font"] = s.heading_font
    if s.body_font:
        upd["body_font"] = s.body_font
    typo = p.typography.model_copy(update=upd)
    return p.model_copy(update={"palette": pal, "typography": typo})


def _rgb(c: str) -> tuple[int, int, int]:
    h = c.lstrip("#")[-6:]
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _near(a: str, b: str) -> bool:
    try:
        ra, rb = _rgb(a), _rgb(b)
    except ValueError:
        return False
    return sum((x - y) ** 2 for x, y in zip(ra, rb, strict=True)) ** 0.5 <= COLOR_NEAR


def series_violations(
    p: DesignProfile, s: SeriesSettings, colors: list[str], fonts: set[str]
) -> list[Violation]:
    """シリーズの色・書体から外れた要素の warning（p はシリーズを当てた型）。"""
    out: list[Violation] = []
    allowed = list(s.colors.values()) + [
        *(getattr(p.palette, k) for k in ("base", "base2", "surface", "main", "accent", "text")),
        p.palette.text_muted,
        *p.palette.speakers,
        *p.palette.chart,
        "#000000",
        "#FFFFFF",
    ]
    off = sorted({c.upper() for c in colors if not any(_near(c, a) for a in allowed)})
    if s.colors and off:
        out.append(
            Violation(
                code="series_color",
                severity="warning",
                message=f"シリーズ「{s.name}」の色から外れた色があります: {', '.join(off[:5])}",
            )
        )
    want = {x for x in (s.heading_font, s.body_font) if x}
    stray = sorted(f for f in fonts if f and want and not any(w in f or f in w for w in want))
    if stray:
        out.append(
            Violation(
                code="series_font",
                severity="warning",
                message=f"シリーズ「{s.name}」の書体ではない書体があります: {', '.join(stray[:3])}",
            )
        )
    return out
