"""Blender アドオン（動画自動化ブリッジ）の通信部分。bpy なしで、実際のアプリ（uvicorn）に
つないで確かめる。"""

import importlib.util
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from fakes import FakeProtector, FakeServers  # noqa: E402
from video_automation.api.app import create_app  # noqa: E402
from video_automation.app.settings import Settings  # noqa: E402

# パッケージの __init__ は bpy を読むので、通信部分（client.py）だけをファイルから読む
_path = Path(__file__).parents[2] / "tools/blender_addon/video_automation_bridge/client.py"
_spec = importlib.util.spec_from_file_location("va_bridge_client", _path)
assert _spec and _spec.loader
bridge = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bridge)
BridgeError, Client, check_server, run_step, plan_strips = (
    bridge.BridgeError,
    bridge.Client,
    bridge.check_server,
    bridge.run_step,
    bridge.plan_strips,
)


def test_only_local_http_is_allowed():
    assert check_server("http://127.0.0.1:8765") == "http://127.0.0.1:8765/api/v1"
    assert check_server("http://localhost:9000/") == "http://localhost:9000/api/v1"
    for bad in (
        "https://127.0.0.1:8765",
        "http://example.com:8765",
        "http://10.0.0.2:8765",
        "http://127.0.0.1",
    ):
        with pytest.raises(BridgeError):
            check_server(bad)


@pytest.fixture
def server(tmp_path):
    fake = FakeServers()
    app = create_app(
        Settings(data_dir=tmp_path, acestep_path=tmp_path / "x"),
        http_transport=fake.transport,
        protector=FakeProtector(),
    )
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(200):
        if srv.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    t.join(timeout=10)


def test_bridge_flow_against_app(server):
    c = Client(server)
    assert c.health()["status"] in ("ok", "degraded")
    pid = c.call("POST", "/projects", {"name": "B", "width": 640, "height": 360})["id"]
    assert any(p["id"] == pid for p in c.projects())
    profiles = c.profiles()
    assert len(profiles) >= 20 and {"purpose", "label", "summary"} <= set(profiles[0])
    r = c.classify("窒化ホウ素の解説動画を作って", True)
    assert r["purpose"] == "explainer"
    assert c.set_purpose(pid, r["purpose"])["purpose"] == "explainer"
    sc = c.call(
        "POST",
        "/scripts/studio",
        {
            "project_id": pid,
            "mode": "MANUAL",
            "title": "t",
            "characters": [{"name": "A", "voice": {"style_id": 10005}}],
        },
    )
    sid = sc["id"]
    c.call(
        "POST",
        f"/scripts/{sid}/dialogue",
        {"lines": [{"speaker": "A", "text": "ダイヤモンドより硬い物質の話です。"}]},
    )
    assert [s["id"] for s in c.scripts(pid)] == [sid]
    msgs: list[str] = []
    assert run_step(c, "voice", pid, sid, report=msgs.append)["message"] == "音声を作りました"
    assert "字幕を 1 件" in run_step(c, "subtitles", pid, sid)["message"]
    # 素材ごとの読み込み: 台本から並べたタイムライン → ストリップの一覧
    c.call("POST", f"/projects/{pid}/timeline/from_script", {"script_id": sid})
    tl = c.timeline_for_blender(pid, sid)
    assert tl["cues"] and tl["cues"][0]["text"].startswith("ダイヤモンド")
    assert all(Path(a["abs_path"]).is_file() for a in tl["assets"])
    plan = plan_strips(tl)
    kinds = [s["type"] for s in plan["strips"]]
    assert "sound" in kinds and kinds.count("text") == 1 and plan["width"] == 640
    assert plan_strips(tl, subtitles=False)["strips"].count(plan["strips"][-1]) == 0
    chk = run_step(c, "check", pid, sid)
    assert chk["message"].startswith("検査:") and "explainer" in chk["message"]
    with pytest.raises(BridgeError):  # 存在しない手順
        run_step(c, "nope", pid, sid)
    with pytest.raises(BridgeError, match="404"):
        c.call("GET", "/projects/nope")


def test_unreachable_app_gives_readable_error():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    with pytest.raises(BridgeError, match="接続できません"):
        Client(f"http://127.0.0.1:{port}").health()


def test_plan_strips_channels_and_transforms():
    tl = {
        "width": 1920,
        "height": 1080,
        "fps_num": 30,
        "fps_den": 1,
        "duration_frames": 300,
        "tracks": [
            {"id": "v2", "kind": "video", "index": 2, "muted": False},
            {"id": "a1", "kind": "audio", "index": 0, "muted": False},
            {"id": "v1", "kind": "video", "index": 1, "muted": True},
        ],
        "assets": [
            {"id": "mv", "name": "m.mp4", "kind": "video", "has_audio": True, "abs_path": "/m"},
            {"id": "im", "name": "i.png", "kind": "image", "has_audio": False, "abs_path": "/i"},
            {"id": "vo", "name": "v.wav", "kind": "audio", "has_audio": True, "abs_path": "/v"},
            {"id": "gone", "name": "x", "kind": "image", "has_audio": False, "abs_path": None},
        ],
        "clips": [
            {
                "id": "c1",
                "track_id": "v1",
                "asset_id": "mv",
                "start_frame": 0,
                "end_frame": 90,
                "source_in_frame": 30,
                "volume": 0.5,
                "transform": {"fit": "blur"},
                "fade_in_frames": 0,
                "fade_out_frames": 10,
                "animation": {"x": 1},
            },
            {
                "id": "c2",
                "track_id": "v2",
                "asset_id": "im",
                "start_frame": 30,
                "end_frame": 60,
                "source_in_frame": 0,
                "volume": 1,
                "transform": {"fit": "fit", "x": 0.25, "y": 0.1, "scale": 0.5, "opacity": 0.8},
                "fade_in_frames": 5,
                "fade_out_frames": 0,
            },
            {
                "id": "c3",
                "track_id": "a1",
                "asset_id": "vo",
                "start_frame": 0,
                "end_frame": 60,
                "source_in_frame": 0,
                "volume": 1,
                "transform": {},
                "fade_in_frames": 0,
                "fade_out_frames": 0,
            },
            {
                "id": "c4",
                "track_id": "v2",
                "asset_id": "gone",
                "start_frame": 0,
                "end_frame": 9,
                "source_in_frame": 0,
                "volume": 1,
                "transform": {},
            },
        ],
        "cues": [{"text": "こんにちは", "start_frame": 0, "end_frame": 30, "speaker": "A"}],
    }
    p = plan_strips(tl, base_channel=3)
    by = {(s["type"], s["name"]): s for s in p["strips"]}
    # 下から: 音声トラック(3) → 映像の音(4) → 映像 index1(5) → index2(6) → 字幕(7)
    assert by[("sound", "v.wav")]["channel"] == 3
    assert by[("sound", "m.mp4")]["channel"] == 4 and by[("sound", "m.mp4")]["volume"] == 0.5
    mv = by[("movie", "m.mp4")]
    assert mv["channel"] == 5 and mv["fit"] == "FIT" and mv["source_in"] == 30 and mv["mute"]
    im = by[("image", "i.png")]
    assert im["channel"] == 6 and im["offset_x"] == 480 and im["offset_y"] == -108
    assert im["scale"] == 0.5 and im["opacity"] == 0.8 and im["fade_in"] == 5
    assert by[("text", "こんにちは")]["channel"] == 7 and p["channels"] == (3, 7)
    assert any("動き" in n for n in p["notes"]) and any("見つからない" in n for n in p["notes"])
