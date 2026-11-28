"""段階4b: サムネイル（S4）・縦型の安全範囲（S5）・終了画面（S9）。"""

import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from video_automation.schemas.design import Purpose
from video_automation.services.design.guard import ClipMotion, DesignManifest, opening_motion
from video_automation.services.design.profiles import END_SCREEN, PROFILES, safe_area_for
from video_automation.services.rendering import thumbnail as T
from video_automation.services.rendering.end_screen import draw_end_screen


def _font() -> str | None:
    if not shutil.which("fc-match"):
        return None
    out = subprocess.run(
        ["fc-match", "-f", "%{file}", "Noto Sans CJK JP:black"], capture_output=True, text=True
    ).stdout
    return out or None


def test_vertical_safe_area_is_used_for_any_purpose() -> None:
    v = safe_area_for(PROFILES[Purpose.EXPLAINER], 1080, 1920)
    assert (v.top, v.bottom, v.left, v.right) == (0.15, 0.35, 0.06, 0.10)
    assert round((1 - v.bottom) * 1920) == 1248  # 字幕は y = 1248px より上
    h = safe_area_for(PROFILES[Purpose.SHORT], 1920, 1080)
    assert (h.top, h.bottom) == (0.05, 0.05)


def test_opening_motion_needs_an_entrance_in_the_first_15_frames() -> None:
    m = DesignManifest(vertical=True, size=(1080, 1920))
    m.clips = [ClipMotion("t", 0.0, 5.0, [(0.0, {"opacity": 0.0}), (0.3, {"opacity": 1.0})])]
    assert opening_motion(m)
    m.clips = [ClipMotion("t", 1.0, 5.0, [(0.0, {"opacity": 0.0}), (0.3, {"opacity": 1.0})])]
    assert not opening_motion(m)  # 0.5 秒より後
    m.clips = [
        ClipMotion(
            "t", 0.0, 5.0, [(0.0, {"zoom": 1.0}), (5.0, {"zoom": 1.1})], preset="ken_burns_in"
        )
    ]
    assert not opening_motion(m)  # ケン・バーンズは数えない
    # 題名（Blender の動画。入りを画の中に持つ）が最初から出ていれば入りの動きがある
    from video_automation.services.design.guard import TextUse

    m.texts = [TextUse("title", [0.08], 6, 0.0, 3.0)]
    assert opening_motion(m)
    m.texts = [TextUse("title", [0.08], 6, 2.0, 3.0), TextUse("card", [0.05], 6, 0.0, 3.0)]
    assert not opening_motion(m)


def test_end_screen_purposes_follow_2_3() -> None:
    assert len(END_SCREEN) == 11
    assert Purpose.EXPLAINER in END_SCREEN and Purpose.KIDS not in END_SCREEN
    assert Purpose.SHORT not in END_SCREEN and Purpose.NEWS not in END_SCREEN


def test_end_screen_image_leaves_the_youtube_frames_clear(tmp_path: Path) -> None:
    p = draw_end_screen(
        tmp_path / "e.png", 1920, 1080, "題名", "一言", _font(), "#FFFFFF", "#3399FF"
    )
    im = Image.open(p)
    assert im.size == (1920, 1080) and im.mode == "RGBA"
    assert im.getpixel((1500, 360))[3] == 0  # 動画の枠の中は透明（YouTube が重ねる）
    assert im.getpixel((1180, 360))[3] > 0  # 枠の線
    assert im.getpixel((300, 800))[3] == 0  # 登録の丸の中も透明


def test_thumbnail_texts_and_lines() -> None:
    assert T.shorten("太陽系の惑星を比較") == "太陽系の惑星を比較"
    assert T.shorten("スマホの電池を長持ちさせる方法") == "スマホの電池"
    assert T.thumb_texts("太陽系の惑星を比較", "大きさや特徴を解説", ["火星の大きさ"]) == [
        "太陽系の惑星を比較",
        "大きさや特徴を解説",
        "火星の大きさ",
    ]
    assert len(T.thumb_texts("猫", "")) == 3
    assert T.split_lines("太陽系の惑星を比較") == ["太陽系の", "惑星を比較"]
    assert T.split_lines("熱と満充電") == ["熱と満充電"]


@pytest.mark.skipif(_font() is None, reason="no Japanese font")
@pytest.mark.parametrize("vertical", [False, True])
def test_thumbnails_follow_s4(tmp_path: Path, vertical: bool) -> None:
    bg = Image.new("RGB", (640, 360), (40, 60, 90))
    for i, layout in enumerate(T.LAYOUTS):
        r = T.draw(
            bg,
            tmp_path / f"t{i}.jpg",
            "太陽系の惑星を比較",
            layout,
            _font(),
            "#FFFFFF",
            "#FFD23F",
            "#111111",
            vertical,
        )
        assert Image.open(r.path).size == T.size_for(vertical)
        assert T.checks(r, vertical) == []
        assert r.char_px >= 80


def test_thumbnail_checks_find_problems(tmp_path: Path) -> None:
    p = tmp_path / "x.jpg"
    Image.new("RGB", (8, 8)).save(p)
    r = T.ThumbResult(p, "あいう", "x", 60, (3500, 1900, 3800, 2100))
    bad = T.checks(r)
    assert len(bad) == 3  # 字数・右下・10% での高さ


def test_end_screen_wraps_at_word_boundaries():
    """終了画面のまとめの文字を、語の途中（「ランキ／ング」）で折らない。"""
    from PIL import Image, ImageDraw, ImageFont

    from video_automation.services.rendering.end_screen import _wrap

    f = ImageFont.load_default(40)
    d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    text = "今日のランキングを見てくれてありがとう"
    w = int(d.textlength("今日のランキ", font=f)) + 2
    lines = _wrap(d, text, f, w)
    assert "".join(lines) == text
    assert all(d.textlength(x, font=f) <= w for x in lines)
    assert not any(x.endswith("ランキ") for x in lines)
    assert _wrap(d, "短い", f, 500) == ["短い"] and _wrap(d, "", f, 100) == []
