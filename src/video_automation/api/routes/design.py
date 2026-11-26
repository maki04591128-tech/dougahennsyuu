"""デザインの型 API。用途の一覧・指示文からの判定・プロジェクトの用途の設定・書き出し前の検査。"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from video_automation.api.dependencies import get_container
from video_automation.app.bootstrap import AppContainer
from video_automation.schemas.design import (
    ClassifyRequest,
    ClassifyResult,
    DesignCheck,
    DesignProfile,
    ProfileSummary,
    ProjectDesign,
    Purpose,
)
from video_automation.schemas.timeline import RenderRequest
from video_automation.services.design.edits import DesignDefaults, EditProposal
from video_automation.services.design.profiles import PROFILES, summarize

router = APIRouter(prefix="/design", tags=["design"])
projects = APIRouter(prefix="/projects/{project_id}/design", tags=["design"])


@router.get("/profiles", response_model=list[ProfileSummary])
async def profiles() -> Any:
    """用途の一覧（使ってよい部品・回数の上限・使わない部品）。"""
    return [summarize(p) for p in PROFILES.values()]


@router.get("/profiles/{purpose}", response_model=DesignProfile)
async def profile(purpose: Purpose) -> Any:
    """用途の型の全体（配色・書体・字幕・安全範囲・部品の許可）。"""
    return PROFILES[purpose]


@router.post("/classify", response_model=ClassifyResult)
async def classify(body: ClassifyRequest, c: AppContainer = Depends(get_container)) -> Any:
    """指示文から用途を判定する。needs_confirmation のときはユーザーに確認する。"""
    return c.design.classify(body)


class EditChoice(BaseModel):
    purpose: Purpose
    component: str
    item: str


def _edits(c: AppContainer):  # noqa: ANN202
    if c.design.edits is None:
        raise HTTPException(status_code=404, detail="直しの記録を使っていません")
    return c.design.edits


@router.get("/edits/proposals", response_model=list[EditProposal])
async def edit_proposals(c: AppContainer = Depends(get_container)) -> Any:
    """利用者の直しから作った、既定値の変更の提案（統合版 W2）。反映は承認したものだけ。"""
    return _edits(c).proposals()


@router.post("/edits/accept", response_model=DesignDefaults)
async def accept_edit(body: EditChoice, c: AppContainer = Depends(get_container)) -> Any:
    """提案を承認して既定値に反映する（次に演出・BGM を作るときから使う）。"""
    try:
        return _edits(c).accept(body.purpose.value, body.component, body.item)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="その提案はありません") from exc


@router.post("/edits/reject", response_model=DesignDefaults)
async def reject_edit(body: EditChoice, c: AppContainer = Depends(get_container)) -> Any:
    """提案を断る（同じ用途の動画がもう 5 本増えるまで、同じ提案を出さない）。"""
    try:
        return _edits(c).reject(body.purpose.value, body.component, body.item)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="その提案はありません") from exc


@router.get("/defaults", response_model=DesignDefaults)
async def learned_defaults(c: AppContainer = Depends(get_container)) -> Any:
    """承認して反映した既定値の変更。"""
    return _edits(c).defaults()


@router.delete("/defaults", response_model=DesignDefaults)
async def reset_defaults(
    purpose: Purpose | None = None, c: AppContainer = Depends(get_container)
) -> Any:
    """反映した既定値の変更を元に戻す（purpose を省くと全部）。"""
    return _edits(c).reset(purpose.value if purpose else None)


@projects.get("", response_model=ProjectDesign)
async def get_design(project_id: str, c: AppContainer = Depends(get_container)) -> Any:
    return await c.design.get(project_id)


@projects.put("", response_model=ProjectDesign)
async def set_design(
    project_id: str, body: ProjectDesign, c: AppContainer = Depends(get_container)
) -> Any:
    """用途と、混ぜる指示が明示されたときだけ使う例外（extras）を設定する。"""
    return await c.design.set(project_id, body)


@projects.get("/check", response_model=DesignCheck)
async def check(
    project_id: str,
    script_id: str | None = None,
    burn_subtitles: bool = True,
    c: AppContainer = Depends(get_container),
) -> Any:
    """書き出す前と同じ検査を、書き出さずに行う。"""
    chk = await c.render.design_check(
        RenderRequest(project_id=project_id, script_id=script_id, burn_subtitles=burn_subtitles)
    )
    assert chk is not None
    return chk


@projects.post("/thumbnails", status_code=202)
async def thumbnails(project_id: str, c: AppContainer = Depends(get_container)) -> Any:
    """統合版 S4: サムネイルを 3 案作る（ジョブ）。結果はおすすめ順のファイルの一覧と注意。"""
    if c.thumbnails is None:
        raise HTTPException(status_code=404, detail="サムネイルを作れません")
    await c.timeline.get_timeline(project_id)  # 存在確認

    async def run(progress: Any) -> dict[str, Any]:
        notes: list[str] = []
        paths = await c.thumbnails.make(project_id, notes)
        return {"thumbnails": paths, "notes": notes}

    return c.jobs.submit("thumbnails", run)
