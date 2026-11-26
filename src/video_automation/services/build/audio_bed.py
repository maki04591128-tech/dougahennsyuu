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
# 統合版 S3: BGM のダッキング。声の区間は声のピーク（timing.SPEECH_PEAK_DB = −3 dBFS）より
# 20 dB 下、1 秒以上の台詞の間・冒頭・最後は 7 dB 上げる。上げ下げは 0.4 秒で滑らかに。
# BGM には highpass 100 Hz と 2 kHz の −3 dB（声の帯域を空ける）を掛ける
BGM_PEAK_DB = -15.1  # make_bgm の和音に下の EQ を掛けたときのピーク（実測）
BGM_DUCK_PEAK_DB = -23.0  # 声の区間の BGM のピーク
BGM_RAISE_DB = 7.0
BGM_FADE_SEC = 0.4
BGM_GAP_SEC = 1.0  # これ以上の台詞の間で BGM を上げる
BGM_EQ = "highpass=f=100,equalizer=f=2000:t=q:w=1:g=-3"


def raise_spans(
    speech: list[tuple[float, float]], total_sec: float, gap: float = BGM_GAP_SEC
) -> list[tuple[float, float]]:
    """BGM を上げる区間（冒頭・gap 秒以上の台詞の間・最後）。speech は声の区間（秒）。"""
    spans: list[tuple[float, float]] = []
    t = 0.0
    for a, b in sorted(speech):
        if a - t >= gap or (t == 0.0 and a > 0):
            spans.append((t, a))
        t = max(t, b)
    if total_sec - t >= gap:
        spans.append((t, total_sec))
    return [(a, b) for a, b in spans if b - a > 0]


def duck_expr(spans: list[tuple[float, float]]) -> str:
    """BGM の音量の式（時刻 t）。声の区間は低く、spans の区間は上げる。"""
    low = 10 ** ((BGM_DUCK_PEAK_DB - BGM_PEAK_DB) / 20)
    high = 10 ** ((BGM_DUCK_PEAK_DB + BGM_RAISE_DB - BGM_PEAK_DB) / 20)
    if not spans:
        return f"{low:.4f}"
    f = BGM_FADE_SEC
    # 区間ごとの台形（端で 0.4 秒かけて上げ下げ。冒頭・最後は端を上げたまま）
    terms = []
    for a, b in spans:
        up = "1" if a <= 0 else f"(t-{a:.3f})/{f}"
        down = f"({b:.3f}-t)/{f}"
        terms.append(f"min(1,max(0,min({up},{down})))")
    return f"{low:.4f}+{high - low:.4f}*min(1,{'+'.join(terms)})"


WHOOSH_VOLUME = 2.0  # ピーク −15 dB 前後（声のピークより 8 dB ほど小さく）
POP_VOLUME = 0.4  # 同じくピーク −14 dB 前後
# 統合版 E3: 入りの種類ごとの「シュッ」の立ち上がり（30fps のフレーム数）。
# 音の山は入りの動きの真ん中 +1f に合わせる。dissolve・dip には付けない
WHOOSH_LEAD_FRAMES = {"zoom": 15, "slide": 8, "swipe": 5, "whip": 4, "wipe": 8, "panel": 8}
POP_PRESETS = frozenset({"pop_in_soft", "pop_in", "pop_express"})
# 入りのプリセットの長さ（30fps。motion.py の値）
ENTRANCE_FRAMES = {"pop_in_soft": 14, "pop_in": 16, "pop_express": 12}


def entrance_frames(preset: str | None, default: int) -> int:
    """入りの長さ（30fps）。スライドは 16f、分からないときは用途の入りの長さ。"""
    if preset in ENTRANCE_FRAMES:
        return ENTRANCE_FRAMES[preset]
    if preset and preset.startswith("slide_in"):
        return 16
    return default


def whoosh_kind(preset: str | None) -> str | None:
    """プリセット名 → 効果音の種類（E3）。音を付けない入りは None。"""
    if not preset:
        return "panel"
    if preset in POP_PRESETS or preset.startswith("zoom"):
        return "zoom"
    for k in ("slide", "swipe", "whip", "wipe"):
        if preset.startswith(k):
            return k
    if preset in ("dissolve", "dip_to_black", "fade"):
        return None
    return "panel"


def whoosh_start(entrance_start: int, entrance_frames: int, kind: str, fps: float) -> int:
    """「シュッ」を置く始まりのフレーム。山（立ち上がりの終わり）を入りの真ん中 +1f に置く。"""
    lead = round(WHOOSH_LEAD_FRAMES[kind] * fps / 30)
    peak = entrance_start + entrance_frames // 2 + round(fps / 30)
    return max(0, peak - lead)


def _chord_expr(freqs: tuple[float, ...]) -> str:
    # 和音の各音を少し揺らし（0.3 Hz）、立ち上がりと減衰を付けて柔らかくする
    env = "(min(1,t/0.6)*(1-0.35*t/4))"
    tones = "+".join(f"sin(2*PI*{f}*t)*(0.9+0.1*sin(2*PI*0.3*t))" for f in freqs)
    bass = f"0.6*sin(2*PI*{freqs[0] / 2}*t)"
    return f"0.11*{env}*({tones}+{bass})"


async def make_bgm(
    ffmpeg: FFmpeg,
    out: Path,
    duration_sec: float,
    speech: list[tuple[float, float]] | None = None,
) -> Path:
    """長さ duration_sec の BGM（WAV）。和音を繰り返し、最後は 2 秒で消す。

    speech（声の区間・秒）を渡すと、声の間は下げて台詞の間は上げる（統合版 S3）。"""
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
        f"{BGM_EQ},"
        f"volume=eval=frame:volume='{duck_expr(raise_spans(speech or [], duration_sec))}',"
        "aformat=channel_layouts=stereo[o]"
    )
    script = out.with_suffix(".filter.txt")
    script.write_text(filt, encoding="utf-8")  # 区間が多いと引数が長くなるのでファイルで渡す
    try:
        await ffmpeg.run(
            [*inputs, "-filter_complex_script", str(script), "-map", "[o]", str(out)],
            timeout=600,
        )
    finally:
        script.unlink(missing_ok=True)
    return out


async def make_whoosh(ffmpeg: FFmpeg, out: Path, lead_sec: float = 0.18) -> Path:
    """場面の切り替えの「シュッ」。立ち上がり lead_sec のあと 0.25 秒で消える（E3）。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    lead = min(max(lead_sec, 0.05), 1.0)
    await ffmpeg.run(
        [
            "-f",
            "lavfi",
            "-i",
            f"anoisesrc=color=pink:d={lead + 0.27:.3f}:a=0.6:r=48000",
            "-af",
            f"bandpass=f=1800:w=1.2,afade=t=in:d={lead:.3f},"
            f"afade=t=out:st={lead + 0.02:.3f}:d=0.25,"
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
