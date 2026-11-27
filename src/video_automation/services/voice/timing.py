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


SPEECH_LUFS = -20.0  # セリフの音量のそろえ先（ITU-R BS.1770 のラウドネス、LUFS）
SPEECH_PEAK_DB = -3.0  # そろえた後のピークの上限（dBFS。-1.5 だとトゥルーピークが -0.9 になった）
SPEECH_MAX_GAIN_DB = 12.0  # 上げ・下げの上限（無音に近い声を無理に持ち上げない）


def _k_weight(n: int, rate: int):  # noqa: ANN202 - numpy の配列
    """ITU-R BS.1770 の K 特性（高域シェルフ＋低域カット）の、rfft の各点での |H|^2。"""
    import numpy as np

    def biquad(b, a):  # noqa: ANN001, ANN202
        w = np.exp(-1j * np.linspace(0, np.pi, n // 2 + 1))
        num = b[0] + b[1] * w + b[2] * w**2
        den = a[0] + a[1] * w + a[2] * w**2
        return np.abs(num / den) ** 2

    # 高域シェルフ（+4 dB, 1500 Hz）
    g, q, fc = 4.0, 1 / np.sqrt(2), 1500.0
    A = 10 ** (g / 40)  # noqa: N806
    w0 = 2 * np.pi * fc / rate
    al = np.sin(w0) / (2 * q)
    c = np.cos(w0)
    sh = biquad(
        (
            A * ((A + 1) + (A - 1) * c + 2 * np.sqrt(A) * al),
            -2 * A * ((A - 1) + (A + 1) * c),
            A * ((A + 1) + (A - 1) * c - 2 * np.sqrt(A) * al),
        ),
        (
            (A + 1) - (A - 1) * c + 2 * np.sqrt(A) * al,
            2 * ((A - 1) - (A + 1) * c),
            (A + 1) - (A - 1) * c - 2 * np.sqrt(A) * al,
        ),
    )
    # 低域カット（38 Hz）
    q, fc = 0.5, 38.0
    w0 = 2 * np.pi * fc / rate
    al = np.sin(w0) / (2 * q)
    c = np.cos(w0)
    hp = biquad(((1 + c) / 2, -(1 + c), (1 + c) / 2), (1 + al, -2 * c, 1 - al))
    return sh * hp


def loudness_lufs(x, rate: int) -> float | None:  # noqa: ANN001
    """モノラルの信号（-1〜1）の統合ラウドネス（BS.1770、ゲート付き）。短すぎ・無音は None。"""
    import numpy as np

    n = len(x)
    block, step = int(0.4 * rate), int(0.1 * rate)
    if n < block or n == 0:
        if n == 0:
            return None
        block = step = n
    y = np.fft.irfft(np.fft.rfft(x) * np.sqrt(_k_weight(n, rate)), n)
    starts = range(0, n - block + 1, step)
    ms = np.array([np.mean(y[s : s + block] ** 2) for s in starts])
    lk = -0.691 + 10 * np.log10(ms + 1e-20)
    gated = ms[lk > -70.0]
    if gated.size == 0:
        return None
    rel = -0.691 + 10 * np.log10(np.mean(gated)) - 10.0
    gated = ms[(lk > -70.0) & (lk > rel)]
    if gated.size == 0:
        return None
    return float(-0.691 + 10 * np.log10(np.mean(gated)))


def level_speech(data: bytes) -> bytes:
    """セリフの音量をそろえる（16 bit PCM の WAV のみ。それ以外はそのまま返す）。

    実機で、短い発話・話し手の違いでセリフ間の音量差が 6 LU を超えていた（-26.8〜-20.5 LUFS）。
    品質検査と同じ BS.1770 のラウドネスで測って SPEECH_LUFS に合わせ、ピークが
    SPEECH_PEAK_DB を超えない範囲で上げ下げする（RMS でそろえると、声の高さの違いで
    6 LU 以上ずれが残った）。"""
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
    mono = x.reshape(-1, params.nchannels).mean(axis=1) if params.nchannels > 1 else x
    level = loudness_lufs(mono, params.framerate)
    if level is None:
        return data
    gain = max(-SPEECH_MAX_GAIN_DB, min(SPEECH_MAX_GAIN_DB, SPEECH_LUFS - level))
    if abs(gain) < 0.3:
        return data
    g = x * (10 ** (gain / 20))
    # ピークだけを柔らかく抑える（ピークで上げ幅を決めると、山の鋭い声が小さいまま残り、
    # 実機でセリフ間の差が 6.4 LU になった）。-6 dBFS から上を SPEECH_PEAK_DB へ滑らかに寄せる
    ceil = 10 ** (SPEECH_PEAK_DB / 20)
    knee = 10 ** (-6.0 / 20)
    a = np.abs(g)
    over = a > knee
    a[over] = knee + (ceil - knee) * np.tanh((a[over] - knee) / (ceil - knee))
    g = np.sign(g) * a
    y = np.clip(np.round(g * 32768.0), -32768, 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setparams(params)
        w.writeframes(y.tobytes())
    return buf.getvalue()


def strongest_phrase_sec(query: AudioQuery) -> float | None:
    """統合版 Q8: アクセントの最も強い句（モーラの音の高さの最大が最も高い句）の始まりの秒。"""
    speed = query.speedScale if query.speedScale > 0 else 1.0
    t = (query.prePhonemeLength or 0.0) / speed
    best: tuple[float, float] | None = None
    for ap in query.accent_phrases:
        start = t
        peak = max((m.pitch or 0.0 for m in ap.moras), default=0.0)
        for m in ap.moras:
            t += ((m.consonant_length or 0.0) + (m.vowel_length or 0.0)) / speed
        if ap.pause_mora is not None:
            pause = (
                query.pauseLength if query.pauseLength is not None else ap.pause_mora.vowel_length
            )
            t += (pause or 0.0) * query.pauseLengthScale / speed
        if peak > 0 and (best is None or peak > best[0]):
            best = (peak, start)
    return None if best is None else round(best[1], 4)


def strongest_phrase_span(query: AudioQuery) -> tuple[float, float] | None:
    """統合版 T38: アクセントの最も強い句（Q8 と同じ選び方）が、発話の全モーラの中で占める
    範囲（前からの割合 0〜1 の始まりと終わり）。文字の位置への読み替えに使う。"""
    counts = [len(ap.moras) for ap in query.accent_phrases]
    total = sum(counts)
    if total == 0:
        return None
    best: tuple[float, int] | None = None
    for i, ap in enumerate(query.accent_phrases):
        peak = max((m.pitch or 0.0 for m in ap.moras), default=0.0)
        if peak > 0 and (best is None or peak > best[0]):
            best = (peak, i)
    if best is None:
        return None
    a = sum(counts[: best[1]])
    return a / total, (a + counts[best[1]]) / total
