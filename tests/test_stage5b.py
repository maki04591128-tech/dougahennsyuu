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


def test_ink_noise_and_frames():
    """V6: しきい値に比例して穴が広がり、穴の縁は文字の色、最初の画は前のショットのまま。"""
    noise = mv.ink_noise(160, 90, seed=1)
    assert noise.shape == (90, 160) and 0.0 <= noise.min() and noise.max() <= 1.0
    assert abs(float((noise < 0.5).mean()) - 0.5) < 0.01
    ts = mv.ink_thresholds()
    assert len(ts) == mv.INK_FRAMES and ts[0] == 0.0 and ts == sorted(ts) and ts[-1] < 1.0
    prev = Image.new("RGBA", (160, 90), (10, 200, 30, 255))
    frames = mv.ink_frames(prev, "#FFFFFF", seed=1)
    assert len(frames) == mv.INK_FRAMES
    a0 = np.asarray(frames[0])
    assert (a0[..., 3] == 255).all() and (a0[..., :3] == (10, 200, 30)).all()
    clear = [float((np.asarray(f)[..., 3] == 0).mean()) for f in frames]
    assert clear == sorted(clear) and clear[-1] > 0.99
    mid = np.asarray(frames[2])
    edge = (mid[..., 3] == 255) & (mid[..., :3] == 255).all(axis=-1)
    assert edge.any()  # 穴の縁の白い線


def test_camera_view_matches_last_frame():
    img = Image.new("RGB", (200, 100), (0, 0, 0))
    img.paste((255, 0, 0), (0, 0, 100, 100))  # 左半分が赤
    v = mv.camera_view(img, "upper", 60, 30)  # 最後は右へパン（zoom 1.04・pan_x +1）
    assert v.size == img.size and v.getpixel((0, 50))[0] == 255
    assert mv.camera_view(img, "silhouette", 60, 30) is img


def _chorus_spans():
    starts = [8, 11, 14, 17, 20, 23, 26, 29, 32, 35]
    secs = ["verse", "verse", "chorus_first", "chorus", "chorus", "chorus", "verse"]
    secs += ["chorus"] * 3
    return mv.line_spans(starts, [s + 2.5 for s in starts], secs, 2.0)


def test_motif_bars_and_planes():
    beats = [i * 0.5 for i in range(80)]
    spans = _chorus_spans()
    bars = mv.motif_bars(spans, beats, 30)
    assert bars == [(360, 405, 420), (810, 855, 870)]  # 14 秒・29 秒のサビの頭の 4 拍前
    cuts = mv.insert_flashes(mv.plan_cuts(beats, spans, 40, 30), [400, 840], 30)
    out = mv.insert_planes(cuts, bars)
    assert all(a.end == b.start for a, b in zip(out, out[1:], strict=False))
    planes = [c for c in out if c.shot == "plane"]
    assert [(c.start, c.end) for c in planes] == [(360, 420), (810, 870)]
    assert not any(
        c.shot == "flash" and (360 <= c.start < 420 or 810 <= c.start < 870) for c in out
    )
    entries = mv.chorus_entries(out, spans, beats, 30)
    assert [out[j].start for j in entries] == [420, 870]
    assert all(out[j - 1].shot == "plane" for j in entries)
    keys = mv.motif_keys(360, 405, 420, -1)
    assert keys[0].x == -mv.MOTIF_OFFSET and keys[1].frame == 45 and keys[1].x == 0.0
    assert keys[-1].frame == 59


def test_motif_shapes_mirror():
    for shape in mv.MOTIF_SHAPES:
        img = mv.motif_image(shape, "#FFFFFF", 320, 180)
        a = np.asarray(img)[..., 3]
        ys, xs = np.nonzero(a > 128)
        assert len(xs) > 200, shape
        assert (
            abs((xs.min() + xs.max()) / 2 - 160) <= 2 and abs((ys.min() + ys.max()) / 2 - 90) <= 4
        )
    tri = np.asarray(mv.motif_image("triangle", "#111111", 320, 180))[..., 3]
    mir = np.asarray(mv.motif_image("triangle", "#111111", 320, 180, mirror=True))[..., 3]
    assert (mir == tri[:, ::-1]).mean() > 0.99 and not (mir == tri).all()
    assert mv.shot_image(
        Image.new("RGB", (40, 40)), "plane", 64, 36, "#3E5C9A", "#FFFFFF"
    ).getpixel((5, 5)) == (0x3E, 0x5C, 0x9A, 255)


def test_bake_shots_frames_and_camera(tmp_path):
    """カットを 1 本の動画に焼く: フレーム数は合計＋1（丸めの余り）、色はカットの順、カメラも付く。"""
    import json
    import shutil
    import subprocess
    from fractions import Fraction

    import pytest

    from video_automation.services.direction import mv_bake

    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg が無い")
    w, h = 320, 180
    paths = []
    for i, col in enumerate([(255, 0, 0), (0, 255, 0), (0, 0, 255)]):
        p = tmp_path / f"{i}.png"
        Image.new("RGB", (w, h), col).save(p)
        paths.append(p)
    half = tmp_path / "half.png"
    img = Image.new("RGB", (w, h), (0, 0, 0))
    img.paste((255, 255, 255), (w // 2, 0, w, h))
    img.save(half)
    fps = Fraction(30)
    items = [(paths[0], 5, []), (half, 20, mv.camera("upper", 20, 30)), (paths[2], 7, [])]
    items += [(paths[1], 1, [])] * 3  # V7 のグリッドの 1 フレームのカット
    assert mv_bake.groups(85, 40) == [(0, 40), (40, 80), (80, 85)]
    parts = []
    for gi, (a, b) in enumerate([(0, 2), (2, 6)]):
        out = tmp_path / f"p{gi}.mp4"
        args = mv_bake.group_args(items[a:b], fps, w, h, out, 1 if b == 6 else 0)
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)
        parts.append(out)
    lst = tmp_path / "l.txt"
    lst.write_text(mv_bake.concat_list(parts))
    full = tmp_path / "full.mp4"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst)]
        + ["-c", "copy", str(full)],
        check=True,
    )
    info = json.loads(
        subprocess.run(
            ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0"]
            + ["-show_entries", "stream=nb_read_frames", "-of", "json", str(full)],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    assert int(info["streams"][0]["nb_read_frames"]) == 5 + 20 + 7 + 3 + 1
    raw = subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-i",
            str(full),
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        capture_output=True,
        check=True,
    ).stdout
    fr = np.frombuffer(raw, np.uint8).reshape(-1, h, w, 3)
    assert fr[2, 90, 160, 0] > 200 and fr[2, 90, 160, 1] < 60  # 赤
    assert fr[30, 90, 160, 2] > 200  # 青

    # 上半身のカメラ（左から右へパン）: 白黒の境目が左へ動く
    def edge(f):
        row = fr[f, 90, :, 0].astype(int)
        return int(np.argmax(row > 128))

    assert edge(6) > edge(23)


def _v7_spans():
    # A(8〜14) B(14〜20) サビ(20〜32) A(32〜38) B(38〜44) サビ(44〜56)
    starts = [8 + 3 * i for i in range(16)]
    secs = ["verse"] * 2 + ["bridge"] * 2 + ["chorus_first"] + ["chorus"] * 3
    secs += ["verse"] * 2 + ["bridge"] * 2 + ["chorus"] * 4
    return mv.line_spans(starts, [s + 2.5 for s in starts], secs, 2.0)


def test_v7_grid_pip_cards():
    beats = [i * 0.5 for i in range(130)]
    spans = _v7_spans()
    cuts = mv.plan_cuts(beats, spans, 65, 30)
    bars = mv.motif_bars(spans, beats, 30)
    assert [(a, b) for a, _, b in bars] == [(540, 600), (1260, 1320)]
    out, used = mv.apply_v7(cuts, spans, beats, 30, [(a, b) for a, _, b in bars])
    assert used == {"grid": 2, "pip": 1, "cards": 1}
    assert all(a.end == b.start for a, b in zip(out, out[1:], strict=False))
    # グリッド: B メロの最後の小節は V11 と重なるので 1 小節前（480〜540）。1f ずつ 16 枠
    g = [c for c in out if c.shot.startswith("grid") and 480 <= c.start < 540]
    assert g[0].start == 480 and g[0].shot == "grid_1" and g[14].shot == "grid_15"
    assert g[-1].shot == "grid" and g[-1].start == 495 and g[-1].end == 540
    # カード: 1 番の A メロ（8〜14 秒）の最後の 6 拍（14 秒まで）、2 拍ごとに 1 枚
    cards = [c for c in out if c.shot.startswith("cards_")]
    assert [(c.start, c.end, c.shot) for c in cards] == [
        (330, 360, "cards_1"),
        (360, 390, "cards_2"),
        (390, 420, "cards_3"),
    ]
    # 画面内の画面: 2 番の A メロ（32〜38 秒）
    pip = [c for c in out if c.shot == "pip"]
    assert [(c.start, c.end) for c in pip] == [(960, 1140)]
    out2 = mv.insert_planes(out, bars)
    assert all(a.end == b.start for a, b in zip(out2, out2[1:], strict=False))


def test_v7_images():
    src = Image.new("RGB", (400, 600), (240, 120, 160))
    w, h = 320, 180
    g3 = np.asarray(mv.shot_image(src, "grid_3", w, h, "#3E5C9A", "#FFFFFF"))
    assert (g3[10, 10, :3] != (0x3E, 0x5C, 0x9A)).any()  # 1 枠目は出ている
    assert (g3[h - 5, w - 5, :3] == (0x3E, 0x5C, 0x9A)).all()  # 16 枠目はまだ
    pip = mv.shot_image(src, "pip", w, h, "#3E5C9A", "#FFFFFF")
    assert pip.getpixel((5, 90))[:3] == (0x3E, 0x5C, 0x9A)
    assert pip.getpixel((160, 90))[:3] != (0x3E, 0x5C, 0x9A)
    c1 = np.asarray(mv.shot_image(src, "cards_1", w, h, "#3E5C9A", "#FFFFFF"))
    c3 = np.asarray(mv.shot_image(src, "cards_3", w, h, "#3E5C9A", "#FFFFFF"))
    white = lambda a: int(((a[..., :3] == 255).all(axis=-1)).sum())  # noqa: E731
    assert white(c3) > white(c1) > 0  # 白い縁が増える


def test_v4_brightness_match():
    fg = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
    fg.paste((250, 250, 250, 255), (10, 10, 30, 30))
    out = np.asarray(mv.match_brightness(fg, 0.3)).astype(float) / 255
    lum = out[15, 15, :3] @ [0.2126, 0.7152, 0.0722]
    assert abs(lum - 0.45) < 0.01 and out[0, 0, 3] == 0
    dark = Image.new("RGBA", (10, 10), (60, 60, 60, 255))
    assert mv.match_brightness(dark, 0.3) is dark  # 暗いものは変えない
    # 切り抜きと面: 白い切り抜きは、濃い主色の面より 0.15 だけ明るい所まで暗くなる
    src = Image.new("RGBA", (100, 150), (0, 0, 0, 0))
    src.paste((255, 255, 255, 255), (30, 20, 70, 140))
    shot = np.asarray(mv.shot_image(src, "cutout", 320, 180, "#3E5C9A", "#FFFFFF")).astype(float)
    assert shot[90, 160, :3].max() < 200


def test_hearing_aid_rows():
    from fractions import Fraction

    from video_automation.services.subtitle.formats import hearing_aid

    rows = [
        (10, 40, "A", "こんにちは"),
        (40, 70, "A", "元気"),
        (80, 99, "B", "はい"),
        (100, 120, "", "…"),
    ]
    out = hearing_aid(rows, Fraction(30), (0, 300), True)
    assert [r[3] for r in out[:4]] == ["（A）こんにちは", "元気", "（B）はい", "…"]
    assert out[4:] == [(0, 60, "", "♪～"), (240, 300, "", "～♪")]
    # BGM が 4 秒より短いときは始まりだけ、名前を書かない設定
    out = hearing_aid(rows, Fraction(30), (0, 90), False)
    assert [r[3] for r in out] == ["こんにちは", "元気", "はい", "…", "♪～"]
    assert hearing_aid(rows, Fraction(30), None, False) == rows


def test_emotion_subtitles_k4():
    """統合版 K4: 感情ごとの字幕の変化（入りの置き換え・大きさ・不透明度・怒りの縁と揺れ）。"""
    from fractions import Fraction

    from video_automation.schemas.subtitle import SubtitleStyle
    from video_automation.services.subtitle.formats import build_ass

    rows = [(0, 90, "A", "あ"), (90, 180, "A", "い"), (180, 270, "A", "う"), (270, 360, "A", "え")]
    st = SubtitleStyle(band=False)
    emo = {0: "sad", 1: "surprise", 2: "tired", 3: "angry"}
    d = [x for x in build_ass(rows, Fraction(30), 640, 360, st, emotions=emo).splitlines()]
    d = [x for x in d if x.startswith("Dialogue")]
    assert "\\fad(400,100)" in d[0]  # 悲しい: 12f
    size = round(360 * st.font_size_ratio)
    assert "\\fscx88" in d[1] and f"\\fs{round(size * 1.08)}" in d[1]  # 驚き: ポップ中・×1.08
    assert "\\1a&H19&" in d[2]  # 疲れ: 不透明度 90%
    shake = d[3:]  # 怒り: 4 つの区間（+2px・−2px）、縁 ×1.3
    assert len(shake) == 4 and all(f"\\bord{st.outline * 1.3:.1f}" in x for x in shake)
    assert "\\pos(321," in shake[1] and "\\pos(319," in shake[2]
    assert shake[0].split(",")[1] == "0:00:09.00" and shake[-1].split(",")[2] == "0:00:12.00"
    # 二重縁: 文字を透明にした層では、不透明度で文字の面を出さない
    dbl = SubtitleStyle(double_outline=True, band=False)
    d = build_ass(rows[2:3], Fraction(30), 640, 360, dbl, emotions={0: "tired"}).splitlines()
    layer1 = next(x for x in d if x.startswith("Dialogue: 1"))
    assert "\\1a&HFF&" in layer1 and "\\1a&H19&" not in layer1
    # 感情を渡さなければ変えない
    plain = build_ass(rows, Fraction(30), 640, 360, st)
    assert "\\fad(400" not in plain and "\\pos(" not in plain


def test_translation_wrap_and_speed():
    """統合版 W5: 英語は 1 行 42 字・2 行は下が長い形。読む速さ。"""
    from video_automation.services.subtitle.translate import reading_speed, wrap

    assert wrap("Hello there.") == "Hello there."
    two = wrap("This is a fairly long subtitle line that needs to be split in two")
    top, bottom = two.split("\n")
    assert len(top) <= 42 and len(bottom) <= 42 and len(bottom) >= len(top)
    assert reading_speed("abc\ndef", 0.3) == 20.0
