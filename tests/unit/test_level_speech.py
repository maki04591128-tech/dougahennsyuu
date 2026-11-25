import io
import math
import wave

from video_automation.services.voice.timing import SPEECH_LEVEL_DB, level_speech


def _wav(amp: float, sec: float = 1.0, rate: int = 24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = bytearray()
        for i in range(int(rate * sec)):
            v = int(amp * 32767 * math.sin(2 * math.pi * 220 * i / rate))
            frames += v.to_bytes(2, "little", signed=True)
        w.writeframes(bytes(frames))
    return buf.getvalue()


def _rms_db(data: bytes) -> float:
    import numpy as np

    with wave.open(io.BytesIO(data), "rb") as w:
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2") / 32768.0
    return 20 * math.log10(float(np.sqrt(np.mean(x**2))))


def test_quiet_and_loud_lines_end_up_close():
    quiet, loud = level_speech(_wav(0.06)), level_speech(_wav(0.3))
    assert abs(_rms_db(quiet) - _rms_db(loud)) < 1.0
    assert abs(_rms_db(loud) - SPEECH_LEVEL_DB) < 1.0


def test_non_pcm16_and_silence_are_untouched():
    silent = _wav(0.0)
    assert level_speech(silent) == silent
    assert level_speech(b"not a wav") == b"not a wav"
