"""mcp_script サーバー（Phase 8 §17: 対話型台本作成）。

台本修正案は承認が必要（仕様 §10）。このサーバー経由（LLM 由来）の生成・セリフ登録は
承認待ちの修正案になり、ユーザーが承認すると台本へ反映される。1 発話の書き直し
（regenerate_dialogue）は候補を返すだけで、採用はユーザーが UI / API で行う。
ユーザーが入力した・手修正した・固定したセリフは AI が変更しない。台本の作成と固定は
内容を変えないため承認なしで行う。
"""

from typing import Any

from mcp.server.fastmcp import FastMCP

from video_automation.mcp_servers._shared import get_container
from video_automation.schemas.script_studio import (
    CandidatesRead,
    CharacterIn,
    DialogueInput,
    DialogueLine,
    ProposalRead,
    RegenerateRequest,
    ScriptDetail,
    ScriptGenerateRequest,
    ScriptSettings,
    ScriptStudioCreate,
    ScriptValidateRequest,
)
from video_automation.schemas.subtitle import ScriptMode

mcp = FastMCP("mcp_script")


NOTE = "台本修正案はユーザーの承認後に反映されます"


def _out(d: ScriptDetail | ProposalRead) -> dict[str, Any]:
    if isinstance(d, ScriptDetail):
        return _brief(d)
    changes = d.diff.changes if d.diff else []
    return {
        "proposal_id": d.id,
        "script_id": d.script_id,
        "status": d.status,
        "summary": d.reason,
        "changes": [c.model_dump(exclude_none=True) for c in changes],
        "notes": d.notes or [],
        "note": NOTE,
    }


def _brief(d: ScriptDetail) -> dict[str, Any]:
    return {
        "script_id": d.id,
        "version": d.version,
        "mode": d.mode,
        "estimated_total_sec": d.estimated_total_sec,
        "turns": [
            {
                "turn_id": t.id,
                "scene": t.scene_index,
                "speaker": t.speaker,
                "text": t.text,
                "source": t.generation_source,
                "locked": t.locked,
            }
            for t in d.segments
        ],
        "notes": d.notes,
    }


async def create_script(
    project_id: str,
    mode: str = "FULL_AI",
    title: str = "",
    characters: list[dict[str, Any]] | None = None,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """台本を作る。mode: FULL_AI（全自動）/ USER_ONE_SIDE（片方をユーザーが書く）/ MANUAL。

    characters: [{name, role, personality, speaking_style, is_user, voice:{style_id}}]
    settings: {topic, purpose, audience, target_duration_sec, structure, tempo,
    must_keywords, banned, reference, response_policy, ai_ratio}
    """
    c = await get_container()
    d = await c.studio.create(
        ScriptStudioCreate(
            project_id=project_id,
            title=title,
            mode=ScriptMode(mode),
            characters=[CharacterIn.model_validate(x) for x in characters or []],
            settings=ScriptSettings.model_validate(settings or {}),
        )
    )
    return _brief(d)


async def generate_full_script(
    script_id: str, instruction: str = "", model_key: str | None = None
) -> dict[str, Any]:
    """FULL_AI の台本の生成案を作る（承認待ち。固定・ユーザー入力・手修正のセリフは残す）。"""
    c = await get_container()
    return _out(
        await c.studio.generate(
            script_id,
            ScriptGenerateRequest(instruction=instruction, model_key=model_key),
            propose=True,
        )
    )


async def set_user_script(
    script_id: str, lines: list[str], speaker: str | None = None, replace: bool = False
) -> dict[str, Any]:
    """ユーザーが書いたセリフを登録する案を出す（承認後、一字一句そのまま固定される）。"""
    c = await get_container()
    req = DialogueInput(
        lines=[DialogueLine(text=t, speaker=speaker) for t in lines], replace=replace
    )
    return _out(await c.studio.add_dialogue(script_id, req, propose=True))


async def generate_counterpart_dialogue(
    script_id: str, instruction: str = "", model_key: str | None = None
) -> dict[str, Any]:
    """USER_ONE_SIDE で相手キャラクターのセリフの生成案を作る（承認待ち）。"""
    c = await get_container()
    return _out(
        await c.studio.generate(
            script_id,
            ScriptGenerateRequest(instruction=instruction, model_key=model_key),
            propose=True,
        )
    )


async def regenerate_dialogue(
    script_id: str, turn_id: str, instruction: str = "", candidates: int = 2
) -> dict[str, Any]:
    """1発話の言い換え候補を作る（適用はしない。ユーザーが選んで採用する）。"""
    c = await get_container()
    res = await c.studio.regenerate(
        script_id,
        turn_id,
        RegenerateRequest(instruction=instruction, candidates=max(2, min(3, candidates))),
    )
    assert isinstance(res, CandidatesRead)
    return {**res.model_dump(), "note": "候補は未適用です。ユーザーが採用すると反映されます"}


async def lock_dialogue_turn(script_id: str, turn_id: str, locked: bool = True) -> dict[str, Any]:
    """セリフを固定（AI の生成・再生成で変更しない）/ 解除する。"""
    c = await get_container()
    return _brief(await c.studio.lock(script_id, turn_id, locked))


async def validate_script(script_id: str, use_llm: bool = False) -> dict[str, Any]:
    """台本を検査する（話者・禁止語・必須語・尺・重複・ユーザー入力の改変。use_llm で整合性も）。"""
    c = await get_container()
    return (await c.studio.validate(script_id, ScriptValidateRequest(use_llm=use_llm))).model_dump(
        mode="json"
    )


async def get_script_versions(script_id: str, diff_from: int | None = None) -> dict[str, Any]:
    """台本の版一覧。diff_from を指定するとその版から最新版への差分も返す。"""
    c = await get_container()
    out: dict[str, Any] = {
        "versions": [v.model_dump(mode="json") for v in await c.studio.versions(script_id)]
    }
    if diff_from is not None:
        out["diff"] = (await c.studio.diff(script_id, diff_from)).model_dump(mode="json")
    return out


for _fn in (
    create_script,
    generate_full_script,
    set_user_script,
    generate_counterpart_dialogue,
    regenerate_dialogue,
    lock_dialogue_turn,
    validate_script,
    get_script_versions,
):
    mcp.tool(name=_fn.__name__, description=(_fn.__doc__ or "").strip().split("\n")[0])(_fn)


def main() -> None:
    mcp.run()
