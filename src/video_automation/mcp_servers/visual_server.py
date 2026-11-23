"""mcp_visual サーバー（Phase 7: シーン・画像/文字アニメーション・切替・図表・カメラ）。

タイムラインを変える操作はすべて LLM 由来として承認待ちになる。素材の生成（タイトル・図表）は
承認不要だが、配置は承認待ち。Blender での生成は時間がかかるため Job ID を返す。
"""

from typing import Any

from mcp.server.fastmcp import FastMCP

from video_automation.mcp_servers._shared import get_container
from video_automation.schemas.animation import (
    InfographicRequest,
    PresetKind,
    SceneRequest,
    Transition,
    TransitionType,
)
from video_automation.schemas.timeline import (
    AddClip,
    AddTrack,
    Batch,
    CommandOrigin,
    CommandRequest,
    SetClipAnimation,
    TitleCardRequest,
    TrackKind,
)

mcp = FastMCP("mcp_visual")
NOTE = "ユーザーの承認後に適用されます"


async def _submit(project_id: str, command: Any, description: str) -> dict[str, Any]:
    c = await get_container()
    row = await c.timeline.submit(
        project_id,
        CommandRequest(command=command, origin=CommandOrigin.LLM, description=description),
    )
    return {"command_id": row.id, "status": row.status.value, "note": NOTE}


async def list_motion_presets(kind: str | None = None) -> list[dict[str, Any]]:
    """使えるモーションプリセット（camera / element / transition / text）。"""
    c = await get_container()
    ps = await c.presets.list(PresetKind(kind) if kind else None)
    return [
        {"name": p.name, "kind": p.kind.value, "label": p.label, "description": p.description}
        for p in ps
    ]


async def animate_image(
    project_id: str,
    clip_id: str,
    preset: str,
    duration_sec: float | None = None,
    intensity: float = 1.0,
    description: str = "",
) -> dict[str, Any]:
    """クリップに登場・退場・動きのプリセット（pop_in, slide_in_left, fade_out 等）を付ける提案。"""
    return await _submit(
        project_id,
        SetClipAnimation(
            clip_id=clip_id, preset=preset, duration_sec=duration_sec, intensity=intensity
        ),
        description or f"アニメーション {preset}",
    )


async def set_camera_motion(
    project_id: str,
    clip_id: str,
    preset: str = "ken_burns_in",
    intensity: float = 1.0,
    description: str = "",
) -> dict[str, Any]:
    """素材の中でのズーム・パン（ken_burns_in, pan_left 等）を付ける提案。"""
    return await _submit(
        project_id,
        SetClipAnimation(clip_id=clip_id, preset=preset, intensity=intensity),
        description or f"カメラ演出 {preset}",
    )


async def create_transition(
    project_id: str,
    clip_id: str,
    type: str = "dissolve",
    duration_frames: int = 15,
    description: str = "",
) -> dict[str, Any]:
    """クリップの入りにシーン切替（dissolve, slide_left, wipe_left 等）を付ける提案。"""
    tran = Transition(type=TransitionType(type), duration_frames=duration_frames)
    return await _submit(
        project_id,
        SetClipAnimation(clip_id=clip_id, transition=tran),
        description or f"シーン切替 {type}",
    )


async def animate_text(
    project_id: str,
    text: str,
    start_frame: int,
    duration_sec: float = 3.0,
    animation: str = "pop",
    position: str = "center",
    box: bool = False,
) -> dict[str, Any]:
    """文字アニメーション（テロップ）を Blender で作り、配置を提案する（Job）。"""
    c = await get_container()
    req = TitleCardRequest.model_validate(
        {
            "project_id": project_id,
            "text": text,
            "duration_sec": duration_sec,
            "animation": animation,
            "position": position,
            "box": box,
        }
    )

    async def run(progress: Any) -> dict[str, Any]:
        asset = await c.titles.create(req)
        tl = await c.timeline.get_timeline(project_id)
        track = next((t.id for t in tl.tracks if t.name == "テロップ"), None)
        cmds: list[Any] = []
        if track is None:
            add = AddTrack(kind=TrackKind.VIDEO, name="テロップ")
            cmds.append(add)
            add.track_id = track = f"tel-{asset.id[:8]}"
        cmds.append(AddClip(track_id=track, asset_id=asset.id, start_frame=start_frame))
        res = await _submit(project_id, Batch(commands=cmds), f"テロップ「{text[:20]}」")
        return {"asset_id": asset.id, **res}

    job = c.jobs.submit("animate_text", run)
    return {"job_id": job.id, "status": job.status.value}


async def create_infographic(
    project_id: str,
    values: list[float],
    labels: list[str] | None = None,
    kind: str = "bar",
    title: str = "",
    unit: str = "",
    duration_sec: float = 4.0,
) -> dict[str, Any]:
    """横棒グラフ・数値カウントアップの素材を作る（Job。配置は add_clip で提案する）。"""
    c = await get_container()
    req = InfographicRequest.model_validate(
        {
            "project_id": project_id,
            "kind": kind,
            "title": title,
            "labels": labels or [],
            "values": values,
            "unit": unit,
            "duration_sec": duration_sec,
        }
    )

    async def run(progress: Any) -> dict[str, Any]:
        return (await c.titles.infographic(req)).model_dump(mode="json")

    job = c.jobs.submit("infographic", run)
    return {"job_id": job.id, "status": job.status.value}


async def create_scene(
    project_id: str,
    start_frame: int,
    duration_sec: float,
    title: str = "",
    background_asset_id: str | None = None,
    background_color: str = "#101820",
    camera_preset: str | None = None,
    title_animation: str = "pop",
    transition: str | None = None,
) -> dict[str, Any]:
    """背景＋タイトル＋カメラ演出＋切替をまとめたシーンを作り、配置を提案する（Job）。"""
    c = await get_container()
    req = SceneRequest(
        start_frame=start_frame,
        duration_sec=duration_sec,
        title=title,
        background_asset_id=background_asset_id,
        background_color=background_color,
        camera_preset=camera_preset,
        title_animation=title_animation,  # type: ignore[arg-type]
        transition=Transition(type=TransitionType(transition)) if transition else None,
        origin="llm",
    )

    async def run(progress: Any) -> dict[str, Any]:
        return await c.scenes.create(project_id, req)

    job = c.jobs.submit("scene", run)
    return {"job_id": job.id, "status": job.status.value}


async def get_job(job_id: str) -> dict[str, Any]:
    """Job の状態と結果。"""
    c = await get_container()
    j = c.jobs.get(job_id)
    return j.model_dump(mode="json") if j else {"error": "not found"}


for _fn in (
    list_motion_presets,
    animate_image,
    set_camera_motion,
    create_transition,
    animate_text,
    create_infographic,
    create_scene,
    get_job,
):
    mcp.tool(name=_fn.__name__, description=(_fn.__doc__ or "").strip().split("\n")[0])(_fn)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
