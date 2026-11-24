"""立ち絵: 台詞からの表情・ポーズ選び、動きの予定、口の開き、パックの読み込みと描画。"""

import shutil
from pathlib import Path

import pytest

from video_automation.services.characters import motion
from video_automation.services.characters.motion import Animator, Line, classify, plan, sample
from video_automation.services.characters.pack import load_pack


@pytest.mark.parametrize(
    ("text", "expr", "pose"),
    [
        ("今日は、使い方を紹介するのだ！", "happy", "wave"),
        ("右上のボタンを押すのね", "neutral", "point"),
        ("えっ、本当に？", "surprised", "oops"),
        ("これで合っているかな？", "thinking", "think"),
        ("間違えることもあるのだ", "troubled", "oops"),
        ("これで完成なのだ！", "excited", "confident"),
        ("BGMもまとめて出せるのよ", "happy", "present"),
        ("やったのだ！", "excited", "cheer"),
        ("そうなのね", "neutral", "neutral"),
    ],
)
def test_classify(text: str, expr: str, pose: str) -> None:
    b = classify(text)
    assert (b.expression, b.pose) == (expr, pose)


def test_plan_speaker_and_listener() -> None:
    lines = [
        Line("A", "こんにちは！", 0, 60, [0, 2, 2, 1] + [0] * 56),
        Line("B", "ボタンを押すのね", 70, 130),
    ]
    a = plan("A", lines, 30.0)
    assert a.spans[0][2:] == ("happy", "wave")
    assert {e.kind for e in a.events} >= {"wave", "hop", "listen"}
    assert a.mouth[1] == 2 and 0 not in a.mouth  # 音声の大きさから
    b = plan("B", lines, 30.0, seed=1)
    assert any(e.kind == "nod" for e in b.events)  # 相手の文の終わりにうなずく
    assert b.mouth  # 音声が無いときも口を動かす


def test_gesture_keys_overshoot_and_settle() -> None:
    hop = motion.GESTURES["hop"]
    assert sample(hop, 0, 30.0) == {}
    down = sample(hop, 4, 30.0)
    up = sample(hop, 10, 30.0)
    assert down["sy"] < 0 < up["sy"] and up["dy"] < 0  # 沈んでから伸びて跳ぶ
    assert sample(hop, 40, 30.0) == {}  # 最後は元に戻る
    assert sample(hop, 8, 60.0) == sample(hop, 4, 30.0)  # フレームレートに合わせて伸びる


def test_animator_pose_change_pops_arm_and_blinks() -> None:
    lines = [Line("A", "ボタンを押すのね", 0, 60), Line("A", "完成なのだ！", 100, 160)]
    sch = plan("A", lines, 30.0)
    an = Animator(sch, 30.0)
    poses = {"point": {"arm_l": "x", "arm_r": "p"}, "confident": {"arm_l": "y", "arm_r": "p"}}
    states = [
        an.state(f, arms_of=lambda p: poses.get(p, {"arm_l": "n", "arm_r": "n"}))
        for f in range(200)
    ]
    assert states[30].pose == "point" and states[130].pose == "confident"
    assert any("arm_l" in s.pop_arms for s in states[100:115])
    assert any(s.eyes_closed for s in states)
    assert states[30].speaking > 0.9 and states[90].speaking < 0.5


def test_mouth_levels_from_audio() -> None:
    import numpy as np

    rate, fps = 24000, 30.0
    x = np.concatenate([np.zeros(rate // 3), 8000 * np.sin(np.arange(rate // 3) * 0.3)])
    lv = motion.mouth_levels(x, rate, fps, 20)
    assert lv[:9] == [0] * 9 and max(lv[11:]) == 2


def test_pack_load_mirror_and_compose(tmp_path: Path, make_tachie_pack) -> None:
    from video_automation.services.characters.motion import State
    from video_automation.services.characters.overlay import Composer

    d = make_tachie_pack(tmp_path)
    p = load_pack(d, 100)
    assert p.size == (40, 100)  # bbox 80x200 → 高さ 100
    pm = load_pack(d, 100, mirror=True)
    arm = pm.parts["arm_r"]["a"]
    assert arm is not None and round(arm.x) == 40 - 5  # 左右反転で反対側へ
    assert p.expression("happy") == p.expression("neutral")  # 欠けた表情は neutral
    comp = Composer(p, toward=1)
    im1 = comp.image(State("neutral", "neutral", 0, False, 1.0))
    im2 = comp.image(State("neutral", "neutral", 2, True, 1.0))
    assert im1.size == p.size and im1.tobytes() != im2.tobytes()
    im3 = comp.image(State("neutral", "wave", 0, False, 1.0, arm=12))
    assert im3.getpixel((2, 15))[2] > 0 or im3.getbbox()  # 腕を回しても描ける


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_render_overlay_webm_with_alpha(tmp_path: Path, make_tachie_pack) -> None:
    import subprocess

    from video_automation.services.characters.overlay import Cast, render_overlay

    d = make_tachie_pack(tmp_path)
    p = load_pack(d, 64, mirror=True)
    lines = [Line("A", "こんにちは！", 0, 20)]
    out = tmp_path / "o.webm"
    w, h = render_overlay(
        [Cast(p, "left", plan("A", lines, 30.0))], 30, 30.0, (160, 100), out, "ffmpeg"
    )
    assert (w, h) == (160, 78) and out.is_file()
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream_tags=alpha_mode",
            "-of",
            "csv=p=0",
            str(out),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "1" in probe.stdout  # 透過つき


def test_layout_portrait_is_smaller_and_inward() -> None:
    from video_automation.services.characters.overlay import layout

    band, hh, x = layout(1080, 1920)
    assert hh < 0.4 and x > 0.15
    assert layout(1920, 1080)[1] > 0.6
