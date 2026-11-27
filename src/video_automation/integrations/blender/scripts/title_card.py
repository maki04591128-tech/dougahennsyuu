"""Blender 同梱スクリプト：タイトル文字（テロップ）のアニメーションを PNG 連番で書き出す。

blender --background --factory-startup --python title_card.py -- args.json
args: text, width, height, fps, frames（動く部分のフレーム数。以降は呼び出し側で静止して延長）,
      font_path, text_color[RGBA], outline_color[RGBA] / outline(bool), size(倍率),
      position("center"|"lower_third"|"top"), animation, output_dir, result_path
animation: pop / slide / zoom / typewriter / bounce / fade / none
  統合版 T16 の題名の入り: decode（T5）・typewriter（T6）・tracking（T7）・flip3d（T9）・
  mask_rise（T10）・slam（T17＋T22 の残像）・stroke（T4。書き順のデータが無い文字の決まり）
  fade / none は1フレームだけ描画し、フェードは呼び出し側（FFmpeg）で付ける。
結果の text_box は最終状態の文字の範囲（ピクセル、左上原点）。帯（背景板）の描画に使う。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import math  # noqa: E402
import random  # noqa: E402

import bpy  # type: ignore[import-not-found]  # noqa: E402
from _common import (  # noqa: E402
    add_outline,
    emission,
    load_args,
    load_font,
    render,
    setup_scene,
    text_object,
    visible_size,
    write_result,
)

VERT_SMALL = "ぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮヵヶ"  # 統合版 T11
VERT_ROTATE = "ー〜～…―‐-「」『』（）()［］[]【】〈〉《》"
PER_CHAR = ("typewriter", "bounce", "decode", "tracking", "flip3d", "mask_rise", "stroke")
CURSOR = "▍"
GLOSS_ALPHA = 0.40
GLOSS_SLOPE = 0.5  # 帯の傾き（画面の y が 1 上がると、帯の位置が 0.5 右へ）


def gloss(mat, scene, w, h, vw, vh, pos_y, box_w, box_h, start, n):  # noqa: ANN001, PLR0913
    """文字の面のマテリアルに、画面の座標で決まる斜めの帯を加算する（帯は文字の形の中だけに出る）。

    帯の中心 P を、文字の範囲の左外から右外へ n フレーム（easeInOutCubic）で動かす。
    """
    nt = mat.node_tree
    N, L = nt.nodes, nt.links
    emit = next(x for x in N if x.bl_idname == "ShaderNodeEmission")
    base = list(emit.inputs["Color"].default_value)
    tc = N.new("ShaderNodeTexCoord")
    sep = N.new("ShaderNodeSeparateXYZ")
    L.new(tc.outputs["Window"], sep.inputs[0])
    # s = x + slope × y（画面の比。y は高さの比を幅の比に直す）
    ky = N.new("ShaderNodeMath")
    ky.operation = "MULTIPLY"
    ky.inputs[1].default_value = GLOSS_SLOPE * h / w
    L.new(sep.outputs["Y"], ky.inputs[0])
    s_ = N.new("ShaderNodeMath")
    s_.operation = "ADD"
    L.new(sep.outputs["X"], s_.inputs[0])
    L.new(ky.outputs[0], s_.inputs[1])
    pos = N.new("ShaderNodeValue")
    d = N.new("ShaderNodeMath")
    d.operation = "SUBTRACT"
    L.new(s_.outputs[0], d.inputs[0])
    L.new(pos.outputs[0], d.inputs[1])
    ad = N.new("ShaderNodeMath")
    ad.operation = "ABSOLUTE"
    L.new(d.outputs[0], ad.inputs[0])
    # 帯の半分の幅: 文字の高さの 35%（幅の比）
    half = max(0.01, 0.35 * box_h / vw)
    band = N.new("ShaderNodeMapRange")
    band.inputs["From Min"].default_value = 0.0
    band.inputs["From Max"].default_value = half
    band.inputs["To Min"].default_value = GLOSS_ALPHA
    band.inputs["To Max"].default_value = 0.0
    band.clamp = True
    L.new(ad.outputs[0], band.inputs["Value"])
    mix = N.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.blend_type = "ADD"
    # ShaderNodeMix は型ごとに同じ名前の入出力を持つので、色（RGBA）のものを選ぶ
    a_in, b_in = [x for x in mix.inputs if x.type == "RGBA"][:2]
    a_in.default_value = base
    b_in.default_value = (1.0, 1.0, 1.0, 1.0)
    fac = next(x for x in mix.inputs if x.name == "Factor" and x.type == "VALUE")
    res = next(x for x in mix.outputs if x.type == "RGBA")
    L.new(band.outputs["Result"], fac)
    L.new(res, emit.inputs["Color"])
    # 文字の範囲（画面の比）。y は上が 1
    x0 = 0.5 - box_w / 2 / vw
    x1 = 0.5 + box_w / 2 / vw
    yc = 0.5 + pos_y / vh
    y0, y1 = yc - box_h / 2 / vh, yc + box_h / 2 / vh
    k = GLOSS_SLOPE * h / w
    lo = x0 + k * y0 - half * 1.2
    hi = x1 + k * y1 + half * 1.2
    sock = pos.outputs[0]
    sock.default_value = lo
    sock.keyframe_insert("default_value", frame=start)
    sock.default_value = hi
    sock.keyframe_insert("default_value", frame=start + n - 1)
    act = nt.animation_data.action if nt.animation_data else None
    if act is not None:
        from bpy_extras import anim_utils

        bag = anim_utils.action_get_channelbag_for_slot(act, nt.animation_data.action_slot)
        for fc in bag.fcurves if bag else []:
            for kp in fc.keyframe_points:
                kp.interpolation, kp.easing = "CUBIC", "EASE_IN_OUT"
    scene.frame_end = max(scene.frame_end, start + n - 1)


SUN_POS = (0.5, 0.9)  # 統合版 L3: Sun Beams の光源（画面の比）
SUN_SIZE = 0.6
SUN_EMIT = 1.8  # しきい値 1.0 を超える明るさにする（文字の面の発光の強さ）


def sun_beams(scene, mat):  # noqa: ANN001
    """統合版 L3: コンポジタの Glare（Sun Beams）で、文字から下へ光の筋を出す。

    透過の書き出しなので、光の筋の明るさを不透明度にも足す（筋が背景の上に見えるように）。"""
    emit = next(x for x in mat.node_tree.nodes if x.bl_idname == "ShaderNodeEmission")
    emit.inputs["Strength"].default_value = SUN_EMIT
    ng = bpy.data.node_groups.new("SunBeams", "CompositorNodeTree")
    ng.interface.new_socket(name="Image", in_out="OUTPUT", socket_type="NodeSocketColor")
    N, L = ng.nodes, ng.links
    rl = N.new("CompositorNodeRLayers")
    rl.scene = scene
    g = N.new("CompositorNodeGlare")
    g.inputs["Type"].default_value = "Sun Beams"
    g.inputs["Threshold"].default_value = 1.0
    g.inputs["Size"].default_value = SUN_SIZE
    g.inputs["Sun Position"].default_value = SUN_POS
    L.new(rl.outputs["Image"], g.inputs["Image"])
    bw = N.new("CompositorNodeRGBToBW")
    L.new(g.outputs["Glare"], bw.inputs[0])
    mx = N.new("ShaderNodeMath")
    mx.operation = "MAXIMUM"
    mx.use_clamp = True
    L.new(rl.outputs["Alpha"], mx.inputs[0])
    L.new(bw.outputs[0], mx.inputs[1])
    sa = N.new("CompositorNodeSetAlpha")
    sa.inputs["Type"].default_value = "Replace Alpha"
    L.new(g.outputs["Image"], sa.inputs["Image"])
    L.new(mx.outputs[0], sa.inputs["Alpha"])
    out = N.new("NodeGroupOutput")
    L.new(sa.outputs[0], out.inputs[0])
    scene.compositing_node_group = ng
    scene.render.use_compositing = True


def fade_material(name, rgba):  # noqa: ANN001
    """不透明度を動かせる発光のマテリアル（Mix の Fac = 不透明度）。"""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    emit = nt.nodes.new("ShaderNodeEmission")
    emit.inputs["Color"].default_value = rgba
    tr = nt.nodes.new("ShaderNodeBsdfTransparent")
    mix = nt.nodes.new("ShaderNodeMixShader")
    mix.name = "alpha"
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(tr.outputs[0], mix.inputs[1])
    nt.links.new(emit.outputs[0], mix.inputs[2])
    nt.links.new(mix.outputs[0], out.inputs[0])
    if hasattr(mat, "surface_render_method"):  # EEVEE Next: 少ないサンプルでも粒にならない
        mat.surface_render_method = "BLENDED"
    elif hasattr(mat, "blend_method"):
        mat.blend_method = "BLEND"
    return mat


def key_alpha(mats, frame, value, interp="LINEAR"):  # noqa: ANN001
    for m in mats:
        sock = m.node_tree.nodes["alpha"].inputs[0]
        sock.default_value = value
        sock.keyframe_insert("default_value", frame=frame)
        fc = None
        ad = m.node_tree.animation_data
        if ad and ad.action:
            from bpy_extras import anim_utils

            bag = anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot)
            fc = bag.fcurves if bag else []
        for c in fc or []:
            for kp in c.keyframe_points:
                if int(round(kp.co.x)) == frame:
                    kp.interpolation = interp


def holdout_plane(name, x0, y0, x1, y1, z):  # noqa: ANN001
    """この四角の後ろを透明にする（統合版 T10 のマスク）。"""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    ho = nt.nodes.new("ShaderNodeHoldout")
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(ho.outputs[0], out.inputs[0])
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(x0, y0, z), (x1, y0, z), (x1, y1, z), (x0, y1, z)], [], [(0, 1, 2, 3)])
    mesh.materials.append(mat)
    ob = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def entrance_frames(anim, n, fps=30):  # noqa: ANN001
    """入りの長さ（フレーム）。呼び出し側の animation_seconds と同じ値。"""
    return {
        "decode": 2 * (n - 1) + 1,
        "tracking": 18,
        "flip3d": 2 * (n - 1) + 10,
        "mask_rise": 2 * (n - 1) + 10,
        "stroke": 6 * n,
        "slam": 16,
    }.get(anim, 0)


KEY_RGB, FILL_RGB, RIM_RGB = (1.0, 0.89, 0.78), (1.0, 1.0, 1.0), (0.435, 0.765, 1.0)  # L1


def solid3d(scene, root):  # noqa: ANN001
    """統合版 T24: 文字に奥行き（文字の大きさの 12%）とベベル（1.5%）を付け、L1 の 3 点照明
    （キー #FFE3C8 100%・フィル 白 40%・リム #6FC3FF 15%）と L2 のリム（Fresnel IOR 0.95 →
    Color Ramp 一定 → 加算）で立体に見せる。正投影なので、行全体を少し傾けて側面を見せる。"""
    char_h = 0.7  # 文字のローカル座標での高さの目安
    for ob in [o for o in scene.objects if o.type == "FONT"]:
        cu = ob.data
        cu.extrude = char_h * 0.12
        cu.bevel_depth = char_h * 0.015
        cu.bevel_resolution = 2
        for i, m in enumerate(list(cu.materials)):
            if m is None:
                continue
            rgba = list(m.diffuse_color)
            cu.materials[i] = _lit_material(f"{m.name}_lit", rgba)
    for o in [o for o in scene.objects if "_ol" in o.name]:  # 縁取りの写しは立体では使わない
        o.hide_render = True
    root.rotation_euler = (math.radians(-12.0), math.radians(16.0), 0.0)
    for name, rgb, power, loc in (
        ("key", KEY_RGB, 1.0, (3.0, 3.0, 4.2)),  # 斜め前上 45°・距離 3（文字の単位）
        ("fill", FILL_RGB, 0.4, (-3.0, 0.5, 4.2)),  # 反対側の斜め前
        ("rim", RIM_RGB, 0.15, (0.0, 3.0, -3.0)),  # 後ろ斜め上
    ):
        ld = bpy.data.lights.new(name, "AREA")
        ld.size = 1.0
        ld.color = rgb
        ld.energy = 600.0 * power
        lo = bpy.data.objects.new(name, ld)
        lo.location = loc
        scene.collection.objects.link(lo)
        direction = -lo.location.normalized()
        lo.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def _lit_material(name, rgba):  # noqa: ANN001
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Base Color"].default_value = rgba
    bsdf.inputs["Roughness"].default_value = 0.45
    fres = nt.nodes.new("ShaderNodeFresnel")
    fres.inputs["IOR"].default_value = 0.95
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.interpolation = "CONSTANT"
    ramp.color_ramp.elements[0].position = 0.0
    ramp.color_ramp.elements[1].position = 0.5
    emit = nt.nodes.new("ShaderNodeEmission")
    emit.inputs["Color"].default_value = (*RIM_RGB, 1.0)
    add = nt.nodes.new("ShaderNodeAddShader")
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(fres.outputs[0], ramp.inputs[0])
    nt.links.new(ramp.outputs[0], emit.inputs["Strength"])
    nt.links.new(bsdf.outputs[0], add.inputs[0])
    nt.links.new(emit.outputs[0], add.inputs[1])
    nt.links.new(add.outputs[0], out.inputs[0])
    return mat


def main() -> None:
    a = load_args()
    w, h, fps, frames = int(a["width"]), int(a["height"]), int(a["fps"]), int(a["frames"])
    anim = a.get("animation", "pop")
    scene = setup_scene(w, h, fps, frames, int(a.get("samples", 8)))
    vw, vh = visible_size(w, h)
    font = load_font(a.get("font_path"))
    mat = emission("text", a.get("text_color", [1, 1, 1, 1]))
    outline_mat = emission("outline", a.get("outline_color", [0, 0, 0, 1]))
    text = a["text"]
    pos_y = {"center": 0.0, "lower_third": -vh * 0.30, "top": vh * 0.32}[
        a.get("position", "center")
    ]

    # 全体の大きさ（幅の 80%・高さの 25% に収め、size 倍）
    probe = text_object("probe", text, font, mat)
    bpy.context.view_layer.update()
    dims = probe.dimensions.copy()
    bpy.data.objects.remove(probe, do_unlink=True)
    size = float(a.get("size", 1.0))
    scale = min(0.8 * vw / max(dims.x, 1e-3), 0.25 * vh / max(dims.y, 1e-3)) * size
    outline = bool(a.get("outline", True))
    ow = 0.04  # 縁の太さ（文字のローカル座標。文字の高さは約 0.7）

    root = bpy.data.objects.new("root", None)
    scene.collection.objects.link(root)
    root.location = (0, pos_y, 0)
    end = max(2, frames)

    def key(obj, path, frame, value):  # noqa: ANN001
        setattr(obj, path, value)
        obj.keyframe_insert(path, frame=frame)

    def ease(obj, path, interpolation, easing, back=None):  # noqa: ANN001
        """obj の path のキーの補間を変える（Blender 5 のスロット付きアクション）。"""
        from bpy_extras import anim_utils

        ad = obj.animation_data
        if ad is None or ad.action is None:
            return
        bag = anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot)
        if bag is None:
            return
        for fc in bag.fcurves:
            if fc.data_path == path:
                for kp in fc.keyframe_points:
                    kp.interpolation, kp.easing = interpolation, easing
                    if back is not None:
                        kp.back = back

    if a.get("vertical") and len(text.replace("\n", "")) > 1:
        # 統合版 T11 縦書き（Y10 墨と和紙の題名）: 上から下へ字送り 1.0、行は右から左。
        # 1 文字ずつ 2f ずらし、各 6f で不透明度 0→1
        cols = [c for c in text.split("\n") if c] or [text]
        longest = max(len(c) for c in cols)
        unit = min(0.85 * vh / max(longest, 1), 0.8 * vw / max(len(cols) * 1.4, 1)) * size
        gp = text_object("gprobe", "国", font, mat)
        bpy.context.view_layer.update()
        g_h = max(gp.dimensions.y, 1e-3)
        bpy.data.objects.remove(gp, do_unlink=True)
        scale_v = unit * 0.9 / g_h  # 字の高さを字送りの 90% に
        tc = a.get("text_color", [1, 1, 1, 1])
        k = 0
        for ci, col in enumerate(cols):
            x = (len(cols) - 1) / 2 * unit * 1.4 - ci * unit * 1.4
            for j, ch in enumerate(col):
                y = ((longest - 1) / 2 - j) * unit
                cm = fade_material(f"v{k}", tc)
                o = text_object(f"v{k}", ch, font, cm)
                o.parent = root
                dx, dy, rot = 0.0, 0.0, 0.0
                if ch in VERT_SMALL:  # 小書きの仮名: 右へ 0.1・上へ 0.1
                    dx, dy = 0.1 * unit, 0.1 * unit
                elif ch in "、。":  # 句読点: 右へ 0.6・上へ 0.6
                    dx, dy = 0.6 * unit, 0.6 * unit
                elif ch in VERT_ROTATE:  # 長音・波線・括弧: 時計回りに 90°
                    rot = math.radians(-90.0)
                o.location = (x + dx, y + dy, 0)
                o.rotation_euler.z = rot
                o.scale = (scale_v,) * 3
                st = 1 + 2 * k
                key_alpha([cm], 1, 0.0)
                if st > 1:
                    key_alpha([cm], st, 0.0)
                key_alpha([cm], st + 6, 1.0)
                k += 1
        box_w, box_h = len(cols) * unit * 1.4, longest * unit
        main_obj = root
    elif anim in PER_CHAR and len(text.replace("\n", "")) > 1 and "\n" not in text:
        # 1文字ずつのオブジェクト。位置は先頭からの部分文字列の幅で決める
        widths = []
        for i in range(1, len(text) + 1):
            o = text_object("m", text[:i], font, mat, align="LEFT")
            bpy.context.view_layer.update()
            widths.append(o.dimensions.x)
            bpy.data.objects.remove(o, do_unlink=True)
        total = widths[-1]
        n = len(text)
        line_h = dims.y * scale
        rnd = random.Random(100)  # 統合版 T1: 乱数の種 = 行の番号 × 100 ＋ 文字の番号
        tc, oc = a.get("text_color", [1, 1, 1, 1]), a.get("outline_color", [0, 0, 0, 1])
        stagger = max(1, round((end - 1) * 0.7 / max(n - 1, 1)))
        if anim == "typewriter":  # 統合版 T6: 2f ずらしでくっきり（1 秒 15 字）
            stagger = 2
        if anim == "mask_rise":  # 統合版 T10: 行の高さの四角の下を隠す
            hb = line_h * 0.62
            holdout_plane("mask", -vw, -vh * 2, vw, pos_y - hb, 1.0)
        for i, ch in enumerate(text):
            left = widths[i - 1] if i else 0.0
            cx = ((left + widths[i]) / 2 - total / 2) * scale
            fades = anim in ("tracking", "stroke")
            cm = fade_material(f"t{i}", tc) if fades else mat
            om = fade_material(f"o{i}", oc) if fades else outline_mat
            o = text_object(f"c{i}", ch, font, cm)
            o.parent = root
            if outline and ch.strip():
                add_outline(o, om, ow)
            start = 1 + i * stagger
            if anim == "typewriter":
                # その文字の番が来たら1フレームで現れる（補間の種類に依らないよう、
                # 直前のフレームで 0・当該フレームで原寸のキーを打つ）
                if start > 1:
                    key(o, "scale", 1, (0, 0, 0))
                    key(o, "scale", start - 1, (0, 0, 0))
                key(o, "scale", start, (scale,) * 3)
                o.location = (cx, 0, 0)
            elif anim == "decode":  # 統合版 T5: 確定の前の 6f は同じ行の文字を 2f ごとに差し替え
                o.location = (cx, 0, 0)
                start = 1 + 2 * i
                if start > 1:
                    key(o, "scale", 1, (0, 0, 0))
                    key(o, "scale", start - 1, (0, 0, 0))
                key(o, "scale", start, (scale,) * 3)
                pool = [c for c in text if c.strip()] or [ch]
                for j, f0 in enumerate(range(start - 6, start, 2)):
                    if f0 + 1 < 1:
                        continue
                    alt = text_object(f"d{i}_{j}", rnd.choice(pool), font, mat)
                    alt.parent = root
                    alt.location = (cx, 0, 0)
                    key(alt, "scale", 1, (0, 0, 0))
                    if f0 > 1:
                        key(alt, "scale", f0, (0, 0, 0))
                    key(alt, "scale", max(1, f0 + 1), (scale,) * 3)
                    key(alt, "scale", max(2, f0 + 3), (0, 0, 0))
                    ease(alt, "scale", "CONSTANT", "AUTO")
                ease(o, "scale", "CONSTANT", "AUTO")
            elif anim == "tracking":  # 統合版 T7: 字間 +30%→0・不透明度 0→1、18f
                o.scale = (scale,) * 3
                key(o, "location", 1, (cx * 1.3, 0, 0))
                key(o, "location", 19, (cx, 0, 0))
                ease(o, "location", "CUBIC", "EASE_OUT")
                key_alpha([cm, om], 1, 0.0)
                key_alpha([cm, om], 19, 1.0)
            elif anim == "flip3d":  # 統合版 T9: 下端を軸に X −90°→0、10f、2f ずらし
                piv = bpy.data.objects.new(f"p{i}", None)
                scene.collection.objects.link(piv)
                piv.parent = root
                piv.location = (cx, -line_h / 2, 0)
                o.parent = piv
                o.location = (0, line_h / 2, 0)
                o.scale = (scale,) * 3
                start = 1 + 2 * i
                key(piv, "rotation_euler", 1, (math.radians(-90), 0, 0))
                key(piv, "rotation_euler", start, (math.radians(-90), 0, 0))
                key(piv, "rotation_euler", start + 10, (0, 0, 0))
                ease(piv, "rotation_euler", "CUBIC", "EASE_OUT")
            elif anim == "mask_rise":  # 統合版 T10: y −行の高さ→0、10f（easeOutExpo）
                o.scale = (scale,) * 3
                start = 1 + 2 * i
                key(o, "location", 1, (cx, -line_h * 1.25, 0))
                key(o, "location", start, (cx, -line_h * 1.25, 0))
                key(o, "location", start + 10, (cx, 0, 0))
                ease(o, "location", "EXPO", "EASE_OUT")
            elif anim == "stroke":  # 統合版 T4（書き順のデータが無い文字）: 順に 0→1 を 4f
                o.scale = (scale,) * 3
                o.location = (cx, 0, 0)
                f0 = 1 + 6 * i
                key_alpha([cm, om], 1, 0.0)
                if f0 > 1:
                    key_alpha([cm, om], f0, 0.0)
                key_alpha([cm, om], f0 + 4, 1.0)
            else:  # bounce: 上から落ちて弾む
                o.scale = (scale,) * 3
                drop = vh * 0.12
                key(o, "location", 1, (cx, drop, 0))
                key(o, "location", start, (cx, drop, 0))
                key(
                    o,
                    "location",
                    min(end, start + max(2, round(fps * 0.25))),
                    (cx, -drop * 0.08, 0),
                )
                key(o, "location", min(end, start + max(3, round(fps * 0.4))), (cx, 0, 0))
                key(o, "scale", 1, (0, 0, 0))
                key(o, "scale", start, (scale,) * 3)
        if anim == "typewriter":  # 統合版 T6: 最後の文字の後ろにカーソルを 15f 周期で点滅
            # 左揃えにして、出した文字の右端（先頭からの幅）の少し右に置く
            cur = text_object("cursor", CURSOR, font, mat, align="LEFT")
            cur.parent = root
            cur.scale = (scale,) * 3
            for i in range(n):
                f = 1 + i * stagger
                cur.location = ((widths[i] - total / 2 + 0.06) * scale, 0, 0)
                cur.keyframe_insert("location", frame=f)
            last = 1 + (n - 1) * stagger
            f, on = last, True
            while f <= end:
                key(cur, "scale", f, (scale if on else 0.0,) * 3)
                f += 8 if on else 7
                on = not on
            ease(cur, "scale", "CONSTANT", "AUTO")
            ease(cur, "location", "CONSTANT", "AUTO")
        main_obj = root
        box_w, box_h = total * scale, dims.y * scale
    else:
        o = text_object("title", text, font, mat)
        o.parent = root
        if outline:
            add_outline(o, outline_mat, ow)
        o.scale = (scale,) * 3
        # 統合版 B4: 題名の入り。pop は 0.8→1.0（back 1.2 の easeOutBack）、
        # slide は画面の高さの 4% 下から（easeOutExpo）
        if anim == "pop":
            key(o, "scale", 1, (scale * 0.8,) * 3)
            key(o, "scale", end, (scale,) * 3)
            ease(o, "scale", "BACK", "EASE_OUT", back=1.2)
        elif anim == "slide":
            key(o, "location", 1, (0, -vh * 0.04, 0))
            key(o, "location", end, (0, 0, 0))
            ease(o, "location", "EXPO", "EASE_OUT")
        elif anim == "zoom":
            key(o, "scale", 1, (scale * 1.6,) * 3)
            key(o, "scale", end, (scale,) * 3)
        elif anim == "slam":
            # 統合版 T17: 行全体を 3.0→1.0 に 4f（easeInCubic）、着地で 1.00→1.03→1.00（6f）
            for f, v in ((1, 3.0), (5, 1.0), (8, 1.03), (11, 1.0)):
                key(o, "scale", f, (scale * v,) * 3)
            ease(o, "scale", "CUBIC", "EASE_IN")
            # 統合版 T22: 2f・4f・6f 遅れの残像（不透明度 50%・30%・15%）
            for d, al in ((2, 0.5), (4, 0.3), (6, 0.15)):
                gm = fade_material(f"ghost{d}", a.get("text_color", [1, 1, 1, 1]))
                g = text_object(f"ghost{d}", text, font, gm)
                g.parent = root
                g.location = (0, 0, -0.02 * d)
                for f, v in ((1, 3.0), (1 + d, 3.0), (5 + d, 1.0)):
                    key(g, "scale", f, (scale * v,) * 3)
                ease(g, "scale", "CUBIC", "EASE_IN")
                key_alpha([gm], 1, al)
                key_alpha([gm], 5 + d, al)
                key_alpha([gm], min(end, 7 + d), 0.0)
        main_obj = o
        box_w, box_h = dims.x * scale, dims.y * scale
    del main_obj

    if a.get("solid3d"):  # 統合版 T24 立体の文字・L1 3 点照明・L2 リム
        solid3d(scene, root)

    if a.get("sun_beams"):  # 統合版 L3（予告編の題名）
        sun_beams(scene, mat)

    gloss_at = int(a.get("gloss_at") or 0)
    if gloss_at > 0:  # 統合版 L7: 斜めの白い帯（不透明度 40%・加算）が題名の上を 1 回通る
        gloss(mat, scene, w, h, vw, vh, pos_y, box_w, box_h, gloss_at, int(a["gloss_frames"]))

    files = render(scene, Path(a["output_dir"]))
    # 文字の範囲（ピクセル）。ワールド座標 → 画素（左上原点）
    px = w / vw
    cx_px, cy_px = w / 2, h / 2 - pos_y * px
    bw, bh = box_w * px, box_h * px
    write_result(
        a["result_path"],
        scene,
        files,
        font_loaded=font is not None,
        text_box=[round(cx_px - bw / 2), round(cy_px - bh / 2), round(bw), round(bh)],
    )


main()
