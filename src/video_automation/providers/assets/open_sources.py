"""Freesound（効果音。APIキー必須）・Openverse・Wikimedia Commons（キー不要）。"""

import re
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
from video_automation.services.assets.license import (
    creative_commons,
    from_short_name,
    strip_html,
    unknown_license,
)

FREESOUND_PREVIEW_NOTE = (
    "Freesound のプレビュー音源（MP3 約128kbps）。原音のダウンロードには OAuth2 認証が必要"
)


class FreesoundSource(AssetSource):
    name = SourceName.FREESOUND
    label = "Freesound"
    media_types = (MediaType.AUDIO,)
    requires_key = True
    terms_url = "https://freesound.org/help/tos_api/"
    download_hosts = ("freesound.org",)
    BASE = "https://freesound.org/apiv2"
    FIELDS = "id,name,username,license,duration,previews,url,tags,images"

    def _h(self) -> dict[str, str]:
        return {"Authorization": f"Token {self.api_key or ''}"}

    def _sound(self, s: dict[str, Any]) -> AssetSearchResult:
        lic_raw = s.get("license") or ""
        info = from_short_name(lic_raw, lic_raw if lic_raw.startswith("http") else None)
        info = info.model_copy(update={"notes": [*info.notes, FREESOUND_PREVIEW_NOTE]})
        prev = s.get("previews") or {}
        url = prev.get("preview-hq-mp3") or prev.get("preview-lq-mp3")
        images = s.get("images") or {}
        user = s.get("username")
        return AssetSearchResult(
            source=self.name,
            source_id=str(s["id"]),
            media_type=MediaType.AUDIO,
            title=s.get("name") or "",
            creator=user,
            creator_url=f"https://freesound.org/people/{user}/" if user else None,
            landing_url=s.get("url"),
            preview_url=images.get("waveform_m") or images.get("spectral_m"),
            variants=[MediaVariant(url=url, label="preview-hq-mp3", mime="audio/mpeg")]
            if url
            else [],
            duration_sec=s.get("duration"),
            tags=list(s.get("tags") or [])[:20],
            license=info,
        )

    async def search(self, req: AssetSearchRequest) -> list[AssetSearchResult]:
        self._require(req.media_type)
        params: dict[str, Any] = {
            "query": req.query,
            "page": req.page,
            "page_size": req.per_page,
            "fields": self.FIELDS,
        }
        if req.max_duration_sec:
            params["filter"] = f"duration:[0 TO {req.max_duration_sec:g}]"
        try:
            data = await self._get_json(f"{self.BASE}/search/", params, self._h())
        except SourceNotFoundError:
            # 旧API（/search/text/）のサーバーにも対応
            data = await self._get_json(f"{self.BASE}/search/text/", params, self._h())
        return [self._sound(s) for s in data.get("results") or []]

    async def get(self, source_id: str, media_type: MediaType) -> AssetSearchResult:
        self._require(media_type)
        data = await self._get_json(
            f"{self.BASE}/sounds/{int(source_id)}/", {"fields": self.FIELDS}, self._h()
        )
        return self._sound(data)


class OpenverseSource(AssetSource):
    name = SourceName.OPENVERSE
    label = "Openverse（CCライセンス）"
    media_types = (MediaType.IMAGE, MediaType.AUDIO)
    requires_key = False
    terms_url = "https://docs.openverse.org/terms_of_service.html"
    download_hosts = None  # Flickr・Wikimedia 等の収集元から配信される（公開アドレスのみ許可）
    BASE = "https://api.openverse.org/v1"
    ANON_PAGE_SIZE = 20  # 匿名利用の上限

    def _path(self, mt: MediaType) -> str:
        return "images" if mt == MediaType.IMAGE else "audio"

    def _item(self, d: dict[str, Any], mt: MediaType) -> AssetSearchResult:
        lic = d.get("license")
        info = (
            creative_commons(lic, d.get("license_version"), d.get("license_url"))
            if lic
            else unknown_license()
        )
        dur = d.get("duration")
        return AssetSearchResult(
            source=self.name,
            source_id=str(d["id"]),
            media_type=mt,
            title=d.get("title") or "",
            creator=d.get("creator"),
            creator_url=d.get("creator_url"),
            landing_url=d.get("foreign_landing_url"),
            preview_url=d.get("thumbnail"),
            variants=[
                MediaVariant(
                    url=d["url"],
                    width=d.get("width"),
                    height=d.get("height"),
                    label="original",
                    size=d.get("filesize"),
                )
            ]
            if d.get("url")
            else [],
            width=d.get("width"),
            height=d.get("height"),
            duration_sec=dur / 1000 if isinstance(dur, int | float) else None,  # ミリ秒
            tags=[t.get("name", "") for t in d.get("tags") or [] if isinstance(t, dict)][:20],
            license=info,
        )

    async def search(self, req: AssetSearchRequest) -> list[AssetSearchResult]:
        self._require(req.media_type)
        params: dict[str, Any] = {
            "q": req.query,
            "page": req.page,
            "page_size": min(req.per_page, self.ANON_PAGE_SIZE),
        }
        if req.allowed_only:
            params["license_type"] = "commercial,modification"
        if req.media_type == MediaType.IMAGE and req.orientation != Orientation.ANY:
            params["aspect_ratio"] = {
                Orientation.LANDSCAPE: "wide",
                Orientation.PORTRAIT: "tall",
                Orientation.SQUARE: "square",
            }[req.orientation]
        data = await self._get_json(f"{self.BASE}/{self._path(req.media_type)}/", params)
        return [self._item(d, req.media_type) for d in data.get("results") or []]

    async def get(self, source_id: str, media_type: MediaType) -> AssetSearchResult:
        self._require(media_type)
        d = await self._get_json(f"{self.BASE}/{self._path(media_type)}/{source_id}/")
        return self._item(d, media_type)


class WikimediaSource(AssetSource):
    name = SourceName.WIKIMEDIA
    label = "Wikimedia Commons"
    media_types = (MediaType.IMAGE, MediaType.VIDEO, MediaType.AUDIO)
    requires_key = False
    terms_url = "https://commons.wikimedia.org/wiki/Commons:Reusing_content_outside_Wikimedia"
    download_hosts = ("wikimedia.org",)
    API = "https://commons.wikimedia.org/w/api.php"
    _FILETYPE = {MediaType.IMAGE: "bitmap", MediaType.VIDEO: "video", MediaType.AUDIO: "audio"}
    _META = "LicenseShortName|LicenseUrl|Artist|ObjectName|ImageDescription|Copyrighted|UsageTerms"

    def _base_params(self) -> dict[str, Any]:
        return {
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "prop": "imageinfo",
            "iiprop": "url|size|mime|extmetadata",
            "iiurlwidth": "640",
            "iiextmetadatafilter": self._META,
        }

    # 大きすぎる画像（数万画素の絵画など）は縮小版も選べるようにする（書き出しが止まらない）。
    # Wikimedia の縮小版は決まった幅だけ作れる（2560 などは 400 になる）
    _THUMB_WIDTHS = (1920, 3840)
    _THUMB_RE = re.compile(r"/\d+px-")

    @classmethod
    def _thumbs(cls, ii: dict[str, Any], mt: MediaType) -> list[MediaVariant]:
        thumb = (ii.get("thumburl") or "").split("?", 1)[0]
        w, h = ii.get("width") or 0, ii.get("height") or 0
        if mt != MediaType.IMAGE or not cls._THUMB_RE.search(thumb) or not w or not h:
            return []
        out = []
        for tw in cls._THUMB_WIDTHS:
            if w > tw * 1.5:  # 元とほとんど変わらない縮小版は作らない
                out.append(
                    MediaVariant(
                        url=cls._THUMB_RE.sub(f"/{tw}px-", thumb, count=1),
                        width=tw,
                        height=round(h * tw / w),
                        label=f"{tw}px",
                        mime=ii.get("thumbmime") or ii.get("mime"),
                    )
                )
        return out

    def _page(self, p: dict[str, Any], mt: MediaType) -> AssetSearchResult | None:
        infos = p.get("imageinfo") or []
        if not infos:
            return None
        ii = infos[0]
        meta = {k: (v or {}).get("value") for k, v in (ii.get("extmetadata") or {}).items()}
        short = strip_html(meta.get("LicenseShortName")) or ""
        if str(meta.get("Copyrighted", "")).lower() == "false" and not short:
            short = "Public domain"
        info = from_short_name(short, meta.get("LicenseUrl"))
        title = strip_html(meta.get("ObjectName")) or (p.get("title") or "").removeprefix("File:")
        mime = ii.get("mime")
        variants = (
            [
                MediaVariant(
                    url=ii["url"],
                    width=ii.get("width") or None,
                    height=ii.get("height") or None,
                    label="original",
                    mime=mime,
                    size=ii.get("size"),
                )
            ]
            if ii.get("url")
            else []
        )
        variants += self._thumbs(ii, mt)
        return AssetSearchResult(
            source=self.name,
            source_id=str(p["pageid"]),
            media_type=mt,
            title=title,
            creator=strip_html(meta.get("Artist")),
            landing_url=ii.get("descriptionurl"),
            preview_url=ii.get("thumburl") if mt != MediaType.AUDIO else None,
            variants=variants,
            width=ii.get("width") or None,
            height=ii.get("height") or None,
            duration_sec=ii.get("duration"),
            license=info,
        )

    async def search(self, req: AssetSearchRequest) -> list[AssetSearchResult]:
        self._require(req.media_type)
        params = {
            **self._base_params(),
            "generator": "search",
            "gsrsearch": f"{req.query} filetype:{self._FILETYPE[req.media_type]}",
            "gsrnamespace": "6",
            "gsrlimit": str(req.per_page),
            "gsroffset": str((req.page - 1) * req.per_page),
        }
        data = await self._get_json(self.API, params)
        pages = sorted(
            (data.get("query") or {}).get("pages") or [], key=lambda p: p.get("index", 0)
        )
        out = [self._page(p, req.media_type) for p in pages]
        return [r for r in out if r is not None]

    async def get(self, source_id: str, media_type: MediaType) -> AssetSearchResult:
        self._require(media_type)
        data = await self._get_json(
            self.API, {**self._base_params(), "pageids": str(int(source_id))}
        )
        pages = (data.get("query") or {}).get("pages") or []
        r = self._page(pages[0], media_type) if pages and not pages[0].get("missing") else None
        if r is None:
            raise SourceNotFoundError(f"wikimedia: {source_id} not found")
        return r
