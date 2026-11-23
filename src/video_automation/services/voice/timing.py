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
