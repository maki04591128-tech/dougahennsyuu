"""Blender 5.x の API を実機で調べる（スキルの記述を実物で確認するため）。

blender -b --factory-startup -P probe.py -- <out_dir>
結果は <out_dir>/probe.json
"""

import json
import sys
import traceback
from pathlib import Path

import bpy

OUT = Path(sys.argv[sys.argv.index("--") + 1])
OUT.mkdir(parents=True, exist_ok=True)
R: dict = {}


def safe(name, fn):
    try:
        R[name] = fn()
    except Exception as e:  # noqa: BLE001
        R[name] = {"error": f"{type(e).__name__}: {e}", "tb": traceback.format_exc()[-600:]}


def enum_items(struct, prop):
    return [i.identifier for i in struct.bl_rna.properties[prop].enum_items]


sc = bpy.context.scene
safe("version", lambda: bpy.app.version_string)
safe("python", lambda: sys.version)
safe("engines", lambda: enum_items(sc.render, "engine"))
safe("default_engine", lambda: sc.render.engine)
safe("view_transforms", lambda: enum_items(sc.view_settings, "view_transform"))
safe("looks", lambda: enum_items(sc.view_settings, "look"))
safe("default_view_transform", lambda: sc.view_settings.view_transform)
safe("image_media_types", lambda: enum_items(sc.render.image_settings, "media_type"))
safe("image_formats_IMAGE", lambda: enum_items(sc.render.image_settings, "file_format"))


def _video_formats():
    s = sc.render.image_settings
    s.media_type = "VIDEO"
    out = enum_items(s, "file_format")
    s.media_type = "IMAGE"
    return out


safe("image_formats_VIDEO", _video_formats)
safe("ffmpeg_formats", lambda: enum_items(sc.render.ffmpeg, "format"))
safe("ffmpeg_codecs", lambda: enum_items(sc.render.ffmpeg, "codec"))
safe("ffmpeg_crf", lambda: enum_items(sc.render.ffmpeg, "constant_rate_factor"))
safe(
    "render_props",
    lambda: sorted(
        p.identifier
        for p in sc.render.bl_rna.properties
        if any(k in p.identifier for k in ("motion", "film", "compositing", "hair", "use_"))
    ),
)
safe("eevee_props", lambda: sorted(p.identifier for p in sc.eevee.bl_rna.properties))
safe("scene_has_node_tree", lambda: hasattr(sc, "node_tree"))
safe("scene_has_compositing_node_group", lambda: hasattr(sc, "compositing_node_group"))


def _glare():
    tree = bpy.data.node_groups.new("probe_comp", "CompositorNodeTree")
    g = tree.nodes.new("CompositorNodeGlare")
    ins = []
    for s in g.inputs:
        item = {"name": s.name, "identifier": s.identifier, "type": s.type}
        if s.type == "MENU":
            item["default"] = s.default_value
            try:
                item["items"] = [
                    i.identifier for i in s.bl_rna.properties["default_value"].enum_items
                ]
            except Exception as e:  # noqa: BLE001
                item["items_error"] = str(e)
        elif hasattr(s, "default_value"):
            try:
                v = s.default_value
                item["default"] = list(v) if hasattr(v, "__len__") else v
            except Exception:  # noqa: BLE001
                pass
        ins.append(item)
    props = [p.identifier for p in g.bl_rna.properties if not p.is_readonly][:40]
    return {"inputs": ins, "outputs": [s.name for s in g.outputs], "props": props}


safe("glare_node", _glare)


def _node_types(prefix):
    return sorted(
        n
        for n in dir(bpy.types)
        if n.startswith(prefix) and getattr(bpy.types, n).bl_rna.base is not None
    )


safe("compositor_nodes", lambda: _node_types("CompositorNode"))
safe("geometry_node_count", lambda: len(_node_types("GeometryNode")))
safe("geometry_nodes", lambda: _node_types("GeometryNode"))
safe("shader_nodes", lambda: _node_types("ShaderNode"))
safe(
    "light_related_shader_nodes",
    lambda: [n for n in _node_types("ShaderNode") if "Light" in n or "Shadow" in n],
)
safe(
    "anim_utils",
    lambda: [
        n
        for n in dir(__import__("bpy_extras.anim_utils", fromlist=["x"]))
        if "channelbag" in n or "action" in n
    ],
)
safe("action_props", lambda: sorted(p.identifier for p in bpy.types.Action.bl_rna.properties))
safe(
    "text_curve_props", lambda: sorted(p.identifier for p in bpy.types.TextCurve.bl_rna.properties)
)
safe("mesh_methods", lambda: [m for m in dir(bpy.types.Mesh) if "shade" in m or "smooth" in m])
safe("material_has_use_nodes", lambda: "use_nodes" in bpy.types.Material.bl_rna.properties)
safe("world_has_use_nodes", lambda: "use_nodes" in bpy.types.World.bl_rna.properties)
safe(
    "data_collections",
    lambda: [
        n
        for n in dir(bpy.data)
        if n in ("grease_pencils", "annotations", "grease_pencils_v3", "node_groups", "fonts")
    ],
)
safe("gp_types", lambda: [n for n in dir(bpy.types) if n.startswith("GreasePencil")][:40])
safe(
    "seq_editor_props",
    lambda: sorted(p.identifier for p in bpy.types.SequenceEditor.bl_rna.properties),
)
safe(
    "keyframe_interpolations",
    lambda: [
        i.identifier for i in bpy.types.Keyframe.bl_rna.properties["interpolation"].enum_items
    ],
)
safe(
    "keyframe_easing",
    lambda: [i.identifier for i in bpy.types.Keyframe.bl_rna.properties["easing"].enum_items],
)
safe(
    "camera_dof_props",
    lambda: sorted(p.identifier for p in bpy.types.CameraDOFSettings.bl_rna.properties),
)
safe(
    "light_types",
    lambda: [i.identifier for i in bpy.types.Light.bl_rna.properties["type"].enum_items],
)
safe(
    "modifier_types",
    lambda: [i.identifier for i in bpy.types.Modifier.bl_rna.properties["type"].enum_items],
)
safe(
    "gpu_backend",
    lambda: getattr(bpy.app, "gpu_backend", None) if hasattr(bpy.app, "gpu_backend") else None,
)

(OUT / "probe.json").write_text(json.dumps(R, ensure_ascii=False, indent=1), encoding="utf-8")
print("probe done", len(R))
