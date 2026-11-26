"""型の配色のコントラスト（統合版 G1）。"""

from video_automation.schemas.design import Purpose
from video_automation.services.design.guard import (
    DesignManifest,
    check,
    contrast_ratio,
    min_contrast,
    palette_contrast,
)
from video_automation.services.design.profiles import PROFILES


def test_contrast_ratio_known_values():
    assert round(contrast_ratio("#000000", "#FFFFFF"), 2) == 21.0
    assert round(contrast_ratio("#777777", "#FFFFFF"), 2) == 4.48
    assert min_contrast(0.054) == 4.5 and min_contrast(0.06) == 3.0


def test_all_22_profiles_have_readable_text_colors():
    assert len(PROFILES) == 22
    for p in PROFILES.values():
        assert palette_contrast(p) == [], p.purpose


def test_replaced_colors_from_the_summary():
    # 統合版 G1 の置き換え（対 base / base2 の最小が 4.6 前後）
    assert PROFILES[Purpose.LECTURE].palette.accent == "#A55200"
    assert PROFILES[Purpose.KIDS].palette.main == "#BC4D00"
    assert PROFILES[Purpose.NEWS].palette.accent == "#FF535E"
    assert PROFILES[Purpose.EXPLAINER].palette.chart[2] == "#869FBC"


def test_low_contrast_palette_is_an_error():
    p = PROFILES[Purpose.LECTURE].model_copy(deep=True)
    p.palette.accent = "#D9822B"  # 元の色（対 base 2.49）
    res = check(p, DesignManifest())
    assert not res.ok and any(v.code == "contrast" for v in res.violations)
