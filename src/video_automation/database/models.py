"""テーブル定義。Phase 1: projects / Phase 2: llm_providers, llm_models,
llm_task_assignments, system_settings。"""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, LargeBinary, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False, default=1920)
    height: Mapped[int] = mapped_column(Integer, nullable=False, default=1080)
    fps_num: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    fps_den: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )


class LlmProvider(Base):
    __tablename__ = "llm_providers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    auto_discovered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # DPAPIで暗号化したAPIキー。平文は保存しない
    api_key_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    last_status: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )


class LlmModel(Base):
    __tablename__ = "llm_models"

    key: Mapped[str] = mapped_column(String(600), primary_key=True)  # provider_id::model_id
    provider_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("llm_providers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    model_id: Mapped[str] = mapped_column(String(500), nullable=False)
    info: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)  # ModelInfo スナップショット
    last_test: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    present: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )


class LlmTaskAssignment(Base):
    __tablename__ = "llm_task_assignments"

    task: Mapped[str] = mapped_column(String(64), primary_key=True)
    model_key: Mapped[str | None] = mapped_column(String(600), nullable=True)  # None=自動
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )
