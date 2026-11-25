"""「動画をつくる」の BGM と効果音（素材をダウンロードせず FFmpeg で作る）。

- BGM: 明るく落ち着いた 4 和音の繰り返し（柔らかい正弦波の和音・ゆっくり揺れる）。歌は無い。
  声より 12 dB 以上小さく置く（screen-tutorial-direction・用途別の決まり）
- 効果音は 2 種だけ: 場面の切り替えの「シュッ」（ノイズを帯域で絞って高さを動かす）と、
  数値の強調の「ポン」（短い減衰音）
"""

from __future__ import annotations

from pathlib import Path

from video_automation.integrations.ffmpeg.runner import FFmpeg

# C - Am - F - G（1 和音 4 秒・16 秒で 1 周）。高さは Hz
_CHORDS = [
    (261.63, 329.63, 392.00),
    (220.00, 261.63, 329.63),
    (174.61, 220.00, 261.63),
    (196.00, 246.94, 293.66),
]
CHORD_SEC = 4.0
BGM_VOLUME = 0.7  # 素の音量は平均 −29 dB。声（平均 −20 dB 前後）より 12 dB 以上小さくなる
WHOOSH_VOLUME = 2.0  # ピーク −15 dB 前後（声のピークより 8 dB ほど小さく）
POP_VOLUME = 0.4  # 同じくピーク −14 dB 前後
WHOOSH_LEAD_SEC = 0.1  # 切り替えより少し前に鳴らす（音が先に聞こえると切り替えが自然）


def _chord_expr(freqs: tuple[float, ...]) -> str:
    # 和音の各音を少し揺らし（0.3 Hz）、立ち上がりと減衰を付けて柔らかくする
    env = "(min(1,t/0.6)*(1-0.35*t/4))"
    tones = "+".join(f"sin(2*PI*{f}*t)*(0.9+0.1*sin(2*PI*0.3*t))" for f in freqs)
    bass = f"0.6*sin(2*PI*{freqs[0] / 2}*t)"
    return f"0.11*{env}*({tones}+{bass})"


async def make_bgm(ffmpeg: FFmpeg, out: Path, duration_sec: float) -> Path:
    """長さ duration_sec の BGM（WAV）。和音を繰り返し、最後は 2 秒で消す。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    inputs: list[str] = []
    for f in _CHORDS:
        inputs += ["-f", "lavfi", "-i", f"aevalsrc='{_chord_expr(f)}':s=48000:d={CHORD_SEC}"]
    loop = len(_CHORDS)
    concat = "".join(f"[{i}]" for i in range(loop)) + f"concat=n={loop}:v=0:a=1[c]"
    fade = max(0.0, duration_sec - 2.0)
    filt = (
        f"{concat};[c]aloop=loop=-1:size={int(48000 * CHORD_SEC * loop)},"
        f"atrim=0:{duration_sec:.3f},lowpass=f=2400,"
        f"aecho=0.8:0.7:60|120:0.25|0.15,afade=t=in:d=1.5,afade=t=out:st={fade:.3f}:d=2,"
        "aformat=channel_layouts=stereo[o]"
    )
    await ffmpeg.run([*inputs, "-filter_complex", filt, "-map", "[o]", str(out)], timeout=600)
    return out


async def make_whoosh(ffmpeg: FFmpeg, out: Path) -> Path:
    """場面の切り替えの「シュッ」（0.45 秒）。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    await ffmpeg.run(
        [
            "-f",
            "lavfi",
            "-i",
            "anoisesrc=color=pink:d=0.45:a=0.6:r=48000",
            "-af",
            "bandpass=f=1800:w=1.2,afade=t=in:d=0.18,afade=t=out:st=0.2:d=0.25,"
            "aformat=channel_layouts=stereo",
            str(out),
        ]
    )
    return out


async def make_pop(ffmpeg: FFmpeg, out: Path) -> Path:
    """数値の強調の「ポン」（0.25 秒）。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    await ffmpeg.run(
        [
            "-f",
            "lavfi",
            "-i",
            "aevalsrc='0.7*sin(2*PI*(880-500*t)*t)*exp(-14*t)':s=48000:d=0.25",
            "-af",
            "aformat=channel_layouts=stereo",
            str(out),
        ]
    )
    return out
