"""Provider Manager (P2-01〜05 統合)。

- 既定ポートの Ollama / LM Studio を自動検出（無制限なポートスキャンはしない）
- OpenAI互換・クラウドAPIの手動登録（APIキーは DPAPI で暗号化して保存）
- 検出結果・Capability を DB に保存
- ACE-Step は Music Provider として別枠で検出
"""

import asyncio
import importlib.util
import re
from uuid import uuid4

import httpx

from video_automation.app.logging import get_logger
from video_automation.app.secrets import SecretProtector, default_protector
from video_automation.app.settings import Settings
from video_automation.database.database import Database
from video_automation.database.repositories.llm import ACTIVE_MODEL_KEY, LlmRepository
from video_automation.providers.audio_generation.acestep import AceStepProvider
from video_automation.providers.base import LLMProvider, ModelNotFoundError, ProviderError
from video_automation.providers.llm.cloud import (
    DEFAULT_URLS,
    AnthropicProvider,
    GeminiProvider,
    OpenAIProvider,
)
from video_automation.providers.llm.lmstudio import LMStudioProvider
from video_automation.providers.llm.ollama import OllamaProvider
from video_automation.providers.llm.openai_compatible import OpenAICompatibleProvider
from video_automation.schemas.provider import (
    DiscoveryResult,
    ModelInfo,
    MusicProviderStatus,
    ProviderConfig,
    ProviderStatus,
    ProviderType,
    RegisterProviderRequest,
)

log = get_logger("providers")

PROVIDER_CLASSES: dict[ProviderType, type[LLMProvider]] = {
    ProviderType.OLLAMA: OllamaProvider,
    ProviderType.LMSTUDIO: LMStudioProvider,
    ProviderType.OPENAI_COMPATIBLE: OpenAICompatibleProvider,
    ProviderType.OPENAI: OpenAIProvider,
    ProviderType.ANTHROPIC: AnthropicProvider,
    ProviderType.GEMINI: GeminiProvider,
}
BUILTIN_IDS = {"ollama-local", "lmstudio-local", "openai-env", "anthropic-env", "gemini-env"}
_PROCESS_NAMES = {
    ProviderType.OLLAMA: ("ollama",),
    ProviderType.LMSTUDIO: ("lm studio", "lmstudio", "llmster"),
}


class ProviderNotFoundError(KeyError):
    pass


def _detect_process(ptype: ProviderType) -> bool | None:
    """Windowsプロセス検出は補助情報（psutil がある場合のみ）。"""
    names = _PROCESS_NAMES.get(ptype)
    if not names or importlib.util.find_spec("psutil") is None:
        return None
    import psutil

    try:
        for p in psutil.process_iter(["name"]):
            n = (p.info.get("name") or "").lower()
            if any(x in n for x in names):
                return True
    except Exception:
        return None
    return False


def split_model_key(key: str) -> tuple[str, str]:
    provider_id, sep, model_id = key.partition("::")
    if not sep or not provider_id or not model_id:
        raise ModelNotFoundError(key)
    return provider_id, model_id


class ProviderManager:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        *,
        protector: SecretProtector | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.db = db
        self.protector = protector or default_protector()
        self._transport = transport
        self._instances: dict[str, tuple[tuple[str, str, bool], LLMProvider]] = {}
        # 検出の同時実行で同じ行を二重挿入しないよう直列化する
        self._discover_lock = asyncio.Lock()

    # ----- 構成 -----
    def _builtin(self) -> list[tuple[ProviderConfig, str | None]]:
        s = self.settings
        out: list[tuple[ProviderConfig, str | None]] = []
        if s.autodetect_ollama:
            out.append(
                (
                    ProviderConfig(
                        id="ollama-local",
                        type=ProviderType.OLLAMA,
                        name="Ollama",
                        base_url=s.ollama_url,
                        auto_discovered=True,
                    ),
                    None,
                )
            )
        if s.autodetect_lmstudio:
            out.append(
                (
                    ProviderConfig(
                        id="lmstudio-local",
                        type=ProviderType.LMSTUDIO,
                        name="LM Studio",
                        base_url=s.lmstudio_url,
                        auto_discovered=True,
                    ),
                    None,
                )
            )
        for ptype, secret in (
            (ProviderType.OPENAI, s.openai_api_key),
            (ProviderType.ANTHROPIC, s.anthropic_api_key),
            (ProviderType.GEMINI, s.gemini_api_key),
        ):
            if secret is not None and secret.get_secret_value():
                out.append(
                    (
                        ProviderConfig(
                            id=f"{ptype.value}-env",
                            type=ptype,
                            name=f"{ptype.value} (env)",
                            base_url=DEFAULT_URLS[ptype],
                            has_api_key=True,
                        ),
                        secret.get_secret_value(),
                    )
                )
        return out

    async def configs(self) -> list[tuple[ProviderConfig, str | None]]:
        out = self._builtin()
        async with self.db.session_factory() as session:
            rows = await LlmRepository(session).list_providers()
        for row in rows:
            if row.id in BUILTIN_IDS:
                continue
            key: str | None = None
            if row.api_key_encrypted:
                try:
                    key = self.protector.unprotect(row.api_key_encrypted).decode("utf-8")
                except Exception:
                    log.warning("api_key_unprotect_failed", provider_id=row.id)
            out.append(
                (
                    ProviderConfig(
                        id=row.id,
                        type=ProviderType(row.type),
                        name=row.name,
                        base_url=row.base_url,
                        enabled=row.enabled,
                        has_api_key=row.api_key_encrypted is not None,
                    ),
                    key,
                )
            )
        return out

    def _instance(self, cfg: ProviderConfig, key: str | None) -> LLMProvider:
        sig = (cfg.base_url, cfg.type.value, key is not None)
        cached = self._instances.get(cfg.id)
        if cached and cached[0] == sig:
            return cached[1]
        provider = PROVIDER_CLASSES[cfg.type](cfg, api_key=key, transport=self._transport)
        self._instances[cfg.id] = (sig, provider)
        return provider

    async def get(self, provider_id: str) -> LLMProvider:
        for cfg, key in await self.configs():
            if cfg.id == provider_id:
                return self._instance(cfg, key)
        raise ProviderNotFoundError(provider_id)

    async def resolve(self, model_key: str) -> tuple[LLMProvider, str]:
        provider_id, model_id = split_model_key(model_key)
        return await self.get(provider_id), model_id

    # ----- 検出 -----
    async def _discover_one(
        self, cfg: ProviderConfig, key: str | None
    ) -> tuple[ProviderStatus, list[ModelInfo]]:
        provider = self._instance(cfg, key)
        try:
            status, models = await asyncio.wait_for(
                provider.discover(), timeout=self.settings.discovery_timeout_s
            )
        except TimeoutError:
            status, models = provider._status(False, error="timeout"), []
        except ProviderError as exc:
            status, models = provider._status(False, error=str(exc)[:200]), []
        status.process_detected = _detect_process(cfg.type)
        return status, models

    async def discover_music(self) -> list[MusicProviderStatus]:
        ace = AceStepProvider(
            self.settings.acestep_url, self.settings.acestep_path, transport=self._transport
        )
        try:
            return [await ace.check_connection()]
        finally:
            await ace.aclose()

    async def discover(self) -> DiscoveryResult:
        async with self._discover_lock:
            return await self._discover()

    async def _discover(self) -> DiscoveryResult:
        cfgs = [(c, k) for c, k in await self.configs() if c.enabled]
        results = await asyncio.gather(*(self._discover_one(c, k) for c, k in cfgs))
        music = await self.discover_music()
        async with self.db.session_factory() as session:
            repo = LlmRepository(session)
            for (cfg, _), (status, models) in zip(cfgs, results, strict=True):
                await repo.upsert_provider(cfg)
                await repo.set_provider_status(status)
                # 接続失敗時は前回のモデル一覧を消さない（一時的な停止を考慮）
                if status.available:
                    await repo.replace_models(cfg.id, models)
            await session.commit()
        statuses = [s for s, _ in results]
        log.info(
            "discovery_done",
            providers=[(s.provider_id, s.available, s.models_count) for s in statuses],
        )
        return DiscoveryResult(providers=statuses, models=await self.list_models(), music=music)

    async def statuses(self) -> list[ProviderStatus]:
        async with self.db.session_factory() as session:
            rows = {r.id: r for r in await LlmRepository(session).list_providers()}
        out = []
        for cfg, _ in await self.configs():
            row = rows.get(cfg.id)
            if row is not None and row.last_status:
                st = ProviderStatus.model_validate(row.last_status)
                st.name, st.base_url = cfg.name, cfg.base_url
            else:
                st = ProviderStatus(
                    provider_id=cfg.id,
                    type=cfg.type,
                    name=cfg.name,
                    base_url=cfg.base_url,
                    available=False,
                    error="not checked",
                )
            out.append(st)
        return out

    async def list_models(self) -> list[ModelInfo]:
        async with self.db.session_factory() as session:
            repo = LlmRepository(session)
            active = await repo.get_setting(ACTIVE_MODEL_KEY)
            return [repo.to_model_info(r, active) for r in await repo.list_models()]

    async def get_model(self, key: str) -> ModelInfo:
        async with self.db.session_factory() as session:
            repo = LlmRepository(session)
            row = await repo.get_model(key)
            if row is None or not row.present:
                raise ModelNotFoundError(key)
            return repo.to_model_info(row, await repo.get_setting(ACTIVE_MODEL_KEY))

    # ----- 登録 -----
    async def register(self, req: RegisterProviderRequest) -> ProviderConfig:
        encrypted: bytes | None = None
        if req.api_key is not None and req.api_key.get_secret_value():
            encrypted = self.protector.protect(req.api_key.get_secret_value().encode("utf-8"))
        slug = re.sub(r"[^a-z0-9]+", "-", req.name.lower()).strip("-")[:30] or "provider"
        cfg = ProviderConfig(
            id=f"{slug}-{uuid4().hex[:8]}",
            type=req.type,
            name=req.name,
            base_url=req.base_url,
            has_api_key=encrypted is not None,
        )
        async with self.db.session_factory() as session:
            await LlmRepository(session).upsert_provider(cfg, encrypted)
            await session.commit()
        log.info("provider_registered", provider_id=cfg.id, type=cfg.type.value)
        return cfg

    async def delete(self, provider_id: str) -> bool:
        if provider_id in BUILTIN_IDS:
            raise ValueError("built-in provider cannot be deleted")
        async with self.db.session_factory() as session:
            ok = await LlmRepository(session).delete_provider(provider_id)
            await session.commit()
        cached = self._instances.pop(provider_id, None)
        if cached:
            await cached[1].aclose()
        return ok

    async def set_active(self, model_key: str) -> ModelInfo:
        model = await self.get_model(model_key)
        async with self.db.session_factory() as session:
            await LlmRepository(session).set_setting(ACTIVE_MODEL_KEY, model_key)
            await session.commit()
        model.is_active = True
        return model

    async def active_key(self) -> str | None:
        async with self.db.session_factory() as session:
            return await LlmRepository(session).get_setting(ACTIVE_MODEL_KEY)

    async def unload_others(self, keep_key: str) -> list[str]:
        """同時ロード1モデル運用：対象以外のロード中モデルをアンロードする。"""
        unloaded: list[str] = []
        for cfg, key in await self.configs():
            if not cfg.enabled or cfg.type not in (ProviderType.OLLAMA, ProviderType.LMSTUDIO):
                continue
            provider = self._instance(cfg, key)
            try:
                loaded = await provider.loaded_models() or []
            except ProviderError:
                continue
            for mid in loaded:
                mk = ModelInfo.make_key(cfg.id, mid)
                if mk == keep_key:
                    continue
                try:
                    await provider.unload_model(mid)
                    unloaded.append(mk)
                except ProviderError:
                    log.warning("unload_failed", model_key=mk)
        return unloaded

    async def aclose(self) -> None:
        for _, p in self._instances.values():
            await p.aclose()
        self._instances.clear()
