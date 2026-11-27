"""見た目の型（統合版 段階5d: Y1〜Y10）。"""

from video_automation.schemas.design import Purpose
from video_automation.services.design.looks import LOOK_PURPOSES, LOOKS, allowed, apply_look
from video_automation.services.design.profiles import PROFILES
from video_automation.services.rendering.finish import finish_chain, look_filter


def test_every_look_is_reachable_from_a_purpose():
    reach = {y for ys in LOOK_PURPOSES.values() for y in ys}
    assert reach == set(LOOKS) == {f"Y{i}" for i in range(1, 11)}


def test_apply_look_changes_only_allowed_purposes():
    lec = PROFILES[Purpose.LECTURE]
    y1 = apply_look(lec, "Y1")
    assert y1.look == "Y1" and y1.palette.base == "#2F4A3C"
    assert y1.palette.text == "#F4F1E6"
    # 講義で選べない型・未指定はそのまま
    assert apply_look(lec, "Y5") is lec
    assert apply_look(lec, None) is lec and apply_look(lec, "") is lec
    assert not allowed(Purpose.SUBTITLE, "Y1") and allowed(Purpose.GAME, "Y6")
    story = apply_look(PROFILES[Purpose.STORY], "Y10")
    assert story.typography.heading_font == "Zen Old Mincho"
    assert apply_look(PROFILES[Purpose.GAME], "Y6").motion.stepped == 2


def test_look_finish_is_appended_to_the_chain():
    for name in ("riso", "synth", "pixel", "film"):
        f = look_filter(name, 1920, 1080)
        assert f
    assert look_filter("nope", 1920, 1080) == ""
    game = PROFILES[Purpose.GAME]
    base = finish_chain(game, 1920, 1080)
    px = finish_chain(apply_look(game, "Y6"), 1920, 1080)
    assert px != base and look_filter("pixel", 1920, 1080) in px
