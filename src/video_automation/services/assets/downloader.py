"""素材ダウンローダー（P6-04）。

- https のみ。リダイレクトは手動で追い、毎回宛先を検査する（最大5回）
- 宛先ホストを名前解決し、ループバック・プライベート・リンクローカル等のアドレスは拒否（SSRF対策）
- 取得元ごとに許可ホストが決まっている場合はそれ以外を拒否
- サイズ上限・Content-Type を検査し、一時ファイルに書いてから完成時に移動する
"""

import asyncio
import ipaddress
import os
import socket
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx

Resolver = Callable[[str], Awaitable[list[str]]]

_ALLOWED_TYPES = {
    "image": ("image/",),
    "video": ("video/", "application/octet-stream", "binary/octet-stream"),
    "audio": ("audio/", "application/ogg", "video/ogg", "application/octet-stream"),
}


class DownloadError(Exception):
    # メッセージに機密（APIキー等）を含めないため、Job のエラー表示に使ってよい
    public_message = True
    code = "download_failed"


class DownloadRejectedError(DownloadError):
    code = "download_rejected"


async def system_resolver(host: str) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    return sorted({str(i[4][0]) for i in infos})


def _public(ip: str) -> bool:
    a = ipaddress.ip_address(ip.split("%", 1)[0])
    return not (
        a.is_private
        or a.is_loopback
        or a.is_link_local
        or a.is_multicast
        or a.is_reserved
        or a.is_unspecified
    )


def host_allowed(host: str, allowed: tuple[str, ...] | None) -> bool:
    if allowed is None:
        return True
    h = host.lower().rstrip(".")
    return any(h == a or h.endswith("." + a) for a in allowed)


class Downloader:
    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: Resolver | None = None,
        user_agent: str = "VideoAutomation",
        max_bytes: int = 1024 * 1024 * 1024,
    ) -> None:
        self.resolver = resolver or system_resolver
        self.max_bytes = max_bytes
        self._client = httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(60.0, connect=10.0),
            headers={"User-Agent": user_agent},
            follow_redirects=False,
        )

    async def _check(self, url: httpx.URL, allowed: tuple[str, ...] | None) -> None:
        if url.scheme != "https":
            raise DownloadRejectedError(f"only https is allowed: {url.scheme}")
        host = url.host
        if not host or not host_allowed(host, allowed):
            raise DownloadRejectedError(f"host not allowed: {host}")
        try:
            ipaddress.ip_address(host)
            raise DownloadRejectedError("IP address URLs are not allowed")
        except ValueError:
            pass
        try:
            ips = await self.resolver(host)
        except OSError as exc:
            raise DownloadError(f"cannot resolve {host}") from exc
        if not ips or not all(_public(ip) for ip in ips):
            raise DownloadRejectedError(f"{host} resolves to a non-public address")

    async def fetch(
        self,
        url: str,
        dest: Path,
        media_kind: str,
        *,
        allowed_hosts: tuple[str, ...] | None = None,
        max_bytes: int | None = None,
        progress: Callable[[float], Awaitable[None]] | None = None,
    ) -> tuple[int, str]:
        """dest に保存し (バイト数, Content-Type) を返す。"""
        limit = min(max_bytes or self.max_bytes, self.max_bytes)
        current = httpx.URL(url)
        for _ in range(6):
            await self._check(current, allowed_hosts)
            req = self._client.build_request("GET", current)
            resp = await self._client.send(req, stream=True)
            try:
                if resp.status_code in (301, 302, 303, 307, 308):
                    loc = resp.headers.get("Location")
                    if not loc:
                        raise DownloadError("redirect without location")
                    current = current.join(loc)
                    continue
                if resp.status_code >= 400:
                    raise DownloadError(f"HTTP {resp.status_code}")
                ctype = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
                if not ctype.startswith(_ALLOWED_TYPES[media_kind]):
                    raise DownloadRejectedError(f"unexpected content type: {ctype or '-'}")
                total = int(resp.headers.get("Content-Length") or 0)
                if total and total > limit:
                    raise DownloadRejectedError(f"file too large: {total} bytes")
                dest.parent.mkdir(parents=True, exist_ok=True)
                tmp = dest.with_name(dest.name + ".part")
                got = 0
                try:
                    with open(tmp, "wb") as f:
                        async for chunk in resp.aiter_bytes(1024 * 256):
                            got += len(chunk)
                            if got > limit:
                                raise DownloadRejectedError("file too large")
                            f.write(chunk)
                            if progress and total:
                                await progress(min(got / total, 0.95))
                    os.replace(tmp, dest)
                except BaseException:
                    tmp.unlink(missing_ok=True)
                    raise
                return got, ctype
            finally:
                await resp.aclose()
        raise DownloadError("too many redirects")

    async def aclose(self) -> None:
        await self._client.aclose()
