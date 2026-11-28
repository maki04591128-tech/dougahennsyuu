"""自由に使える書体（SIL Open Font License 1.1）を google/fonts から data/fonts に取ってくる。

使い方: python tools/fetch_fonts.py [書体のフォルダ（既定 data/fonts）]

- 一覧は src/video_automation/services/rendering/font_catalog.py の FONTS。演出との組み合わせも
  そこに書いてある
- 書体ごとにライセンスの文を OFL_<書体>.txt として同じフォルダに置き、一覧を FONTS.md に書く
- 既にあるファイルは取り直さない。書体はリポジトリに入れない
"""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from video_automation.services.rendering.font_catalog import (  # noqa: E402
    FONTS,
    GOOGLE_FONTS_RAW,
    LOOK_FONTS,
)


def _get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as r:  # noqa: S310 - 固定の https の URL
        return r.read()


def fetch(dest: Path) -> list[str]:
    dest.mkdir(parents=True, exist_ok=True)
    log: list[str] = []
    for f in FONTS:
        out = dest / f.file
        if out.is_file() and out.stat().st_size > 0:
            log.append(f"ok    {f.file}（あり）")
        else:
            data = _get(f"{GOOGLE_FONTS_RAW}/{f.path}")
            if data[:4] not in (b"\x00\x01\x00\x00", b"OTTO", b"true", b"ttcf"):
                raise RuntimeError(f"書体のファイルではありません: {f.path}")
            out.write_bytes(data)
            log.append(f"got   {f.file}（{len(data) // 1024} KB）")
        lic = dest / f"OFL_{f.file.split('-')[0]}.txt"
        if not lic.is_file():
            lic.write_bytes(_get(f"{GOOGLE_FONTS_RAW}/{f.folder}/OFL.txt"))
    lines = [
        "# 集めた書体（SIL Open Font License 1.1）",
        "",
        "tools/fetch_fonts.py が google/fonts から取得。動画・画像に使うのに条件は無い"
        "（書体のファイルを単体で売ることだけが禁止）。ライセンスの文は OFL_*.txt。",
        "",
        "| 書体 | ファイル | 演出との組み合わせ |",
        "|---|---|---|",
        *[f"| {f.family} | {f.file} | {f.use} |" for f in FONTS],
        "",
        "見た目の型の題名: " + "、".join(f"{k} → {v}" for k, v in LOOK_FONTS.items()),
        "",
    ]
    (dest / "FONTS.md").write_text("\n".join(lines), encoding="utf-8")
    return log


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else root / "data" / "fonts"
    for line in fetch(target):
        print(line)
    print(f"done: {len(FONTS)} fonts in {target}")
