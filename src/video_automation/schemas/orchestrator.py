"""対話型編集（Phase 8 Part A）のスキーマ: 承認・会話・編集計画・履歴。"""

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


# ---------- 承認（P8-05） ----------
class ApprovalKind(StrEnum):
    COMMAND = "command"  # タイムライン変更
    RENDER = "render"  # 最終レンダリング
    ASSET = "asset"  # 素材ダウンロード
    SCRIPT = "script"  # 台本修正案


class ApprovalItem(BaseModel):
    kind: ApprovalKind
    id: str
    project_id: str
    title: str
    details: list[str] = Field(default_factory=list)  # 人が読める差分・内容
    created_at: datetime
    message_id: str | None = None  # 提案した AI アシスタントの発言


class ApprovalDecision(BaseModel):
    approve: bool


class ApprovalResult(BaseModel):
    kind: ApprovalKind
    id: str
    status: str
    message: str = ""


class PolicyEntry(BaseModel):
    key: str
    label: str
    mode: str
    options: list[str]
    configurable: bool


class PolicyUpdate(BaseModel):
    asset_download: Literal["required", "auto"] | None = None
    unknown_license: Literal["ack", "forbidden"] | None = None


# ---------- 会話（P8-01 / P8-09） ----------
class ConversationCreate(BaseModel):
    project_id: str
    title: str = Field(default="", max_length=200)


class ConversationRead(BaseModel):
    id: str
    project_id: str
    title: str
    created_at: datetime


class ActionResult(BaseModel):
    tool: str
    ok: bool
    summary: str
    approval: ApprovalItem | None = None  # 承認待ちになった場合
    data: dict[str, Any] | None = None
    error: str | None = None


class MessageRead(BaseModel):
    id: str
    sequence: int
    role: Literal["user", "assistant"]
    content: str
    model_key: str | None
    reply_to: str | None
    superseded: bool
    actions: list[ActionResult] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    created_at: datetime


class ConversationDetail(ConversationRead):
    messages: list[MessageRead]


class SendMessage(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    model_key: str | None = None  # 省略時はタスク割当（編集計画）


class RerunRequest(BaseModel):
    model_key: str | None = None  # 別のモデルで同じ指示をやり直す


# ---------- 履歴（P8-08） ----------
class HistoryEntry(BaseModel):
    command_id: str
    sequence: int
    status: str
    origin: str
    description: str
    changes: list[str]
    created_at: datetime


class RevertRequest(BaseModel):
    to_command_id: str | None = None  # この命令の直後の状態へ戻す（None で全て取り消し）


class RevertResult(BaseModel):
    undone: list[str]
