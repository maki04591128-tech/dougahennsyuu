"""Blender 5.x の演出レシピを実機で試す（各レシピは静止画を書き出す）。

blender -b --factory-startup -P recipes.py -- <out_dir> <font_path> [only]
結果: <out_dir>/recipes.json（成否・所要時間・エラー）と PNG / MP4
"""

import json
import math
import sys
import time
import traceback
from pathlib import Path

import bpy
from bpy_extras import anim_utils
from mathutils import Vector

ARGS = sys.argv[sys.argv.index("--") + 1 :]
OUT = Path(ARGS[0])
FONT = ARGS[1] if len(ARGS) > 1 else ""
ONLY = ARGS[2] if len(ARGS) > 2 else ""
OUT.mkdir(parents=True, exist_ok=True)
RES: dict = {}


# ---------------------------------------------------------------- 共通
def new_scene(w=960, h=540, samples=16, transparent=False):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.render.engine = "BLENDER_EEVEE"
    sc.render.resolution_x, sc.render.resolution_y = w, h
    sc.render.resolution_percentage = 100
    sc.render.fps = 30
    sc.eevee.taa_render_samples = samples
    sc.view_settings.view_transform = "Standard"  # 指定した色をそのまま出す（図・文字向け）
    sc.render.image_settings.media_type = "IMAGE"
    sc.render.image_settings.file_format = "PNG"
    sc.render.film_transparent = transparent
    sc.render.image_settings.color_mode = "RGBA" if transparent else "RGB"
    return sc


def link(obj):
    bpy.context.scene.collection.objects.link(obj)
    return obj


def world(color=(0.02, 0.03, 0.06), strength=1.0):
    w = bpy.data.worlds.new("World")
    bpy.context.scene.world = w
    bg = w.node_tree.nodes.get("Background")
    if bg is None:  # 空のワールドではノードを作る
        bg = w.node_tree.nodes.new("ShaderNodeBackground")
        out = w.node_tree.nodes.new("ShaderNodeOutputWorld")
        w.node_tree.links.new(bg.outputs["Background"], out.inputs["Surface"])
    bg.inputs["Color"].default_value = (*color, 1)
    bg.inputs["Strength"].default_value = strength
    return w


def material(name, color, metallic=0.0, rough=0.4, emission=None, strength=0.0):
    m = bpy.data.materials.new(name)
    bsdf = m.node_tree.nodes.get("Principled BSDF")
    if bsdf is None:
        bsdf = m.node_tree.nodes.new("ShaderNodeBsdfPrincipled")
        out = m.node_tree.nodes.get("Material Output") or m.node_tree.nodes.new(
            "ShaderNodeOutputMaterial"
        )
        m.node_tree.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    bsdf.inputs["Base Color"].default_value = (*color, 1)
    bsdf.inputs["Metallic"].default_value = metallic
    bsdf.inputs["Roughness"].default_value = rough
    if emission is not None:
        bsdf.inputs["Emission Color"].default_value = (*emission, 1)
        bsdf.inputs["Emission Strength"].default_value = strength
    return m


def camera(loc=(0, -10, 1.5), target=(0, 0, 0), lens=50, dof=None):
    data = bpy.data.cameras.new("Cam")
    data.lens = lens
    cam = link(bpy.data.objects.new("Cam", data))
    cam.location = loc
    tgt = link(bpy.data.objects.new("Target", None))
    tgt.location = target
    c = cam.constraints.new("TRACK_TO")
    c.target = tgt
    c.track_axis = "TRACK_NEGATIVE_Z"
    c.up_axis = "UP_Y"
    if dof:
        data.dof.use_dof = True
        data.dof.focus_object = tgt
        data.dof.aperture_fstop = dof
    bpy.context.scene.camera = cam
    return cam, tgt


def light(kind="AREA", loc=(4, -4, 6), energy=800, size=4.0, color=(1, 1, 1)):
    d = bpy.data.lights.new(kind, kind)
    d.energy = energy
    d.color = color
    if kind == "AREA":
        d.size = size
    ob = link(bpy.data.objects.new(kind, d))
    ob.location = loc
    ob.rotation_euler = (Vector((0, 0, 0)) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
    return ob


def three_point():
    light("AREA", (4, -5, 5), 900, 5)
    light("AREA", (-5, -3, 2), 300, 6, (0.7, 0.8, 1.0))
    light("AREA", (0, 6, 4), 700, 3, (0.6, 0.8, 1.0))  # 輪郭光


def ease(obj, interpolation="BEZIER", easing="AUTO"):
    """obj の全キーの補間を変える（5.x: F カーブは action のスロットの channelbag にある）。"""
    ad = obj.animation_data
    bag = anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot)
    for fc in bag.fcurves:
        for kp in fc.keyframe_points:
            kp.interpolation = interpolation
            kp.easing = easing
    return bag


def bloom(strength=1.0, threshold=0.8, size=None):
    """コンポジタでグロー（5.0+: scene.compositing_node_group、Glare ノードの入力はソケット）。"""
    sc = bpy.context.scene
    tree = bpy.data.node_groups.new("Comp", "CompositorNodeTree")
    sc.compositing_node_group = tree
    sc.render.use_compositing = True
    rl = tree.nodes.new("CompositorNodeRLayers")
    g = tree.nodes.new("CompositorNodeGlare")
    out = tree.nodes.new("NodeGroupOutput")
    tree.interface.new_socket(name="Image", in_out="OUTPUT", socket_type="NodeSocketColor")
    tree.links.new(rl.outputs["Image"], g.inputs["Image"])
    tree.links.new(g.outputs["Image"], out.inputs["Image"])
    set_input(g, "Type", "Bloom")
    set_input(g, "Threshold", threshold)
    set_input(g, "Strength", strength)
    if size is not None:
        set_input(g, "Size", size)
    return g


def set_input(node, name, value):
    s = node.inputs.get(name)
    if s is None:
        raise KeyError(f"{node.bl_idname} has no input {name!r}: {[i.name for i in node.inputs]}")
    if s.type == "MENU" and isinstance(value, str):
        last = None
        for v in (value, value.upper(), value.title(), value.replace(" ", "_").upper()):
            try:
                s.default_value = v
                return
            except (TypeError, ValueError) as e:
                last = e
        raise ValueError(f"menu {name!r} rejected {value!r}: {last}")
    s.default_value = value


def render_still(name, frame=1):
    sc = bpy.context.scene
    sc.frame_set(frame)
    sc.render.filepath = str(OUT / f"{name}.png")
    t0 = time.perf_counter()
    bpy.ops.render.render(write_still=True)
    return round(time.perf_counter() - t0, 2)


def fit_width(obj, cam, frac=0.8):
    """obj の幅を、カメラから見た画面幅の frac 倍にする（文字が画面からはみ出さないように）。"""
    bpy.context.view_layer.update()
    w = obj.dimensions.x or 1e-6
    dist = (cam.matrix_world.translation - obj.matrix_world.translation).length
    view_w = 2 * dist * math.tan(cam.data.angle_x / 2)
    k = view_w * frac / w
    obj.scale = (obj.scale.x * k, obj.scale.y * k, obj.scale.z * k)
    return k


def font():
    if FONT and Path(FONT).is_file():
        return bpy.data.fonts.load(FONT)
    return None


def text(body, size=1.0, extrude=0.0, bevel=0.0, mat=None, align="CENTER"):
    cu = bpy.data.curves.new("T", "FONT")
    cu.body = body
    f = font()
    if f:
        cu.font = f
    cu.size = size
    cu.extrude = extrude
    cu.bevel_depth = bevel
    cu.bevel_resolution = 3
    cu.align_x = align
    cu.align_y = "CENTER"
    ob = link(bpy.data.objects.new("Text", cu))
    if mat:
        ob.data.materials.append(mat)
    return ob


# ---------------------------------------------------------------- レシピ
def r_title_3d():
    """立体文字（押し出し＋面取り）・金属＋発光・弾むスケール・被写界深度・グロー。"""
    sc = new_scene()
    sc.frame_end = 40
    world((0.01, 0.015, 0.04))
    three_point()
    cam, _ = camera((0, -9, 0.6), (0, 0, -0.2), 50, dof=2.8)
    t = text("ウルツァイト窒化ホウ素", 0.9, 0.12, 0.02, material("Title", (0.9, 0.95, 1.0), 0.8, 0.25))
    t.rotation_euler = (math.radians(90), 0, 0)
    k = fit_width(t, cam, 0.8)
    sub = text("w-BN", 0.6, 0.06, 0.01, material("Accent", (0.3, 0.8, 1.0), 0, 0.3, (0.3, 0.8, 1.0), 6))
    sub.rotation_euler = (math.radians(90), 0, 0)
    sub.location = (0, 0, -1.0)
    t.scale = (0.01,) * 3
    t.keyframe_insert("scale", frame=1)
    t.scale = (k, k, k)
    t.keyframe_insert("scale", frame=18)
    ease(t, "BACK", "EASE_OUT")
    sub.location.y = 2
    sub.keyframe_insert("location", frame=10)
    sub.location.y = 0
    sub.keyframe_insert("location", frame=26)
    ease(sub, "EXPO", "EASE_OUT")
    floor = bpy.data.meshes.new("Floor")
    floor.from_pydata([(-20, -20, -1.6), (20, -20, -1.6), (20, 20, -1.6), (-20, 20, -1.6)], [], [(0, 1, 2, 3)])
    fo = link(bpy.data.objects.new("Floor", floor))
    fo.data.materials.append(material("Floor", (0.03, 0.04, 0.08), 0.0, 0.15))
    bloom(0.8, 0.9)
    return {"f12": render_still("title_3d_f12", 12), "f40": render_still("title_3d_f40", 40)}


def r_gn_grid():
    """ジオメトリノード: 格子に並べた箱がノイズで波打つ（モーショングラフィックスの背景）。"""
    sc = new_scene()
    sc.frame_end = 60
    world((0.01, 0.01, 0.02))
    three_point()
    me = bpy.data.meshes.new("Base")
    ob = link(bpy.data.objects.new("Waves", me))
    tree = bpy.data.node_groups.new("Waves", "GeometryNodeTree")
    tree.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    N = tree.nodes
    L = tree.links
    gin, gout = N.new("NodeGroupInput"), N.new("NodeGroupOutput")
    grid = N.new("GeometryNodeMeshGrid")
    grid.inputs["Size X"].default_value = 12
    grid.inputs["Size Y"].default_value = 12
    grid.inputs["Vertices X"].default_value = 30
    grid.inputs["Vertices Y"].default_value = 30
    cube = N.new("GeometryNodeMeshCube")
    cube.inputs["Size"].default_value = (0.32, 0.32, 1.0)
    inst = N.new("GeometryNodeInstanceOnPoints")
    pos = N.new("GeometryNodeInputPosition")
    t = N.new("GeometryNodeInputSceneTime")
    noise = N.new("ShaderNodeTexNoise")
    noise.noise_dimensions = "4D"
    noise.inputs["Scale"].default_value = 0.35
    comb = N.new("ShaderNodeCombineXYZ")
    comb.inputs["X"].default_value = 1
    comb.inputs["Y"].default_value = 1
    mul = N.new("ShaderNodeMath")
    mul.operation = "MULTIPLY"
    mul.inputs[1].default_value = 3.0
    setmat = N.new("GeometryNodeSetMaterial")
    setmat.inputs["Material"].default_value = material("Bars", (0.2, 0.6, 1.0), 0.3, 0.3, (0.1, 0.4, 1.0), 1.5)
    L.new(grid.outputs["Mesh"], inst.inputs["Points"])
    L.new(cube.outputs["Mesh"], inst.inputs["Instance"])
    L.new(pos.outputs["Position"], noise.inputs["Vector"])
    L.new(t.outputs["Seconds"], noise.inputs["W"])
    L.new(noise.outputs["Fac"], mul.inputs[0])
    L.new(mul.outputs["Value"], comb.inputs["Z"])
    L.new(comb.outputs["Vector"], inst.inputs["Scale"])
    L.new(inst.outputs["Instances"], setmat.inputs["Geometry"])
    L.new(setmat.outputs["Geometry"], gout.inputs["Geometry"])
    del gin
    mod = ob.modifiers.new("Waves", "NODES")
    mod.node_group = tree
    camera((9, -9, 7), (0, 0, 0), 35)
    bloom(0.6, 1.0)
    return {"f1": render_still("gn_grid_f1", 1), "f45": render_still("gn_grid_f45", 45)}


def r_molecule():
    """分子模型（球と棒）を親の空オブジェクトで回す。リンク複製でメッシュを共有。"""
    sc = new_scene()
    sc.frame_end = 120
    world((0.015, 0.02, 0.04))
    three_point()
    root = link(bpy.data.objects.new("Mol", None))
    bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, radius=0.35)
    proto_b = bpy.context.active_object
    proto_b.data.shade_smooth()
    proto_b.data.materials.append(material("B", (1.0, 0.7, 0.7), 0, 0.3))
    bpy.ops.mesh.primitive_cylinder_add(vertices=24, radius=0.08, depth=1.0)
    proto_bond = bpy.context.active_object
    proto_bond.data.shade_smooth()
    proto_bond.data.materials.append(material("Bond", (0.8, 0.8, 0.85), 0.5, 0.3))
    mat_n = material("N", (0.25, 0.35, 1.0), 0, 0.3)
    atoms = []
    # 簡単な六員環を 2 層（ウルツ鉱型のイメージ）
    for layer, z in enumerate((-0.7, 0.7)):
        for i in range(6):
            a = math.radians(60 * i + 30 * layer)
            p = Vector((1.6 * math.cos(a), 1.6 * math.sin(a), z))
            ob = proto_b.copy()  # メッシュを共有（ob.data は同じ）
            if (i + layer) % 2:
                ob.data = proto_b.data.copy()
                ob.data.materials[0] = mat_n
            ob.location = p
            ob.parent = root
            link(ob)
            atoms.append(p)
    for i, p in enumerate(atoms):
        for q in atoms[i + 1 :]:
            d = (q - p).length
            if d < 1.75:
                b = proto_bond.copy()
                b.location = (p + q) / 2
                b.scale = (1, 1, d)
                b.rotation_euler = (q - p).to_track_quat("Z", "Y").to_euler()
                b.parent = root
                link(b)
    for ob in (proto_b, proto_bond):
        bpy.data.objects.remove(ob)
    root.rotation_euler = (0.4, 0, 0)
    root.keyframe_insert("rotation_euler", frame=1)
    root.rotation_euler = (0.4, 0, math.radians(360))
    root.keyframe_insert("rotation_euler", frame=121)
    ease(root, "LINEAR")
    camera((0, -8, 2.5), (0, 0, 0), 50, dof=4.0)
    bloom(0.3, 1.0)
    return {"f1": render_still("molecule_f1", 1), "f40": render_still("molecule_f40", 40)}


def r_bar3d():
    """3D 棒グラフ: 伸びる棒・数値ラベル・カメラが少し回り込む。"""
    sc = new_scene()
    sc.frame_end = 60
    world((0.02, 0.025, 0.05))
    three_point()
    vals = [("ダイヤモンド", 97, (1.0, 0.75, 0.3)), ("w-BN", 114, (0.3, 0.8, 1.0))]
    vmax = max(v for _, v, _ in vals)
    for i, (lab, v, col) in enumerate(vals):
        x = (i - 0.5) * 2.4
        bpy.ops.mesh.primitive_cube_add(size=1)
        bar = bpy.context.active_object
        bar.data.materials.append(material(f"bar{i}", col, 0.2, 0.35, col, 0.6))
        h = 4 * v / vmax
        # 原点を底に: メッシュを上へずらす
        for vtx in bar.data.vertices:
            vtx.co.z += 0.5
        bar.location = (x, 0, 0)
        bar.scale = (1.2, 1.2, 0.001)
        bar.keyframe_insert("scale", frame=1 + i * 6)
        bar.scale = (1.2, 1.2, h)
        bar.keyframe_insert("scale", frame=30 + i * 6)
        ease(bar, "EXPO", "EASE_OUT")
        num = text(f"{v} GPa", 0.45, 0.03, 0.005, material(f"n{i}", (1, 1, 1)))
        num.rotation_euler = (math.radians(90), 0, 0)
        num.location = (x, -0.7, h + 0.45)
        lb = text(lab, 0.35, 0.02, 0.0, material(f"l{i}", (0.85, 0.9, 1.0)))
        lb.rotation_euler = (math.radians(90), 0, 0)
        lb.location = (x, -0.7, -0.4)
    cam, _ = camera((-3, -13, 4.0), (0, 0, 2.0), 40)
    cam.keyframe_insert("location", frame=1)
    cam.location = (3, -13, 3.5)
    cam.keyframe_insert("location", frame=60)
    ease(cam, "SINE", "EASE_IN_OUT")
    return {"f20": render_still("bar3d_f20", 20), "f60": render_still("bar3d_f60", 60)}


def r_grease_pencil():
    """Grease Pencil v3: 手書き風の矢印をコードで描く（drawing.add_strokes と points）。"""
    sc = new_scene()
    world((0.95, 0.95, 0.93))
    gp = bpy.data.grease_pencils.new("GP")
    ob = link(bpy.data.objects.new("GP", gp))
    mat = bpy.data.materials.new("Ink")
    bpy.data.materials.create_gpencil_data(mat)
    mat.grease_pencil.color = (0.85, 0.1, 0.1, 1)
    gp.materials.append(mat)
    layer = gp.layers.new("Lines")
    frame = layer.frames.new(1)
    dr = frame.drawing
    pts = [(-3 + 0.3 * i, 0, 0.6 * math.sin(i / 3)) for i in range(21)]
    dr.add_strokes([len(pts), 3])
    s = dr.strokes[0]
    for p, co in zip(s.points, pts, strict=True):
        p.position = co
        p.radius = 0.04
    head = dr.strokes[1]
    end = Vector(pts[-1])
    for p, co in zip(head.points, [end + Vector((-0.4, 0, 0.35)), end, end + Vector((-0.45, 0, -0.3))], strict=True):
        p.position = co
        p.radius = 0.04
    camera((0, -10, 0), (0, 0, 0), 35)
    light("SUN", (0, -5, 5), 3)
    return {"f1": render_still("grease_pencil", 1)}


def r_transparent_overlay():
    """背景を透明にして RGBA PNG で出す（動画編集で重ねる用の素材）。"""
    new_scene(transparent=True)
    three_point()
    t = text("114 GPa", 1.2, 0.15, 0.02, material("Num", (0.3, 0.8, 1.0), 0.3, 0.3, (0.3, 0.8, 1.0), 2))
    t.rotation_euler = (math.radians(90), math.radians(-8), 0)
    camera((0, -8, 0.4), (0, 0, 0), 50)
    return {"f1": render_still("overlay_rgba", 1)}


def r_video_out():
    """動画で直接書き出す（5.0+: media_type='VIDEO' を先に設定してから FFMPEG）。"""
    sc = new_scene(640, 360, 8)
    world((0.02, 0.03, 0.06))
    three_point()
    bpy.ops.mesh.primitive_monkey_add(size=1.5)
    m = bpy.context.active_object
    m.data.shade_smooth()
    m.data.materials.append(material("M", (0.9, 0.5, 0.2), 0, 0.4))
    m.keyframe_insert("rotation_euler", frame=1)
    m.rotation_euler.z = math.radians(180)
    m.keyframe_insert("rotation_euler", frame=24)
    camera((0, -6, 1), (0, 0, 0), 50)
    sc.frame_start, sc.frame_end = 1, 24
    s = sc.render.image_settings
    s.media_type = "VIDEO"
    s.file_format = "FFMPEG"
    sc.render.ffmpeg.format = "MPEG4"
    sc.render.ffmpeg.codec = "H264"
    sc.render.ffmpeg.constant_rate_factor = "HIGH"
    sc.render.filepath = str(OUT / "video_out.mp4")
    t0 = time.perf_counter()
    bpy.ops.render.render(animation=True)
    files = [p.name for p in OUT.glob("video_out*")]
    return {"sec": round(time.perf_counter() - t0, 2), "files": files}


def r_perf_1080():
    """1080p・64 サンプルで立体文字＋グロー 1 フレームの時間（書き出し時間の見積もり用）。"""
    sc = new_scene(1920, 1080, 64)
    world((0.01, 0.015, 0.04))
    three_point()
    cam, _ = camera((0, -9, 0.6), (0, 0, -0.2), 50, dof=2.8)
    t = text("ウルツァイト窒化ホウ素", 0.9, 0.12, 0.02, material("Title", (0.9, 0.95, 1.0), 0.8, 0.25))
    t.rotation_euler = (math.radians(90), 0, 0)
    fit_width(t, cam, 0.8)
    bloom(0.8, 0.9)
    first = render_still("perf_1080_a", 1)
    second = render_still("perf_1080_b", 2)  # シェーダーのコンパイル後
    return {"first": first, "second": second}


def ease_id(idb, interpolation="BEZIER", easing="AUTO"):
    """オブジェクト以外（カーブなどのデータ）に付けたキーの補間を変える。"""
    ad = idb.animation_data
    bag = anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot)
    for fc in bag.fcurves:
        for kp in fc.keyframe_points:
            kp.interpolation = interpolation
            kp.easing = easing


def r_curve_draw():
    """線が伸びて描かれる（カーブの bevel_factor_end をキーフレーム）。矢印・軌跡の演出。"""
    sc = new_scene()
    sc.frame_end = 40
    world((0.02, 0.03, 0.06))
    three_point()
    cu = bpy.data.curves.new("Path", "CURVE")
    cu.dimensions = "3D"
    sp = cu.splines.new("BEZIER")
    pts = [(-4, 0, -1), (-1.5, 0, 1.2), (1.5, 0, -1.2), (4, 0, 1)]
    sp.bezier_points.add(len(pts) - 1)
    for bp, co in zip(sp.bezier_points, pts, strict=True):
        bp.co = co
        bp.handle_left_type = bp.handle_right_type = "AUTO"
    cu.bevel_depth = 0.06
    cu.use_fill_caps = True
    ob = link(bpy.data.objects.new("Path", cu))
    ob.data.materials.append(material("Line", (0.3, 0.8, 1.0), 0, 0.3, (0.3, 0.8, 1.0), 4))
    cu.bevel_factor_end = 0.0
    cu.keyframe_insert("bevel_factor_end", frame=1)
    cu.bevel_factor_end = 1.0
    cu.keyframe_insert("bevel_factor_end", frame=30)
    ease_id(cu, "CUBIC", "EASE_IN_OUT")
    camera((0, -12, 0), (0, 0, 0), 50)
    bloom(0.7, 1.0)
    return {"f15": render_still("curve_draw_f15", 15), "f35": render_still("curve_draw_f35", 35)}


def r_gn_text_reveal():
    """ジオメトリノードで文字を 1 文字ずつ出す（String to Curves → 塗り → 押し出し）。"""
    sc = new_scene()
    sc.frame_end = 40
    world((0.02, 0.02, 0.04))
    three_point()
    ob = link(bpy.data.objects.new("Reveal", bpy.data.meshes.new("Reveal")))
    tree = bpy.data.node_groups.new("Reveal", "GeometryNodeTree")
    tree.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    N, L = tree.nodes, tree.links
    out = N.new("NodeGroupOutput")
    st = N.new("FunctionNodeInputString")
    st.string = "押し込み強度 114 GPa"
    t = N.new("GeometryNodeInputSceneTime")
    div = N.new("ShaderNodeMath")
    div.operation = "DIVIDE"
    div.inputs[1].default_value = 2.0  # 2 フレームに 1 文字
    sl = N.new("FunctionNodeSliceString")
    s2c = N.new("GeometryNodeStringToCurves")
    f = font()
    if f:  # 5.x はフォントも入力ソケット（以前は node.font）
        if "Font" in s2c.inputs:
            s2c.inputs["Font"].default_value = f
        else:
            s2c.font = f
    s2c.inputs["Size"].default_value = 0.8
    real = N.new("GeometryNodeRealizeInstances")
    fill = N.new("GeometryNodeFillCurve")
    ext = N.new("GeometryNodeExtrudeMesh")
    ext.inputs["Offset Scale"].default_value = 0.12
    join = N.new("GeometryNodeJoinGeometry")
    mat = N.new("GeometryNodeSetMaterial")
    mat.inputs["Material"].default_value = material("RevealM", (1, 1, 1), 0.2, 0.3)
    L.new(st.outputs["String"], sl.inputs["String"])
    L.new(t.outputs["Frame"], div.inputs[0])
    L.new(div.outputs["Value"], sl.inputs["Length"])
    L.new(sl.outputs["String"], s2c.inputs["String"])
    L.new(s2c.outputs["Curve Instances"], real.inputs["Geometry"])
    L.new(real.outputs["Geometry"], fill.inputs["Curve"])
    L.new(fill.outputs["Mesh"], ext.inputs["Mesh"])
    L.new(fill.outputs["Mesh"], join.inputs["Geometry"])
    L.new(ext.outputs["Mesh"], join.inputs["Geometry"])
    L.new(join.outputs["Geometry"], mat.inputs["Geometry"])
    L.new(mat.outputs["Geometry"], out.inputs["Geometry"])
    ob.modifiers.new("Reveal", "NODES").node_group = tree
    ob.rotation_euler = (math.radians(90), 0, 0)
    ob.location = (-3.4, 0, 0)  # String to Curves は左端が原点
    camera((0, -10, 0.5), (0, 0, 0), 50)
    return {"f10": render_still("gn_text_f10", 10), "f40": render_still("gn_text_f40", 40)}


def r_toon_freestyle():
    """トゥーン調（Shader to RGB → カラーランプ）＋ Freestyle の輪郭線。"""
    sc = new_scene()
    world((0.9, 0.92, 0.95))
    light("SUN", (3, -4, 6), 4)
    m = bpy.data.materials.new("Toon")
    nt = m.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    dif = nt.nodes.new("ShaderNodeBsdfDiffuse")
    s2r = nt.nodes.new("ShaderNodeShaderToRGB")
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.interpolation = "CONSTANT"
    ramp.color_ramp.elements[0].color = (0.1, 0.25, 0.6, 1)
    ramp.color_ramp.elements[1].position = 0.4
    ramp.color_ramp.elements[1].color = (0.35, 0.7, 1.0, 1)
    emi = nt.nodes.new("ShaderNodeEmission")
    outn = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(dif.outputs["BSDF"], s2r.inputs["Shader"])
    nt.links.new(s2r.outputs["Color"], ramp.inputs["Fac"])
    nt.links.new(ramp.outputs["Color"], emi.inputs["Color"])
    nt.links.new(emi.outputs["Emission"], outn.inputs["Surface"])
    bpy.ops.mesh.primitive_monkey_add(size=2)
    mk = bpy.context.active_object
    mk.data.shade_smooth()
    sub = mk.modifiers.new("Sub", "SUBSURF")
    sub.levels = sub.render_levels = 2
    mk.data.materials.append(m)
    sc.render.use_freestyle = True
    sc.render.line_thickness_mode = "ABSOLUTE"
    sc.render.line_thickness = 2.0
    fs = bpy.context.view_layer.freestyle_settings
    ls = fs.linesets[0] if fs.linesets else fs.linesets.new("Lines")
    if ls.linestyle is None:  # 空のシーンでは線のスタイルが無い
        ls.linestyle = bpy.data.linestyles.new("Ink")
    ls.linestyle.color = (0.05, 0.05, 0.1)
    camera((0, -7, 0.5), (0, 0, 0), 50)
    return {"f1": render_still("toon_freestyle", 1)}


def r_camera_shake():
    """F カーブのノイズ修飾子で手持ちカメラ風の揺れ（キーは 1 つでよい）。"""
    sc = new_scene()
    sc.frame_end = 60
    world((0.02, 0.03, 0.06))
    three_point()
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=3, radius=1.2)
    bpy.context.active_object.data.materials.append(material("S", (0.9, 0.4, 0.2), 0, 0.3))
    cam, _ = camera((0, -8, 1), (0, 0, 0), 50)
    cam.keyframe_insert("location", frame=1)
    ad = cam.animation_data
    bag = anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot)
    for fc in bag.fcurves:
        mod = fc.modifiers.new("NOISE")
        mod.scale = 12
        mod.strength = 0.25
    a = render_still("shake_f10", 10)
    sc.frame_set(10)
    p10 = tuple(round(v, 3) for v in cam.location)
    sc.frame_set(30)
    p30 = tuple(round(v, 3) for v in cam.location)
    return {"f10": a, "loc10": p10, "loc30": p30, "moved": p10 != p30}


def r_vse():
    """動画編集（シーケンサー）: 動画を置き、文字ストリップを重ねて MP4 に書き出す（5.x は strips）。"""
    sc = new_scene(640, 360, 8)
    se = sc.sequence_editor_create()
    src = OUT / "video_out.mp4"
    mv = se.strips.new_movie("clip", str(src), 1, 1)
    try:
        tx = se.strips.new_effect("title", "TEXT", 2, 1, length=24)
    except TypeError:
        tx = se.strips.new_effect("title", "TEXT", 2, 1, frame_end=25)
    tx.text = "Blender VSE"
    tx.font_size = 40
    sc.frame_start, sc.frame_end = 1, mv.frame_final_duration
    s = sc.render.image_settings
    s.media_type = "VIDEO"
    s.file_format = "FFMPEG"
    sc.render.ffmpeg.format = "MPEG4"
    sc.render.ffmpeg.codec = "H264"
    sc.render.filepath = str(OUT / "vse_out.mp4")
    bpy.ops.render.render(animation=True)
    return {"strips": [x.name for x in se.strips], "duration": mv.frame_final_duration,
            "out": (OUT / "vse_out.mp4").is_file()}


RECIPES = [
    r_title_3d,
    r_gn_grid,
    r_molecule,
    r_bar3d,
    r_grease_pencil,
    r_transparent_overlay,
    r_video_out,
    r_perf_1080,
    r_curve_draw,
    r_gn_text_reveal,
    r_toon_freestyle,
    r_camera_shake,
    r_vse,
]
for fn in RECIPES:
    if ONLY and fn.__name__ != ONLY:
        continue
    t0 = time.perf_counter()
    try:
        out = fn()
        RES[fn.__name__] = {"ok": True, "sec": round(time.perf_counter() - t0, 2), "out": out}
    except Exception as e:  # noqa: BLE001
        RES[fn.__name__] = {
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "tb": traceback.format_exc()[-1500:],
        }
    (OUT / "recipes.json").write_text(json.dumps(RES, ensure_ascii=False, indent=1), encoding="utf-8")
print("recipes done")
