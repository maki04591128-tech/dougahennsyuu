import httpx
import pytest

from video_automation.services.assets.downloader import (
    Downloader,
    DownloadRejectedError,
    host_allowed,
)


async def resolver(host):
    return {"private.example": ["192.168.1.10"], "v6local.example": ["::1"]}.get(
        host, ["93.184.216.34"]
    )


def make(handler, max_bytes=1024 * 1024):
    return Downloader(
        transport=httpx.MockTransport(handler), resolver=resolver, max_bytes=max_bytes
    )


def ok_image(req):
    return httpx.Response(
        200, content=b"\xff\xd8" + b"0" * 100, headers={"Content-Type": "image/jpeg"}
    )


async def test_rejects_unsafe_destinations(tmp_path):
    d = make(ok_image)
    for url in (
        "http://example.org/a.jpg",  # https 以外
        "https://127.0.0.1/a.jpg",  # IPアドレス直接
        "https://private.example/a.jpg",  # プライベートアドレスに解決
        "https://v6local.example/a.jpg",
    ):
        with pytest.raises(DownloadRejectedError):
            await d.fetch(url, tmp_path / "x.jpg", "image")
    with pytest.raises(DownloadRejectedError):
        await d.fetch(
            "https://evil.org/a.jpg", tmp_path / "x.jpg", "image", allowed_hosts=("pexels.com",)
        )
    assert not list(tmp_path.iterdir())
    await d.aclose()


async def test_redirects_are_rechecked(tmp_path):
    def handler(req):
        if req.url.host == "cdn.example.org":
            return httpx.Response(302, headers={"Location": "https://private.example/x.jpg"})
        return ok_image(req)

    d = make(handler)
    with pytest.raises(DownloadRejectedError, match="non-public"):
        await d.fetch("https://cdn.example.org/a.jpg", tmp_path / "x.jpg", "image")

    def handler2(req):
        if req.url.path == "/a.jpg":
            return httpx.Response(301, headers={"Location": "/b.jpg"})
        return ok_image(req)

    d2 = make(handler2)
    n, ctype = await d2.fetch("https://cdn.example.org/a.jpg", tmp_path / "x.jpg", "image")
    assert n == 102 and ctype == "image/jpeg" and (tmp_path / "x.jpg").stat().st_size == 102
    await d.aclose()
    await d2.aclose()


async def test_size_and_type_limits(tmp_path):
    big = make(
        lambda r: httpx.Response(200, content=b"0" * 2000, headers={"Content-Type": "video/mp4"}),
        max_bytes=1000,
    )
    with pytest.raises(DownloadRejectedError, match="too large"):
        await big.fetch("https://cdn.example.org/v.mp4", tmp_path / "v.mp4", "video")
    html = make(
        lambda r: httpx.Response(200, content=b"<html>", headers={"Content-Type": "text/html"})
    )
    with pytest.raises(DownloadRejectedError, match="content type"):
        await html.fetch("https://cdn.example.org/a.jpg", tmp_path / "a.jpg", "image")
    assert not list(tmp_path.iterdir())  # .part も残さない
    # Wikimedia の動画は application/ogg で届く
    ogv = make(
        lambda r: httpx.Response(
            200, content=b"OggS" + b"0" * 10, headers={"Content-Type": "application/ogg"}
        )
    )
    n, ctype = await ogv.fetch("https://cdn.example.org/v.ogv", tmp_path / "v.ogv", "video")
    assert n == 14 and ctype == "application/ogg"
    await big.aclose()
    await html.aclose()
    await ogv.aclose()


def test_host_allowed():
    assert host_allowed("images.pexels.com", ("pexels.com",))
    assert host_allowed("pexels.com", ("pexels.com",))
    assert not host_allowed("evilpexels.com", ("pexels.com",))
    assert host_allowed("anything.org", None)
