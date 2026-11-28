"""集めた自由な書体（OFL）の一覧・ファイルの探し方・演出との組み合わせ。"""

from pathlib import Path

from video_automation.schemas.design import Purpose
from video_automation.services.design.looks import apply_look
from video_automation.services.design.profiles import PROFILES
from video_automation.services.rendering import lyric_screen as ls
from video_automation.services.rendering.font_catalog import (
    FONTS,
    LOOK_FONTS,
    find_font,
    missing,
)
from video_automation.services.rendering.lyrics import DIRECTIONS


def _fake_dir(tmp_path: Path) -> Path:
    d = tmp_path / "fonts"
    d.mkdir()
    for f in FONTS:
        (d / f.file).write_bytes(b"\x00\x01\x00\x00")
    return d


def test_every_font_named_by_effects_is_collected(tmp_path):
    d = [_fake_dir(tmp_path)]
    assert not missing(d)
    assert all(f.license == "OFL-1.1" and f.path.endswith((".ttf", ".otf")) for f in FONTS)
    # U7・U21 の字形、曲の方向性 8 種、見た目の型の題名がすべて手元の書体に当たる
    names = [*ls.FONT_OF.values(), *LOOK_FONTS.values()]
    names += [x for sw in ls.DIRECTION_FONT_SWAP.values() for x in sw.values()]
    names += [f"{v[2]} {v[3]}" for v in DIRECTIONS.values()]
    for n in names:
        assert find_font(n, d), n


def test_find_font_prefers_exact_weight_then_bold(tmp_path):
    d = tmp_path / "f"
    d.mkdir()
    for n in ("ShipporiMincho-Regular.ttf", "ShipporiMincho-ExtraBold.ttf", "ZenOldMincho-Bold.ttf",
              "ZenOldMincho-SemiBold.ttf"):  # fmt: skip
        (d / n).write_bytes(b"x")
    assert find_font("Shippori Mincho ExtraBold", [d]).endswith("ShipporiMincho-ExtraBold.ttf")
    assert find_font("Shippori Mincho Regular", [d]).endswith("ShipporiMincho-Regular.ttf")
    assert find_font("Zen Old Mincho", [d]).endswith("ZenOldMincho-Bold.ttf")
    assert find_font("Dela Gothic One", [d]) is None


def test_look_gives_heading_font_and_direction_swaps_font():
    p = apply_look(PROFILES[Purpose.LECTURE], "Y1")
    assert p.typography.heading_font == "Klee One SemiBold"
    p = apply_look(PROFILES[Purpose.STORY], "Y10")
    assert p.typography.heading_font == "Zen Old Mincho"  # 統合版の指定はそのまま
    seen: list[str] = []
    f = ls.fonts_for("idol", lambda n: seen.append(n) or None)
    f("RocknRoll One")
    f("Zen Maru Gothic Medium")
    assert seen == ["Mochiy Pop One", "Zen Maru Gothic Medium"]
