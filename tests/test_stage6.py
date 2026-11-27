"""段階6: BGM の生成の指示（Q5）。"""

from video_automation.schemas.design import Purpose
from video_automation.services.build import bgm_prompts as bp
from video_automation.services.music.music_service import INSTRUMENTAL_LYRICS


def test_bgm_caption_for_every_purpose():
    assert set(bp.BGM) == set(Purpose)  # 22 用途すべてに 1 つずつ
    none = {p for p, v in bp.BGM.items() if v is None}
    assert none == {Purpose.SUBTITLE, Purpose.INTERVIEW, Purpose.MV, Purpose.LYRIC}
    assert bp.bgm_spec(Purpose.EXPLAINER) == (
        "lo-fi, warm, soft piano, light drums, calm, unobtrusive",
        88,
    )
    assert bp.bgm_spec(Purpose.TRAILER)[1] == 100


def test_cut_length_on_beat():
    assert bp.cut_length(60.0, 120) == 60.0  # ちょうど拍の頭
    n = bp.cut_length(60.1, 88)
    assert n >= 60.1 and abs(n / (60 / 88) - round(n / (60 / 88))) < 1e-6
    assert INSTRUMENTAL_LYRICS == "[intro]\n[inst]\n[outro]"
