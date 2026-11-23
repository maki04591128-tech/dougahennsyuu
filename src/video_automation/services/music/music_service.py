"""BGM 生成（P6-07, P6-08）。

ACE-Step 1.5（別プロセスの acestep-api）に生成を依頼し、結果を素材として登録する。
"""

import asyncio
import time
from typing import Any
from uuid import uuid4

import httpx
from sqlalchemy import select

from video_automation.app.logging import get_logger
from video_automation.app.settings import Settings
from video_automation.database.database import Database
from video_automation.database.models import AssetLicenseRow, MusicGeneration, Project
from video_automation.providers.audio_generation.acestep import (
    TASK_FAILED,
    TASK_SUCCEEDED,
    AceStepError,
    AceStepProvider,
    AceStepUnavailableError,
)
from video_automation.schemas.asset_source import (
    MusicEngineStatus,
    MusicGenerateRequest,
    MusicGenerationRead,
)
from video_automation.schemas.timeline import AssetKind, AssetRead
from video_automation.services.assets.license import ACESTEP_LICENSE
from video_automation.services.model_discovery.provider_manager import ProviderManager
from video_automation.services.timeline.timeline_service import (
    TimelineNotFoundError,
    TimelineService,
)

log = get_logger("music")

INSTRUMENTAL_LYRICS = "[Instrumental]"


class MusicService:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        timeline: TimelineService,
        providers: ProviderManager,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.db = db
        self.timeline = timeline
        self.providers = providers
        key = settings.acestep_api_key.get_secret_value() if settings.acestep_api_key else None
        self.engine = AceStepProvider(
            settings.acestep_url, settings.acestep_path, transport=transport, api_key=key or None
        )
        self.poll_interval = settings.music_poll_interval_s

    async def status(self) -> MusicEngineStatus:
        st = MusicEngineStatus(
            available=False,
            base_url=self.engine.base_url,
            installed=self.engine._installed(),
        )
        try:
            h = await self.engine.health()
            m = await self.engine.models()
        except AceStepError as exc:
            st.error = str(exc)[:200]
            return st
        st.available = True
        m = m or {}
        # 文書の形式 {models:[{name,is_default}], default_model} と、実機の版が返す
        # OpenAI 互換の {object:"list", data:[{id}]} の両方に対応する
        names = [x.get("name", "") for x in m.get("models") or [] if isinstance(x, dict)]
        names += [x.get("id", "") for x in m.get("data") or [] if isinstance(x, dict)]
        loaded = (h or {}).get("loaded_model") if isinstance(h, dict) else None
        st.models = [n for n in names if n] or ([loaded] if loaded else [])
        st.default_model = m.get("default_model") or loaded
        return st

    def _body(self, req: MusicGenerateRequest) -> dict[str, Any]:
        body: dict[str, Any] = {
            "prompt": req.prompt,
            "lyrics": INSTRUMENTAL_LYRICS if req.instrumental else req.lyrics,
            "audio_duration": req.duration_sec,
            "vocal_language": req.vocal_language,
            "inference_steps": req.inference_steps,
            "batch_size": req.batch_size,
            "audio_format": req.audio_format,
            "use_random_seed": req.seed is None,
        }
        if req.seed is not None:
            body["seed"] = req.seed
        for k in ("bpm", "key_scale", "time_signature", "model"):
            v = getattr(req, k)
            if v not in (None, ""):
                body[k] = v
        return body

    async def generate(self, req: MusicGenerateRequest, progress: Any) -> list[AssetRead]:
        async with self.db.session_factory() as s:
            if await s.get(Project, req.project_id) is None:
                raise TimelineNotFoundError(f"project {req.project_id}")
        await self.engine.health()  # 未起動なら AceStepUnavailableError
        if self.settings.music_unload_llm:
            # VRAM を空けるためロード中の LLM をアンロード（失敗しても生成は続行）
            try:
                unloaded = await self.providers.unload_others("")
                if unloaded:
                    log.info("llm_unloaded_for_music", models=unloaded)
            except Exception as exc:
                log.warning("llm_unload_failed", error=type(exc).__name__)
        body = self._body(req)
        task = await self.engine.release_task(body)
        task_id = str(task.get("task_id") or "")
        if not task_id:
            raise AceStepError("ACE-Step: no task_id")
        log.info("music_task_released", task_id=task_id, queue=task.get("queue_position"))
        started = time.monotonic()
        # 進捗はAPIから得られないため、曲の長さに応じた目安で 0.05→0.9 まで進める
        expected = max(20.0, req.duration_sec * req.batch_size * 0.6)
        while True:
            await asyncio.sleep(self.poll_interval)
            items = await self.engine.query_result([task_id])
            item = next((x for x in items if str(x.get("task_id")) == task_id), None)
            elapsed = time.monotonic() - started
            if item and item.get("status") == TASK_SUCCEEDED:
                break
            if item and item.get("status") == TASK_FAILED:
                raise AceStepError(f"ACE-Step: generation failed ({str(item.get('result'))[:200]})")
            if elapsed > self.settings.music_timeout_s:
                raise AceStepError("ACE-Step: generation timed out")
            await progress(min(0.05 + 0.85 * elapsed / expected, 0.9))
        results = [r for r in item.get("result") or [] if r.get("file")]
        if not results:
            raise AceStepError("ACE-Step: no audio in result")
        out_dir = self.settings.data_dir / "projects" / req.project_id / "assets"
        out_dir.mkdir(parents=True, exist_ok=True)
        assets: list[AssetRead] = []
        for i, r in enumerate(results):
            data = await self.engine.download(r["file"])
            path = out_dir / f"music_{uuid4().hex[:8]}.{req.audio_format}"
            path.write_bytes(data)
            name = f"BGM「{req.prompt[:24]}」" + (f" #{i + 1}" if len(results) > 1 else "")
            try:
                asset = await self.timeline.register_generated(
                    req.project_id, path, AssetKind.AUDIO, "acestep", name
                )
            except BaseException:
                path.unlink(missing_ok=True)
                raise
            metas = r.get("metas") or {}
            async with self.db.session_factory() as s:
                s.add(
                    MusicGeneration(
                        project_id=req.project_id,
                        asset_id=asset.id,
                        task_id=task_id,
                        prompt=req.prompt,
                        lyrics=body["lyrics"],
                        params=body,
                        dit_model=r.get("dit_model"),
                        lm_model=r.get("lm_model"),
                        seed=str(r.get("seed_value") or "") or None,
                        duration_sec=asset.duration_sec,
                        result={k: v for k, v in r.items() if k != "wave"},
                    )
                )
                s.add(
                    AssetLicenseRow(
                        asset_id=asset.id,
                        source="acestep",
                        source_id=task_id,
                        title=name,
                        license_code=ACESTEP_LICENSE.code,
                        license=ACESTEP_LICENSE.model_dump(mode="json"),
                        verdict="allowed",
                        attribution=_credit(r.get("dit_model")),
                        snapshot={"request": body, "metas": metas},
                    )
                )
                await s.commit()
            assets.append(AssetRead.model_validate(asset))
        log.info(
            "music_generated",
            task_id=task_id,
            files=len(assets),
            sec=round(time.monotonic() - started, 1),
        )
        return assets

    async def list(self, project_id: str) -> list[MusicGenerationRead]:
        async with self.db.session_factory() as s:
            rows = (
                (
                    await s.execute(
                        select(MusicGeneration)
                        .where(MusicGeneration.project_id == project_id)
                        .order_by(MusicGeneration.created_at)
                    )
                )
                .scalars()
                .all()
            )
        return [MusicGenerationRead.model_validate(r) for r in rows]

    async def aclose(self) -> None:
        await self.engine.aclose()


def _credit(model: str | None) -> str:
    return "BGM: ACE-Step 1.5 で生成（AI生成" + (f", {model}）" if model else "）")


__all__ = ["AceStepUnavailableError", "MusicService"]
