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
            "testsrc2=s=320x180:d=4:r=30",
            "-f",
            "lavfi",
            "-i",
            "sine=d=4",
            "-shortest",
            "-pix_fmt",
            "yuv420p",
            str(clean),
        ],
        check=True,
    )
    cues = [
        {
            "segment": "a",
            "text": "夢の中へ",
            "start_frame": 2,
            "end_frame": 30,
            "section": "verse",
            "motion": ["zoom", "breathe", "shrink"],
        },
        {
            "segment": "b",
            "text": "100%の光",
            "start_frame": 35,
            "end_frame": 80,
            "section": "chorus",
            "motion": ["neon", "sway", "flicker"],
        },
        {
            "segment": "c",
            "text": "ひとりで",
            "start_frame": 82,
            "end_frame": 118,
            "section": "drop",
            "motion": ["tilt3d", "static", "fall_back"],
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
    assert rep["sections"] == {"verse": 1, "chorus": 1, "drop": 1}
    assert rep["motion"] == {  # V5 の改訂
        "zoom": 1,
        "breathe": 1,
        "shrink": 1,
        "neon": 1,
        "sway": 1,
        "flicker": 1,
        "tilt3d": 1,
        "static": 1,
        "fall_back": 1,
    }
    assert rep["used"]["shake_punch"] == 1


def test_lyric_motion_table():
    """V5 の改訂: 上の行ほど優先して、行ごとの入り・待機・出を 1 つに決める。"""
    from video_automation.services.rendering import lyrics

    v = ["朝が来る", "窓を開けた", "風が吹いて", "君を思うよ！"]
    c = ["夢の中へ", "光を追って", "涙を越えて", "明日へ行こう"]
    txt = (
        v
        + ["空を見上げ", "声がする"]
        + c
        + ["もう一度", "はじまりの歌"]
        + c
        + ["さよならさよなら", "ラララ〜"]
    )
    s = lyrics.sections(txt)
    m = lyrics.lyric_motion(txt, s, "ballad")
    assert m[0] == ("zoom", "breathe", "blur_out")  # A メロ・バラードの待機は呼吸
    assert m[3] == ("shake", "static", "flicker")  # 「！」で終わる行
    assert m[4] == ("stretch", "breathe", "stretch_out") and m[5][0] == "curve"  # B メロ
    assert m[6][0] == "circle" and m[9][0] == "gather"  # 最初のサビの 1 行目・最後の行
    assert m[12][0] == "slam"  # ラストサビの 1 行目
    assert m[16][0] == "stack" and m[17][0] == "track"  # くり返す語・アウトロの最後
    assert lyrics.lyric_motion(["あ", "い"], ["verse"] * 2, "rock")[0][1] == "static"


@pytest.mark.skipif(not BPY, reason="VA_BPY_PYTHON is not set")
def test_title_gloss_sweep(tmp_path):
    """統合版 L7: 入りの後、題名の文字の面を斜めの白い帯が左から右へ 1 回通る。"""
    import json
    import subprocess
    from pathlib import Path

    import numpy as np
    from PIL import Image

    font = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
    args = {
        "text": "光の題名",
        "width": 320,
        "height": 180,
        "fps": 30,
        "frames": 12,
        "font_path": font if Path(font).is_file() else None,
        "text_color": [0.05, 0.2, 0.6, 1],
        "outline": False,
        "outline_color": [0, 0, 0, 1],
        "size": 1.0,
        "position": "center",
        "output_dir": str(tmp_path / "out"),
        "animation": "none",
        "gloss_at": 2,
        "gloss_frames": 10,
        "result_path": str(tmp_path / "r.json"),
        "samples": 1,
    }
    (tmp_path / "out").mkdir()
    (tmp_path / "a.json").write_text(json.dumps(args), encoding="utf-8")
    script = Path(__file__).resolve().parents[1] / (
        "src/video_automation/integrations/blender/scripts/title_card.py"
    )
    r = subprocess.run(
        [BPY, str(script), "--", str(tmp_path / "a.json")], capture_output=True, text=True
    )
    assert r.returncode == 0, r.stderr[-2000:]
    frames = sorted((tmp_path / "out").glob("frame_*.png"))
    assert len(frames) == 12

    def bright_x(p):  # 文字の中で最も明るい列の x（文字の無い所は除く）
        a = np.asarray(Image.open(p).convert("RGBA")).astype(float)
        m = a[..., 3] > 200
        lum = (a[..., :3].sum(axis=-1) * m).sum(axis=0) / np.maximum(m.sum(axis=0), 1)
        lum[m.sum(axis=0) < 3] = 0
        return int(np.argmax(lum)), float(lum.max())

    base = bright_x(frames[0])[1]
    xs = [bright_x(f) for f in frames[3:9]]
    assert all(v > base * 1.15 for _, v in xs[1:-1])  # 帯が通っている間は明るい
    assert xs[0][0] < xs[-1][0]  # 左から右へ
    assert abs(bright_x(frames[-1])[1] - base) < base * 0.1  # 通り過ぎたら元の色


@pytest.mark.skipif(not BPY, reason="VA_BPY_PYTHON is not set")
def test_title_sun_beams(tmp_path):
    """統合版 L3: 予告編の題名から下へ光の筋（透過の書き出しで、筋の所も半透明で残る）。"""
    import json
    import subprocess
    from pathlib import Path

    import numpy as np
    from PIL import Image

    font = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
    out = {}
    for beams in (False, True):
        d = tmp_path / str(beams)
        d.mkdir()
        args = {
            "text": "終章",
            "width": 320,
            "height": 180,
            "fps": 30,
            "frames": 1,
            "font_path": font if Path(font).is_file() else None,
            "text_color": [0.95, 0.9, 0.8, 1],
            "outline": False,
            "outline_color": [0, 0, 0, 1],
            "size": 1.0,
            "position": "center",
            "output_dir": str(d),
            "animation": "none",
            "sun_beams": beams,
            "result_path": str(d / "r.json"),
            "samples": 1,
        }
        (d / "a.json").write_text(json.dumps(args), encoding="utf-8")
        script = Path(__file__).resolve().parents[1] / (
            "src/video_automation/integrations/blender/scripts/title_card.py"
        )
        r = subprocess.run(
            [BPY, str(script), "--", str(d / "a.json")], capture_output=True, text=True
        )
        assert r.returncode == 0, r.stderr[-2000:]
        a = np.asarray(Image.open(d / "frame_0001.png").convert("RGBA"))
        out[beams] = a[..., 3]
    below = slice(140, 180)  # 文字より下
    assert out[False][below].max() == 0
    assert (out[True][below] > 10).mean() > 0.05  # 下へ光の筋が出る


@pytest.mark.skipif(not BPY, reason="VA_BPY_PYTHON is not set")
def test_liquid_background_loops(tmp_path):
    """統合版 M6: メタボールの液体の背景が描かれ、フレームごとに動く。"""
    import json
    import subprocess
    from pathlib import Path

    import numpy as np
    from PIL import Image

    args = {
        "style": "liquid",
        "width": 160,
        "height": 90,
        "fps": 30,
        "frames": 6,
        "colors": {
            "base": [0, 0, 0, 1],
            "base2": [0.1, 0.1, 0.1, 1],
            "main": [0.9, 0.4, 0.1, 1],
            "accent": [0.1, 0.6, 0.9, 1],
        },
        "output_dir": str(tmp_path / "o"),
        "result_path": str(tmp_path / "r.json"),
    }
    (tmp_path / "a.json").write_text(json.dumps(args), encoding="utf-8")
    script = Path(__file__).resolve().parents[1] / (
        "src/video_automation/integrations/blender/scripts/motion_bg.py"
    )
    r = subprocess.run(
        [BPY, str(script), "--", str(tmp_path / "a.json")], capture_output=True, text=True
    )
    assert r.returncode == 0, r.stderr[-2000:]
    fr = [
        np.asarray(Image.open(p).convert("RGBA")).astype(int)
        for p in sorted((tmp_path / "o").glob("*.png"))
    ]
    assert len(fr) == 6 and all((f[..., 3] > 0).mean() > 0.03 for f in fr)
    assert np.abs(fr[0] - fr[3]).mean() > 1  # 動いている


def test_title_entrance_by_purpose():
    """統合版 T16: 用途ごとの題名の入り。1 文字ずつの入りは改行の無い短い題名だけ。"""
    from video_automation.schemas.design import Purpose
    from video_automation.schemas.timeline import TitleCardRequest
    from video_automation.services.design.profiles import PROFILES
    from video_automation.services.direction.service import title_entrance
    from video_automation.services.rendering.title_card import TitleCardService

    want = {
        Purpose.GAME: "decode",
        Purpose.DOCUMENTARY: "typewriter",
        Purpose.CORPORATE: "tracking",
        Purpose.RANKING: "flip3d",
        Purpose.NEWS: "mask_rise",
        Purpose.SHORT: "slam",
        Purpose.LECTURE: "stroke",
        Purpose.VLOG: "slide",
        Purpose.EXPLAINER: "pop",
        Purpose.SUBTITLE: "fade",
    }
    for p, a in want.items():
        assert title_entrance(PROFILES[p], "短い題名", False) == a
    assert title_entrance(PROFILES[Purpose.GAME], "二行の\n題名", False) == "pop"
    assert title_entrance(PROFILES[Purpose.GAME], "あ" * 30, False) == "pop"
    assert title_entrance(PROFILES[Purpose.MV], "曲名", True) == "bounce"
    req = TitleCardRequest(project_id="p", text="題名五文字", animation="decode", duration_sec=3)
    assert abs(TitleCardService.animation_seconds(req) - 9 / 30) < 1e-9
    req = req.model_copy(update={"animation": "typewriter"})
    assert TitleCardService.animation_seconds(req) == 3  # カーソルが最後まで点滅する


@pytest.mark.skipif(not BPY, reason="VA_BPY_PYTHON is not set")
def test_title_entrances_render(tmp_path):
    """統合版 T5・T6・T7・T9・T10・T17・T4: 入りの途中は欠け、終わりは全部の文字がそろう。"""
    import json
    import subprocess
    from pathlib import Path

    import numpy as np
    from PIL import Image

    font = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
    script = Path(__file__).resolve().parents[1] / (
        "src/video_automation/integrations/blender/scripts/title_card.py"
    )
    ink = {}
    for anim, frames in (("decode", 9), ("mask_rise", 16), ("tracking", 19), ("slam", 16)):
        d = tmp_path / anim
        (d / "out").mkdir(parents=True)
        args = {
            "text": "題名です",
            "width": 320,
            "height": 180,
            "fps": 30,
            "frames": frames,
            "font_path": font if Path(font).is_file() else None,
            "text_color": [1, 1, 1, 1],
            "outline": False,
            "outline_color": [0, 0, 0, 1],
            "size": 1.0,
            "position": "center",
            "output_dir": str(d / "out"),
            "animation": anim,
            "result_path": str(d / "r.json"),
            "samples": 1,
        }
        (d / "a.json").write_text(json.dumps(args), encoding="utf-8")
        r = subprocess.run(
            [BPY, str(script), "--", str(d / "a.json")], capture_output=True, text=True
        )
        assert r.returncode == 0, r.stderr[-2000:]
        fs = sorted((d / "out").glob("frame_*.png"))
        assert len(fs) == frames
        alpha = [np.asarray(Image.open(f).convert("RGBA"))[..., 3].astype(float).sum() for f in fs]
        ink[anim] = alpha
    for anim in ("decode", "mask_rise", "tracking"):
        a = ink[anim]
        assert a[0] < a[-1] * 0.6, anim  # 最初は欠けている（隠れている・薄い）
    # 叩きつける: 最初は 3 倍で画面からはみ出すほど大きく、着地後は元の大きさ
    assert ink["slam"][0] > ink["slam"][-1] * 2
    assert abs(ink["decode"][-1] - ink["mask_rise"][-1]) < ink["decode"][-1] * 0.05


def test_cut_transition_filters_and_overlays():
    """統合版 E7・E8: 次のショットの入り。E6・E9・E10: 重ねる透過の動画。"""
    from fractions import Fraction

    from video_automation.schemas.animation import ClipAnimation, Transition, TransitionType
    from video_automation.services.animation.render_anim import clip_filters
    from video_automation.services.direction import transitions as tz

    def f(t):
        anim = ClipAnimation(transition_in=Transition(type=t, duration_frames=6))
        return clip_filters(
            anim, fps=Fraction(30), k=0.0, off=0.0, clip_sec=3.0, src_size=(640, 360),
            canvas=(640, 360), is_image=True,
        )  # fmt: skip

    whip = f(TransitionType.WHIP)
    assert whip.dx and not whip.post  # 右から飛び込む（不透明度は変えない）
    punch = f(TransitionType.ZOOM_PUNCH)
    assert any("0.7+0.3" in x for x in punch.resize) and not punch.post
    page = f(TransitionType.PAGE_TURN)
    assert any("X/W+Y/H" in x for x in page.post)  # 右下の角から左上へ現れる
    cube = f(TransitionType.CUBE)
    assert cube.dx and not cube.post
    fold = tz.page_fold(64, 36, "#F4EFE4")
    assert len(fold) == tz.OVERLAY["E11"] and fold[-1].getchannel("A").getextrema()[1] == 0
    assert max(im.getchannel("A").getextrema()[1] for im in fold) == 255
    fr = tz.shape_wipe(64, 36, ["#ff0000", "#00ff00", "#0000ff"])
    assert len(fr) == tz.OVERLAY["E6"]
    alpha = [im.getchannel("A").getextrema()[1] for im in fr]
    assert alpha[0] == 0 and alpha[-1] == 0  # 始まりと終わりは透明
    full = fr[tz.CUT_AT["E6"]].getchannel("A")
    assert full.getextrema()[0] == 255  # 下の画が替わるフレームは全面を覆う
    assert len(tz.light_leak(64, 36)) == 12 and len(tz.glitch(64, 36, [], 1)) == 6
    assert tz.pick_cuts([10, 600, 1300, 1900, 1950], 30, 3000, "explainer") == [10, 1900]


def test_effect_frames():
    """統合版 X1〜X3・X5・X6 の長さと、透明で終わること。"""
    from video_automation.services.direction import effects as fx

    c = fx.confetti(96, 54, ["#ff0000", "#00ff00"])
    assert len(c) == 45 and c[-1].getchannel("A").getextrema()[1] == 0  # 45f で消える
    s = fx.sparkle(20)
    assert len(s) == 10 and s[0].getchannel("A").getextrema()[1] == 0
    assert len(fx.focus_lines(96, 54)) == 6
    sm, em = fx.smoke(96, 54), fx.embers(96, 54)
    assert len(sm) == len(em) == 150


def test_number_roll_and_year():
    """統合版 T35（左の桁が 12f、右へ 3f ずつ遅れて止まる）・T43（年号）。"""
    from video_automation.services.rendering import numbers as nb

    assert nb.find_year("1964年の東京で") == "1964" and nb.find_year("3000年") is None
    assert nb.roll_text("2024", 12)[0] == "2" and nb.roll_text("2024", 12)[1] != "0"
    assert nb.roll_text("2024", 21) == "2024"
    assert nb.roll_text("2024", 11)[0] == "1"  # 止まる 1 フレーム前は 1 つ手前の数字
    assert nb.roll_text("1,234円", 30) == "1,234円"  # カンマ・単位は回さない
    assert nb.roll_frames_count("2024") == 22
    fr = nb.year_frames("2024", 360, None, "#FFFFFF", "#000000", True)
    assert len(fr) == 22 and fr[0].size == fr[-1].size
    assert len(nb.year_frames("2024", 360, None, "#FFFFFF", "#000000", False)) == 1


@pytest.mark.skipif(not BPY or FFMPEG is None, reason="VA_BPY_PYTHON is not set")
def test_rich_direction_more_lyric_motions(tmp_path):
    """Blender: V5 の改訂の残りの入り・待機・出（行全体の動きで近似）が使われる。"""
    import json
    from pathlib import Path

    clean = tmp_path / "clean.mp4"
    subprocess.run(
        [FFMPEG, "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=320x180:d=7:r=30", "-f",
         "lavfi", "-i", "sine=d=7", "-shortest", "-pix_fmt", "yuv420p", str(clean)],
        check=True,
    )  # fmt: skip
    motions = [
        ["track", "pulse", "blur_out"],
        ["glitch_in", "wave", "glitch_out"],
        ["curve", "static", "curve_out"],
        ["circle", "drift", "scatter"],
        ["warp_in", "breathe", "warp_out"],
        ["gather", "static", "fall"],
        ["stack", "static", "rise_out"],  # 並べて動かす（同じ行を縦に 3 つ）
        ["fade_up", "outline_light", "fade"],  # T19 輪郭を走る光
        ["morph", "static", "shrink"],  # T23 前の行（同じ字数）の形から変形
    ]
    cues = [
        {"segment": str(i), "text": "そら", "start_frame": 2 + 19 * i,
         "end_frame": 19 + 19 * i, "section": "verse", "motion": m}
        for i, m in enumerate(motions)
    ]  # fmt: skip
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
        "beats": list(range(0, 120, 15)),
        "parts": {"lyrics": True},
        "params": {},
        "limits": {},
        "colors": {"base": "#000000", "main": "#3366CC", "accent": "#FFCC00", "text": "#FFFFFF"},
        "lyric_font": "",
        "lyric_texture": "shadow",
        "plan_only": True,
    }
    (tmp_path / "a.json").write_text(json.dumps(args), encoding="utf-8")
    script = Path(__file__).resolve().parents[1] / (
        "src/video_automation/integrations/blender/scripts/rich_direction.py"
    )
    r = subprocess.run([BPY, str(script), "--", str(tmp_path / "a.json")], capture_output=True,
                       text=True, timeout=900)  # fmt: skip
    rep = json.loads((tmp_path / "r.json").read_text())
    assert rep["parts"]["lyrics"]["ok"], r.stderr[-2000:]
    got = rep["motion"]
    for m in motions:
        for name in m:
            assert got.get(name, 0) >= 1, (name, got)
    assert rep.get("morphed") == 1, rep  # T23: 字数の同じ前の行から変形した
    # 穴・離れた部分（「ら」の上の点）も、縮めて消す線・広げる線として形に入れる（以前は外側の線
    # だけで、変形の間は穴が塗りつぶされた）
    assert rep.get("morph_loops", 0) > 2, rep


@pytest.mark.skipif(not BPY, reason="VA_BPY_PYTHON is not set")
def test_title_solid3d_lit(tmp_path):
    """統合版 T24・L1・L2: 立体の文字は面ごとに明るさが違い（照明）、縁にリムの青みが出る。"""
    import json
    import subprocess
    from pathlib import Path

    import numpy as np
    from PIL import Image

    font = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
    args = {
        "text": "立体",
        "width": 320,
        "height": 180,
        "fps": 30,
        "frames": 1,
        "font_path": font if Path(font).is_file() else None,
        "text_color": [0.8, 0.8, 0.8, 1],
        "outline": False,
        "outline_color": [0, 0, 0, 1],
        "size": 1.0,
        "position": "center",
        "output_dir": str(tmp_path / "out"),
        "animation": "none",
        "solid3d": True,
        "result_path": str(tmp_path / "r.json"),
        "samples": 4,
    }
    (tmp_path / "out").mkdir()
    (tmp_path / "a.json").write_text(json.dumps(args), encoding="utf-8")
    script = Path(__file__).resolve().parents[1] / (
        "src/video_automation/integrations/blender/scripts/title_card.py"
    )
    r = subprocess.run(
        [BPY, str(script), "--", str(tmp_path / "a.json")], capture_output=True, text=True
    )
    assert r.returncode == 0, r.stderr[-2000:]
    (frame,) = sorted((tmp_path / "out").glob("frame_*.png"))
    a = np.asarray(Image.open(frame).convert("RGBA")).astype(float)
    m = a[..., 3] > 200
    assert m.sum() > 500
    lum = a[..., :3].mean(axis=-1)[m]
    assert lum.std() > 12  # 平らな発光ではなく、照明で明暗がある
    blue = (a[..., 2] - a[..., 0])[m]
    assert (blue > 25).mean() > 0.01  # リム（#6FC3FF）の縁


@pytest.mark.skipif(not BPY, reason="VA_BPY_PYTHON is not set")
def test_title_vertical(tmp_path):
    """統合版 T11（Y10 の題名）: 縦書きで上から下へ並び、1 文字ずつ現れる。"""
    import json
    import subprocess
    from pathlib import Path

    import numpy as np
    from PIL import Image

    font = "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc"
    args = {
        "text": "夜明けの街",
        "width": 320,
        "height": 180,
        "fps": 30,
        "frames": 16,
        "font_path": font if Path(font).is_file() else None,
        "text_color": [0.9, 0.9, 0.9, 1],
        "outline": False,
        "outline_color": [0, 0, 0, 1],
        "size": 1.0,
        "position": "center",
        "output_dir": str(tmp_path / "out"),
        "animation": "stroke",
        "vertical": True,
        "result_path": str(tmp_path / "r.json"),
        "samples": 1,
    }
    (tmp_path / "out").mkdir()
    (tmp_path / "a.json").write_text(json.dumps(args), encoding="utf-8")
    script = Path(__file__).resolve().parents[1] / (
        "src/video_automation/integrations/blender/scripts/title_card.py"
    )
    r = subprocess.run(
        [BPY, str(script), "--", str(tmp_path / "a.json")], capture_output=True, text=True
    )
    assert r.returncode == 0, r.stderr[-2000:]
    frames = sorted((tmp_path / "out").glob("frame_*.png"))

    def box(p):
        a = np.asarray(Image.open(p).convert("RGBA"))[..., 3] > 128
        ys, xs = np.where(a)
        return (xs.min(), ys.min(), xs.max(), ys.max()) if len(xs) else None

    last = box(frames[-1])
    assert last is not None and (last[3] - last[1]) > 2.5 * (last[2] - last[0])  # 縦に長い
    early = box(frames[3])
    assert early is None or early[3] < last[3]  # 上から順に現れる


@pytest.mark.skipif(not BPY, reason="VA_BPY_PYTHON is not set")
def test_title_rack_focus(tmp_path):
    """統合版 L6: 題名の焦点を奥から文字へ 24f で送る（最初はぼけ、24f 目にはくっきり）。"""
    import json
    import subprocess
    from pathlib import Path

    import numpy as np
    from PIL import Image

    font = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
    args = {
        "text": "記録",
        "width": 320,
        "height": 180,
        "fps": 30,
        "frames": 26,
        "font_path": font if Path(font).is_file() else None,
        "text_color": [1, 1, 1, 1],
        "outline": False,
        "outline_color": [0, 0, 0, 1],
        "size": 1.0,
        "position": "center",
        "output_dir": str(tmp_path / "out"),
        "animation": "none",
        "rack_focus": True,
        "result_path": str(tmp_path / "r.json"),
        "samples": 4,
    }
    (tmp_path / "out").mkdir()
    (tmp_path / "a.json").write_text(json.dumps(args), encoding="utf-8")
    script = Path(__file__).resolve().parents[1] / (
        "src/video_automation/integrations/blender/scripts/title_card.py"
    )
    r = subprocess.run(
        [BPY, str(script), "--", str(tmp_path / "a.json")], capture_output=True, text=True
    )
    assert r.returncode == 0, r.stderr[-2000:]
    frames = sorted((tmp_path / "out").glob("frame_*.png"))
    first, last = (np.asarray(Image.open(f).getchannel("A")).astype(float) for f in
                   (frames[0], frames[-1]))  # fmt: skip
    assert (first > 250).sum() < (last > 250).sum() * 0.9  # 最初はぼけて芯が細い
    partial = lambda a: ((a > 10) & (a < 245)).sum()  # noqa: E731
    assert partial(first) > partial(last)  # ぼけの縁が広い
