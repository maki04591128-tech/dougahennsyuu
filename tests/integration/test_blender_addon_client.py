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
BridgeError, Client, check_server, run_step = (
    bridge.BridgeError,
    bridge.Client,
    bridge.check_server,
    bridge.run_step,
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
