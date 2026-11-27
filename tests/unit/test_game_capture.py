"""ゲーム実況の録画（OBS は偽物）・見どころの切り出し（FFmpeg は本物）。"""

import base64
import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from video_automation.integrations.obs.client import auth_string
from video_automation.services.capture import highlights as hl
from video_automation.services.capture import session as cap
from video_automation.services.capture.session import Mark


def test_auth_string_matches_protocol() -> None:
    secret = base64.b64encode(hashlib.sha256(b"pw" + b"salt").digest()).decode()
    want = base64.b64encode(hashlib.sha256((secret + "ch").encode()).digest()).decode()
    assert auth_string("pw", "salt", "ch") == want


def test_window_id_escapes_colon() -> None:
    assert cap.obs_window_id("a:b", "C", "x.exe") == "a#3Ab:C:x.exe"


def test_short_recording_is_kept_whole() -> None:
    spans = hl.plan_spans([Mark(10, "a")], 300.0, limit=900)
    assert [(s.start, s.end) for s in spans] == [(0.0, 300.0)]


def test_long_recording_fits_limit_and_keeps_order() -> None:
    # 1 時間の録画に 120 個の印（3 個に 1 個は見どころ）
    marks = [
        Mark(30.0 * i + 5, f"m{i}", "highlight" if i % 3 == 0 else "event", 1 + i % 3)
        for i in range(120)
    ]
    marks.append(Mark(1200, "場面", "scene", 3))
    spans = hl.plan_spans(marks, 3600.0, limit=900)
    total = sum(s.length for s in spans)
    assert 600 < total <= 900
    assert spans[0].start == 0.0  # 最初の画面は必ず残す
    assert all(
        a.end <= b.start for a, b in zip(spans, spans[1:], strict=False)
    )  # 時刻の順・重なりなし
    out = hl.retime(marks, spans)
    assert out and all(0 <= m.t < total for m in out)
    assert [m.t for m in out] == sorted(m.t for m in out)
    assert not any(m.kind == "scene" and m.t == 1200 for m in out)
    ref = hl.reference_text(out)
    assert ref.startswith("プレイ記録（時刻は録画の頭から）:") and "★" in ref


def test_high_importance_wins_when_budget_is_tight() -> None:
    marks = [Mark(100, "小", importance=1), Mark(500, "大", importance=3)]
    spans = hl.plan_spans(marks, 2000.0, limit=40)
    texts = [m.text for m in hl.retime(marks, spans)]
    assert "大" in texts and "小" not in texts


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg")
def test_cut_with_ffmpeg(tmp_path: Path) -> None:
    src = tmp_path / "rec.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=s=320x180:r=30:d=20",
         "-f", "lavfi", "-i", "sine=f=440:d=20", "-shortest", "-pix_fmt", "yuv420p", str(src)],
        check=True,
    )  # fmt: skip
    spans = [hl.Span(0, 3), hl.Span(8, 12.5)]
    dst = tmp_path / "out.mp4"
    subprocess.run(["ffmpeg", *hl.ffmpeg_args(src, dst, spans)], check=True)
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(dst)],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    assert abs(float(r.stdout) - 7.5) < 0.2


class FakeObs:
    calls: list[tuple[str, dict[str, Any]]] = []
    duration_ms = 0
    out_path = ""

    def __enter__(self) -> "FakeObs":
        return self

    def __exit__(self, *_: Any) -> None:
        pass

    def call(self, rt: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        FakeObs.calls.append((rt, data or {}))
        if rt == "GetSceneList":
            return {"scenes": []}
        if rt == "GetInputList":
            return {"inputs": []}
        if rt == "GetSceneItemId":
            return {"sceneItemId": 7}
        if rt == "GetSpecialInputs":
            return {"desktop1": "デスクトップ音声", "mic1": "マイク"}
        if rt == "GetRecordStatus":
            return {"outputActive": True, "outputDuration": FakeObs.duration_ms}
        if rt == "StopRecord":
            return {"outputPath": FakeObs.out_path}
        return {}


def test_capture_session_flow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cap, "capture_root", lambda: tmp_path / "va_capture")
    FakeObs.calls = []
    s = cap.start(obs_factory=FakeObs)
    names = [c[0] for c in FakeObs.calls]
    assert "StartRecord" in names and "CreateInput" in names
    kinds = {c[1].get("inputKind") for c in FakeObs.calls if c[0] == "CreateInput"}
    assert kinds == {"window_capture", "wasapi_process_output_capture"}
    muted = {c[1]["inputName"] for c in FakeObs.calls if c[0] == "SetInputMute"}
    assert muted == {"デスクトップ音声", "マイク"}  # ゲームの音だけ録る
    with pytest.raises(cap.ObsError):
        cap.start(obs_factory=FakeObs)  # 録画中に 2 回目は断る
    FakeObs.duration_ms = 12345
    m = cap.mark("スライムを倒した", "highlight", 5, obs_factory=FakeObs)
    assert m.t == 12.35 and m.importance == 3
    with pytest.raises(ValueError):
        cap.mark("x", "bad", obs_factory=FakeObs)
    out = Path(s.folder) / "2026.mp4"
    out.write_bytes(b"x")
    FakeObs.out_path = str(out)
    done = cap.stop(obs_factory=FakeObs, probe=lambda p: 42.0, close_obs=False)
    assert done.state == "stopped" and done.duration_sec == 42.0 and done.video == str(out)
    saved = json.loads((Path(s.folder) / "session.json").read_text("utf-8"))
    assert saved["marks"][0]["text"] == "スライムを倒した"
    assert cap.current() is None
