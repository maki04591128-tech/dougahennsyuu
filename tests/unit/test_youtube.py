"""YouTube への投稿（通信は httpx.MockTransport の偽物）。"""

import json
from pathlib import Path

import httpx
import pytest

from video_automation.integrations.youtube import oauth, uploader


class PlainProtector:
    def protect(self, data: bytes) -> bytes:
        return b"x" + data

    def unprotect(self, blob: bytes) -> bytes:
        return blob[1:]


def _client_file(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / oauth.CLIENT_FILE).write_text(
        json.dumps({"installed": {"client_id": "cid", "client_secret": "sec"}}), "utf-8"
    )


def test_meta_validate_and_body() -> None:
    m = uploader.VideoMeta(title="題" * 101, description="<b>", publish_at="2026-12-01T00:00:00Z",
                           privacy="public")  # fmt: skip
    errs = m.validate()
    assert any("100" in e for e in errs)
    assert any("< >" in e for e in errs)
    assert any("予約" in e for e in errs)
    ok = uploader.VideoMeta(title="AIが遊ぶ #1", tags=["ゲーム"])
    assert ok.validate() == []
    b = ok.body()
    assert b["status"]["privacyStatus"] == "private"  # 既定は非公開
    assert b["snippet"]["categoryId"] == "20"
    assert b["status"]["containsSyntheticMedia"] is False


def test_pkce_and_auth_url() -> None:
    v, c = oauth.pkce_pair()
    assert 43 <= len(v) <= 128 and "=" not in c
    url = oauth.auth_url(oauth.ClientInfo("cid", "s"), "http://127.0.0.1:9", c, "st")
    assert "code_challenge_method=S256" in url and "youtube.upload" in url
    assert "access_type=offline" in url


def test_token_store_and_refresh(tmp_path: Path) -> None:
    _client_file(tmp_path)
    store = oauth.TokenStore(tmp_path, PlainProtector())
    with pytest.raises(oauth.YouTubeAuthError):
        store.load()
    store.save("rtok")
    assert b"rtok" in store.path.read_bytes()

    def handler(req: httpx.Request) -> httpx.Response:
        form = dict(x.split("=") for x in req.content.decode().split("&"))
        assert form["refresh_token"] == "rtok" and form["grant_type"] == "refresh_token"
        return httpx.Response(200, json={"access_token": "atok"})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    assert oauth.access_token(tmp_path, http, store) == "atok"


def test_missing_client_file(tmp_path: Path) -> None:
    with pytest.raises(oauth.YouTubeAuthError, match="client_secret.json"):
        oauth.load_client(tmp_path)


def _video(tmp_path: Path, n: int) -> Path:
    p = tmp_path / "v.mp4"
    p.write_bytes(bytes(range(256)) * (n // 256) + b"\0" * (n % 256))
    return p


def test_upload_resumes_after_server_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(uploader, "CHUNK", 256 * 1024)
    size = 256 * 1024 * 2 + 100
    video = _video(tmp_path, size)
    got = bytearray()
    calls = {"put": 0, "fail_once": True}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST":
            body = json.loads(req.content)
            assert body["status"]["privacyStatus"] == "private"
            assert req.headers["X-Upload-Content-Length"] == str(size)
            return httpx.Response(200, headers={"Location": "https://up.example/s1"})
        calls["put"] += 1
        cr = req.headers["Content-Range"]
        if cr.startswith("bytes */"):  # どこまで届いたか
            return httpx.Response(308, headers={"Range": f"bytes=0-{len(got) - 1}"})
        if calls["fail_once"] and len(got) > 0:
            calls["fail_once"] = False
            return httpx.Response(503)
        start = int(cr.split()[1].split("-")[0])
        assert start == len(got)
        got.extend(req.content)
        if len(got) == size:
            return httpx.Response(200, json={"id": "VID1", "status": {"privacyStatus": "private"}})
        return httpx.Response(308, headers={"Range": f"bytes=0-{len(got) - 1}"})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    seen: list[int] = []
    res = uploader.upload(http, "t", video, uploader.VideoMeta(title="a"),
                          progress=lambda s, t: seen.append(s))  # fmt: skip
    assert res["id"] == "VID1"
    assert bytes(got) == video.read_bytes()
    assert seen[-1] == size


def test_upload_client_error_stops(tmp_path: Path) -> None:
    video = _video(tmp_path, 1000)

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST":
            return httpx.Response(200, headers={"Location": "https://up.example/s"})
        return httpx.Response(403, text="quotaExceeded")

    http = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(uploader.YouTubeUploadError, match="403"):
        uploader.upload(http, "t", video, uploader.VideoMeta(title="a"))


def test_upload_refuses_bad_meta(tmp_path: Path) -> None:
    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(uploader.YouTubeUploadError, match="題名"):
        uploader.upload(http, "t", _video(tmp_path, 10), uploader.VideoMeta(title=" "))


def test_thumbnail(tmp_path: Path) -> None:
    img = tmp_path / "t.png"
    img.write_bytes(b"\x89PNG")

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.params["videoId"] == "V" and req.headers["Content-Type"] == "image/png"
        return httpx.Response(200, json={})

    uploader.set_thumbnail(httpx.Client(transport=httpx.MockTransport(handler)), "t", "V", img)
