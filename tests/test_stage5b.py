"""段階5b: MV のショットとカット（V1・V2・V3・V7・V8・V12）。"""

from collections import Counter

import numpy as np
from PIL import Image

from video_automation.services.direction import mv_shots as mv


def _spans():
    starts = [10, 14, 18, 22, 26, 30, 34, 38, 42, 46]
    secs = ["verse", "verse", "bridge", "bridge", "chorus_first"] + ["chorus"] * 3 + ["verse"] * 2
    return mv.line_spans(starts, [s + 3 for s in starts], secs, 2.0)


def test_plan_cuts_density_by_section():
    beats = [i * 0.5 for i in range(240)]  # 120 BPM
    cuts = mv.plan_cuts(beats, _spans(), 120, 30)
    assert cuts[0].start == 0 and cuts[-1].end == 3600
    assert all(a.end == b.start for a, b in zip(cuts, cuts[1:], strict=False))
    # イントロ（0〜10 秒）は 2 拍（30f）ごと、最後の 1 小節は 1 拍
    intro = [c for c in cuts if c.end <= 240]
    assert {c.end - c.start for c in intro} == {30}
    # サビ（26 秒〜）の最初は 1 拍（15f）のカット
    ch = [c for c in cuts if 780 <= c.start < 1260]
    assert Counter(c.end - c.start for c in ch).most_common(1)[0][0] == 15
    assert ch[0].shot == "face"
    assert {c.shot for c in cuts} <= set(mv.SHOTS)


def test_fast_song_doubles_one_beat():
    beats = [i * 0.3 for i in range(400)]  # BPM 200（1 拍 0.3 秒 < 0.34）
    cuts = mv.plan_cuts(beats, _spans(), 120, 30)
    ch = [c for c in cuts if 780 <= c.start < 1260]
    assert min(c.end - c.start for c in ch) >= 18  # 2 拍（0.6 秒）より短いカットは無い


def test_flashes_one_per_second():
    cuts = [mv.Cut(0, 300, "full")]
    out = mv.insert_flashes(cuts, [30, 40, 100], 30)
    fl = [c for c in out if c.shot == "flash"]
    assert [(c.start, c.end) for c in fl] == [(30, 32), (100, 102)]
    assert out[0].end == 30 and out[-1].end == 300


def _chara(alpha: bool) -> Image.Image:
    im = Image.new("RGBA", (200, 300), (0, 0, 0, 0) if alpha else (40, 60, 90, 255))
    im.paste((230, 120, 150, 255), (50, 30, 150, 290))
    return im


def test_shot_images():
    for alpha in (True, False):
        src = _chara(alpha)
        assert mv.has_cutout(src) is alpha
        for shot in mv.SHOTS:
            im = mv.shot_image(src, shot, 320, 180, "#FFC82E", "#111111", 1)
            assert im.size == (320, 180)
    sil = mv.shot_image(_chara(True), "silhouette", 320, 180, "#FFC82E", "#111111")
    assert sil.getpixel((2, 2))[:3] == (255, 255, 255) and sil.getpixel((160, 90))[:3] == (
        17,
        17,
        17,
    )
    cut = mv.shot_image(_chara(True), "cutout", 320, 180, "#FFC82E", "#111111")
    assert cut.getpixel((2, 2))[:3] == (255, 200, 46)  # V12 の主色の面


def test_flash_keeps_brightness():
    a = np.random.default_rng(0).integers(0, 255, (40, 60, 3), dtype=np.uint8)
    im = Image.fromarray(a, "RGB")
    v0 = np.asarray(im.convert("HSV"))[..., 2]
    v1 = np.asarray(mv.hue_rotate(im).convert("HSV"))[..., 2]
    assert np.abs(v0.astype(int) - v1.astype(int)).max() <= 2
    ps = np.asarray(mv.pixel_sort(im))
    assert sorted(map(tuple, ps.reshape(-1, 3))) == sorted(map(tuple, a.reshape(-1, 3)))


def test_camera():
    assert [k.zoom for k in mv.camera("full", 60, 30)] == [1.0, 1.06]
    assert mv.camera("full", 60, 30)[1].frame == 24
    assert [k.pan_x for k in mv.camera("upper", 60, 30)] == [-1.0, 1.0]
    assert mv.camera("eye", 60, 30) == [] and mv.camera("face", 1, 30) == []


def test_letterbox():
    assert mv.letterbox_bar(1920, 1080) == 138  # (1080 − 1920 / 2.39) / 2
    spans = [(10, 20, "verse"), (20, 30, "chorus_first"), (30, 40, "drop"), (40, 50, "chorus")]
    assert mv.letterbox_spans(spans, 60) == [(0.0, 20), (30, 40)]
    assert mv.letterbox_spans([(0, 10, "verse")], 60) == []


def test_film_light_and_rim():
    """V4: 右上が明るくなり、切り抜きの縁が明るくなる。"""
    im = Image.new("RGBA", (320, 180), (60, 60, 60, 255))
    out = mv.film(im)
    assert out.getpixel((315, 5))[0] > out.getpixel((5, 175))[0]  # 右上の光
    a = Image.new("L", (320, 180), 0)
    a.paste(255, (100, 40, 220, 160))
    rim = mv.film(im, a)
    assert rim.getpixel((101, 100))[0] > mv.film(im).getpixel((101, 100))[0]  # 縁の帯


def test_paper_texture_only_for_mv():
    """V9: MV・リリックだけ、紙の質感を乗算 10% で重ねて 2f ごとにずらす。"""
    import shutil
    import subprocess

    from video_automation.schemas.design import Purpose
    from video_automation.services.design.profiles import PROFILES
    from video_automation.services.rendering import finish

    mvc = finish.finish_chain(PROFILES[Purpose.MV], 640, 360)
    assert "all_mode=multiply:all_opacity=0.1" in mvc and "floor(n/2)" in mvc
    assert "multiply" not in finish.finish_chain(PROFILES[Purpose.EXPLAINER], 640, 360)
    if shutil.which("ffmpeg"):
        out = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=white:s=640x360:d=1:r=30",
                "-filter_complex",
                f"[0:v]{finish.paper(640, 360)}[o]",
                "-map",
                "[o]",
                "-frames:v",
                "4",
                "-pix_fmt",
                "rgb24",
                "-f",
                "rawvideo",
                "-",
            ],
            capture_output=True,
            check=True,
        ).stdout
        f = np.frombuffer(out, np.uint8).reshape(4, 360, 640, 3).astype(int)
        assert 230 < f.mean() < 255  # 白が少しだけ暗くなる
        assert (f[0] == f[1]).all() and not (f[1] == f[2]).all()  # 2f ごとにずれる
