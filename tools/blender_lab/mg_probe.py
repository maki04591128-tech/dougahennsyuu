"""凝った画面演出に使う Blender 5.x の機能を実機で確かめる（スキル作成用）。

blender -b --factory-startup -P mg_probe.py -- <出力フォルダ> <フォント>
結果: <出力>/mg_probe.json と確認用の PNG
"""

import json
import sys
import traceback
from pathlib import Path

import bpy

argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
OUT = Path(argv[0] if argv else ".")
FONT = argv[1] if len(argv) > 1 else ""
OUT.mkdir(parents=True, exist_ok=True)
R = {}


def test(name):
    def deco(fn):
        try:
            R[name] = {"ok": True, "info": fn()}
        except Exception as e:  # noqa: BLE001
            R[name] = {
                "ok": False,
                "error": f"{type(e).__name__}: {e}",
                "tb": traceback.format_exc()[-700:],
            }
        return fn

    return deco


def props(obj, skip=("rna_type",)):
    return {p.identifier: p.type for p in obj.bl_rna.properties if p.identifier not in skip}


def base_scene(name="S", w=960, h=540, end=60):
    sc = bpy.data.scenes.new(name)
    sc.render.resolution_x, sc.render.resolution_y = w, h
    sc.render.resolution_percentage = 100
    sc.render.fps = 30
    sc.frame_start, sc.frame_end = 1, end
    sc.view_settings.view_transform = "Standard"
    sc.render.image_settings.file_format = "PNG"
    return sc


def still(sc, frame, name):
    # 背景実行では frame_set しても render(write_still) は 1 フレーム目を描くことがあるため、
    # 範囲を 1 フレームにしてアニメーションとして描く（出力名は # に番号が入る）
    s0, s1 = sc.frame_start, sc.frame_end
    sc.frame_start = sc.frame_end = frame
    stem = name.rsplit(".", 1)[0]
    sc.render.filepath = str(OUT / (stem + "_#"))
    bpy.ops.render.render(animation=True, scene=sc.name)
    sc.frame_start, sc.frame_end = s0, s1
    return any(OUT.glob(stem + "_*.png"))


bpy.ops.wm.read_factory_settings(use_empty=True)


# 1) コンポジタのノード（演出に使うもの）が作れるか、入力名
@test("compositor_nodes")
def _():
    t = bpy.data.node_groups.new("probe", "CompositorNodeTree")
    names = sorted(n for n in dir(bpy.types) if n.startswith("CompositorNode"))
    want = [
        n
        for n in names
        if any(
            k in n
            for k in (
                "Chrom",
                "Noise",
                "Vign",
                "Blur",
                "Glare",
                "Lens",
                "Displace",
                "Pixel",
                "Kuwahara",
                "Posterize",
                "Translate",
                "Transform",
                "Scale",
                "Rotate",
                "SeparateColor",
                "CombineColor",
                "Mix",
                "Retime",
                "SunBeams",
                "Split",
                "Tune",
                "Sepia",
                "Box",
                "Ellipse",
                "SequencerStrip",
                "MaskToSDF",
                "Dilate",
                "Inpaint",
                "ColorBalance",
                "Curve",
                "Keying",
                "AlphaOver",
                "SetAlpha",
                "Invert",
                "Levels",
                "Map",
            )
        )
    ]
    info = {}
    for n in want:
        try:
            nd = t.nodes.new(n)
            info[n] = {"in": [s.name for s in nd.inputs], "out": [s.name for s in nd.outputs]}
            for s in nd.inputs:
                if s.type == "MENU":
                    info[n].setdefault("menus", {})[s.name] = str(s.default_value)
        except Exception as e:  # noqa: BLE001
            info[n] = f"ERR {e}"
    return {"all": names, "detail": info}


# 2) コンポジタのエフェクト／トランジションストリップ（5.2+）
@test("compositor_transition_strip")
def _():
    sc = base_scene("Trans", end=40)
    se = sc.sequence_editor_create()
    a = se.strips.new_effect("A", "COLOR", 1, 1, length=40)
    a.color = (0.9, 0.2, 0.2)
    b = se.strips.new_effect("B", "COLOR", 2, 1, length=40)
    b.color = (0.1, 0.4, 0.9)
    tr = se.strips.new_effect("wipe", "COMPOSITOR", 3, 1, length=40, input1=a, input2=b)
    p = props(tr)
    ng = bpy.data.node_groups.new("ShapeWipe", "CompositorNodeTree")
    info = {"strip_props": p}
    # 作成直後のインターフェースを確認してから自前で作る
    info["default_interface"] = [
        (i.name, getattr(i, "socket_type", "")) for i in ng.interface.items_tree
    ]
    for nm, st in (
        ("Image", "NodeSocketColor"),
        ("Second", "NodeSocketColor"),
        ("Fader", "NodeSocketFloat"),
    ):
        ng.interface.new_socket(name=nm, in_out="INPUT", socket_type=st)
    ng.interface.new_socket(name="Image", in_out="OUTPUT", socket_type="NodeSocketColor")
    gi, go = ng.nodes.new("NodeGroupInput"), ng.nodes.new("NodeGroupOutput")
    box = ng.nodes.new("CompositorNodeBoxMask")
    mix = ng.nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    info["box_inputs"] = [s.name for s in box.inputs]
    # 進み具合で横幅を広げる箱マスク = ワイプ
    mul = ng.nodes.new("ShaderNodeMath")
    mul.operation = "MULTIPLY"
    mul.inputs[1].default_value = 2.2
    comb = ng.nodes.new("ShaderNodeCombineXYZ")
    comb.inputs["Y"].default_value = 2.0
    ng.links.new(gi.outputs["Fader"], mul.inputs[0])
    ng.links.new(mul.outputs[0], comb.inputs["X"])
    size = [s for s in box.inputs if s.name == "Size"]
    if size:
        ng.links.new(comb.outputs[0], size[0])
    ng.links.new(box.outputs[0], mix.inputs["Factor"])
    ng.links.new(gi.outputs["Image"], mix.inputs[6])
    ng.links.new(gi.outputs["Second"], mix.inputs[7])
    ng.links.new(mix.outputs[2], go.inputs["Image"])
    for k in ("node_group", "compositor_node_group"):
        if k in p:
            setattr(tr, k, ng)
            info["assigned"] = k
    # 進み具合: 既定のフェード（use_default_fade）か effect_fader のキー
    tr.use_default_fade = False
    tr.effect_fader = 0.0
    tr.keyframe_insert("effect_fader", frame=1)
    tr.effect_fader = 1.0
    tr.keyframe_insert("effect_fader", frame=40)
    info["interface_after"] = [
        (i.name, getattr(i, "socket_type", ""), getattr(i, "in_out", ""))
        for i in ng.interface.items_tree
    ]
    # 入力の割り当て（5.3: パネルに出る入力）
    info["strip_after"] = {
        k: str(getattr(tr, k)) for k in p if "fader" in k or "input" in k or "group" in k
    }
    info["rendered"] = [still(sc, f, f"trans_{f:02d}.png") for f in (5, 20, 35)]
    return info


# 3) マスクモディファイア: 文字の形で映像を抜く（トラックマット）
@test("mask_modifier_text_matte")
def _():
    sc = base_scene("Matte", end=10)
    se = sc.sequence_editor_create()
    bg = se.strips.new_effect("bg", "COLOR", 1, 1, length=10)
    bg.color = (0.02, 0.02, 0.05)
    fill = se.strips.new_effect("fill", "COLOR", 2, 1, length=10)
    fill.color = (1.0, 0.6, 0.1)
    m = se.strips.new_effect("matte", "TEXT", 3, 1, length=10)
    m.text = "MATTE 文字"
    if FONT:
        m.font = bpy.data.fonts.load(FONT)
    m.font_size = 160
    m.anchor_x = m.anchor_y = "CENTER"
    m.location = (0.5, 0.5)
    m.mute = True
    mod = fill.modifiers.new("matte", "MASK")
    p = props(mod)
    for k in ("input_mask_type",):
        if k in p:
            setattr(mod, k, "STRIP")
    mod.input_mask_strip = m
    fill.blend_type = "ALPHA_OVER"
    return {"mod_props": p, "text_props": sorted(props(m)), "rendered": still(sc, 5, "matte.png")}


# 4) 速度変更（リタイミング）
@test("retiming_keys")
def _():
    sc = base_scene("Ret", end=60)
    se = sc.sequence_editor_create()
    c = se.strips.new_effect("c", "COLOR", 1, 1, length=60)
    info = {"strip_has": [k for k in props(c) if "retim" in k]}
    try:
        k = c.retiming_keys.add(timeline_frame=30)
        info["key_props"] = props(k)
        info["methods"] = [m for m in dir(k) if not m.startswith("_")]
    except Exception as e:  # noqa: BLE001
        info["add_error"] = str(e)
    return info


# 5) Geometry Nodes で 1 文字ずつ動かす（String to Curves → Curve Instances → Index で遅延）
@test("gn_per_character_text")
def _():
    sc = base_scene("GNText", end=40)
    sc.render.engine = "BLENDER_EEVEE"
    w = bpy.data.worlds.new("W")
    sc.world = w
    w.node_tree.nodes["Background"].inputs["Color"].default_value = (0.02, 0.03, 0.08, 1)
    me = bpy.data.meshes.new("t")
    ob = bpy.data.objects.new("Text", me)
    sc.collection.objects.link(ob)
    tr = bpy.data.node_groups.new("PerChar", "GeometryNodeTree")
    tr.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tr.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    N, L = tr.nodes, tr.links
    out = N.new("NodeGroupOutput")
    s2c = N.new("GeometryNodeStringToCurves")
    s2c.inputs["String"].default_value = "ワクワクAI"
    s2c.inputs["Size"].default_value = 1.0
    if FONT:
        s2c.inputs["Font"].default_value = bpy.data.fonts.load(FONT)
    info = {"s2c_out": [s.name for s in s2c.outputs], "s2c_in": [s.name for s in s2c.inputs]}
    idx = N.new("GeometryNodeInputIndex")
    tm = N.new("GeometryNodeInputSceneTime")
    # t_local = clamp((frame - index*3) / 10)
    m1 = N.new("ShaderNodeMath")
    m1.operation = "MULTIPLY"
    m1.inputs[1].default_value = 3.0
    sub = N.new("ShaderNodeMath")
    sub.operation = "SUBTRACT"
    div = N.new("ShaderNodeMath")
    div.operation = "DIVIDE"
    div.inputs[1].default_value = 10.0
    div.use_clamp = True
    rng = N.new("ShaderNodeFloatCurve")  # 速度の形（オーバーシュート）をカーブで決める
    cm = rng.mapping
    c = cm.curves[0]
    c.points[0].location = (0, 0)
    c.points[1].location = (1, 1)
    c.points.new(0.6, 1.12)
    cm.update()
    oneminus = N.new("ShaderNodeMath")
    oneminus.operation = "SUBTRACT"
    oneminus.inputs[0].default_value = 1.0
    off = N.new("ShaderNodeCombineXYZ")
    mul_y = N.new("ShaderNodeMath")
    mul_y.operation = "MULTIPLY"
    mul_y.inputs[1].default_value = -0.8
    tri = N.new("GeometryNodeTranslateInstances")
    sci = N.new("GeometryNodeScaleInstances")
    rlz = N.new("GeometryNodeRealizeInstances")
    fill = N.new("GeometryNodeFillCurve")
    L.new(idx.outputs[0], m1.inputs[0])
    L.new(tm.outputs["Frame"], sub.inputs[0])
    L.new(m1.outputs[0], sub.inputs[1])
    L.new(sub.outputs[0], div.inputs[0])
    L.new(div.outputs[0], rng.inputs["Value"])
    L.new(rng.outputs[0], oneminus.inputs[1])
    L.new(oneminus.outputs[0], mul_y.inputs[0])
    L.new(mul_y.outputs[0], off.inputs["Y"])
    L.new(s2c.outputs["Curve Instances"], tri.inputs["Instances"])
    L.new(off.outputs[0], tri.inputs["Translation"])
    L.new(tri.outputs[0], sci.inputs["Instances"])
    L.new(rng.outputs[0], sci.inputs["Scale"])
    L.new(sci.outputs[0], rlz.inputs[0])
    L.new(rlz.outputs[0], fill.inputs["Curve"])
    L.new(fill.outputs[0], out.inputs["Geometry"])
    ob.modifiers.new("GN", "NODES").node_group = tr
    mat = bpy.data.materials.new("W")
    mat.node_tree.nodes["Principled BSDF"].inputs["Emission Color"].default_value = (1, 1, 1, 1)
    mat.node_tree.nodes["Principled BSDF"].inputs["Emission Strength"].default_value = 1.0
    me.materials.append(mat)
    sm = N.new("GeometryNodeSetMaterial")
    sm.inputs["Material"].default_value = mat
    L.new(fill.outputs[0], sm.inputs["Geometry"])
    L.new(sm.outputs[0], out.inputs["Geometry"])
    cd = bpy.data.cameras.new("C")
    cd.type = "ORTHO"
    cd.ortho_scale = 7
    cam = bpy.data.objects.new("C", cd)
    cam.location = (2.6, 0.3, 10)
    sc.collection.objects.link(cam)
    sc.camera = cam
    sc.eevee.taa_render_samples = 8
    info["rendered"] = [still(sc, f, f"gntext_{f:02d}.png") for f in (6, 14, 30)]
    return info


# 6) Grease Pencil（5.x の新 API）で線を描いて「線が伸びる」
@test("grease_pencil_v3_stroke")
def _():
    info = {"data_attrs": [a for a in dir(bpy.data) if "grease" in a]}
    gp = (
        bpy.data.grease_pencils.new("GP")
        if hasattr(bpy.data, "grease_pencils")
        else bpy.data.grease_pencils_v3.new("GP")
    )
    layer = gp.layers.new("L")
    fr = layer.frames.new(1)
    d = fr.drawing
    d.add_strokes([16])
    st = d.strokes[0]
    for i, pt in enumerate(st.points):
        pt.position = (i * 0.3 - 2.4, 0, ((i % 4) - 1.5) * 0.4)
        pt.radius = 0.05
    info["stroke_attrs"] = [a for a in dir(st) if not a.startswith("_")][:60]
    info["layer_props"] = [k for k in props(layer)][:40]
    ob = bpy.data.objects.new("GPObj", gp)
    info["modifier_types"] = [
        i.identifier
        for i in bpy.types.Modifier.bl_rna.properties["type"].enum_items
        if i.identifier.startswith("GREASE_PENCIL")
    ]
    md = ob.modifiers.new("build", "GREASE_PENCIL_BUILD")
    info["build_props"] = props(md)
    return info


# 7) 文字ストリップの追加機能（行間・スタイル）
@test("text_strip_props")
def _():
    sc = base_scene("TX", end=5)
    se = sc.sequence_editor_create()
    t = se.strips.new_effect("t", "TEXT", 1, 1, length=5)
    return props(t)


(OUT / "mg_probe.json").write_text(
    json.dumps(R, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
)
print("mg probe done")
