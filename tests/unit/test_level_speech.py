import io
import math
import wave

import numpy as np

from video_automation.services.voice.timing import SPEECH_LUFS, level_speech, loudness_lufs


def _wav(amp: float, freq: float = 220, sec: float = 1.5, rate: int = 24000) -> bytes:
    t = np.arange(int(rate * sec)) / rate
    x = (amp * np.sin(2 * math.pi * freq * t) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(x.tobytes())
    return buf.getvalue()


def _lufs(data: bytes) -> float:
    with wave.open(io.BytesIO(data), "rb") as w:
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2") / 32768.0
        rate = w.getframerate()
    v = loudness_lufs(x, rate)
    assert v is not None
    return v


def test_1khz_reference_level():
    # BS.1770: 1 kHz・0 dBFS の正弦波（振幅 1）は約 -3.01 LUFS
    x = np.sin(2 * math.pi * 1000 * np.arange(48000 * 2) / 48000)
    assert abs(loudness_lufs(x, 48000) - (-3.01)) < 0.3


def test_low_and_high_voices_end_up_close():
    a = level_speech(_wav(0.05, 150))
    b = level_speech(_wav(0.2, 2500))
    assert abs(_lufs(a) - _lufs(b)) < 1.0
    assert abs(_lufs(a) - SPEECH_LUFS) < 1.0


def test_non_pcm16_and_silence_are_untouched():
    silent = _wav(0.0)
    assert level_speech(silent) == silent
    assert level_speech(b"not a wav") == b"not a wav"
