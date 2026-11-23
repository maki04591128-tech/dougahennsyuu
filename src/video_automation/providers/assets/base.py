"""Asset Provider Interface（P6-01）。

各素材サービスは search（一覧）と get（ID指定の詳細）を実装する。ダウンロードは
クライアントから URL を受け取らず、必ず get で取得元から取り直した URL を使う
（任意URLの取得を防ぎ、ライセンス情報も取得時点のものを記録するため）。
"""

from abc import ABC, abstractmethod
from typing import Any

import httpx

from video_automation.schemas.asset_source import (
    AssetSearchRequest,
    AssetSearchResult,
    MediaType,
    SourceName,
)


class AssetSourceError(Exception):
    # メッセージに機密（APIキー等）を含めないため、Job のエラー表示に使ってよい
    public_message = True
    code = "asset_source_error"


class SourceNotConfiguredError(AssetSourceError):
    code = "source_not_configured"


class SourceAuthError(AssetSourceError):
    code = "source_auth_failed"


class SourceRateLimitedError(AssetSourceError):
    code = "source_rate_limited"

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class SourceNotFoundError(AssetSourceError):
    code = "source_item_not_found"


class UnsupportedMediaTypeError(AssetSourceError):
    code = "unsupported_media_type"


class AssetSource(ABC):
    name: SourceName
    label: str
    media_types: tuple[MediaType, ...]
    requires_key: bool
    terms_url: str
    # ダウンロードを許可するホスト（None=任意の公開ホスト。Openverse は収集元が多岐にわたるため）
    download_hosts: tuple[str, ...] | None = None

    def __init__(
        self,
        api_key: str | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        user_agent: str = "VideoAutomation",
        timeout: float = 15.0,
    ) -> None:
        self.api_key = api_key
        self._client = httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(timeout, connect=5.0),
            headers={"User-Agent": user_agent},
            trust_env=True,
            follow_redirects=False,
        )

    @property
    def configured(self) -> bool:
        return not self.requires_key or bool(self.api_key)

    def _require(self, media_type: MediaType) -> None:
        if not self.configured:
            raise SourceNotConfiguredError(f"{self.name}: API key is not set")
        if media_type not in self.media_types:
            raise UnsupportedMediaTypeError(f"{self.name} does not provide {media_type}")

    async def _get_json(
        self, url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None
    ) -> Any:
        try:
            r = await self._client.get(url, params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise AssetSourceError(f"{self.name}: {type(exc).__name__}") from exc
        if r.status_code in (401, 403):
            raise SourceAuthError(f"{self.name}: authentication failed ({r.status_code})")
        if r.status_code == 404:
            raise SourceNotFoundError(f"{self.name}: not found")
        if r.status_code == 429:
            ra = r.headers.get("Retry-After") or r.headers.get("X-RateLimit-Reset")
            try:
                retry = float(ra) if ra else None
            except ValueError:
                retry = None
            raise SourceRateLimitedError(f"{self.name}: rate limited", retry)
        if r.status_code >= 400:
            raise AssetSourceError(f"{self.name}: HTTP {r.status_code}")
        try:
            return r.json()
        except ValueError as exc:
            raise AssetSourceError(f"{self.name}: invalid JSON") from exc

    @abstractmethod
    async def search(self, req: AssetSearchRequest) -> list[AssetSearchResult]: ...

    @abstractmethod
    async def get(self, source_id: str, media_type: MediaType) -> AssetSearchResult: ...

    async def aclose(self) -> None:
        await self._client.aclose()


def pick_variant(
    r: AssetSearchResult, target_w: int, target_h: int
) -> tuple[str, int | None, int | None]:
    """プロジェクト解像度を満たす最小の版（無ければ最大）を選ぶ。寸法不明は original を優先。"""
    vs = [v for v in r.variants if v.url]
    if not vs:
        raise AssetSourceError(f"{r.source}: no downloadable file")
    if r.media_type == MediaType.AUDIO:
        v = vs[0]
        return v.url, v.width, v.height
    sized = [v for v in vs if v.width and v.height]
    if not sized:
        orig = next((v for v in vs if v.label == "original"), vs[0])
        return orig.url, orig.width, orig.height
    need_long, need_short = max(target_w, target_h), min(target_w, target_h)
    ok = [
        v
        for v in sized
        if max(v.width or 0, v.height or 0) >= need_long
        and min(v.width or 0, v.height or 0) >= need_short
    ]
    if ok:
        v = min(ok, key=lambda x: (x.width or 0) * (x.height or 0))
    else:
        v = max(sized, key=lambda x: (x.width or 0) * (x.height or 0))
    return v.url, v.width, v.height
