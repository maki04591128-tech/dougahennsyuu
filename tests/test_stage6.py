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


def test_tachie_entrance_and_gaze():
    """統合版 F2（下から 10f・着地で縦 0.96→1.00）・F4（話していない方が話者へ 8f で向く）。"""
    from video_automation.services.characters.overlay import entrance, gaze_curve

    d0, s0 = entrance(0, 30)
    assert abs(d0 - 0.12) < 1e-9 and s0 == 1.0
    assert entrance(10, 30) == (0.0, 0.96) and entrance(16, 30) == (0.0, 1.0)
    g = gaze_curve([[(10, 40)], [(60, 90)]], 100, 30)
    assert g[0][5] == 0 and g[1][5] == 0  # 誰も話していない
    assert g[1][18] == 1.0 and g[0][18] == 0.0  # A が話す → B が A を向く（8f）
    assert 0 < g[1][13] < 1
    assert g[0][70] == 1.0 and g[1][70] == 0.0  # B に替わったら戻る
    assert gaze_curve([[(0, 10)]], 20, 30) == [[0.0] * 20]  # 1 人なら向かない
