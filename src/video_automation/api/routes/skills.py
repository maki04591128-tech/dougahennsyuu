"""ローカル LLM のスキル API。一覧・全文・依頼に関係する節の検索・読み直し。"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from video_automation.api.dependencies import get_container
from video_automation.app.bootstrap import AppContainer

router = APIRouter(prefix="/skills", tags=["skills"])


class SkillSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    names: list[str] | None = None
    budget: int = Field(default=4000, ge=500, le=20000)
    keep_code: bool = False


def _summary(s: Any) -> dict[str, Any]:
    return {
        "name": s.name,
        "description": s.description,
        "source": s.source,
        "chars": s.size,
        "sections": len(s.sections),
    }


@router.get("")
async def list_skills(c: AppContainer = Depends(get_container)) -> list[dict[str, Any]]:
    """スキルの一覧（同梱と、ユーザーのフォルダのもの。同じ名前はユーザーのものが優先）。"""
    return [_summary(s) for s in c.skills.all()]


@router.post("/reload")
async def reload(c: AppContainer = Depends(get_container)) -> list[dict[str, Any]]:
    """スキルのフォルダを読み直す（SKILL.md を置いた・直したとき）。"""
    c.skills.reload()
    return [_summary(s) for s in c.skills.all()]


@router.post("/search")
async def search(body: SkillSearchRequest, c: AppContainer = Depends(get_container)) -> dict:
    """依頼文に関係する節（LLM のプロンプトに入れるのと同じ選び方）。"""
    ctx, picked = c.skills.context(
        body.query, names=body.names, budget=body.budget, keep_code=body.keep_code
    )
    return {
        "context": ctx,
        "sections": [
            {"skill": p.skill, "path": p.path, "score": p.score, "chars": len(p.text)}
            for p in picked
        ],
    }


@router.get("/{name}")
async def read(name: str, c: AppContainer = Depends(get_container)) -> dict[str, Any]:
    s = c.skills.get(name)
    if s is None:
        raise HTTPException(status_code=404, detail=f"skill {name} not found")
    return {**_summary(s), "body": s.body, "headings": [x.path for x in s.sections]}
