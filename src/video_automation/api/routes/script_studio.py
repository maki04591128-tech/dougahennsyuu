"""対話型台本作成 API（仕様 §17 / Phase 8）。

旧形式（本文テキストから作成する POST /scripts）はそのまま残し、
キャラクター・モード・生成設定を持つ台本は /scripts/studio で作成する。
"""

from fastapi import APIRouter, Depends

from video_automation.api.dependencies import get_container
from video_automation.app.bootstrap import AppContainer
from video_automation.schemas.script_studio import (
    ApplyCandidate,
    CandidatesRead,
    DialogueInput,
    GenerationRunRead,
    LockRequest,
    RegenerateRequest,
    ScriptDetail,
    ScriptGenerateRequest,
    ScriptStudioCreate,
    ScriptUpdate,
    ScriptValidateRequest,
    ScriptValidation,
    TurnUpdate,
    VersionDiff,
    VersionRead,
)

router = APIRouter(prefix="/scripts", tags=["script-studio"])


@router.post("/studio", response_model=ScriptDetail, status_code=201)
async def create(body: ScriptStudioCreate, c: AppContainer = Depends(get_container)):
    return await c.studio.create(body)


@router.get("/{script_id}/detail", response_model=ScriptDetail)
async def detail(script_id: str, c: AppContainer = Depends(get_container)):
    return await c.studio.detail(script_id)


@router.put("/{script_id}", response_model=ScriptDetail)
async def update(script_id: str, body: ScriptUpdate, c: AppContainer = Depends(get_container)):
    return await c.studio.update(script_id, body)


@router.post("/{script_id}/generate", response_model=ScriptDetail)
async def generate(
    script_id: str, body: ScriptGenerateRequest, c: AppContainer = Depends(get_container)
):
    return await c.studio.generate(script_id, body)


@router.post("/{script_id}/dialogue", response_model=ScriptDetail)
async def add_dialogue(
    script_id: str, body: DialogueInput, c: AppContainer = Depends(get_container)
):
    return await c.studio.add_dialogue(script_id, body)


@router.put("/{script_id}/dialogue/{turn_id}", response_model=ScriptDetail)
async def update_turn(
    script_id: str, turn_id: str, body: TurnUpdate, c: AppContainer = Depends(get_container)
):
    return await c.studio.update_turn(script_id, turn_id, body)


@router.delete("/{script_id}/dialogue/{turn_id}", response_model=ScriptDetail)
async def delete_turn(script_id: str, turn_id: str, c: AppContainer = Depends(get_container)):
    return await c.studio.delete_turn(script_id, turn_id)


@router.put("/{script_id}/dialogue/{turn_id}/lock", response_model=ScriptDetail)
async def lock(
    script_id: str, turn_id: str, body: LockRequest, c: AppContainer = Depends(get_container)
):
    return await c.studio.lock(script_id, turn_id, body.locked)


@router.post(
    "/{script_id}/dialogue/{turn_id}/regenerate",
    response_model=ScriptDetail | CandidatesRead,
)
async def regenerate(
    script_id: str,
    turn_id: str,
    body: RegenerateRequest,
    c: AppContainer = Depends(get_container),
):
    return await c.studio.regenerate(script_id, turn_id, body)


@router.post("/{script_id}/dialogue/{turn_id}/apply", response_model=ScriptDetail)
async def apply_candidate(
    script_id: str, turn_id: str, body: ApplyCandidate, c: AppContainer = Depends(get_container)
):
    return await c.studio.apply_candidate(script_id, turn_id, body.text)


@router.post("/{script_id}/validate", response_model=ScriptValidation)
async def validate(
    script_id: str, body: ScriptValidateRequest, c: AppContainer = Depends(get_container)
):
    return await c.studio.validate(script_id, body)


@router.get("/{script_id}/versions", response_model=list[VersionRead])
async def versions(script_id: str, c: AppContainer = Depends(get_container)):
    return await c.studio.versions(script_id)


@router.get("/{script_id}/versions/diff", response_model=VersionDiff)
async def diff(
    script_id: str,
    from_version: int,
    to_version: int | None = None,
    c: AppContainer = Depends(get_container),
):
    return await c.studio.diff(script_id, from_version, to_version)


@router.post("/{script_id}/versions/{version}/restore", response_model=ScriptDetail)
async def restore(script_id: str, version: int, c: AppContainer = Depends(get_container)):
    return await c.studio.restore(script_id, version)


@router.get("/{script_id}/runs", response_model=list[GenerationRunRead])
async def runs(script_id: str, c: AppContainer = Depends(get_container)):
    return await c.studio.runs(script_id)
