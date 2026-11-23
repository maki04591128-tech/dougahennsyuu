"""Blender 同梱スクリプト：タイトルカード（文字アニメーション）を PNG 連番で書き出す。

blender --background --factory-startup --python title_card.py -- args.json
args: text, width, height, fps, frames, font_path, text_color[RGBA], bg_color[RGBA],
      output_dir, result_path, animation("pop"|"slide"|"none")
Blender 4.x / 5.x の API 差（EEVEE のエンジン名等）をここで吸収する。
"""

import json
import sys
from pathlib import Path

import bpy  # type: ignore[import-not-found]


def main() -> None:
    args = json.loads(Path(sys.argv[sys.argv.index("--") + 1]).read_text(encoding="utf-8"))
    w, h, fps, frames = (
        int(args["width"]),
        int(args["height"]),
        int(args["fps"]),
        int(args["frames"]),
    )
    out_dir = Path(args["output_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)

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
    scene.render.resolution_x, scene.render.resolution_y = w, h
    scene.render.resolution_percentage = 100
    scene.render.fps = fps
    scene.frame_start, scene.frame_end = 1, frames
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    bg = args.get("bg_color", [0, 0, 0, 0])
    scene.render.film_transparent = bg[3] < 1.0
    scene.view_settings.view_transform = "Standard"
    if scene.world is None:
        scene.world = bpy.data.worlds.new("World")
    scene.world.color = bg[:3]

    # カメラ（正投影）。ortho_scale は長辺方向の可視幅
    cam_data = bpy.data.cameras.new("cam")
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = 10.0
    cam = bpy.data.objects.new("cam", cam_data)
    cam.location = (0, 0, 10)
    scene.collection.objects.link(cam)
    scene.camera = cam
    visible_w = 10.0 if w >= h else 10.0 * w / h

    # 文字
    curve = bpy.data.curves.new("title", "FONT")
    curve.body = args["text"]
    curve.align_x, curve.align_y = "CENTER", "CENTER"
    font_path = args.get("font_path")
    if font_path and Path(font_path).is_file():
        curve.font = bpy.data.fonts.load(font_path)
    text = bpy.data.objects.new("title", curve)
    scene.collection.objects.link(text)
    mat = bpy.data.materials.new("title_mat")
    color = args.get("text_color", [1, 1, 1, 1])
    mat.diffuse_color = color
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    nodes.clear()
    emit = nodes.new("ShaderNodeEmission")
    emit.inputs["Color"].default_value = color
    outn = nodes.new("ShaderNodeOutputMaterial")
    mat.node_tree.links.new(emit.outputs[0], outn.inputs[0])
    curve.materials.append(mat)

    bpy.context.view_layer.update()
    dims = text.dimensions
    scale = min(0.8 * visible_w / max(dims.x, 1e-3), 0.25 * visible_w / max(dims.y, 1e-3))
    anim = args.get("animation", "pop")
    if anim == "pop":
        text.scale = (scale * 0.6,) * 3
        text.keyframe_insert("scale", frame=1)
        text.scale = (scale,) * 3
        text.keyframe_insert("scale", frame=max(2, min(frames, int(fps * 0.4))))
    elif anim == "slide":
        text.scale = (scale,) * 3
        text.location = (0, -visible_w * 0.15, 0)
        text.keyframe_insert("location", frame=1)
        text.location = (0, 0, 0)
        text.keyframe_insert("location", frame=max(2, min(frames, int(fps * 0.5))))
    else:
        text.scale = (scale,) * 3

    scene.render.filepath = str(out_dir / "frame_")
    bpy.ops.render.render(animation=True)
    files = sorted(p.name for p in out_dir.glob("frame_*.png"))
    Path(args["result_path"]).write_text(
        json.dumps(
            {
                "engine": scene.render.engine,
                "blender": bpy.app.version_string,
                "frames": len(files),
                "pattern": str(out_dir / "frame_%04d.png"),
                "font_loaded": bool(font_path and Path(font_path).is_file()),
            }
        ),
        encoding="utf-8",
    )


main()
