"""段階3 Q3: 素材の見た目を揃える。"""

from pathlib import Path

import numpy as np
from PIL import Image

from video_automation.schemas.design import Purpose
from video_automation.services.assets import look
from video_automation.services.design.profiles import PROFILES

PAL = look.palette_colors(PROFILES[Purpose.EXPLAINER].palette)


def _photo(seed: int = 0, base=(0.2, 0.2, 0.2), cast=(0.0, 0.0, 0.0), spread=0.15) -> np.ndarray:
    """写真に近い、なめらかに色が変わる画像（平らな色が少ない）。"""
    rng = np.random.default_rng(seed)
    h, w = 120, 160
    yy, xx = np.mgrid[0:h, 0:w]
    img = np.stack(
        [
            base[i] + cast[i] + spread * np.sin(xx / (9 + 4 * i) + yy / (13 + 3 * i))
            for i in range(3)
        ],
        axis=-1,
    )
    img += rng.normal(0, 0.03, img.shape)
    return np.clip(img, 0, 1)


def test_lab_round_trip() -> None:
    rgb = np.random.default_rng(1).random((50, 3))
    assert np.allclose(look.from_lab(look.to_lab(rgb)), rgb, atol=1e-6)
    white = look.to_lab(np.array([1.0, 1.0, 1.0]))
    assert abs(white[0] - 100) < 1e-3 and abs(white[1]) < 1e-3


def test_dark_photo_is_brought_to_mid_luma() -> None:
    img = _photo()
    assert np.median(look.luma(img)) < 0.3
    out, res = look.match(img, PAL, individual=True)  # 1〜3 だけ
    assert "luma" in res.steps
    assert 0.45 <= float(np.median(look.luma(out))) <= 0.55


def test_gray_world_takes_half_of_the_cast() -> None:
    img = _photo(base=(0.45, 0.45, 0.45), cast=(0.15, 0.0, -0.1), spread=0.05)
    lin = look._lin(img).reshape(-1, 3).mean(axis=0)
    out, res = look.match(img, PAL, individual=True)
    assert "gray_world" in res.steps
    lin2 = look._lin(out).reshape(-1, 3).mean(axis=0)
    # 赤と青の差が、およそ半分になる（輝度のガンマで少し変わる）
    before, after = lin[0] - lin[2], lin2[0] - lin2[2]
    assert 0.3 * before < after < 0.7 * before


def test_saturation_is_capped() -> None:
    img = _photo(base=(0.85, 0.15, 0.1), spread=0.1)
    assert np.percentile(look.saturation(img), 95) > 0.85
    out, res = look.match(img, PAL, individual=True)
    assert "saturation" in res.steps
    assert np.percentile(look.saturation(out), 95) <= 0.851


def test_lab_is_pulled_toward_the_palette() -> None:
    img = _photo(base=(0.5, 0.5, 0.45), spread=0.08)
    pre, r1 = look.match(img, PAL, individual=True)
    out, r2 = look.match(img, PAL, individual=False)
    assert "lab" in r2.steps and "lab" not in r1.steps
    ref = look.to_lab(np.array([look.hex_rgb(c) for c in PAL])).mean(axis=0)
    m0 = look.to_lab(pre).reshape(-1, 3).mean(axis=0)
    m1 = look.to_lab(out).reshape(-1, 3).mean(axis=0)
    # 平均は基準の方へ（40% 前後。色域で切るのでぴったりではない）
    for k in range(3):
        if abs(ref[k] - m0[k]) > 2:
            moved = (m1[k] - m0[k]) / (ref[k] - m0[k])
            assert 0.2 < moved < 0.6, (k, moved)


def test_screens_figures_and_documents_are_individual() -> None:
    flat = np.zeros((90, 160, 3))
    flat[:, :80] = (0.9, 0.9, 0.95)
    flat[:30, 80:] = (0.2, 0.4, 0.8)
    assert look.is_individual(flat)
    gray = np.repeat(_photo()[..., :1], 3, axis=-1)
    assert look.is_individual(gray)  # 色の無い画像（白黒の写真・書類）
    photo = _photo(base=(0.4, 0.3, 0.2), spread=0.2)
    assert not look.is_individual(photo)
    assert look.is_individual(photo, "設定画面のスクリーンショット")
    _out, res = look.match(flat, PAL)
    assert res.individual and "lab" not in res.steps


def test_match_file_keeps_alpha_and_ignores_transparent_pixels(tmp_path: Path) -> None:
    rgb = (_photo(base=(0.55, 0.5, 0.45)) * 255).astype(np.uint8)
    a = np.zeros(rgb.shape[:2], np.uint8)
    a[20:100, 30:130] = 255
    rgb[a == 0] = 0  # 透明の所は黒
    assert 0.45 <= np.median(look.luma(rgb[a > 0] / 255)) <= 0.55
    src, dst = tmp_path / "in.png", tmp_path / "out.png"
    Image.fromarray(np.dstack([rgb, a]), "RGBA").save(src)
    res = look.match_file(src, dst, PAL, "cut")
    with Image.open(dst) as im:
        assert im.mode == "RGBA"
        assert np.array_equal(np.asarray(im.getchannel("A")), a)
    # 透明の黒い所を数えると暗く見えて明るくしすぎる。数えなければ輝度は直さない
    assert "luma" not in res.steps
