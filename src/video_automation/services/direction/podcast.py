"""ポッドキャストの波形（統合版 P2）。

画面の下寄りの中央に、横幅は画面の幅の 60%・高さ 9% の波形（FFmpeg の showwaves、cline）。
振幅は最大で画面の高さの 5%。話している人の声だけを、その人の色（3 節の「枠・線」）で描く。

話者ごとに、その人の発話の音声（VOICEVOX の WAV）だけを並べたチャンネルを作り、
チャンネルを重ねて色分けした 1 本の透過の動画にする（書き出しの入力は 1 つ）。
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

WIDTH, HEIGHT = 0.60, 0.09  # 画面に対する比
AMPLITUDE = 0.05  # 振幅の最大（画面の高さに対する比）
CENTER_Y = 0.80  # 波形の中心の高さ（画面の上からの比）
RATE = 24000


def _read(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as f:
        n, ch, sw, sr = f.getnframes(), f.getnchannels(), f.getsampwidth(), f.getframerate()
        raw = f.readframes(n)
    if sw != 2:
        raise ValueError("16bit の WAV だけ読めます")
    x = np.frombuffer(raw, "<i2").astype(np.float32) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return x, sr


def speaker_tracks(tracks: list[list[tuple[Path, float]]], total_sec: float, out: Path) -> float:
    """話者ごとの発話の音声を、それぞれの開始の秒に置き、話者ごとに 1 チャンネルの WAV にする
    （24kHz・話者の数のチャンネル）。振幅の最大（0〜1。全話者）を返す。"""
    n = max(1, round(total_sec * RATE))
    buf = np.zeros((n, max(1, len(tracks))), np.float32)
    for ch, items in enumerate(tracks):
        for path, at in items:
            x, sr = _read(path)
            if sr != RATE:  # VOICEVOX は 24kHz。違うときは線形で合わせる
                t = np.arange(round(len(x) * RATE / sr)) * sr / RATE
                x = np.interp(t, np.arange(len(x)), x).astype(np.float32)
            a = round(at * RATE)
            if a >= n:
                continue
            k = min(len(x), n - a)
            buf[a : a + k, ch] += x[:k]
    peak = float(np.abs(buf).max())
    pcm = (np.clip(buf, -1, 1) * 32767).astype("<i2")
    with wave.open(str(out), "wb") as f:
        f.setnchannels(buf.shape[1])
        f.setsampwidth(2)
        f.setframerate(RATE)
        f.writeframes(pcm.tobytes())
    return peak


def wave_size(w: int, h: int) -> tuple[int, int]:
    """波形の動画の大きさ（偶数）。"""
    return max(2, round(w * WIDTH) // 2 * 2), max(2, round(h * HEIGHT) // 2 * 2)


def waveform_args(
    wav: Path,
    peak: float,
    colors: list[str],
    w: int,
    h: int,
    fps: str,
    total_sec: float,
    out: Path,
) -> list[str]:
    """話者ごとのチャンネルの音声 → 透過の波形の動画（PNG の mov）の FFmpeg の引数（-y を除く）。

    チャンネルを同じ場所に重ねて、チャンネルごとに話者の色で描く（話している人の波形だけが出る）。
    showwaves は音の最大で枠の高さいっぱいに描くので、最大の振幅が画面の高さの 5%
    （片側）になるよう音量を掛ける。"""
    ww, wh = wave_size(w, h)
    # 片側の振幅の目標（画面の高さの 5%。波形の枠の半分を超えない）
    half = min(AMPLITUDE * h, wh / 2)
    gain = min(8.0, half / (wh / 2) / max(peak, 1e-3))
    col = "|".join("0x" + c.lstrip("#") for c in colors)
    return [
        "-i",
        str(wav),
        "-filter_complex",
        f"[0:a]volume={gain:.4f},showwaves=s={ww}x{wh}:mode=cline:colors={col}:"
        f"rate={fps}:scale=lin:split_channels=0,format=rgba[v]",
        "-map",
        "[v]",
        "-t",
        f"{total_sec:.3f}",
        "-c:v",
        "png",
        "-pix_fmt",
        "rgba",
        str(out),
    ]
