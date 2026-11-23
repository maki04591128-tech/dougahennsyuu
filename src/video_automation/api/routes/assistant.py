"""対話型編集 API（Phase 8 Part A）: 会話・承認・履歴・プロジェクト状態。"""

from typing import Any

from fastapi import APIRouter, Depends

from video_automation.api.dependencies import get_container
from video_automation.app.bootstrap import AppContainer
from video_automation.schemas.orchestrator import (
    ApprovalDecision,
    ApprovalItem,
    ApprovalKind,
    ApprovalResult,
    ConversationCreate,
    ConversationDetail,
    ConversationRead,
    HistoryEntry,
    MessageRead,
    PolicyEntry,
    PolicyUpdate,
    RerunRequest,
    RevertRequest,
    RevertResult,
    SendMessage,
)

conversations = APIRouter(prefix="/conversations", tags=["assistant"])
approvals = APIRouter(prefix="/approvals", tags=["approvals"])
projects = APIRouter(prefix="/projects", tags=["assistant"])


@conversations.post("", response_model=ConversationRead, status_code=201)
async def create(body: ConversationCreate, c: AppContainer = Depends(get_container)):
    return await c.orchestrator.create(body)


@conversations.get("", response_model=list[ConversationRead])
async def list_conversations(project_id: str, c: AppContainer = Depends(get_container)):
    return await c.orchestrator.list_conversations(project_id)


@conversations.get("/{conversation_id}", response_model=ConversationDetail)
async def get(conversation_id: str, c: AppContainer = Depends(get_container)):
    return await c.orchestrator.get(conversation_id)


@conversations.post("/{conversation_id}/messages", response_model=MessageRead)
async def send(conversation_id: str, body: SendMessage, c: AppContainer = Depends(get_container)):
    return await c.orchestrator.send(conversation_id, body.text, body.model_key)


@conversations.post("/messages/{message_id}/rerun", response_model=MessageRead)
async def rerun(message_id: str, body: RerunRequest, c: AppContainer = Depends(get_container)):
    return await c.orchestrator.rerun(message_id, body.model_key)


@approvals.get("/policy", response_model=list[PolicyEntry])
async def get_policy(c: AppContainer = Depends(get_container)):
    return c.approvals.policy()


@approvals.put("/policy", response_model=list[PolicyEntry])
async def set_policy(body: PolicyUpdate, c: AppContainer = Depends(get_container)):
    return await c.approvals.set_policy(body)


@approvals.post("/{kind}/{item_id}", response_model=ApprovalResult)
async def decide(
    kind: ApprovalKind,
    item_id: str,
    body: ApprovalDecision,
    c: AppContainer = Depends(get_container),
):
    return await c.approvals.decide(kind, item_id, body.approve)


@projects.get("/{project_id}/approvals", response_model=list[ApprovalItem])
async def pending(project_id: str, c: AppContainer = Depends(get_container)):
    return await c.approvals.pending(project_id)


@projects.get("/{project_id}/history", response_model=list[HistoryEntry])
async def history(project_id: str, c: AppContainer = Depends(get_container)):
    return await c.history.list(project_id)


@projects.post("/{project_id}/history/revert", response_model=RevertResult)
async def revert(project_id: str, body: RevertRequest, c: AppContainer = Depends(get_container)):
    return await c.history.revert(project_id, body.to_command_id)


@projects.get("/{project_id}/state")
async def state(project_id: str, c: AppContainer = Depends(get_container)) -> dict[str, Any]:
    return await c.orchestrator.project_state(project_id)
