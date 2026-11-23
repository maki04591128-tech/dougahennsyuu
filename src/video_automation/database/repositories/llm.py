"""LLM Provider / Model / タスク割当 / システム設定のリポジトリ (Phase 2)。"""

from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from video_automation.database.models import LlmModel, LlmProvider, LlmTaskAssignment, SystemSetting
from video_automation.schemas.provider import (
    ModelInfo,
    ModelState,
    ProviderConfig,
    ProviderStatus,
)

ACTIVE_MODEL_KEY = "llm.active_model"


class LlmRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ----- providers -----
    async def list_providers(self) -> list[LlmProvider]:
        res = await self.session.execute(select(LlmProvider).order_by(LlmProvider.created_at))
        return list(res.scalars())

    async def get_provider(self, provider_id: str) -> LlmProvider | None:
        return await self.session.get(LlmProvider, provider_id)

    async def upsert_provider(
        self, cfg: ProviderConfig, api_key_encrypted: bytes | None = None
    ) -> LlmProvider:
        row = await self.get_provider(cfg.id)
        if row is None:
            row = LlmProvider(id=cfg.id)
            self.session.add(row)
        row.type, row.name, row.base_url = cfg.type.value, cfg.name, cfg.base_url
        row.enabled, row.auto_discovered = cfg.enabled, cfg.auto_discovered
        if api_key_encrypted is not None:
            row.api_key_encrypted = api_key_encrypted
        await self.session.flush()
        return row

    async def set_provider_status(self, status: ProviderStatus) -> None:
        row = await self.get_provider(status.provider_id)
        if row is not None:
            row.last_status = status.model_dump(mode="json")

    async def delete_provider(self, provider_id: str) -> bool:
        row = await self.get_provider(provider_id)
        if row is None:
            return False
        await self.session.delete(row)
        await self.session.flush()
        return True

    # ----- models -----
    async def replace_models(self, provider_id: str, models: list[ModelInfo]) -> None:
        """検出結果で上書き。見つからなかったモデルは present=False（テスト履歴は保持）。"""
        await self.session.execute(
            update(LlmModel).where(LlmModel.provider_id == provider_id).values(present=False)
        )
        for m in models:
            row = await self.session.get(LlmModel, m.key)
            info = m.model_dump(mode="json", exclude={"is_active", "last_test"})
            if row is None:
                self.session.add(
                    LlmModel(
                        key=m.key,
                        provider_id=provider_id,
                        model_id=m.model_id,
                        info=info,
                        present=True,
                    )
                )
            else:
                # 実測済みCapabilityは申告値で上書きしない
                old_caps = (row.info or {}).get("capabilities") or {}
                probed = {
                    k: old_caps[k]
                    for k, src in (old_caps.get("source") or {}).items()
                    if src == "probed" and k in old_caps
                }
                if probed:
                    info["capabilities"].update(probed)
                    info["capabilities"]["source"].update({k: "probed" for k in probed})
                    if old_caps.get("tool_calling_fallback"):
                        info["capabilities"]["tool_calling_fallback"] = True
                row.info, row.present = info, True
        await self.session.flush()

    async def list_models(self, present_only: bool = True) -> list[LlmModel]:
        q = select(LlmModel).order_by(LlmModel.provider_id, LlmModel.model_id)
        if present_only:
            q = q.where(LlmModel.present.is_(True))
        return list((await self.session.execute(q)).scalars())

    async def get_model(self, key: str) -> LlmModel | None:
        return await self.session.get(LlmModel, key)

    async def save_model_test(self, key: str, info: dict[str, Any], test: dict[str, Any]) -> None:
        row = await self.get_model(key)
        if row is not None:
            row.info, row.last_test = info, test

    # ----- task assignment / settings -----
    async def get_assignments(self) -> dict[str, str | None]:
        res = await self.session.execute(select(LlmTaskAssignment))
        return {r.task: r.model_key for r in res.scalars()}

    async def set_assignment(self, task: str, model_key: str | None) -> None:
        row = await self.session.get(LlmTaskAssignment, task)
        if row is None:
            self.session.add(LlmTaskAssignment(task=task, model_key=model_key))
        else:
            row.model_key = model_key
        await self.session.flush()

    async def get_setting(self, key: str) -> Any:
        row = await self.session.get(SystemSetting, key)
        return None if row is None else row.value

    async def set_setting(self, key: str, value: Any) -> None:
        row = await self.session.get(SystemSetting, key)
        if row is None:
            self.session.add(SystemSetting(key=key, value=value))
        else:
            row.value = value
        await self.session.flush()

    @staticmethod
    def to_model_info(row: LlmModel, active_key: str | None) -> ModelInfo:
        info = ModelInfo.model_validate(row.info)
        info.is_active = row.key == active_key
        info.last_test = row.last_test
        # 推論テスト成功済みなら INFERENCE_READY（ロード有無は loaded で別管理）
        if row.last_test and row.last_test.get("success"):
            info.state = ModelState.INFERENCE_READY
        return info
