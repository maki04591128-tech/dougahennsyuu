"""Blender 同梱スクリプトの共通処理（シーン・カメラ・発光マテリアル・文字）。

Blender 4.x / 5.x の API 差（EEVEE のエンジン名・設定項目）をここで吸収する。
"""

import json
import sys
from pathlib import Path
from typing import Any

import bpy  # type: ignore[import-not-found]


def load_args() -> dict[str, Any]:
    return json.loads(Path(sys.argv[sys.argv.index("--") + 1]).read_text(encoding="utf-8"))


def setup_scene(w: int, h: int, fps: int, frames: int, samples: int = 8) -> Any:
    scene = bpy.context.scene
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    engines = {
        e.identifier for e in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items
    }
    for name in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "BLENDER_WORKBENCH"):
        if name in engines:
            scene.render.engine = name
            break
    # 発光マテリアルの平面図形なので高品質設定は不要（既定の64サンプルだと1フレーム数秒）
    ee = getattr(scene, "eevee", None)
    if ee is not None:
        for attr, val in (
            ("taa_render_samples", samples),
            ("use_gtao", False),
            ("use_bloom", False),
            ("use_ssr", False),
            ("use_motion_blur", False),
            ("use_shadows", False),
            ("use_raytracing", False),
            ("use_volumetric_shadows", False),
        ):
            if hasattr(ee, attr):
                setattr(ee, attr, val)
    if hasattr(scene.render, "use_motion_blur"):
        scene.render.use_motion_blur = False
    scene.render.use_persistent_data = True
    scene.render.resolution_x, scene.render.resolution_y = w, h
    scene.render.resolution_percentage = 100
    scene.render.fps = fps
    scene.frame_start, scene.frame_end = 1, max(1, frames)
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.film_transparent = True
    scene.view_settings.view_transform = "Standard"
    if scene.world is None:
        scene.world = bpy.data.worlds.new("World")
    scene.world.color = (0, 0, 0)
    # 正投影カメラ。長辺方向の可視幅を 10 とする
    cam_data = bpy.data.cameras.new("cam")
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = 10.0
    cam = bpy.data.objects.new("cam", cam_data)
    cam.location = (0, 0, 10)
    scene.collection.objects.link(cam)
    scene.camera = cam
    return scene


def visible_size(w: int, h: int) -> tuple[float, float]:
    """カメラに写る範囲（ワールド座標の幅・高さ）。"""
    return (10.0, 10.0 * h / w) if w >= h else (10.0 * w / h, 10.0)


def emission(name: str, rgba: list[float]) -> Any:
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = rgba
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    nodes.clear()
    emit = nodes.new("ShaderNodeEmission")
    emit.inputs["Color"].default_value = rgba
    out = nodes.new("ShaderNodeOutputMaterial")
    mat.node_tree.links.new(emit.outputs[0], out.inputs[0])
    return mat


def load_font(path: str | None) -> Any:
    if path and Path(path).is_file():
        return bpy.data.fonts.load(path)
    return None


def text_object(name: str, body: str, font: Any, mat: Any, align: str = "CENTER") -> Any:
    curve = bpy.data.curves.new(name, "FONT")
    curve.body = body
    curve.align_x, curve.align_y = align, "CENTER"
    curve.space_character = 0.98  # 統合版 C2: 字間 −2%
    if font is not None:
        curve.font = font
    curve.materials.append(mat)
    obj = bpy.data.objects.new(name, curve)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def add_outline(obj: Any, mat: Any, width: float) -> list[Any]:
    """文字の縁取り。同じ文字を8方向に width ずらして少し奥に並べる（均一な縁になる）。

    親子付けして元の文字の拡大・移動に追従させる。width は文字のローカル座標の長さ。"""
    dups = []
    d = width * 0.7071
    for i, (dx, dy) in enumerate(
        [(width, 0), (-width, 0), (0, width), (0, -width), (d, d), (d, -d), (-d, d), (-d, -d)]
    ):
        dup = bpy.data.objects.new(f"{obj.name}_ol{i}", obj.data.copy())
        dup.data.materials.clear()
        dup.data.materials.append(mat)
        bpy.context.scene.collection.objects.link(dup)
        dup.parent = obj
        dup.location = (dx, dy, -0.01)
        dups.append(dup)
    return dups


def rect_mesh(name: str, w: float, h: float, mat: Any) -> Any:
    """左下を原点とする w x h の長方形（棒グラフの棒）。"""
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(0, 0, 0), (w, 0, 0), (w, h, 0), (0, h, 0)], [], [(0, 1, 2, 3)])
    mesh.update()
    mesh.materials.append(mat)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def render(scene: Any, out_dir: Path) -> list[str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    scene.render.filepath = str(out_dir / "frame_")
    bpy.ops.render.render(animation=True)
    return sorted(p.name for p in out_dir.glob("frame_*.png"))


def write_result(path: str, scene: Any, files: list[str], **extra: Any) -> None:
    Path(path).write_text(
        json.dumps(
            {
                "engine": scene.render.engine,
                "blender": bpy.app.version_string,
                "frames": len(files),
                **extra,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
