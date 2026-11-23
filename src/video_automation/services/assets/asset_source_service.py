"""素材検索・取得・ライセンス管理（Phase 6: P6-01〜05）。"""

import asyncio
import hashlib
import json
import mimetypes
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from sqlalchemy import delete, select

from video_automation.app.logging import get_logger
from video_automation.app.secrets import SecretProtector
from video_automation.app.settings import Settings
from video_automation.core.jobs.job_manager import JobManager
from video_automation.database.database import Database
from video_automation.database.models import (
    Asset,
    AssetLicenseRow,
    AssetRequestRow,
    AssetSourceKey,
    AudioFile,
    MusicGeneration,
    Project,
    SearchCacheRow,
    SearchResultRow,
)
from video_automation.providers.assets.base import (
    AssetSource,
    AssetSourceError,
    SourceNotConfiguredError,
    UnsupportedMediaTypeError,
    pick_variant,
)
from video_automation.providers.assets.open_sources import (
    FreesoundSource,
    OpenverseSource,
    WikimediaSource,
)
from video_automation.providers.assets.stock import PexelsSource, PixabaySource
from video_automation.schemas.asset_source import (
    AssetDownloadRequest,
    AssetDownloadResponse,
    AssetRef,
    AssetRequestRead,
    AssetRequestStatus,
    AssetSearchRequest,
    AssetSearchResponse,
    AssetSearchResult,
    CreditEntry,
    CreditsResponse,
    LicenseCheck,
    LicenseInfo,
    LicenseInspectRequest,
    LicenseVerdict,
    MediaType,
    SourceName,
    SourceStatus,
)
from video_automation.schemas.timeline import AssetKind, AssetRead
from video_automation.services.assets.downloader import Downloader, Resolver
from video_automation.services.assets.license import ACESTEP_LICENSE, check, evaluate
from video_automation.services.timeline.timeline_service import (
    CommandError,
    TimelineNotFoundError,
    TimelineService,
)

log = get_logger("assets")

SOURCE_CLASSES: dict[SourceName, type[AssetSource]] = {
    SourceName.PEXELS: PexelsSource,
    SourceName.PIXABAY: PixabaySource,
    SourceName.FREESOUND: FreesoundSource,
    SourceName.OPENVERSE: OpenverseSource,
    SourceName.WIKIMEDIA: WikimediaSource,
}

_EXT_BY_TYPE = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "audio/mpeg": ".mp3",
    "audio/ogg": ".ogg",
    "application/ogg": ".ogg",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/flac": ".flac",
}


class LicenseAckRequiredError(Exception):
    """ライセンスが不明・非営利限定などで、ユーザーの明示承認が必要。"""

    code = "license_ack_required"

    def __init__(self, check: LicenseCheck) -> None:
        super().__init__("; ".join(check.reasons) or "license acknowledgement required")
        self.check = check


class AssetSourceService:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        timeline: TimelineService,
        jobs: JobManager,
        protector: SecretProtector,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: Resolver | None = None,
    ) -> None:
        self.settings = settings
        self.db = db
        self.timeline = timeline
        self.jobs = jobs
        self.protector = protector
        self._transport = transport
        self._instances: dict[SourceName, tuple[str | None, AssetSource]] = {}
        self.downloader = Downloader(
            transport=transport,
            resolver=resolver,
            user_agent=settings.http_user_agent,
            max_bytes=settings.asset_max_download_mb * 1024 * 1024,
        )

    # ---------- APIキー・取得元 ----------
    def _env_key(self, name: SourceName) -> str | None:
        secret = getattr(self.settings, f"{name.value}_api_key", None)
        return (
            secret.get_secret_value() if secret is not None and secret.get_secret_value() else None
        )

    async def _stored_keys(self) -> dict[str, bytes]:
        async with self.db.session_factory() as s:
            rows = (await s.execute(select(AssetSourceKey))).scalars().all()
        return {r.source: r.api_key_encrypted for r in rows}

    async def _key(self, name: SourceName, stored: dict[str, bytes] | None = None) -> str | None:
        env = self._env_key(name)
        if env:
            return env
        stored = stored if stored is not None else await self._stored_keys()
        blob = stored.get(name.value)
        if blob is None:
            return None
        try:
            return self.protector.unprotect(blob).decode("utf-8")
        except Exception:
            log.warning("asset_key_unprotect_failed", source=name.value)
            return None

    async def source(self, name: SourceName, stored: dict[str, bytes] | None = None) -> AssetSource:
        key = await self._key(name, stored) if SOURCE_CLASSES[name].requires_key else None
        cached = self._instances.get(name)
        if cached and cached[0] == key:
            return cached[1]
        if cached:
            await cached[1].aclose()
        inst = SOURCE_CLASSES[name](
            key, transport=self._transport, user_agent=self.settings.http_user_agent
        )
        self._instances[name] = (key, inst)
        return inst

    async def list_sources(self) -> list[SourceStatus]:
        stored = await self._stored_keys()
        out = []
        for name, cls in SOURCE_CLASSES.items():
            src = await self.source(name, stored)
            out.append(
                SourceStatus(
                    name=name,
                    label=cls.label,
                    media_types=list(cls.media_types),
                    requires_key=cls.requires_key,
                    configured=src.configured,
                    key_from_env=bool(self._env_key(name)),
                    terms_url=cls.terms_url,
                )
            )
        return out

    async def set_key(self, name: SourceName, api_key: str) -> SourceStatus:
        if not SOURCE_CLASSES[name].requires_key:
            raise CommandError(f"{name.value} does not use an API key")
        blob = self.protector.protect(api_key.strip().encode("utf-8"))
        async with self.db.session_factory() as s:
            row = await s.get(AssetSourceKey, name.value)
            if row is None:
                s.add(AssetSourceKey(source=name.value, api_key_encrypted=blob))
            else:
                row.api_key_encrypted = blob
            await s.commit()
        log.info("asset_key_saved", source=name.value)  # キー自体はログに出さない
        return next(x for x in await self.list_sources() if x.name == name)

    async def delete_key(self, name: SourceName) -> None:
        async with self.db.session_factory() as s:
            await s.execute(delete(AssetSourceKey).where(AssetSourceKey.source == name.value))
            await s.commit()

    # ---------- 検索 ----------
    def _cache_key(self, name: SourceName, req: AssetSearchRequest) -> str:
        body = req.model_dump(mode="json", exclude={"sources", "allowed_only", "max_duration_sec"})
        raw = json.dumps({"s": name.value, **body}, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    async def _search_one(
        self, name: SourceName, req: AssetSearchRequest, stored: dict[str, bytes]
    ) -> tuple[list[AssetSearchResult], bool]:
        key = self._cache_key(name, req)
        now = datetime.now(UTC)
        async with self.db.session_factory() as s:
            row = await s.get(SearchCacheRow, key)
            if row is not None and _aware(row.expires_at) > now:
                return [AssetSearchResult.model_validate(r) for r in row.results], True
        src = await self.source(name, stored)
        results = await src.search(req)
        results = [self._annotate(r) for r in results]
        ttl = timedelta(hours=self.settings.asset_search_cache_hours)
        if ttl.total_seconds() > 0:
            dumped = [r.model_dump(mode="json") for r in results]
            async with self.db.session_factory() as s:
                await s.merge(
                    SearchCacheRow(key=key, source=name.value, results=dumped, expires_at=now + ttl)
                )
                for r, d in zip(results, dumped, strict=True):
                    await s.merge(
                        SearchResultRow(
                            source=name.value,
                            media_type=r.media_type.value,
                            source_id=r.source_id,
                            data=d,
                            expires_at=now + ttl,
                        )
                    )
                await s.execute(delete(SearchCacheRow).where(SearchCacheRow.expires_at < now))
                await s.execute(delete(SearchResultRow).where(SearchResultRow.expires_at < now))
                await s.commit()
        return results, False

    @staticmethod
    def _annotate(r: AssetSearchResult) -> AssetSearchResult:
        c = check(r)
        return r.model_copy(update={"verdict": c.verdict, "attribution": c.attribution})

    async def search(self, req: AssetSearchRequest) -> AssetSearchResponse:
        stored = await self._stored_keys()
        resp = AssetSearchResponse(results=[])
        targets: list[SourceName] = []
        for name in req.sources or list(SOURCE_CLASSES):
            cls = SOURCE_CLASSES[name]
            if req.media_type not in cls.media_types:
                resp.skipped[name.value] = f"{req.media_type.value} 非対応"
            elif not (await self.source(name, stored)).configured:
                resp.skipped[name.value] = "APIキー未設定"
            else:
                targets.append(name)
        outs = await asyncio.gather(
            *(self._search_one(n, req, stored) for n in targets), return_exceptions=True
        )
        per_source: list[list[AssetSearchResult]] = []
        for name, out in zip(targets, outs, strict=True):
            if isinstance(out, BaseException):
                if not isinstance(out, AssetSourceError):
                    log.error("asset_search_failed", source=name.value, error=type(out).__name__)
                resp.errors[name.value] = str(out)[:200] or type(out).__name__
                continue
            results, cached = out
            if cached:
                resp.cached.append(name.value)
            if req.max_duration_sec:
                results = [
                    r
                    for r in results
                    if r.duration_sec is None or r.duration_sec <= req.max_duration_sec
                ]
            if req.allowed_only:
                results = [r for r in results if r.verdict == LicenseVerdict.ALLOWED]
            per_source.append(results)
        # 取得元ごとに交互に並べる（1つのサービスに偏らないように）
        for i in range(max((len(x) for x in per_source), default=0)):
            resp.results.extend(x[i] for x in per_source if i < len(x))
        return resp

    async def cached_result(self, ref: AssetRef) -> AssetSearchResult | None:
        async with self.db.session_factory() as s:
            row = await s.get(
                SearchResultRow, (ref.source.value, ref.media_type.value, ref.source_id)
            )
        if row is None or _aware(row.expires_at) < datetime.now(UTC):
            return None
        return AssetSearchResult.model_validate(row.data)

    async def thumbnail(self, ref: AssetRef) -> tuple[bytes, str]:
        """検索済みの素材のサムネイル（UI 表示用。外部へはサーバー側から取りに行く）。"""
        r = await self.cached_result(ref)
        if r is None or not r.preview_url:
            raise TimelineNotFoundError("thumbnail not available (search again)")
        cls = SOURCE_CLASSES[ref.source]
        allowed = None if cls.download_hosts is None else (*cls.download_hosts, "openverse.org")
        with tempfile.TemporaryDirectory(prefix="va_thumb_") as tmp:
            dest = Path(tmp) / "thumb"
            _, ctype = await self.downloader.fetch(
                r.preview_url, dest, "image", allowed_hosts=allowed, max_bytes=5 * 1024 * 1024
            )
            return dest.read_bytes(), ctype

    async def asset_path(self, asset_id: str) -> tuple[Path, str]:
        async with self.db.session_factory() as s:
            a = await s.get(Asset, asset_id)
        if a is None:
            raise TimelineNotFoundError(f"asset {asset_id}")
        root = self.settings.data_dir.resolve()
        path = (root / a.path).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise TimelineNotFoundError(f"asset file {asset_id}")
        return path, path.name

    # ---------- ライセンス ----------
    async def fetch_detail(self, ref: AssetRef) -> AssetSearchResult:
        src = await self.source(ref.source)
        if ref.media_type not in src.media_types:
            raise UnsupportedMediaTypeError(f"{ref.source.value} does not provide {ref.media_type}")
        return self._annotate(await src.get(ref.source_id, ref.media_type))

    async def inspect(self, req: LicenseInspectRequest) -> LicenseCheck:
        if req.ref is not None:
            return check(await self.fetch_detail(req.ref))
        async with self.db.session_factory() as s:
            row = await s.get(AssetLicenseRow, req.asset_id)
            asset = await s.get(Asset, req.asset_id)
        if asset is None:
            raise TimelineNotFoundError(f"asset {req.asset_id}")
        if row is None:
            info = _own_license(asset.source)
            verdict, reasons = evaluate(info)
            return LicenseCheck(license=info, verdict=verdict, reasons=reasons)
        lic = LicenseInfo.model_validate(row.license)
        reasons = [] if row.verdict == LicenseVerdict.ALLOWED else ["ユーザーが条件を確認して取得"]
        return LicenseCheck(
            license=lic,
            verdict=LicenseVerdict(row.verdict),
            reasons=reasons,
            attribution=row.attribution,
        )

    # ---------- ダウンロード ----------
    async def _project(self, project_id: str) -> Project:
        async with self.db.session_factory() as s:
            p = await s.get(Project, project_id)
        if p is None:
            raise TimelineNotFoundError(f"project {project_id}")
        return p

    async def _existing(self, project_id: str, ref: AssetRef) -> Asset | None:
        async with self.db.session_factory() as s:
            return (
                await s.execute(
                    select(Asset)
                    .join(AssetLicenseRow, AssetLicenseRow.asset_id == Asset.id)
                    .where(
                        Asset.project_id == project_id,
                        AssetLicenseRow.source == ref.source.value,
                        AssetLicenseRow.source_id == ref.source_id,
                    )
                )
            ).scalar_one_or_none()

    async def download(self, req: AssetDownloadRequest) -> AssetDownloadResponse:
        await self._project(req.project_id)
        detail = await self.fetch_detail(req)
        c = check(detail)
        needs_ack = c.verdict != LicenseVerdict.ALLOWED
        if req.origin == "llm":
            if needs_ack or self.settings.llm_asset_download_requires_approval:
                async with self.db.session_factory() as s:
                    row = AssetRequestRow(
                        project_id=req.project_id,
                        source=req.source.value,
                        source_id=req.source_id,
                        media_type=req.media_type.value,
                        title=detail.title[:500],
                        license_code=detail.license.code,
                        verdict=c.verdict.value,
                        status=AssetRequestStatus.PENDING_APPROVAL.value,
                    )
                    s.add(row)
                    await s.commit()
                    rid = row.id
                return AssetDownloadResponse(status="PENDING_APPROVAL", request_id=rid, license=c)
        elif needs_ack and not req.acknowledge_license:
            raise LicenseAckRequiredError(c)
        job = self._submit(req.project_id, detail, acknowledged=needs_ack)
        return AssetDownloadResponse(status="QUEUED", job_id=job.id, license=c)

    def _submit(
        self,
        project_id: str,
        detail: AssetSearchResult,
        acknowledged: bool,
        request_id: str | None = None,
    ) -> Any:
        async def run(progress: Any) -> dict[str, Any]:
            try:
                asset = await self._download(project_id, detail, acknowledged, progress)
            except BaseException as exc:
                if request_id:
                    await self._update_request(
                        request_id, status=AssetRequestStatus.FAILED.value, error=str(exc)[:1000]
                    )
                raise
            if request_id:
                await self._update_request(
                    request_id, status=AssetRequestStatus.DONE.value, asset_id=asset.id
                )
            return asset.model_dump(mode="json")

        return self.jobs.submit("asset_download", run)

    async def _download(
        self, project_id: str, r: AssetSearchResult, acknowledged: bool, progress: Any
    ) -> AssetRead:
        ref = AssetRef(source=r.source, source_id=r.source_id, media_type=r.media_type)
        existing = await self._existing(project_id, ref)
        if existing is not None:
            return AssetRead.model_validate(existing)
        p = await self._project(project_id)
        url, _, _ = pick_variant(r, p.width, p.height)
        cls = SOURCE_CLASSES[r.source]
        out_dir = self.settings.data_dir / "projects" / project_id / "assets"
        stem = f"{r.source.value}_{_safe(r.source_id)}_{uuid4().hex[:6]}"
        tmp = out_dir / (stem + ".download")
        size, ctype = await self.downloader.fetch(
            url, tmp, r.media_type.value, allowed_hosts=cls.download_hosts, progress=progress
        )
        ext = _EXT_BY_TYPE.get(ctype) or Path(httpx.URL(url).path).suffix.lower()[:6]
        ext = ext or mimetypes.guess_extension(ctype) or ".bin"
        dest = out_dir / (stem + ext)
        tmp.replace(dest)
        kind = {
            MediaType.IMAGE: AssetKind.IMAGE,
            MediaType.VIDEO: AssetKind.VIDEO,
            MediaType.AUDIO: AssetKind.AUDIO,
        }[r.media_type]
        try:
            info = await self.timeline.ffmpeg.probe(dest)
            ok = {
                AssetKind.IMAGE: info.is_image,
                AssetKind.VIDEO: info.has_video and not info.is_image,
                AssetKind.AUDIO: info.has_audio,
            }[kind]
            if not ok:
                raise CommandError(f"downloaded file is not a valid {kind.value}")
            asset = await self.timeline.register_generated(
                project_id, dest, kind, r.source.value, (r.title or r.source_id)[:200]
            )
        except BaseException:
            dest.unlink(missing_ok=True)
            raise
        c = check(r)
        async with self.db.session_factory() as s:
            s.add(
                AssetLicenseRow(
                    asset_id=asset.id,
                    source=r.source.value,
                    source_id=r.source_id,
                    landing_url=r.landing_url,
                    download_url=url,
                    title=r.title[:500],
                    creator=(r.creator or "")[:300] or None,
                    license_code=r.license.code,
                    license=r.license.model_dump(mode="json"),
                    verdict=c.verdict.value,
                    acknowledged=acknowledged,
                    attribution=c.attribution,
                    snapshot=r.model_dump(mode="json"),
                )
            )
            await s.commit()
        log.info(
            "asset_downloaded",
            source=r.source.value,
            source_id=r.source_id,
            bytes=size,
            license=r.license.code,
        )
        return AssetRead.model_validate(asset)

    # ---------- LLM 由来の要求の承認 ----------
    async def _update_request(self, request_id: str, **values: Any) -> None:
        async with self.db.session_factory() as s:
            row = await s.get(AssetRequestRow, request_id)
            if row is not None:
                for k, v in values.items():
                    setattr(row, k, v)
                await s.commit()

    async def list_requests(
        self, project_id: str, status: AssetRequestStatus | None = None
    ) -> list[AssetRequestRead]:
        q = select(AssetRequestRow).where(AssetRequestRow.project_id == project_id)
        if status is not None:
            q = q.where(AssetRequestRow.status == status.value)
        async with self.db.session_factory() as s:
            rows = (await s.execute(q.order_by(AssetRequestRow.created_at))).scalars().all()
        return [AssetRequestRead.model_validate(r) for r in rows]

    async def decide(self, request_id: str, approve: bool) -> AssetRequestRead:
        async with self.db.session_factory() as s:
            row = await s.get(AssetRequestRow, request_id)
            if row is None:
                raise TimelineNotFoundError(f"asset request {request_id}")
            if row.status != AssetRequestStatus.PENDING_APPROVAL.value:
                raise CommandError(f"request is {row.status}")
            ref = AssetRef(
                source=SourceName(row.source),
                source_id=row.source_id,
                media_type=MediaType(row.media_type),
            )
            project_id = row.project_id
            if not approve:
                row.status = AssetRequestStatus.REJECTED.value
                await s.commit()
                return AssetRequestRead.model_validate(row)
        # 承認時に取得元から取り直す（承認待ちの間に条件が変わっている可能性があるため）
        detail = await self.fetch_detail(ref)
        needs_ack = check(detail).verdict != LicenseVerdict.ALLOWED
        job = self._submit(project_id, detail, acknowledged=needs_ack, request_id=request_id)
        await self._update_request(
            request_id,
            status=AssetRequestStatus.APPROVED.value,
            job_id=job.id,
            license_code=detail.license.code,
            verdict=check(detail).verdict.value,
        )
        async with self.db.session_factory() as s:
            row = await s.get(AssetRequestRow, request_id)
            return AssetRequestRead.model_validate(row)

    # ---------- クレジット ----------
    async def credits(self, project_id: str) -> CreditsResponse:
        await self._project(project_id)
        async with self.db.session_factory() as s:
            assets = (
                (await s.execute(select(Asset).where(Asset.project_id == project_id)))
                .scalars()
                .all()
            )
            lic = {
                r.asset_id: r
                for r in (
                    await s.execute(
                        select(AssetLicenseRow).where(
                            AssetLicenseRow.asset_id.in_([a.id for a in assets])
                        )
                    )
                ).scalars()
            }
            voice_ids = [a.audio_file_id for a in assets if a.audio_file_id]
            voice_credit = {
                af.id: af.credit
                for af in (
                    await s.execute(select(AudioFile).where(AudioFile.id.in_(voice_ids)))
                ).scalars()
            }
            music = {
                m.asset_id: m
                for m in (
                    await s.execute(
                        select(MusicGeneration).where(MusicGeneration.project_id == project_id)
                    )
                ).scalars()
            }
        entries: list[CreditEntry] = []
        voice_names: list[str] = []
        for a in assets:
            if a.id in lic:
                row = lic[a.id]
                entries.append(
                    CreditEntry(
                        asset_id=a.id,
                        name=a.name,
                        kind=a.kind,
                        source=row.source,
                        text=row.attribution or row.title,
                        license_code=row.license_code,
                    )
                )
            elif a.audio_file_id and voice_credit.get(a.audio_file_id):
                c = voice_credit[a.audio_file_id] or ""
                if c not in voice_names:
                    voice_names.append(c)
            elif a.id in music:
                m = music[a.id]
                model = f"（{m.dit_model}）" if m.dit_model else ""
                entries.append(
                    CreditEntry(
                        asset_id=a.id,
                        name=a.name,
                        kind=a.kind,
                        source="acestep",
                        text=f"BGM: ACE-Step 1.5{model} で生成（AI生成）",
                        license_code="acestep-generated",
                    )
                )
        lines: list[str] = []
        if voice_names:
            lines.append("音声: " + " / ".join(voice_names))
        groups = {"video": "映像", "image": "画像", "audio": "音声素材・BGM"}
        for kind, label in groups.items():
            items = [e for e in entries if e.kind == kind]
            if items:
                lines.append(f"{label}:")
                lines.extend(f"- {e.text}" for e in items)
        return CreditsResponse(entries=entries, text="\n".join(lines))

    async def aclose(self) -> None:
        for _, inst in self._instances.values():
            await inst.aclose()
        self._instances.clear()
        await self.downloader.aclose()


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _safe(s: str) -> str:
    return "".join(c for c in s if c.isalnum() or c in "-_")[:40] or "item"


def _own_license(source: str) -> LicenseInfo:
    """取得元の記録が無い素材（ユーザーの取り込み・生成物）。"""
    if source == "acestep":
        return ACESTEP_LICENSE
    names = {
        "import": "ユーザーが取り込んだ素材（権利はユーザーが確認）",
        "voice": "VOICEVOX で生成した音声（クレジット表記が必要）",
        "blender": "Blender で生成したタイトル",
    }
    return LicenseInfo(
        code="own",
        name=names.get(source, "自作・生成物"),
        attribution_required=source == "voice",
        commercial_use=True,
        modification=True,
    )


__all__ = ["AssetSourceService", "LicenseAckRequiredError", "SourceNotConfiguredError"]
