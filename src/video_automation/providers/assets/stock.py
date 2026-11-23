"""Pexels・Pixabay（写真・動画。APIキー必須、商用利用可の独自ライセンス）。"""

from typing import Any

from video_automation.providers.assets.base import AssetSource, SourceNotFoundError
from video_automation.schemas.asset_source import (
    AssetSearchRequest,
    AssetSearchResult,
    MediaType,
    MediaVariant,
    Orientation,
    SourceName,
)
from video_automation.services.assets.license import PEXELS_LICENSE, PIXABAY_LICENSE


def _non_ascii(s: str) -> bool:
    return any(ord(c) > 127 for c in s)


class PexelsSource(AssetSource):
    name = SourceName.PEXELS
    label = "Pexels"
    media_types = (MediaType.IMAGE, MediaType.VIDEO)
    requires_key = True
    terms_url = "https://www.pexels.com/license/"
    # 動画ファイルは videos.pexels.com（古い素材は vimeo 経由）から配信される
    download_hosts = ("pexels.com", "vimeo.com", "vimeocdn.com")
    BASE = "https://api.pexels.com/v1"

    def _h(self) -> dict[str, str]:
        return {"Authorization": self.api_key or ""}

    def _photo(self, p: dict[str, Any]) -> AssetSearchResult:
        src = p.get("src") or {}
        variants = (
            [
                MediaVariant(
                    url=src["original"],
                    width=p.get("width"),
                    height=p.get("height"),
                    label="original",
                )
            ]
            if src.get("original")
            else []
        )
        for k in ("large2x", "large"):
            if src.get(k):
                variants.append(MediaVariant(url=src[k], label=k))
        return AssetSearchResult(
            source=self.name,
            source_id=str(p["id"]),
            media_type=MediaType.IMAGE,
            title=p.get("alt") or "",
            creator=p.get("photographer"),
            creator_url=p.get("photographer_url"),
            landing_url=p.get("url"),
            preview_url=src.get("medium") or src.get("small"),
            variants=variants,
            width=p.get("width"),
            height=p.get("height"),
            license=PEXELS_LICENSE,
        )

    def _video(self, v: dict[str, Any]) -> AssetSearchResult:
        files = [
            f
            for f in v.get("video_files") or []
            if f.get("link") and f.get("quality") != "hls" and f.get("file_type") == "video/mp4"
        ]
        user = v.get("user") or {}
        return AssetSearchResult(
            source=self.name,
            source_id=str(v["id"]),
            media_type=MediaType.VIDEO,
            title="",
            creator=user.get("name"),
            creator_url=user.get("url"),
            landing_url=v.get("url"),
            preview_url=v.get("image"),
            variants=[
                MediaVariant(
                    url=f["link"],
                    width=f.get("width"),
                    height=f.get("height"),
                    label=f.get("quality") or "",
                    mime=f.get("file_type"),
                )
                for f in files
            ],
            width=v.get("width"),
            height=v.get("height"),
            duration_sec=v.get("duration"),
            license=PEXELS_LICENSE,
        )

    async def search(self, req: AssetSearchRequest) -> list[AssetSearchResult]:
        self._require(req.media_type)
        params: dict[str, Any] = {"query": req.query, "page": req.page, "per_page": req.per_page}
        if req.orientation != Orientation.ANY:
            params["orientation"] = req.orientation.value
        if _non_ascii(req.query):
            params["locale"] = "ja-JP"
        if req.media_type == MediaType.IMAGE:
            data = await self._get_json(f"{self.BASE}/search", params, self._h())
            return [self._photo(p) for p in data.get("photos") or []]
        data = await self._get_json(f"{self.BASE}/videos/search", params, self._h())
        return [self._video(v) for v in data.get("videos") or []]

    async def get(self, source_id: str, media_type: MediaType) -> AssetSearchResult:
        self._require(media_type)
        if media_type == MediaType.IMAGE:
            return self._photo(
                await self._get_json(f"{self.BASE}/photos/{source_id}", headers=self._h())
            )
        return self._video(
            await self._get_json(f"{self.BASE}/videos/videos/{source_id}", headers=self._h())
        )


class PixabaySource(AssetSource):
    name = SourceName.PIXABAY
    label = "Pixabay"
    media_types = (MediaType.IMAGE, MediaType.VIDEO)
    requires_key = True
    terms_url = "https://pixabay.com/service/license-summary/"
    download_hosts = ("pixabay.com",)
    BASE = "https://pixabay.com/api/"

    def _creator_url(self, h: dict[str, Any]) -> str | None:
        if h.get("user") and h.get("user_id"):
            return f"https://pixabay.com/users/{h['user']}-{h['user_id']}/"
        return None

    def _image(self, h: dict[str, Any]) -> AssetSearchResult:
        w, ht = h.get("imageWidth"), h.get("imageHeight")
        variants = []
        if h.get("largeImageURL"):
            # 通常のAPIキーでは最大 1280px（フル解像度はPixabayの承認が必要）
            lw = lh = None
            if w and ht:
                scale = min(1.0, 1280 / max(w, ht))
                lw, lh = round(w * scale), round(ht * scale)
            variants.append(
                MediaVariant(url=h["largeImageURL"], width=lw, height=lh, label="large")
            )
        return AssetSearchResult(
            source=self.name,
            source_id=str(h["id"]),
            media_type=MediaType.IMAGE,
            title=h.get("tags") or "",
            creator=h.get("user"),
            creator_url=self._creator_url(h),
            landing_url=h.get("pageURL"),
            preview_url=h.get("webformatURL") or h.get("previewURL"),
            variants=variants,
            width=w,
            height=ht,
            tags=[t.strip() for t in (h.get("tags") or "").split(",") if t.strip()],
            license=PIXABAY_LICENSE,
        )

    def _video(self, h: dict[str, Any]) -> AssetSearchResult:
        vids = h.get("videos") or {}
        variants = [
            MediaVariant(
                url=v["url"],
                width=v.get("width"),
                height=v.get("height"),
                label=k,
                size=v.get("size") or None,
                mime="video/mp4",
            )
            for k in ("large", "medium", "small", "tiny")
            if (v := vids.get(k)) and v.get("url")
        ]
        thumb = next((v.get("thumbnail") for v in vids.values() if v.get("thumbnail")), None)
        best = variants[0] if variants else None
        return AssetSearchResult(
            source=self.name,
            source_id=str(h["id"]),
            media_type=MediaType.VIDEO,
            title=h.get("tags") or "",
            creator=h.get("user"),
            creator_url=self._creator_url(h),
            landing_url=h.get("pageURL"),
            preview_url=thumb,
            variants=variants,
            width=best.width if best else None,
            height=best.height if best else None,
            duration_sec=h.get("duration"),
            tags=[t.strip() for t in (h.get("tags") or "").split(",") if t.strip()],
            license=PIXABAY_LICENSE,
        )

    def _params(self, extra: dict[str, Any]) -> dict[str, Any]:
        return {"key": self.api_key, "safesearch": "true", **extra}

    async def search(self, req: AssetSearchRequest) -> list[AssetSearchResult]:
        self._require(req.media_type)
        p: dict[str, Any] = {"q": req.query[:100], "page": req.page, "per_page": req.per_page}
        if _non_ascii(req.query):
            p["lang"] = "ja"
        if req.orientation in (Orientation.LANDSCAPE, Orientation.PORTRAIT):
            p["orientation"] = (
                "horizontal" if req.orientation == Orientation.LANDSCAPE else "vertical"
            )
        if req.media_type == MediaType.IMAGE:
            data = await self._get_json(self.BASE, self._params(p))
            return [self._image(h) for h in data.get("hits") or []]
        data = await self._get_json(self.BASE + "videos/", self._params(p))
        return [self._video(h) for h in data.get("hits") or []]

    async def get(self, source_id: str, media_type: MediaType) -> AssetSearchResult:
        self._require(media_type)
        url = self.BASE if media_type == MediaType.IMAGE else self.BASE + "videos/"
        data = await self._get_json(url, self._params({"id": source_id}))
        hits = data.get("hits") or []
        if not hits:
            raise SourceNotFoundError(f"pixabay: {source_id} not found")
        return self._image(hits[0]) if media_type == MediaType.IMAGE else self._video(hits[0])
