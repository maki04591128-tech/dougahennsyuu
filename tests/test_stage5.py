"""段階5: 仕上げ（M9・L4・L5・S7）と音ハメ（M1）・周波数帯ごとの音反応（M2）。"""

import shutil
import subprocess
import time

import numpy as np
import pytest

from video_automation.schemas.design import Component, Purpose
from video_automation.services.design.profiles import PROFILES
from video_automation.services.rendering import finish, sync

FFMPEG = shutil.which("ffmpeg")

# ---------------- 仕上げ ----------------


def test_finish_components_by_purpose():
    """2.3 の「使ってよい新しい演出」の表の用途だけに付ける。"""
    grain = {p for p, x in PROFILES.items() if x.allows(Component.GRAIN)}
    glow = {p for p, x in PROFILES.items() if x.allows(Component.GLOW)}
    hal = {p for p, x in PROFILES.items() if x.allows(Component.HALATION)}
    assert grain == {
        Purpose.VLOG,
        Purpose.PROMO,
        Purpose.EVENT,
        Purpose.MV,
        Purpose.LYRIC,
        Purpose.TRAILER,
    }
    assert glow == {Purpose.PROMO, Purpose.MV, Purpose.LYRIC, Purpose.TRAILER}
    assert hal == {Purpose.TRAILER}


def test_finish_chain_contents():
    assert finish.finish_chain(None, 1920, 1080) == ""
    ex = finish.finish_chain(PROFILES[Purpose.EXPLAINER], 1920, 1080)
    assert ex == finish.DITHER  # S7 だけ（グレインを掛けない用途）
    tr = finish.finish_chain(PROFILES[Purpose.TRAILER], 1920, 1080)
    assert "gblur=sigma=4.0" in tr and "gblur=sigma=48.0" in tr  # L4 の 8・96px
    assert "colorchannelmixer=rr=1:gg=0.35:bb=0.2" in tr  # L5
    assert "noise=c0s=10" in tr and "vignette" in tr and "mergeplanes" in tr  # M9
    assert finish.DITHER not in tr  # グレインを掛ける用途では S7 を掛けない
    assert finish.applied(PROFILES[Purpose.TRAILER]) == [
        "グロー",
        "ハレーション",
        "グレイン・周辺減光・色収差",
    ]


def _frame(chain: str, src: str, w: int, h: int) -> np.ndarray:
    out = subprocess.run(
        [
            FFMPEG,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            src,
            "-filter_complex",
            f"[0:v]{chain},format=rgb24[o]",
            "-map",
            "[o]",
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-",
        ],
        capture_output=True,
        check=True,
    ).stdout
    return np.frombuffer(out, np.uint8).reshape(h, w, 3).astype(int)


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg")
def test_aberration_shifts_edges_not_center():
    """色収差: 端で赤と青がずれ、中央はずれない。"""
    w, h = 640, 360
    src = f"color=c=black:s={w}x{h},drawbox=x=0:y=0:w=600:h={h}:color=white:t=fill"
    img = _frame(finish.aberration(w, h), src, w, h)
    # 右寄りの縦の境目（x=600）: 赤は外へ広がり（右へはみ出す）、青は内へ縮む
    row = img[h // 2]
    assert row[600, 0] > 100 and row[600, 2] < 100
    assert row[599, 2] < 100 or row[598, 2] < 100
    plain = _frame("null", src, w, h)
    assert abs(img[h // 2, 160] - plain[h // 2, 160]).max() <= 2  # 白の内側は変わらない


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg")
def test_glow_spreads_only_from_bright_luma():
    """L4: 白い所だけがにじむ。原色の赤（輝度が低い）はにじまない。"""
    w, h = 320, 180
    white = "color=c=black:s=320x180,drawbox=x=150:y=80:w=20:h=20:color=white:t=fill"
    red = "color=c=black:s=320x180,drawbox=x=150:y=80:w=20:h=20:color=red:t=fill"
    g = finish.glow(h)
    a = _frame(g, white, w, h)
    b = _frame(g, red, w, h)
    assert a[90, 143].max() > 5  # 白の周り（7px 離れた所）が明るい
    assert b[90, 143].max() <= 2


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg")
def test_trailer_chain_runs_and_load():
    """負荷: 1080p・2 秒で全部の仕上げを通しても、極端に遅くならない。"""
    ch = finish.finish_chain(PROFILES[Purpose.TRAILER], 1920, 1080)
    t0 = time.perf_counter()
    subprocess.run(
        [
            FFMPEG,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=1920x1080:d=2:r=30",
            "-filter_complex",
            f"[0:v]{ch}[o]",
            "-map",
            "[o]",
            "-f",
            "null",
            "-",
        ],
        check=True,
    )
    assert time.perf_counter() - t0 < 60


# ---------------- 音ハメ・音反応 ----------------


def _song(sec: float = 30.0, bpm: float = 120.0, sr: int = sync.SR) -> tuple[np.ndarray, list]:
    t = np.arange(int(sr * sec)) / sr
    x = np.zeros_like(t)
    beat = 60 / bpm
    beats = [k * beat for k in range(int(sec / beat))]
    rng = np.random.default_rng(0)
    n = int(0.1 * sr)
    for k, b in enumerate(beats):
        s = int(b * sr)
        x[s : s + n] += np.sin(2 * np.pi * 60 * t[:n]) * np.exp(-t[:n] * 30)  # キック
        if k % 2:
            x[s : s + n] += 0.2 * rng.standard_normal(n) * np.exp(-t[:n] * 60)
    return x.astype(np.float32), beats


def test_band_curves_ranges_and_release():
    x, _ = _song()
    c = sync.band_curves(x, sync.SR, 30.0)
    assert set(c) == {"kick", "snare", "hihat"}
    assert len(c["kick"]) == 900
    assert min(c["kick"]) >= 1.0 and max(c["kick"]) == pytest.approx(1.02)
    assert min(c["snare"]) >= 1.0 and max(c["snare"]) <= 1.08
    assert min(c["hihat"]) >= 0.3 and max(c["hihat"]) <= 0.8
    # 下がるときは 150ms（4.5f）で戻る: 1f の下がり幅は最大 1/4.5
    k = np.array(c["kick"])
    drop = (k[:-1] - k[1:]) / 0.02
    assert drop.max() <= 1 / 4.5 + 0.01  # 値は小数 4 桁に丸める
    assert sync.band_curves(x, sync.SR, 30.0, ("kick",)).keys() == {"kick"}


def test_hits_bars_peaks_consonants():
    x, beats = _song()
    x[sync.SR * 12 : sync.SR * 12 + 3000] += 2.0  # 大きな山
    h = sync.hits(x, sync.SR, 30.0, beats, [3.0, 7.0, 12.0, 20.0, 25.0])
    assert h.bars == [round(b * 30) for b in beats[::4]]  # 1 小節に 1 回
    assert len(h.peaks) <= 8 and any(abs(p - 360) <= 2 for p in h.peaks)
    assert all(b - a >= 60 for a, b in zip(h.peaks, h.peaks[1:], strict=False))  # 2 秒以上離す
    assert len(h.consonants) <= 3
    assert all(any(abs(c - round(t * 30)) <= 2 for t in (3, 7, 12, 20, 25)) for c in h.consonants)
    assert sync.hits(np.zeros(1000, np.float32), sync.SR, 30.0, [], None).peaks == []


def test_sync_load_long_song_is_fast():
    """負荷: 4 分の曲でも数秒で終わる（分けて計算する）。"""
    x, beats = _song(240.0)
    t0 = time.perf_counter()
    sync.band_curves(x, sync.SR, 30.0)
    sync.hits(x, sync.SR, 30.0, beats, [float(i) for i in range(200)])
    assert time.perf_counter() - t0 < 20


BPY = __import__("os").environ.get("VA_BPY_PYTHON")


@pytest.mark.skipif(not BPY or FFMPEG is None, reason="VA_BPY_PYTHON is not set")
def test_rich_direction_beat_pulse_uses_m1_and_kick(tmp_path):
    """Blender: 拍頭・ピークの脈動と、キックの拡大を画面全体の調整レイヤーに付ける。"""
    import json
    from pathlib import Path

    clean = tmp_path / "clean.mp4"
    subprocess.run(
        [
            FFMPEG,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=320x180:d=3:r=30",
            "-f",
            "lavfi",
            "-i",
            "sine=d=3",
            "-shortest",
            "-pix_fmt",
            "yuv420p",
            str(clean),
        ],
        check=True,
    )
    kick = [1.0 + 0.02 * (i % 15 == 0) for i in range(90)]
    args = {
        "clean_path": str(clean),
        "out_path": str(tmp_path / "out.mp4"),
        "result_path": str(tmp_path / "r.json"),
        "font": "",
        "width": 320,
        "height": 180,
        "fps_num": 30,
        "fps_den": 1,
        "title": "",
        "catch": "",
        "chapters": [],
        "cues": [],
        "beats": [],
        "bars": [15, 45, 75],
        "peaks": [30],
        "consonants": [],
        "kick": kick,
        "parts": {"beat_pulse": True},
        "params": {},
        "limits": {},
        "colors": {"base": "#000000", "main": "#3366CC", "accent": "#FFCC00", "text": "#FFFFFF"},
    }
    (tmp_path / "a.json").write_text(json.dumps(args), encoding="utf-8")
    script = Path(__file__).resolve().parents[1] / (
        "src/video_automation/integrations/blender/scripts/rich_direction.py"
    )
    r = subprocess.run(
        [BPY, str(script), "--", str(tmp_path / "a.json")],
        capture_output=True,
        text=True,
        timeout=600,
    )
    rep = json.loads((tmp_path / "r.json").read_text())
    assert rep["parts"]["beat_pulse"]["ok"], r.stderr[-2000:]
    assert rep["used"]["beat_cut"] == 4  # 拍頭 3 + ピーク 1
    assert (tmp_path / "out.mp4").is_file()


# ---------------- 歌詞の置き方（M3）・ジャンル（M7） ----------------


def test_lyric_sections():
    from video_automation.services.rendering import lyrics

    v = ["朝が来る", "窓を開ける", "風が吹く", "君を思う"]
    c = ["夢の中へ", "光を追って", "涙を越えて", "明日へ行こう"]
    txt = v + ["空を見上げ", "声がする"] + c + v[:2] + ["もう一度", "はじまりの歌"] + c + ["間"] + c
    s = lyrics.sections(txt)
    assert s[:4] == ["verse"] * 4  # 2 行だけのくり返し（朝が来る・窓を開ける）はサビにしない
    assert s[4:6] == ["bridge", "bridge"] and s[6] == "chorus_first"
    assert s[7:10] == ["chorus"] * 3 and s[10:12] == ["verse", "verse"]
    rms = [1.0] * len(txt)
    for i in range(len(txt) - 4, len(txt)):
        rms[i] = 0.2
    assert lyrics.sections(txt, rms)[-4:] == ["drop"] * 4  # 静かな 2 回目以降のサビ
    assert lyrics.sections(["a", "b", "c"]) == ["verse"] * 3
    assert lyrics.particle_spans("夢の中へ光を追って") == [(1, 2), (3, 4), (5, 6)]
    assert lyrics.genre_style(None) == ("M PLUS Rounded 1c", "Bold", "matte")
    assert lyrics.genre_style("edm")[2] == "glow"


def test_lyric_sections_load():
    """負荷: 200 行の歌詞でも 1 秒以内。"""
    from video_automation.services.rendering import lyrics

    txt = [f"行{i % 37}" for i in range(200)]
    t0 = time.perf_counter()
    lyrics.sections(txt)
    assert time.perf_counter() - t0 < 1.0


@pytest.mark.skipif(not BPY or FFMPEG is None, reason="VA_BPY_PYTHON is not set")
def test_rich_direction_lyrics_sections_and_noise_shake(tmp_path):
    """Blender: サビは斜め・A メロは中央（M3）、影の質感（M7）、コマ打ち（M5）、揺れは Noise（E5）。"""
    import json
    from pathlib import Path

    clean = tmp_path / "clean.mp4"
    subprocess.run(
        [
            FFMPEG,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=320x180:d=3:r=30",
            "-f",
            "lavfi",
            "-i",
            "sine=d=3",
            "-shortest",
            "-pix_fmt",
            "yuv420p",
            str(clean),
        ],
        check=True,
    )
    cues = [
        {"segment": "a", "text": "夢の中へ", "start_frame": 2, "end_frame": 30, "section": "verse"},
        {
            "segment": "b",
            "text": "100%の光",
            "start_frame": 35,
            "end_frame": 80,
            "section": "chorus",
        },
    ]
    args = {
        "clean_path": str(clean),
        "out_path": str(tmp_path / "out.mp4"),
        "result_path": str(tmp_path / "r.json"),
        "font": "",
        "width": 320,
        "height": 180,
        "fps_num": 30,
        "fps_den": 1,
        "title": "",
        "catch": "",
        "chapters": [],
        "cues": cues,
        "beats": [],
        "parts": {"lyrics": True, "punch": True},
        "params": {},
        "limits": {},
        "colors": {"base": "#000000", "main": "#3366CC", "accent": "#FFCC00", "text": "#FFFFFF"},
        "stepped": 2,
        "lyric_font": "",
        "lyric_texture": "shadow",
    }
    (tmp_path / "a.json").write_text(json.dumps(args), encoding="utf-8")
    script = Path(__file__).resolve().parents[1] / (
        "src/video_automation/integrations/blender/scripts/rich_direction.py"
    )
    r = subprocess.run(
        [BPY, str(script), "--", str(tmp_path / "a.json")],
        capture_output=True,
        text=True,
        timeout=900,
    )
    rep = json.loads((tmp_path / "r.json").read_text())
    assert rep["parts"]["lyrics"]["ok"], r.stderr[-2000:]
    assert rep["parts"]["punch"]["ok"], r.stderr[-2000:]
    assert rep["sections"] == {"verse": 1, "chorus": 1}
    assert rep["used"]["shake_punch"] == 1
