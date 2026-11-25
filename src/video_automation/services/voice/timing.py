"""音声の時間情報（Phase 4 の字幕同期で使う）。

VOICEVOX系エンジンの合成手順（前後無音追加 → 句読点無音の固定長/倍率 → 話速で全音素を除算 →
音素長をフレーム(93.75fps)単位に丸め）に合わせて、アクセント句ごとの開始・終了時刻を求める。
実際の音声長は WAV ヘッダから取得し、計算値との差は実機テストで検証する。
"""

import io
import wave

from video_automation.schemas.voice import AudioQuery, PhraseTiming

DEFAULT_FRAME_RATE = 93.75


def _frames(seconds: float, frame_rate: float) -> int:
    return round(seconds * frame_rate)


def phrase_timings(query: AudioQuery, frame_rate: float = DEFAULT_FRAME_RATE) -> list[PhraseTiming]:
    speed = query.speedScale if query.speedScale > 0 else 1.0

    def f(sec: float | None) -> int:
        return _frames((sec or 0.0) / speed, frame_rate)

    cursor = f(query.prePhonemeLength)
    out: list[PhraseTiming] = []
    for i, ap in enumerate(query.accent_phrases):
        start = cursor
        for m in ap.moras:
            cursor += f(m.consonant_length) + f(m.vowel_length)
        end = cursor
        if ap.pause_mora is not None:
            pause = (
                query.pauseLength if query.pauseLength is not None else ap.pause_mora.vowel_length
            )
            cursor += f(pause * query.pauseLengthScale)
        out.append(
            PhraseTiming(
                index=i,
                text="".join(m.text for m in ap.moras),
                start_sec=round(start / frame_rate, 4),
                end_sec=round(end / frame_rate, 4),
            )
        )
    return out


def estimated_duration(query: AudioQuery, frame_rate: float = DEFAULT_FRAME_RATE) -> float:
    timings = phrase_timings(query, frame_rate)
    speed = query.speedScale if query.speedScale > 0 else 1.0
    tail = 0.0
    if query.accent_phrases and query.accent_phrases[-1].pause_mora is not None:
        last = query.accent_phrases[-1].pause_mora
        pause = query.pauseLength if query.pauseLength is not None else last.vowel_length
        tail = _frames(pause * query.pauseLengthScale / speed, frame_rate) / frame_rate
    end = timings[-1].end_sec + tail if timings else query.prePhonemeLength / speed
    return round(end + _frames(query.postPhonemeLength / speed, frame_rate) / frame_rate, 4)


def wav_info(data: bytes) -> tuple[int, float, int]:
    """(sample_rate, duration_sec, channels) を返す。"""
    with wave.open(io.BytesIO(data), "rb") as w:
        rate, frames, ch = w.getframerate(), w.getnframes(), w.getnchannels()
    return rate, round(frames / rate, 4), ch


def mora_times(
    query: AudioQuery, frame_rate: float = DEFAULT_FRAME_RATE
) -> list[tuple[float, float]]:
    """発話モーラ（句読点の無音を除く）ごとの (開始, 終了) 秒。phrase_timings と同じ丸め。"""
    speed = query.speedScale if query.speedScale > 0 else 1.0

    def f(sec: float | None) -> int:
        return _frames((sec or 0.0) / speed, frame_rate)

    cursor = f(query.prePhonemeLength)
    out: list[tuple[float, float]] = []
    for ap in query.accent_phrases:
        for m in ap.moras:
            start = cursor
            cursor += f(m.consonant_length) + f(m.vowel_length)
            out.append((start / frame_rate, cursor / frame_rate))
        if ap.pause_mora is not None:
            pause = (
                query.pauseLength if query.pauseLength is not None else ap.pause_mora.vowel_length
            )
            cursor += f(pause * query.pauseLengthScale)
    return out


SPEECH_LEVEL_DB = -20.0  # セリフの音量のそろえ先（話している部分の RMS、dBFS）
SPEECH_PEAK_DB = -1.5  # そろえた後のピークの上限（dBFS）
SPEECH_MAX_GAIN_DB = 9.0  # 上げ・下げの上限（無音に近い声を無理に持ち上げない）


def level_speech(data: bytes) -> bytes:
    """セリフの音量をそろえる（16 bit PCM の WAV のみ。それ以外はそのまま返す）。

    実機で、短い発話・話し手の違いでセリフ間の音量差が 6 LU を超えていた（-26.8〜-20.5 LUFS）。
    話している部分（50 ms ごとの区間のうち、最も大きい区間から 30 dB 以内）の RMS を
    SPEECH_LEVEL_DB に合わせ、ピークが SPEECH_PEAK_DB を超えない範囲で上げ下げする。"""
    import numpy as np

    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            params = w.getparams()
            raw = w.readframes(w.getnframes())
    except (wave.Error, EOFError):
        return data
    if params.sampwidth != 2 or not raw:
        return data
    x = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    win = max(1, int(params.framerate * params.nchannels * 0.05))
    n = len(x) // win
    if n == 0:
        return data
    rms = np.sqrt(np.mean(x[: n * win].reshape(n, win) ** 2, axis=1) + 1e-12)
    db = 20 * np.log10(rms)
    voiced = db[db > db.max() - 30.0]
    if voiced.size == 0 or db.max() < -60.0:
        return data
    level = 10 * np.log10(np.mean(10 ** (voiced / 10)))
    gain = SPEECH_LEVEL_DB - level
    peak = float(np.max(np.abs(x)))
    if peak > 0:
        gain = min(gain, SPEECH_PEAK_DB - 20 * np.log10(peak))
    gain = max(-SPEECH_MAX_GAIN_DB, min(SPEECH_MAX_GAIN_DB, gain))
    if abs(gain) < 0.5:
        return data
    y = np.clip(np.round(x * (10 ** (gain / 20)) * 32768.0), -32768, 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setparams(params)
        w.writeframes(y.tobytes())
    return buf.getvalue()
