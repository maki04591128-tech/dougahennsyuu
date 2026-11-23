"""mcp_render サーバー（Phase 5）。タイムライン・編集・レンダリング。

LLM からの編集命令は origin=llm として「承認待ち」で登録され、ユーザーが UI/API で
承認するまで適用されない。最終レンダリングも同様（仕様 §10）。
"""

from typing import Any

from mcp.server.fastmcp import FastMCP

from video_automation.mcp_servers._shared import get_container
from video_automation.schemas.timeline import (
    AddClip,
    CommandOrigin,
    CommandRequest,
    MoveClip,
    PreviewRequest,
    RenderProfileName,
    RenderRequest,
    TrimClip,
)

mcp = FastMCP("mcp_render")


async def inspect_timeline(project_id: str) -> dict[str, Any]:
    """タイムライン（トラック・クリップ・素材・長さ）を返す。"""
    c = await get_container()
    return (await c.timeline.get_timeline(project_id)).model_dump(mode="json")


async def _submit(project_id: str, command: Any, description: str) -> dict[str, Any]:
    c = await get_container()
    row = await c.timeline.submit(
        project_id,
        CommandRequest(
            command=command,
            origin=CommandOrigin.LLM,
            description=description,
        ),
    )
    return {
        "command_id": row.id,
        "status": row.status.value,
        "note": "ユーザーの承認後に適用されます",
    }


async def add_clip(
    project_id: str,
    track_id: str,
    asset_id: str,
    start_frame: int,
    duration_frames: int | None = None,
    source_in_frame: int = 0,
    description: str = "",
) -> dict[str, Any]:
    """クリップ追加を提案する（承認待ち）。"""
    return await _submit(
        project_id,
        AddClip(
            track_id=track_id,
            asset_id=asset_id,
            start_frame=start_frame,
            duration_frames=duration_frames,
            source_in_frame=source_in_frame,
        ),
        description,
    )


async def trim_clip(
    project_id: str,
    clip_id: str,
    start_frame: int | None = None,
    end_frame: int | None = None,
    description: str = "",
) -> dict[str, Any]:
    """クリップのトリミングを提案する（承認待ち）。"""
    return await _submit(
        project_id,
        TrimClip(clip_id=clip_id, start_frame=start_frame, end_frame=end_frame),
        description,
    )


async def move_clip(
    project_id: str,
    clip_id: str,
    start_frame: int,
    track_id: str | None = None,
    description: str = "",
) -> dict[str, Any]:
    """クリップの移動を提案する（承認待ち）。"""
    return await _submit(
        project_id,
        MoveClip(clip_id=clip_id, start_frame=start_frame, track_id=track_id),
        description,
    )


async def apply_edit(
    project_id: str, command: dict[str, Any], description: str = ""
) -> dict[str, Any]:
    """共通 Command Schema の編集命令（batch 可）を提案する（承認待ち）。"""
    req = CommandRequest.model_validate({"command": command})
    return await _submit(project_id, req.command, description)


async def validate_project(project_id: str) -> dict[str, Any]:
    """素材ファイルの欠落・重なり等を検査する。"""
    c = await get_container()
    return (await c.timeline.validate_project(project_id)).model_dump()


async def preview_timeline(project_id: str, frame: int, script_id: str | None = None) -> str:
    """指定フレームのプレビュー画像を書き出し、そのパス（data 配下）を返す。"""
    c = await get_container()
    await c.render.preview_frame(project_id, PreviewRequest(frame=frame, script_id=script_id))
    return f"cache/preview/{project_id}_frame.png"


async def render_project(
    project_id: str, profile: str = "fhd_landscape", script_id: str | None = None
) -> dict[str, Any]:
    """最終レンダリングを依頼する（承認待ち）。"""
    c = await get_container()
    r = await c.render.request(
        RenderRequest(
            project_id=project_id,
            profile=RenderProfileName(profile),
            script_id=script_id,
            origin=CommandOrigin.LLM,
        )
    )
    return {"render_id": r.id, "status": r.status.value}


async def get_render_status(render_id: str) -> dict[str, Any]:
    """レンダリングの状態と進捗。"""
    c = await get_container()
    r = await c.render.get(render_id)
    out = r.model_dump(mode="json")
    if r.job_id and (job := c.jobs.get(r.job_id)) is not None:
        out["progress"] = job.progress
    return out


async def run_quality_check(
    project_id: str, render_id: str | None = None, script_id: str | None = None
) -> dict[str, Any]:
    """品質検査（音声・字幕・映像・ライセンス）を開始し Job ID を返す（読み取りのみ）。"""
    from video_automation.schemas.qa import QARequest

    c = await get_container()
    await c.timeline.get_timeline(project_id)
    job = c.qa.submit(project_id, QARequest(render_id=render_id, script_id=script_id))
    return {"job_id": job.id, "status": job.status.value}


async def get_quality_report(report_id: str) -> dict[str, Any]:
    """品質検査の結果（項目ごとの error / warning / info）。"""
    c = await get_container()
    r = await c.qa.get(report_id)
    return {
        "report_id": r.id,
        "status": r.status,
        "sections": [
            {
                "name": s.name.value,
                "status": s.status,
                "metrics": s.metrics,
                "checks": [x.model_dump(mode="json", exclude_none=True) for x in s.checks],
            }
            for s in r.sections
        ],
    }


for fn, desc in (
    (inspect_timeline, "タイムラインを取得する"),
    (add_clip, "クリップ追加を提案（承認待ち）"),
    (trim_clip, "クリップのトリミングを提案（承認待ち）"),
    (move_clip, "クリップの移動を提案（承認待ち）"),
    (apply_edit, "編集命令を提案（承認待ち）"),
    (preview_timeline, "指定フレームのプレビュー画像を作る"),
    (validate_project, "プロジェクトを検査する"),
    (render_project, "最終レンダリングを依頼（承認待ち）"),
    (get_render_status, "レンダリング状態を取得する"),
    (run_quality_check, "品質検査（音声・字幕・映像・ライセンス）を開始する"),
    (get_quality_report, "品質検査の結果を取得する"),
):
    mcp.tool(name=fn.__name__, description=desc)(fn)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
