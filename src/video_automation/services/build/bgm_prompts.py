"""BGM の生成の指示（統合版 Q5・2.3 の表）。用途ごとの caption と BPM（ACE-Step 1.5 に渡す）。

- caption は API の `prompt`、BPM は `bpm` に渡す。歌なしの lyrics は `[intro]\\n[inst]\\n[outro]`
- 長さは動画の長さ＋2 秒。最後は拍の頭で切って 1.5 秒のフェード
- 「生成しない」用途（字幕動画・インタビュー・MV・リリック）は None
"""

from __future__ import annotations

import math

from video_automation.schemas.design import Purpose

INSTRUMENTAL = "[intro]\n[inst]\n[outro]"
EXTRA_SEC = 2.0
FADE_SEC = 1.5

BGM: dict[Purpose, tuple[str, int] | None] = {
    Purpose.SUBTITLE: None,
    Purpose.INTERVIEW: None,
    Purpose.DOCUMENTARY: ("ambient, cinematic, soft piano, strings, reflective", 80),
    Purpose.STORY: ("ambient, music box, soft strings, gentle", 72),
    Purpose.PODCAST: ("lo-fi, mellow, soft keys, light drums", 85),
    Purpose.EXPLAINER: ("lo-fi, warm, soft piano, light drums, calm, unobtrusive", 88),
    Purpose.LECTURE: ("lo-fi, minimal piano, calm, unobtrusive", 84),
    Purpose.TUTORIAL: ("minimal, clean, soft synth pluck, light percussion", 100),
    Purpose.RECIPE: ("acoustic, ukulele, light guitar, cheerful", 108),
    Purpose.NEWS: ("minimal, synth pad, light pulse, neutral", 96),
    Purpose.CORPORATE: ("corporate, inspiring, piano, strings, polished", 110),
    Purpose.SHORT: ("upbeat pop, punchy drums, bright synth", 124),
    Purpose.GAME: ("electronic, chiptune, energetic drums, synth bass", 128),
    Purpose.VLOG: ("acoustic, light guitar, warm, relaxed", 104),
    Purpose.RANKING: ("upbeat pop, punchy drums, bright synth, playful", 120),
    Purpose.PRODUCT: ("modern pop, clean drums, bright synth", 118),
    Purpose.PROMO: ("upbeat, energetic, punchy drums, bright synth, catchy", 126),
    Purpose.EVENT: ("upbeat, indie pop, guitar, claps, festive", 120),
    Purpose.KIDS: ("cheerful, xylophone, ukulele, handclaps, playful", 112),
    Purpose.MV: None,
    Purpose.LYRIC: None,
    Purpose.TRAILER: ("cinematic, dark, drums, strings, building tension", 100),
}


def bgm_spec(purpose: Purpose) -> tuple[str, int] | None:
    return BGM.get(purpose)


def cut_length(video_sec: float, bpm: int) -> float:
    """BGM の長さ: 動画の長さ以上で、最初の拍の頭（0 秒を拍とみなす）。"""
    beat = 60.0 / bpm
    return math.ceil(video_sec / beat - 1e-9) * beat
