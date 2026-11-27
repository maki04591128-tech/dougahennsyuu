"""統合版 段階5d の背景 R1〜R18。"""

import shutil

import numpy as np
import pytest

from video_automation.schemas.design import Purpose
from video_automation.services.design.profiles import PROFILES
from video_automation.services.rendering import backgrounds as rb


def _pal(p: str = "explainer") -> dict:
    return PROFILES[Purpose(p)].palette.model_dump()


def test_every_purpose_maps_to_a_style() -> None:
    assert set(rb.PURPOSE_BACKGROUND.values()) == set(rb.STYLES)  # 18 種すべて使う
    assert "mv" not in rb.PURPOSE_BACKGROUND and "subtitle" not in rb.PURPOSE_BACKGROUND


@pytest.mark.parametrize("style", sorted(rb.STYLES))
def test_style_draws_moves_and_loops(style: str) -> None:
    p = rb.STYLES[style](192, 108, _pal())
    a = np.asarray(p.frame(0.0), float)
    b = np.asarray(p.frame(0.3), float)
    assert a.shape == (108, 192, 3)
    assert a.std() > 0.5 or style in ("R17",)  # 模様がある（平らな 1 色ではない）
    if style not in rb.STATIC and style != "R17":
        assert np.abs(a - b).mean() > 0.05  # 動いている
    if style not in rb.NONPERIODIC:  # 1 本の最後の次が最初につながる
        end = np.asarray(p.frame(1.0), float)
        assert np.abs(a - end).mean() < 1.0


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg")
def test_render_video_and_static(tmp_path) -> None:
    out = rb.render("ffmpeg", "R2", tmp_path / "bg.mp4", (192, 108), 10.0, _pal("story"), 2.0)
    assert out.suffix == ".mp4" and out.stat().st_size > 0
    png = rb.render("ffmpeg", "R10", tmp_path / "p.mp4", (192, 108), 10.0, _pal("lecture"))
    assert png.suffix == ".png" and png.is_file()
