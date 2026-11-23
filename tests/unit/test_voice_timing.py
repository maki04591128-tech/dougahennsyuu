from fakes import FakeNemo, _wav
from video_automation.schemas.voice import AudioQuery, DictionaryWordRequest
from video_automation.services.voice.timing import estimated_duration, phrase_timings, wav_info
from video_automation.services.voice.voice_service import mora_count


def _q(text="あい、うえお", **kw):
    data = {"accent_phrases": FakeNemo().phrases(text), **kw}
    return AudioQuery.model_validate(data)


def test_phrase_timings_follow_frames_and_pause():
    t = phrase_timings(_q())
    # pre 0.1s → 9 frames(=0.096s)、1モーラ 0.15s → 14 frames
    assert t[0].text == "あい" and t[0].start_sec == round(9 / 93.75, 4)
    assert t[0].end_sec == round((9 + 2 * 14) / 93.75, 4)
    # 句読点 0.3s → 28 frames の無音の後に次の句
    assert t[1].start_sec == round((9 + 28 + 28) / 93.75, 4)


def test_speed_and_pause_scale_change_timing():
    base = estimated_duration(_q())
    fast = estimated_duration(_q(speedScale=2.0))
    assert abs(fast - base / 2) < 0.03
    no_pause = phrase_timings(_q(pauseLengthScale=0.0))
    assert no_pause[1].start_sec == no_pause[0].end_sec
    fixed = phrase_timings(_q(pauseLength=1.0))
    assert round(fixed[1].start_sec - fixed[0].end_sec, 2) == round(94 / 93.75, 2)


def test_wav_info_and_mora_count():
    rate, dur, ch = wav_info(_wav(1.25))
    assert (rate, dur, ch) == (24000, 1.25, 1)
    assert mora_count("キョウト") == 3 and mora_count("ヴァイオリン") == 5
    assert DictionaryWordRequest(surface="X", pronunciation="エックス", accent_type=1)


def test_pronunciation_must_be_katakana():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        DictionaryWordRequest(surface="X", pronunciation="えっくす", accent_type=1)
