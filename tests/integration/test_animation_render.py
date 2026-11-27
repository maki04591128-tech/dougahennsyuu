"""アニメーション（Phase 7）を実際に FFmpeg で描画し、フレームの画素で動きを確かめる。"""

import shutil
import subprocess

import pytest

W, H = 320, 180
pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg")


def _img(path, spec):
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", spec, "-frames:v", "1", str(path)],
        check=True,
    )


@pytest.fixture
def imgs(media_dir):
    p = {
        "red": media_dir / "red.png",
        "blue": media_dir / "blue.png",
        "marker": media_dir / "marker.png",
    }
    if not p["red"].exists():
        _img(p["red"], f"color=c=red:s={W}x{H}")
        _img(p["blue"], f"color=c=blue:s={W}x{H}")
        # 黒地の中央に白い 40x40 の四角（カメラの拡大・移動の確認用）
        _img(p["marker"], f"color=c=black:s={W}x{H},drawbox=x=140:y=70:w=40:h=40:c=white:t=fill")
    return p


def rgb(png: bytes) -> bytes:
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", "pipe:0", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        input=png,
        capture_output=True,
        check=True,
    )
    assert len(r.stdout) == W * H * 3
    return r.stdout


def bbox(data: bytes, pred):
    xs, ys = [], []
    for y in range(H):
        row = y * W * 3
        for x in range(W):
            i = row + x * 3
            if pred(data[i], data[i + 1], data[i + 2]):
                xs.append(x)
                ys.append(y)
    return (min(xs), max(xs), min(ys), max(ys)) if xs else None


def mean(data: bytes, x0=0, x1=W, y0=0, y1=H):
    n, acc = 0, [0, 0, 0]
    for y in range(y0, y1):
        for x in range(x0, x1):
            i = (y * W + x) * 3
            for c in range(3):
                acc[c] += data[i + c]
            n += 1
    return tuple(round(v / n) for v in acc)


RED = lambda r, g, b: r > 120 and b < 80  # noqa: E731
WHITE = lambda r, g, b: r > 150 and g > 150 and b > 150  # noqa: E731


async def _setup(c, imgs, names, length=60, fit="fill"):
    pid = (await c.post("/projects", json={"name": "A", "width": W, "height": H})).json()["id"]
    ids = []
    cmds = [{"type": "add_track", "kind": "video", "track_id": "v"}]
    for i, n in enumerate(names):
        a = (await c.post("/assets/import", json={"project_id": pid, "path": str(imgs[n])})).json()
        cid = f"c{i}"
        ids.append(cid)
        cmds.append(
            {
                "type": "add_clip",
                "track_id": "v",
                "asset_id": a["id"],
                "clip_id": cid,
                "start_frame": i * length,
                "duration_frames": length,
                "transform": {"fit": fit, "scale": 0.5 if fit == "fit" else 1.0},
            }
        )
    r = await c.post(
        f"/projects/{pid}/commands", json={"command": {"type": "batch", "commands": cmds}}
    )
    assert r.status_code == 201, r.text
    return pid, ids


async def anim(c, pid, **cmd):
    r = await c.post(
        f"/projects/{pid}/commands", json={"command": {"type": "set_clip_animation", **cmd}}
    )
    assert r.status_code == 201, r.text
    return r.json()


async def frame(c, pid, f):
    r = await c.post(f"/projects/{pid}/preview", json={"frame": f, "width": W})
    assert r.status_code == 200, r.text
    return rgb(r.content)


async def test_element_presets_move_scale_fade(mclient, imgs):
    c = mclient
    pid, (cid,) = await _setup(c, imgs, ["red"], fit="fit")  # 中央に 160x90 の赤
    full = bbox(await frame(c, pid, 0), RED)
    assert full[1] - full[0] == pytest.approx(159, abs=1) and full[3] - full[2] == pytest.approx(
        89, abs=1
    )
    await anim(c, pid, clip_id=cid, preset="slide_in_left_far")
    early, late = bbox(await frame(c, pid, 3), RED), bbox(await frame(c, pid, 20), RED)
    assert early is not None and early[1] < 120  # 左から入ってくる途中
    assert late == full
    # ポップイン: 小さい・薄い → 元の大きさ（移動のキーフレームは残す＝merge）
    await anim(c, pid, clip_id=cid, preset="pop_in")
    tl = (await c.get(f"/projects/{pid}/timeline")).json()
    props = {p for k in tl["clips"][0]["animation"]["keyframes"] for p in k if k[p] is not None}
    assert {"x", "scale", "opacity"} <= props
    assert bbox(await frame(c, pid, 0), RED) is None  # 不透明度 0
    mid = bbox(await frame(c, pid, 17), RED)  # pop_in は 16f
    assert mid == full
    # フェードアウト（末尾）: 最後のフレームはほぼ消える。途中は半透明
    await anim(c, pid, clip_id=cid, clear=True)
    await anim(c, pid, clip_id=cid, preset="fade_out")
    assert mean(await frame(c, pid, 30), 100, 220, 60, 120)[0] > 230
    half = mean(await frame(c, pid, 52), 100, 220, 60, 120)[0]
    assert 60 < half < 200, half
    assert mean(await frame(c, pid, 59), 100, 220, 60, 120)[0] < 30
    # 回転
    await anim(c, pid, clip_id=cid, clear=True)
    await anim(
        c,
        pid,
        clip_id=cid,
        animation={"keyframes": [{"frame": 0, "rotation": 45, "easing": "hold"}]},
    )
    rot = bbox(await frame(c, pid, 10), RED)
    assert rot[3] - rot[2] > 130  # 45度回すと縦にも広がる
    # 回転しながら戻る: 最後は元の範囲だけ（前のフレームの残像が残らない）
    await anim(
        c,
        pid,
        clip_id=cid,
        animation={
            "keyframes": [
                {"frame": 0, "rotation": 90, "easing": "linear"},
                {"frame": 20, "rotation": 0},
            ]
        },
    )
    await c.post("/render", json={"project_id": pid, "quality": 35, "external_subtitles": False})
    r = await c.post(
        "/render",
        json={
            "project_id": pid,
            "quality": 35,
            "external_subtitles": False,
            "start_frame": 0,
            "end_frame": 40,
        },
    )
    job = await c.app.state.jobs.wait(r.json()["job_id"])
    assert job.status == "SUCCEEDED", job.error
    mp4 = c.app.state.container.settings.data_dir / job.result["output_path"]
    raw = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(mp4),
            "-vf",
            f"select=eq(n\\,35),scale={W}:{H}",
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        capture_output=True,
        check=True,
    ).stdout
    after = bbox(raw, RED)
    assert (
        after[1] - after[0] <= full[1] - full[0] + 4
        and after[3] - after[2] <= full[3] - full[2] + 4
    )


async def test_camera_zoom_and_pan(mclient, imgs):
    c = mclient
    pid, (cid,) = await _setup(c, imgs, ["marker"])
    base = bbox(await frame(c, pid, 0), WHITE)
    assert base[1] - base[0] == pytest.approx(39, abs=1)
    await anim(c, pid, clip_id=cid, preset="ken_burns_in")
    end = bbox(await frame(c, pid, 59), WHITE)
    assert end[1] - end[0] == pytest.approx(40 * 1.15, abs=2)  # 中央へ寄る
    await anim(c, pid, clip_id=cid, preset="pan_left")  # カメラ系同士は置き換え
    a0, a1 = bbox(await frame(c, pid, 0), WHITE), bbox(await frame(c, pid, 59), WHITE)
    assert a0[1] - a0[0] == pytest.approx(48, abs=2)  # 1.2倍
    assert a0[0] < 140 < a1[0]  # 視点が右→左へ動くので、四角は左→右へ
    tl = (await c.get(f"/projects/{pid}/timeline")).json()
    kf = tl["clips"][0]["animation"]["keyframes"]
    assert kf[0]["zoom"] == 1.2 and kf[0]["pan_x"] == 1.0 and kf[-1]["frame"] == 59


@pytest.mark.parametrize(
    ("ttype", "check"),
    [
        ("dissolve", "mix"),
        ("slide_left", "split_lr_red_blue"),
        ("wipe_left", "split_lr_blue_red"),
        ("dip_to_black", "dark"),
        ("zoom_in", "mix"),
    ],
)
async def test_transitions(mclient, imgs, ttype, check):
    c = mclient
    pid, (a, b) = await _setup(c, imgs, ["red", "blue"], length=30)
    await anim(c, pid, clip_id=b, transition={"type": ttype, "duration_frames": 10})
    before = mean(await frame(c, pid, 29))
    assert before[0] > 200 or ttype == "dip_to_black"  # 切替前は赤
    m = await frame(c, pid, 35)  # 切替の途中
    if check == "mix":
        r, _, bl = mean(m, 140, 180, 70, 110)
        assert r > 40 and bl > 40, (r, bl)
    elif check == "split_lr_red_blue":  # 右から青が入ってくる
        assert mean(m, 0, 40)[0] > 200 and mean(m, 280, 320)[2] > 200
    elif check == "split_lr_blue_red":  # 左から青で拭われる
        assert mean(m, 0, 40)[2] > 200 and mean(m, 280, 320)[0] > 200
    else:
        r, _, bl = mean(await frame(c, pid, 30))
        assert r < 60 and bl < 60  # 黒を経由
    after = mean(await frame(c, pid, 45))
    assert after[2] > 200 and after[0] < 30


async def test_page_turn_and_cube_transitions(mclient, imgs):
    """統合版 E11: 右下の角から左上へ現れる。"""
    c = mclient
    pid, (a, b) = await _setup(c, imgs, ["red", "blue"], length=30)
    await anim(c, pid, clip_id=b, transition={"type": "page_turn", "duration_frames": 10})
    m = await frame(c, pid, 35)
    assert mean(m, 280, 320, 140, 180)[2] > 200  # 右下は次の画（青）
    assert mean(m, 0, 40, 0, 40)[0] > 200  # 左上はまだ前の画（赤）


async def test_cube_transition_pushes_previous(mclient, imgs):
    """統合版 E12: 前の画が左へ押し出される。"""
    c = mclient
    pid, (a, b) = await _setup(c, imgs, ["marker", "blue"], length=30)
    base = bbox(await frame(c, pid, 10), WHITE)
    await anim(c, pid, clip_id=b, transition={"type": "cube", "duration_frames": 10})
    mid = bbox(await frame(c, pid, 35), WHITE)
    # 前の画の印は左へ押し出される（押し出さないスライドなら印は元の位置に残る）
    assert mid is None or mid[1] < base[0] - 40, (base, mid)
    after = mean(await frame(c, pid, 45))
    assert after[2] > 200


async def test_render_mp4_with_animation_and_range(mclient, imgs, media_dir):
    c = mclient
    pid, (a, b) = await _setup(c, imgs, ["marker", "blue"], length=30)
    await anim(c, pid, clip_id=a, preset="ken_burns_in")
    await anim(c, pid, clip_id=b, preset="dissolve")
    await anim(c, pid, clip_id=b, preset="pop_in", duration_sec=0.3, intensity=0.5)
    # 動画素材にもカメラ（ズーム・パン）を付ける
    v = (
        await c.post(
            "/assets/import", json={"project_id": pid, "path": str(media_dir / "clip.mp4")}
        )
    ).json()
    await c.post(
        f"/projects/{pid}/commands",
        json={
            "command": {
                "type": "add_clip",
                "track_id": "v",
                "asset_id": v["id"],
                "clip_id": "vid",
                "start_frame": 60,
                "duration_frames": 30,
            }
        },
    )
    await anim(c, pid, clip_id="vid", preset="pan_right")
    await anim(c, pid, clip_id="vid", preset="slide_left", duration_sec=0.2)
    r = await c.post(
        "/render",
        json={
            "project_id": pid,
            "profile": "fhd_landscape",
            "quality": 35,
            "external_subtitles": False,
        },
    )
    job = await c.app.state.jobs.wait(r.json()["job_id"])
    assert job.status == "SUCCEEDED", job.error
    assert abs(job.result["duration_sec"] - 3.0) < 0.1
    # 範囲指定（トランジションの途中から）でも失敗しない
    r = await c.post(
        "/render",
        json={
            "project_id": pid,
            "start_frame": 33,
            "end_frame": 70,
            "quality": 35,
            "external_subtitles": False,
        },
    )
    job = await c.app.state.jobs.wait(r.json()["job_id"])
    assert job.status == "SUCCEEDED", job.error
    assert abs(job.result["duration_sec"] - 37 / 30) < 0.1


async def test_animation_validation_split_and_undo(mclient, imgs):
    c = mclient
    pid, (cid,) = await _setup(c, imgs, ["red"])
    bad = await c.post(
        f"/projects/{pid}/commands",
        json={
            "command": {
                "type": "set_clip_animation",
                "clip_id": cid,
                "animation": {"keyframes": [{"frame": 60, "x": 1}]},
            }
        },
    )
    assert bad.status_code == 422  # クリップ外
    bad = await c.post(
        f"/projects/{pid}/commands",
        json={
            "command": {
                "type": "set_clip_animation",
                "clip_id": cid,
                "animation": {"keyframes": [{"frame": 5, "x": 1}, {"frame": 5, "x": 0}]},
            }
        },
    )
    assert bad.status_code == 422  # 重複フレーム
    for body in (
        {"preset": "nope"},
        {"preset": "text_pop"},
        {"transition": {"type": "dissolve", "duration_frames": 61}},
        {},
    ):
        r = await c.post(
            f"/projects/{pid}/commands",
            json={"command": {"type": "set_clip_animation", "clip_id": cid, **body}},
        )
        assert r.status_code == 422, body
    await anim(
        c,
        pid,
        clip_id=cid,
        animation={"keyframes": [{"frame": 0, "x": -1, "easing": "linear"}, {"frame": 59, "x": 1}]},
        transition={"type": "dissolve", "duration_frames": 10},
    )
    # 分割: 後半は分割点の値から始まり、トランジションは前半だけ
    r = await c.post(
        f"/projects/{pid}/commands",
        json={
            "command": {"type": "split_clip", "clip_id": cid, "at_frame": 30, "new_clip_id": "c2"}
        },
    )
    assert r.status_code == 201, r.text
    tl = (await c.get(f"/projects/{pid}/timeline")).json()
    first, second = sorted(tl["clips"], key=lambda x: x["start_frame"])
    assert first["animation"]["transition_in"]["type"] == "dissolve"
    assert second["animation"]["transition_in"] is None
    assert second["animation"]["keyframes"][0]["frame"] == 0
    assert second["animation"]["keyframes"][0]["x"] == pytest.approx(-1 + 2 * 30 / 59, abs=0.01)
    assert first["animation"]["keyframes"][-1]["frame"] == 29
    await c.post(f"/projects/{pid}/undo")
    await c.post(f"/projects/{pid}/undo")
    tl = (await c.get(f"/projects/{pid}/timeline")).json()
    assert len(tl["clips"]) == 1 and tl["clips"][0]["animation"] is None


async def test_presets_api(mclient, imgs):
    c = mclient
    ps = (await c.get("/motion/presets")).json()
    names = {p["name"] for p in ps}
    assert {"ken_burns_in", "pop_in", "dissolve", "text_typewriter", "float"} <= names
    assert all(p["builtin"] for p in ps)
    cams = (await c.get("/motion/presets", params={"kind": "camera"})).json()
    assert {p["kind"] for p in cams} == {"camera"}
    r = await c.post(
        "/motion/presets",
        json={
            "name": "my_drop",
            "kind": "element",
            "label": "落下",
            "keyframes": [{"at": 0, "y": -1, "easing": "back_out"}, {"at": 0.5, "y": 0}],
        },
    )
    assert r.status_code == 201
    assert (
        await c.post(
            "/motion/presets",
            json={"name": "pop_in", "kind": "element", "label": "x", "keyframes": [{"at": 0}]},
        )
    ).status_code == 422
    pid, (cid,) = await _setup(c, imgs, ["red"])
    await anim(c, pid, clip_id=cid, preset="my_drop", duration_sec=1.0)
    tl = (await c.get(f"/projects/{pid}/timeline")).json()
    kfs = tl["clips"][0]["animation"]["keyframes"]
    assert [k["frame"] for k in kfs] == [0, 30] and tl["clips"][0]["animation"][
        "preset"
    ] == "my_drop"
    saved = (
        await c.post(
            "/motion/presets/from_clip",
            json={"clip_id": cid, "name": "copy_drop", "label": "コピー"},
        )
    ).json()
    assert [k["at"] for k in saved["keyframes"]] == [0.0, 1.0]
    await anim(c, pid, clip_id=cid, preset="float")
    tl = (await c.get(f"/projects/{pid}/timeline")).json()
    assert len(tl["clips"][0]["animation"]["keyframes"]) >= 2
    assert (await c.delete("/motion/presets/copy_drop")).status_code == 204
    assert (await c.delete("/motion/presets/pop_in")).status_code == 422
    assert (await c.delete("/motion/presets/none")).status_code == 404


async def test_transition_becomes_cut_without_handle(mclient, imgs, media_dir):
    """統合版 E2: 前の動画に続きが無いときは、止めた絵で埋めずにカットにする。"""
    c = mclient
    vid = media_dir / "red_1s.mp4"
    if not vid.exists():
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"color=c=red:s={W}x{H}:r=30:d=1",
                "-pix_fmt",
                "yuv420p",
                str(vid),
            ],
            check=True,
        )
    pid = (await c.post("/projects", json={"name": "E2", "width": W, "height": H})).json()["id"]
    av = (await c.post("/assets/import", json={"project_id": pid, "path": str(vid)})).json()
    ab = (
        await c.post("/assets/import", json={"project_id": pid, "path": str(imgs["blue"])})
    ).json()
    cmds = [
        {"type": "add_track", "kind": "video", "track_id": "v"},
        {
            "type": "add_clip",
            "track_id": "v",
            "asset_id": av["id"],
            "clip_id": "a",
            "start_frame": 0,
            "duration_frames": 30,
        },
        {
            "type": "add_clip",
            "track_id": "v",
            "asset_id": ab["id"],
            "clip_id": "b",
            "start_frame": 30,
            "duration_frames": 30,
        },
    ]
    r = await c.post(
        f"/projects/{pid}/commands", json={"command": {"type": "batch", "commands": cmds}}
    )
    assert r.status_code == 201, r.text
    await anim(c, pid, clip_id="b", transition={"type": "dissolve", "duration_frames": 10})
    m = mean(await frame(c, pid, 33))
    assert m[2] > 200 and m[0] < 30, m  # 混ざらずに青（カット）


async def test_motion_blur_ghost_for_moving_image(mclient, imgs, tmp_path):
    """統合版 E4: 動く画像の後ろに、半フレーム前の姿を不透明度 50% で重ねる（止まると消える）。"""
    c = mclient
    pid, (cid,) = await _setup(c, imgs, ["red"], fit="fit")
    await anim(c, pid, clip_id=cid, preset="slide_in_left_far")
    render = c.app.state.container.render

    async def shot(f, blur):
        out = tmp_path / f"f{f}_{int(bool(blur))}.png"
        plan = await render.plan(pid, W, H, out, single_frame=f, blur=blur)
        await c.app.state.container.ffmpeg.run(["-y", *plan.args])
        return rgb(out.read_bytes())

    half = lambda r, g, b: 50 < r < 200 and g < 60 and b < 60  # noqa: E731
    sharp, ghost = await shot(10, set()), await shot(10, {cid})
    assert bbox(sharp, half) is None  # 付けなければ残像は無い
    main = bbox(ghost, lambda r, g, b: r > 200 and g < 60)
    trail = bbox(ghost, half)
    assert trail is not None and trail[0] < main[0]  # 左から入る動きの後ろ（左）に残像
    assert bbox(await shot(30, {cid}), half) is None  # 止まった後は残像なし
