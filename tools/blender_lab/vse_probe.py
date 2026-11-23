"""Blender 5.x のシーケンサー（動画編集）の API を実機で調べる。

blender -b --factory-startup -P vse_probe.py -- <out_dir> [<audio_file>]
結果は <out_dir>/vse_probe.json
"""

import json
import sys
import traceback
from pathlib import Path

import bpy

ARGS = sys.argv[sys.argv.index("--") + 1 :]
OUT = Path(ARGS[0])
AUDIO = ARGS[1] if len(ARGS) > 1 else ""
OUT.mkdir(parents=True, exist_ok=True)
R: dict = {}


def safe(name, fn):
    try:
        R[name] = fn()
    except Exception as e:  # noqa: BLE001
        R[name] = {"error": f"{type(e).__name__}: {e}", "tb": traceback.format_exc()[-800:]}


def props(t):
    return sorted(p.identifier for p in t.bl_rna.properties if p.identifier != "rna_type")


def enum(t, prop):
    try:
        return [i.identifier for i in t.bl_rna.properties[prop].enum_items]
    except Exception as e:  # noqa: BLE001
        return f"err {e}"


sc = bpy.context.scene
se = sc.sequence_editor_create()
safe("version", lambda: bpy.app.version_string)
safe("strip_types", lambda: enum(bpy.types.Strip, "type"))
safe("blend_types", lambda: enum(bpy.types.Strip, "blend_type"))
safe("modifier_types", lambda: enum(bpy.types.StripModifier, "type"))
safe("strips_methods", lambda: [m for m in dir(se.strips) if not m.startswith("_")])
safe(
    "new_effect_doc",
    lambda: [
        (p.identifier, p.type, [i.identifier for i in p.enum_items] if p.type == "ENUM" else None)
        for p in se.strips.bl_rna.functions["new_effect"].parameters
    ],
)
safe(
    "new_movie_params",
    lambda: [p.identifier for p in se.strips.bl_rna.functions["new_movie"].parameters],
)
safe(
    "new_image_params",
    lambda: [p.identifier for p in se.strips.bl_rna.functions["new_image"].parameters],
)
safe(
    "new_sound_params",
    lambda: [p.identifier for p in se.strips.bl_rna.functions["new_sound"].parameters],
)
safe("strip_props", lambda: props(bpy.types.Strip))
safe("transform_props", lambda: props(bpy.types.StripTransform))
safe("crop_props", lambda: props(bpy.types.StripCrop))
safe("text_props", lambda: props(bpy.types.TextStrip))
safe("color_strip_props", lambda: props(bpy.types.ColorStrip))
safe("sound_strip_props", lambda: props(bpy.types.SoundStrip))
safe("movie_strip_props", lambda: props(bpy.types.MovieStrip))
safe("image_strip_props", lambda: props(bpy.types.ImageStrip))
safe("scene_strip_props", lambda: props(bpy.types.SceneStrip))
safe("effect_types_present", lambda: sorted(n for n in dir(bpy.types) if n.endswith("Strip")))
safe(
    "modifier_classes",
    lambda: {
        n: props(getattr(bpy.types, n))
        for n in dir(bpy.types)
        if n.endswith("Modifier") and issubclass(getattr(bpy.types, n), bpy.types.StripModifier)
    },
)
safe("retiming_funcs", lambda: [m for m in dir(bpy.types.MovieStrip) if "retim" in m])
safe("retiming_key_props", lambda: props(bpy.types.RetimingKey))
safe(
    "seq_compositor_nodes",
    lambda: [
        n
        for n in dir(bpy.types)
        if n.startswith("CompositorNode") and ("Sequencer" in n or "Strip" in n)
    ],
)
safe("timeline_markers", lambda: [m for m in dir(sc.timeline_markers) if not m.startswith("_")])
safe("ffmpeg_audio_codecs", lambda: enum(sc.render.ffmpeg, "audio_codec"))


def _aud():
    import aud  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    info = {"aud": [m for m in dir(aud) if not m.startswith("_")], "numpy": np.__version__}
    if AUDIO:
        s = aud.Sound(AUDIO)
        info["specs"] = list(s.specs)
        info["length"] = s.length
        d = s.data()
        info["data_shape"] = list(d.shape)
    return info


safe("aud", _aud)

(OUT / "vse_probe.json").write_text(json.dumps(R, ensure_ascii=False, indent=1), encoding="utf-8")
print("vse probe done")
