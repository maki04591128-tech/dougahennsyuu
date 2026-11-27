"""文字アニメーション・図表・シーン作成（Phase 7）。Blender は偽の実行ファイル（実機は live テスト）。"""

import shutil
import subprocess

import httpx
import pytest
from asgi_lifespan import LifespanManager

from fakes import FakeProtector, blender_calls, fake_blender
from video_automation.api.app import create_app

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg")
W, H = 320, 180


@pytest.fixture
async def vclient(tmp_path, media_settings, fake):
    s = media_settings.model_copy(update={"blender_path": fake_blender(tmp_path)})
    app = create_app(s, http_transport=fake.transport, protector=FakeProtector())
    async with LifespanManager(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t/api/v1"
        ) as c:
            c.app, c.tmp = app, tmp_path  # type: ignore[attr-defined]
            yield c


def rgba_frame(path, n):
    r = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-vf",
            f"select=eq(n\\,{n})",
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgba",
            "-",
        ],
        capture_output=True,
        check=True,
    )
    assert len(r.stdout) == W * H * 4
    return r.stdout


def px(data, x, y):
    i = (y * W + x) * 4
    return tuple(data[i : i + 4])


def frames_of(path):
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-count_frames",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=nb_read_frames,codec_name,pix_fmt",
            "-of",
            "default=nw=1",
            str(path),
        ],
        capture_output=True,
        text=True,
    ).stdout
    return dict(line.split("=") for line in out.split())


async def _job(c, r):
    assert r.status_code == 202, r.text
    job = await c.app.state.jobs.wait(r.json()["id"])
    assert job.status == "SUCCEEDED", job.error
    return job.result


async def test_title_card_animations_hold_fade_and_box(vclient):
    c = vclient
    pid = (await c.post("/projects", json={"name": "T", "width": W, "height": H})).json()["id"]
    a = await _job(
        c,
        await c.post(
            "/assets/title_card",
            json={
                "project_id": pid,
                "text": "字幕テスト",
                "duration_sec": 2,
                "animation": "typewriter",
                "position": "lower_third",
                "box": True,
                "fade_out_sec": 0.3,
            },
        ),
    )
    call = blender_calls(c.tmp)[-1]
    assert call["script"] == "title_card" and call["animation"] == "typewriter"
    assert call["position"] == "lower_third" and call["outline"] is True
    # 統合版 T6: カーソルが表示の終わりまで点滅するので、1 行なら全体（2 秒＝60 フレーム）を描く
    assert call["frames"] == 60
    path = c.app.state.container.settings.data_dir / a["path"]
    info = frames_of(path)
    assert info["nb_read_frames"] == "60" and info["codec_name"] == "png"
    assert info["pix_fmt"] == "rgba" and abs(a["duration_sec"] - 2.0) < 0.05
    mid = rgba_frame(path, 30)
    assert px(mid, W // 2, H // 2)[3] == 255  # 文字（偽: 中央の白い四角）
    band = px(mid, 75, H // 2)  # 文字の左外側・帯の内側
    assert 100 < band[3] < 180 and band[0] < 20  # 半透明の黒い帯
    assert px(mid, 5, 5)[3] == 0  # 帯の外は透明
    last = rgba_frame(path, 59)
    assert px(last, W // 2, H // 2)[3] < 40  # 末尾はフェードアウト

    # fade / none は1フレームだけ描画（フェードは FFmpeg 側）
    for anim in ("fade", "none"):
        b = await _job(
            c,
            await c.post(
                "/assets/title_card",
                json={
                    "project_id": pid,
                    "text": "見出し",
                    "duration_sec": 1,
                    "animation": anim,
                    "fade_out_sec": 0,
                },
            ),
        )
        assert blender_calls(c.tmp)[-1]["frames"] == 1
        p2 = c.app.state.container.settings.data_dir / b["path"]
        assert frames_of(p2)["nb_read_frames"] == "30"
        first = px(rgba_frame(p2, 0), W // 2, H // 2)[3]
        assert (first < 30) if anim == "fade" else (first == 255)
        assert px(rgba_frame(p2, 29), W // 2, H // 2)[3] == 255
    bad = await c.post(
        "/assets/title_card", json={"project_id": pid, "text": "x", "animation": "explode"}
    )
    assert bad.status_code == 422


async def test_infographic(vclient):
    c = vclient
    pid = (await c.post("/projects", json={"name": "T", "width": W, "height": H})).json()["id"]
    a = await _job(
        c,
        await c.post(
            "/assets/infographic",
            json={
                "project_id": pid,
                "kind": "bar",
                "title": "月間再生数",
                "labels": ["1月", "2月"],
                "values": [1200, 3400],
                "unit": "回",
                "duration_sec": 3,
                "animation_sec": 1,
            },
        ),
    )
    call = blender_calls(c.tmp)[-1]
    assert call["script"] == "infographic" and call["values"] == [1200, 3400]
    assert call["frames"] == 30 and len(call["accent_colors"]) == 4
    assert a["kind"] == "video" and a["name"].startswith("図表「月間再生数")
    path = c.app.state.container.settings.data_dir / a["path"]
    assert frames_of(path)["nb_read_frames"] == "90"
    assert 100 < px(rgba_frame(path, 45), 20, 20)[3] < 150  # 全体の背景板
    bad = await c.post(
        "/assets/infographic", json={"project_id": pid, "labels": ["a"], "values": [1, 2]}
    )
    assert bad.status_code == 422


async def test_create_scene(vclient, media_dir):
    c = vclient
    pid = (await c.post("/projects", json={"name": "S", "width": W, "height": H})).json()["id"]
    r = await _job(
        c,
        await c.post(
            f"/projects/{pid}/scenes",
            json={
                "start_frame": 0,
                "duration_sec": 2,
                "background_color": "#203040",
                "title": "第1章",
                "title_animation": "bounce",
                "camera_preset": "ken_burns_in",
            },
        ),
    )
    assert r["command"]["status"] == "APPLIED" and len(r["assets"]) == 2
    img = (
        await c.post(
            "/assets/import", json={"project_id": pid, "path": str(media_dir / "tall.png")}
        )
    ).json()
    r2 = await _job(
        c,
        await c.post(
            f"/projects/{pid}/scenes",
            json={
                "start_frame": 60,
                "duration_sec": 1,
                "background_asset_id": img["id"],
                "camera_preset": "pan_up",
                "transition": {"type": "dissolve", "duration_frames": 10},
            },
        ),
    )
    assert r2["assets"] == [img["id"]]
    tl = (await c.get(f"/projects/{pid}/timeline")).json()
    tracks = {t["name"]: t for t in tl["tracks"]}
    assert set(tracks) == {"シーン背景", "テロップ"}  # 2回目は既存トラックを使う
    assert tracks["シーン背景"]["index"] < tracks["テロップ"]["index"]
    bg = sorted(
        (x for x in tl["clips"] if x["track_id"] == tracks["シーン背景"]["id"]),
        key=lambda x: x["start_frame"],
    )
    assert [(x["start_frame"], x["end_frame"]) for x in bg] == [(0, 60), (60, 90)]
    assert bg[0]["animation"]["preset"] == "ken_burns_in"
    assert bg[1]["animation"]["transition_in"]["type"] == "dissolve"
    assert blender_calls(c.tmp)[-1]["animation"] == "bounce"
    # 生成した背景は描画できる（プレビュー）
    png = await c.post(f"/projects/{pid}/preview", json={"frame": 70, "width": W})
    assert png.status_code == 200
    # LLM からのシーン作成は承認待ち
    r3 = await _job(
        c,
        await c.post(
            f"/projects/{pid}/scenes", json={"start_frame": 90, "duration_sec": 1, "origin": "llm"}
        ),
    )
    assert r3["command"]["status"] == "PENDING_APPROVAL"
    # 同じ色の背景は使い回す
    assert len([a for a in tl["assets"] if a["source"] == "generated"]) == 1
    r4 = await c.post(
        f"/projects/{pid}/scenes",
        json={"start_frame": 300, "duration_sec": 1, "camera_preset": "nope"},
    )
    job = await c.app.state.jobs.wait(r4.json()["id"])
    assert job.status == "FAILED" and "unknown motion preset" in job.error
