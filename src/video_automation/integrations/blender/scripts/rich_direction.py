"""書き出し済みの映像（字幕なし）に MV/PV 風の画面演出を付けて書き出す（Blender 5.x の VSE）。

RenderService から JSON 引数で呼ばれる同梱スクリプト。字幕は付けない（呼び出し側が最後に焼き込む）。
どの演出を使うか（parts）は、呼び出し側が「演出の強さ」と「用途で許された部品」から決めて渡す。
ここでは渡された部品だけを作る。

引数（JSON）:
  clean_path, out_path, result_path, font, width, height, fps_num, fps_den
  title, catch: 冒頭（コールドオープン）の文字
  chapters: [[開始フレーム(0 始まり), 章の名前], ...]
  cues: [{"segment": 台本の行 id, "text": 字幕, "start_frame": 0 始まり, "end_frame"}, ...]
  beats: [拍のフレーム（0 始まり）, ...]（MV の拍に合わせた脈動。無ければ使わない）
  bars, peaks, consonants: [フレーム, ...]（統合版 M1 の音ハメ。無ければ beats の 4 拍ごと）
  kick: [フレームごとの拡大率, ...]（統合版 M2 のキックの音反応。1.00〜1.02）
  parts: {intro, letterbox, transitions, sfx, chapter_labels, ken_burns, look, punch, glitch,
          flash, iris, lyrics}: bool（lyrics は字幕を動く文字で出す。呼び出し側は焼き込まない）
  params: {intro_frames, ken_burns, bloom, diffusion, dispersion, vignette, flash_alpha}
  limits: {punch: n, chapter_labels: n}（-1 は制限なし）
  colors: {base, main, accent, text}（#RRGGBB。用途の配色）
出力（result_path）: {intro_frames, frames, used: {部品: 回数}, parts, render_sec}
"""

import json
import math
import random
import re
import struct
import sys
import time
import traceback
import wave
from pathlib import Path

import bpy  # type: ignore[import-not-found]

A = json.loads(Path(sys.argv[sys.argv.index("--") + 1]).read_text(encoding="utf-8"))
W, H = int(A["width"]), int(A["height"])
DRAFT = bool(A.get("draft"))  # 統合版 W4: 下書き
U = min(W, H) / 1080  # 1080p を基準にした大きさの倍率
FPS_NUM, FPS_DEN = int(A.get("fps_num", 30)), int(A.get("fps_den", 1))
FPS = FPS_NUM / FPS_DEN
FONT = A.get("font") or ""
PARTS = {k: bool(v) for k, v in (A.get("parts") or {}).items()}
P = A.get("params") or {}
LIMITS = A.get("limits") or {}
OUT = Path(A["out_path"]).resolve()  # 相対パスは Blender では別の場所になるため
WORK = OUT.parent
REPORT: dict = {"parts": {}, "used": {}}


def on(name):
    return PARTS.get(name, False)


def srgb(hex_color):
    """#RRGGBB（sRGB）→ Blender の色（リニア）。"""
    h = hex_color.lstrip("#")
    out = []
    for i in (0, 2, 4):
        c = int(h[i : i + 2], 16) / 255
        out.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    return tuple(out)


COL = A.get("colors") or {}
BASE = srgb(COL.get("base", "#0B1D3A"))
MAIN = srgb(COL.get("main", "#3BB8DB"))
ACCENT = srgb(COL.get("accent", "#FFC752"))
INK = srgb(COL.get("text", "#EEF2F8"))


def use(name, n=1):
    REPORT["used"][name] = REPORT["used"].get(name, 0) + n


def part(name):
    def deco(fn):
        def run(*a, **k):
            try:
                out = fn(*a, **k)
                REPORT["parts"][name] = {"ok": True}
                return out
            except Exception as e:  # noqa: BLE001
                REPORT["parts"][name] = {
                    "ok": False,
                    "error": f"{type(e).__name__}: {e}",
                    "tb": traceback.format_exc()[-900:],
                }
                return None

        return run

    return deco


# ------------------------------------------------------------ 5.3 の位置・長さ（古い版にも対応）
def right(s):
    v = getattr(s, "right_handle", None)
    return v if v is not None else s.frame_final_end


def set_duration(s, n):
    if hasattr(s, "right_handle"):
        s.right_handle = s.left_handle + n
    else:
        s.frame_final_duration = n


# ------------------------------------------------------------ アニメーションの補助
def channelbag(sc):
    from bpy_extras import anim_utils

    ad = sc.animation_data
    if ad is None or ad.action is None:
        return None
    return anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot)


def ease(sc, name_part, interpolation, easing="AUTO"):
    bag = channelbag(sc)
    if bag is None:
        return 0
    n = 0
    for fc in bag.fcurves:
        if name_part in fc.data_path:
            for kp in fc.keyframe_points:
                kp.interpolation, kp.easing = interpolation, easing
            n += 1
    return n


def kt(strip, prop, frame, value):
    setattr(strip.transform, prop, value)
    strip.transform.keyframe_insert(prop, frame=frame)


def kp_(strip, prop, frame, value):
    setattr(strip, prop, value)
    strip.keyframe_insert(prop, frame=frame)


def comp_tree(name, inputs=(("Image", "NodeSocketColor"),)):
    ng = bpy.data.node_groups.new(name, "CompositorNodeTree")
    for nm, st in inputs:
        ng.interface.new_socket(name=nm, in_out="INPUT", socket_type=st)
    ng.interface.new_socket(name="Image", in_out="OUTPUT", socket_type="NodeSocketColor")
    return ng, ng.nodes.new("NodeGroupInput"), ng.nodes.new("NodeGroupOutput")


def mix(ng, blend="MIX", fac=1.0, a=None, b=None):
    m = ng.nodes.new("ShaderNodeMix")
    m.data_type = "RGBA"
    m.blend_type = blend
    m.inputs["Factor"].default_value = fac
    if a is not None:
        m.inputs[6].default_value = a
    if b is not None:
        m.inputs[7].default_value = b
    return m


def font():
    return bpy.data.fonts.load(FONT, check_existing=True) if FONT else None


def eevee_scene(name, length, samples=16):
    sc = bpy.data.scenes.new(name)
    sc.render.resolution_x, sc.render.resolution_y = W, H
    sc.render.fps, sc.render.fps_base = FPS_NUM, FPS_DEN
    sc.frame_start, sc.frame_end = 1, length
    engines = {
        e.identifier for e in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items
    }
    for eng in ("BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"):
        if eng in engines:
            sc.render.engine = eng
            break
    sc.eevee.taa_render_samples = 1 if DRAFT else samples  # 統合版 W4
    sc.render.film_transparent = True
    sc.view_settings.view_transform = "Standard"
    cd = bpy.data.cameras.new(name)
    cd.type = "ORTHO"
    cd.ortho_scale = 16.0 * max(1.0, H / W)  # 長辺の可視幅 = 16（横長は横、縦長は縦）
    cam = bpy.data.objects.new(name + "_cam", cd)
    cam.location = (0, 0, 10)
    sc.collection.objects.link(cam)
    sc.camera = cam
    return sc


# ------------------------------------------------------------ 部品: 1 文字ずつ弾む文字
STEP = int(A.get("stepped") or 0)  # 統合版 M5: コマ打ち（文字の動きを n フレームごとに）
LYRIC_FONT = A.get("lyric_font") or ""  # 統合版 M7: ジャンルの書体
LYRIC_TEXTURE = A.get("lyric_texture") or ""  # 統合版 M7: ジャンルの質感


def kinetic_text_scene(
    name, text, size, color, length, delay=2, dur=12, out_at=None, y=0.0, font_path="", texture="",
    outline_light=False,
):  # fmt: skip
    sc = eevee_scene(name, length)
    ob = bpy.data.objects.new(name, bpy.data.meshes.new(name))
    sc.collection.objects.link(ob)
    tr = bpy.data.node_groups.new(name, "GeometryNodeTree")
    tr.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tr.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    N, L = tr.nodes, tr.links
    out = N.new("NodeGroupOutput")
    s2c = N.new("GeometryNodeStringToCurves")
    s2c.inputs["String"].default_value = text
    s2c.inputs["Size"].default_value = size
    aligned = True
    for k, v in (("Align X", "Center"), ("Align Y", "Middle")):
        try:
            s2c.inputs[k].default_value = v
        except Exception:  # noqa: BLE001
            aligned = False
    if font_path:
        s2c.inputs["Font"].default_value = bpy.data.fonts.load(font_path, check_existing=True)
    elif FONT:
        s2c.inputs["Font"].default_value = font()
    idx, tm = N.new("GeometryNodeInputIndex"), N.new("GeometryNodeInputSceneTime")

    def math_node(op, a=None, b=None, clamp=False):
        m = N.new("ShaderNodeMath")
        m.operation = op
        m.use_clamp = clamp
        for i, v in enumerate((a, b)):
            if v is None:
                continue
            if isinstance(v, (int, float)):
                m.inputs[i].default_value = v
            else:
                L.new(v, m.inputs[i])
        return m.outputs[0]

    frame = tm.outputs["Frame"]
    if STEP > 1:  # 統合版 M5: 時刻を STEP フレームごとに止める（カメラには付けない）
        frame = math_node("MULTIPLY", math_node("FLOOR", math_node("DIVIDE", frame, STEP)), STEP)
    t_in = math_node(
        "DIVIDE",
        math_node("SUBTRACT", frame, math_node("MULTIPLY", idx.outputs[0], delay)),
        dur,
        True,
    )
    curve = N.new("ShaderNodeFloatCurve")
    c = curve.mapping.curves[0]
    c.points[0].location = (0, 0)
    c.points[1].location = (1, 1)
    c.points.new(0.55, 1.14)  # 行き過ぎて戻る
    c.points.new(0.8, 0.97)
    curve.mapping.update()
    L.new(t_in, curve.inputs["Value"])
    scale = curve.outputs[0]
    if out_at:
        t_out = math_node(
            "DIVIDE",
            math_node("SUBTRACT", frame, math_node("ADD", idx.outputs[0], out_at)),
            8,
            True,
        )
        scale = math_node(
            "MULTIPLY", scale, math_node("SUBTRACT", 1.0, math_node("POWER", t_out, 2.0))
        )
    lift = math_node("MULTIPLY", math_node("SUBTRACT", 1.0, t_in), -0.6 * size)
    off = N.new("ShaderNodeCombineXYZ")
    L.new(lift, off.inputs["Y"])
    tri = N.new("GeometryNodeTranslateInstances")
    sci = N.new("GeometryNodeScaleInstances")
    rlz = N.new("GeometryNodeRealizeInstances")
    fill = N.new("GeometryNodeFillCurve")
    setm = N.new("GeometryNodeSetMaterial")
    L.new(s2c.outputs["Curve Instances"], tri.inputs["Instances"])
    L.new(off.outputs[0], tri.inputs["Translation"])
    L.new(tri.outputs[0], sci.inputs["Instances"])
    L.new(scale, sci.inputs["Scale"])
    # 統合版 C5: 各文字を、その文字の外接矩形の中心を基点に拡大する（左下を基点にすると、
    # 拡大の途中で文字が右上へずれる）
    try:
        ib = N.new("GeometryNodeInputInstanceBounds")
        mid = N.new("ShaderNodeVectorMath")
        mid.operation = "ADD"
        half = N.new("ShaderNodeVectorMath")
        half.operation = "SCALE"
        half.inputs["Scale"].default_value = 0.5
        L.new(ib.outputs["Min"], mid.inputs[0])
        L.new(ib.outputs["Max"], mid.inputs[1])
        L.new(mid.outputs[0], half.inputs[0])
        L.new(half.outputs[0], sci.inputs["Center"])
        sci.inputs["Local Space"].default_value = True
    except Exception:  # noqa: BLE001  Instance Bounds の無い古い Blender は今のまま
        pass
    L.new(sci.outputs[0], rlz.inputs[0])
    L.new(rlz.outputs[0], fill.inputs["Curve"])
    L.new(fill.outputs[0], setm.inputs["Geometry"])
    if outline_light:  # 統合版 T19: 輪郭の線の長さ 15% の光の区間を 24f で 1 周させる
        light = outline_light_nodes(N, L, rlz.outputs[0], frame, math_node)
        join = N.new("GeometryNodeJoinGeometry")
        L.new(setm.outputs[0], join.inputs[0])
        L.new(light, join.inputs[0])
        L.new(join.outputs[0], out.inputs["Geometry"])
    else:
        L.new(setm.outputs[0], out.inputs["Geometry"])
    mat = bpy.data.materials.new(name)
    b = mat.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*color, 1)
    b.inputs["Emission Color"].default_value = (*color, 1)
    # 統合版 M7: glow は発光を強く、それ以外の質感は今は影だけ（halftone 等は未対応）
    b.inputs["Emission Strength"].default_value = 3.0 if texture == "glow" else 1.0
    setm.inputs["Material"].default_value = mat
    ob.modifiers.new("GN", "NODES").node_group = tr
    ob.location.y = y
    if not aligned:
        ob.location.x = -len(text) * size * 0.5
    if texture == "shadow":  # 右下へ文字の大きさの 4% ずらした暗い影
        sh = bpy.data.objects.new(name + "_shadow", ob.data)
        sc.collection.objects.link(sh)
        smat = bpy.data.materials.new(name + "_shadow")
        sb = smat.node_tree.nodes["Principled BSDF"]
        sb.inputs["Base Color"].default_value = (0.02, 0.02, 0.03, 1)
        sb.inputs["Emission Strength"].default_value = 0.0
        stree = tr.copy()
        for nd in stree.nodes:
            if nd.bl_idname == "GeometryNodeSetMaterial":
                nd.inputs["Material"].default_value = smat
        sh.modifiers.new("GN", "NODES").node_group = stree
        sh.location = (ob.location.x + size * 0.04, y - size * 0.04, -0.01)
    return sc


PX = 16.0 / W  # 文字の場面の 1px（カメラの可視幅は長辺で 16）
T19_PERIOD = 24  # 1 周のフレーム数
T19_LEN = 0.15  # 光の区間の長さ（輪郭の長さに対する比）


def outline_light_nodes(N, L, curves, frame, math_node):
    """統合版 T19: 文字の輪郭（曲線）を Trim Curve で長さ 15% に切り、24f で 1 周させて、
    太さ 2px の線（白・発光。L4 のグローの代わりに発光を強く）にする。出力は線の形。"""
    start = math_node("FRACT", math_node("DIVIDE", frame, T19_PERIOD))
    end = math_node("MINIMUM", math_node("ADD", start, T19_LEN), 1.0)
    trim = N.new("GeometryNodeTrimCurve")
    try:
        trim.mode = "FACTOR"
    except Exception:  # noqa: BLE001
        pass
    L.new(curves, trim.inputs["Curve"])
    facs = [i for i in trim.inputs if i.name in ("Start", "End") and i.enabled]
    L.new(start, next(i for i in facs if i.name == "Start"))
    L.new(end, next(i for i in facs if i.name == "End"))
    prof = N.new("GeometryNodeCurvePrimitiveCircle")
    prof.inputs["Resolution"].default_value = 6
    prof.inputs["Radius"].default_value = PX  # 太さ 2px
    c2m = N.new("GeometryNodeCurveToMesh")
    L.new(trim.outputs[0], c2m.inputs["Curve"])
    L.new(prof.outputs["Curve"], c2m.inputs["Profile Curve"])
    lm = bpy.data.materials.get("T19_light") or bpy.data.materials.new("T19_light")
    b = lm.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (1, 1, 1, 1)
    b.inputs["Emission Color"].default_value = (1, 1, 1, 1)
    b.inputs["Emission Strength"].default_value = 4.0
    sm = N.new("GeometryNodeSetMaterial")
    sm.inputs["Material"].default_value = lm
    L.new(c2m.outputs[0], sm.inputs["Geometry"])
    return sm.outputs[0]


def _key(ob, path, frame, value, index=-1):
    if index >= 0:
        getattr(ob, path)[index] = value
    else:
        setattr(ob, path, value)
    ob.keyframe_insert(path, index=index, frame=frame)


NEON_ALPHA = (0.0, 1.0, 0.3, 1.0, 0.5, 1.0)  # 点滅・ネオン: 2f ごと（10f）
FLICKER_OUT = (1.0, 0.2, 1.0, 0.2, 0.0)  # 点滅して消える: 2f ごと（8f）
EXIT_F = 8  # 行全体の出の長さ


def line_alpha(entrance, exit_, length, out_at):
    """行の場面のストリップの不透明度のキー（場面の中のフレーム, 値）。使った (入り, 出) も返す。"""
    keys = []
    ent = ext = None
    if entrance == "neon":
        keys += [(1 + 2 * j, a) for j, a in enumerate(NEON_ALPHA)]
        ent = "neon"
    elif entrance in FADE_IN:  # 入りの不透明度 0→1（行全体の近似）
        keys += [(1, 0.0), (1 + FADE_IN[entrance], 1.0)]
    if out_at < length:
        if exit_ == "flicker":
            keys += [(out_at + 2 * j, a) for j, a in enumerate(FLICKER_OUT)]
            ext = "flicker"
        elif exit_ in ("fade", "dissolve", "fall_back", "rise_out", "stretch_out"):
            n = 10 if exit_ == "dissolve" else EXIT_F
            keys += [(out_at, 1.0), (min(length, out_at + n), 0.0)]
            ext = exit_
        elif exit_ in FADE_OUT:
            keys += [(out_at, 1.0), (min(length, out_at + FADE_OUT[exit_]), 0.0)]
            ext = exit_
    return keys, ent, ext


# 統合版 V5 の改訂の残り（行全体の動きで近似した入り・出の不透明度のフレーム数）
FADE_IN = {"track": 18, "glitch_in": 8, "warp_in": 10, "gather": 10, "circle": 6, "curve": 8}
FADE_OUT = {"blur_out": 6, "glitch_out": 6, "warp_out": 6, "curve_out": 10, "scatter": 8,
            "fall": 45}  # fmt: skip


def line_motion(sc, entrance, hold, exit_, length, out_at, beats=()):
    """統合版 V5 の改訂: 行全体（物体）の入り・待機・出。文字ごとの弾み（bounce）は場面の中の
    ジオメトリーノードが受け持ち、ここでは行全体の拡大・位置・回転を動かす。不透明度（点滅・
    ネオン・フェード）は line_alpha で場面のストリップに付ける。作っていない動きは使わない。"""
    obs = [o for o in sc.objects if o.type == "MESH"]
    used = []
    _, ent_a, ext_a = line_alpha(entrance, exit_, length, out_at)
    for ob in obs:
        x0, y0 = ob.location.x, ob.location.y
        if entrance == "zoom":  # 拡大して登場: scale 1.4→1.0、10f
            _key(ob, "scale", 1, (1.4, 1.4, 1.4))
            _key(ob, "scale", 11, (1.0, 1.0, 1.0))
        elif entrance == "stretch":  # 伸ばして登場: y 3.0→1.0・x 0.6→1.0、8f
            _key(ob, "scale", 1, (0.6, 3.0, 1.0))
            _key(ob, "scale", 9, (1.0, 1.0, 1.0))
        elif entrance == "slam":  # T17 叩きつける: 行全体 3.0→1.0、4f
            _key(ob, "scale", 1, (3.0, 3.0, 3.0))
            _key(ob, "scale", 5, (1.0, 1.0, 1.0))
        elif entrance == "fade_up":  # せり上がる: y +8px→0、10f
            _key(ob, "location", 1, y0 - 8 * PX, 1)
            _key(ob, "location", 11, y0, 1)
        elif entrance == "shake":  # 震わせる: x ±3px を 2f ごとに 6 回
            for j in range(7):
                _key(ob, "location", 12 + 2 * j, x0 + (3 * PX if j % 2 else -3 * PX) * (j < 6), 0)
        elif entrance == "tilt3d":  # 3D で傾ける: Y 軸 −35°→−20°、24f
            _key(ob, "rotation_euler", 1, math.radians(-35.0), 1)
            _key(ob, "rotation_euler", 25, math.radians(-20.0), 1)
        elif entrance == "track":  # T7 字間が詰まりながら: 横に 1.3→1.0、18f（字間の近似）
            _key(ob, "scale", 1, (1.3, 1.0, 1.0))
            _key(ob, "scale", 19, (1.0, 1.0, 1.0))
        elif entrance == "glitch_in":  # 色ズレ・グリッチ: x ±6px のずれを 8f で 0 に
            for j in range(5):
                _key(
                    ob, "location", 1 + 2 * j, x0 + (6 * PX if j % 2 else -6 * PX) * (4 - j) / 4, 0
                )
        elif entrance == "warp_in":  # 歪ませながら: 縦を 1.2→0.85→1.0 と揺らして 10f
            for f_, sy in ((1, 1.2), (4, 0.85), (7, 1.06), (11, 1.0)):
                _key(ob, "scale", f_, (1.0, sy, 1.0))
        elif entrance == "gather":  # 一文字ずつ集まる: 行全体 0.5→1.0、10f（easeOutExpo の近似）
            _key(ob, "scale", 1, (0.5, 0.5, 0.5))
            _key(ob, "scale", 11, (1.0, 1.0, 1.0))
        elif entrance == "circle":  # 円形: 行全体を Z 90°→0 回す、24f
            z0 = ob.rotation_euler.z
            _key(ob, "rotation_euler", 1, z0 + math.radians(90.0), 2)
            _key(ob, "rotation_euler", 25, z0, 2)
        elif entrance == "curve":  # 曲線に沿う: 左から弧を描いて 20f で定位置へ
            for f_, u in ((1, 0.0), (8, 0.5), (21, 1.0)):
                _key(ob, "location", f_, x0 - 0.4 * W * PX * (1 - u), 0)
                _key(ob, "location", f_, y0 + 0.08 * H * PX * math.sin(math.pi * u), 1)
        elif entrance == "stack":  # 並べて動かす: 同じ行を縦に 3 つ、4f ずつ遅らせて上から落とす
            hh = max(0.3, ob.dimensions.y) * 1.15
            copies = [ob]
            for _j in (1, 2):
                cp = ob.copy()  # 形（データ）は共有する
                cp.animation_data_clear()
                sc.collection.objects.link(cp)
                copies.append(cp)
            # 上（1 つ目の写し）→ 中央（元の物体）→ 下（2 つ目の写し）の順に、4f ずつ遅らせる
            for k, (cp, yj) in enumerate(
                ((copies[1], y0 + hh), (copies[0], y0), (copies[2], y0 - hh))
            ):
                f0 = 1 + 4 * k
                _key(cp, "location", 1, yj + 0.08 * H * PX, 1)
                _key(cp, "location", f0, yj + 0.08 * H * PX, 1)
                _key(cp, "location", f0 + 8, yj, 1)
        elif entrance == "morph":
            pass  # T23: 形の変形は morph_line が受け持つ（行全体は動かさない）
        elif entrance == ent_a:
            pass  # ネオン（不透明度だけ）
        else:
            entrance = "bounce"  # 作っていない入りは 1 文字ずつの弾み
        hold_end = max(12, out_at)
        if hold == "breathe":  # 呼吸: 1.000→1.015→1.000、周期 3.2 秒
            per = round(3.2 * FPS)
            f = 12
            while f < hold_end:
                _key(ob, "scale", f, (1.0, 1.0, 1.0))
                if f + per // 2 < hold_end:
                    _key(ob, "scale", f + per // 2, (1.015, 1.015, 1.015))
                f += per
        elif hold == "drift":  # 漂う: 右へ画面の幅の 1%／秒
            _key(ob, "location", 12, x0, 0)
            _key(ob, "location", hold_end, x0 + 0.01 * W * PX * (hold_end - 12) / FPS, 0)
        elif hold == "pulse" and beats:  # 拍で脈打つ: 拍ごとに 1.00→1.08→1.00、6f
            for b in beats:
                if 12 <= b < hold_end - 6:
                    _key(ob, "scale", b, (1.0, 1.0, 1.0))
                    _key(ob, "scale", b + 3, (1.08, 1.08, 1.08))
                    _key(ob, "scale", b + 6, (1.0, 1.0, 1.0))
        elif hold == "wave":  # T8 波打つ（行全体の上下の近似）: 文字の高さ × 0.08、0.8Hz
            per = round(FPS / 0.8)
            f, k = 12, 0
            while f < hold_end:
                _key(ob, "location", f, y0 + (0.08 * 0.5 if k % 2 else -0.08 * 0.5) * (k > 0), 1)
                f += per // 2
                k += 1
        elif hold == "sway":  # 揺れ: 周期 2 秒・±8px
            per = round(2 * FPS)
            f, k = 12, 0
            while f < hold_end:
                _key(ob, "location", f, x0 + (8 * PX if k % 2 else -8 * PX) * (k > 0), 0)
                f += per // 2
                k += 1
        elif hold not in ("static", "outline_light"):  # T19 は文字の場面のノードが受け持つ
            hold = "static"
        end_ = min(length, out_at + EXIT_F)
        if exit_ == "shrink" and out_at < length:  # 縮んで消える: 1→0.8（文字ごとの出と重ねる）
            _key(ob, "scale", out_at, (1.0, 1.0, 1.0))
            _key(ob, "scale", min(length, out_at + 5), (0.8, 0.8, 0.8))
        elif exit_ == "spin_out" and out_at < length:  # 回って消える: Z +90°・1→0、8f
            z0 = ob.rotation_euler.z
            _key(ob, "rotation_euler", out_at, z0, 2)
            _key(ob, "rotation_euler", end_, z0 + math.radians(90.0), 2)
            _key(ob, "scale", out_at, tuple(ob.scale))
            _key(ob, "scale", end_, (0.01, 0.01, 0.01))
        elif exit_ == "fall_back" and out_at < length:  # 後ろへ倒れる: X 0→−70°、8f
            _key(ob, "rotation_euler", out_at, ob.rotation_euler.x, 0)
            _key(ob, "rotation_euler", end_, math.radians(-70.0), 0)
        elif exit_ == "rise_out" and out_at < length:  # 上へ抜ける: y +8%、8f
            _key(ob, "location", out_at, y0, 1)
            _key(ob, "location", end_, y0 + 0.08 * H * PX, 1)
        elif exit_ == "stretch_out" and out_at < length:  # 伸びて消える: y 1→3・x 1→0.6、8f
            _key(ob, "scale", out_at, (1.0, 1.0, 1.0))
            _key(ob, "scale", end_, (0.6, 3.0, 1.0))
        elif exit_ == "blur_out" and out_at < length:  # 膨らんでぼける: 1→1.1、6f（ぼかしは無し）
            _key(ob, "scale", out_at, (1.0, 1.0, 1.0))
            _key(ob, "scale", min(length, out_at + 6), (1.1, 1.1, 1.1))
        elif exit_ == "glitch_out" and out_at < length:  # 色ズレして消える: x ±6px、6f
            for j in range(4):
                _key(ob, "location", out_at + 2 * j, x0 + (6 * PX if j % 2 else -6 * PX), 0)
        elif exit_ == "warp_out" and out_at < length:  # 歪ませて消える: 縦 1→1.2→0.8、6f
            for f_, sy in ((0, 1.0), (3, 1.2), (6, 0.8)):
                _key(ob, "scale", min(length, out_at + f_), (1.0, sy, 1.0))
        elif exit_ == "curve_out" and out_at < length:  # 曲線に沿って右へ抜ける: 10f
            _key(ob, "location", out_at, x0, 0)
            _key(ob, "location", min(length, out_at + 10), x0 + 0.5 * W * PX, 0)
        elif exit_ == "scatter" and out_at < length:  # T18 飛び散る（行全体の近似）: 1→1.4、8f
            _key(ob, "scale", out_at, (1.0, 1.0, 1.0))
            _key(ob, "scale", min(length, out_at + 8), (1.4, 1.4, 1.4))
        elif exit_ == "fall" and out_at < length:  # T21 物理で落ちる: 重力で 45f で画面の下へ
            g_end = min(length, out_at + 45)
            for j in range(0, g_end - out_at + 1, 5):
                u = j / 45
                _key(ob, "location", out_at + j, y0 - 1.2 * H * PX * u * u, 1)
            _key(ob, "rotation_euler", out_at, ob.rotation_euler.z, 2)
            _key(ob, "rotation_euler", g_end, ob.rotation_euler.z + math.radians(-25.0), 2)
        elif exit_ in (ext_a,) and ext_a is not None:
            pass  # 不透明度だけ（点滅・フェード）
        else:
            exit_ = "shrink_chars"
        used = [entrance, hold, exit_]
    return used


T23_POINTS = 64
T23_FRAMES = 12


def _glyph_outlines(text, size, font_path):
    """文字列の各文字の最も外側の輪郭（最も大きい閉じた線）を、T23_POINTS 点の列で返す。
    文字の区切りは、輪郭の中心の x を字数で等分した帯で決める（和文はほぼ等幅のため）。"""
    from mathutils import Vector
    from mathutils.geometry import interpolate_bezier

    cu = bpy.data.curves.new("t23_src", "FONT")
    cu.body = text
    cu.size = size
    cu.align_x, cu.align_y = "CENTER", "CENTER"
    if font_path:
        cu.font = bpy.data.fonts.load(font_path, check_existing=True)
    elif FONT:
        cu.font = font()
    ob = bpy.data.objects.new("t23_src", cu)
    bpy.context.scene.collection.objects.link(ob)
    dg = bpy.context.evaluated_depsgraph_get()
    ev = ob.evaluated_get(dg)
    tmp = ev.to_curve(dg)
    loops = []
    for sp in tmp.splines:
        pts = []
        bp = sp.bezier_points
        if len(bp) >= 2:
            for i in range(len(bp)):
                a, b = bp[i], bp[(i + 1) % len(bp)]
                seg = interpolate_bezier(a.co, a.handle_right, b.handle_left, b.co, 8)
                pts += [Vector((v.x, v.y)) for v in seg[:-1]]
        else:
            pts = [Vector((q.co.x, q.co.y)) for q in sp.points]
        if len(pts) >= 3:
            loops.append(pts)
    ev.to_curve_clear()
    bpy.data.objects.remove(ob)
    bpy.data.curves.remove(cu)
    chars = [ch for ch in text if not ch.isspace()]
    if not loops or not chars:
        return []
    xs = [p.x for lp in loops for p in lp]
    x0, x1 = min(xs), max(xs)
    bw = max(1e-6, (x1 - x0) / len(chars))

    def area(lp):
        return abs(sum(lp[i].x * lp[i - 1].y - lp[i - 1].x * lp[i].y for i in range(len(lp)))) / 2

    out = []
    for k in range(len(chars)):
        mine = [
            lp for lp in loops if x0 + bw * k <= sum(p.x for p in lp) / len(lp) < x0 + bw * (k + 1)
        ]
        if not mine:
            c = Vector((x0 + bw * (k + 0.5), 0.0))
            out.append([c.copy() for _ in range(T23_POINTS)])
            continue
        out.append(_resample(max(mine, key=area), T23_POINTS))
    return out


def _resample(lp, n):
    """閉じた折れ線を周の長さで等間隔の n 点に（始点は最も上の点）。"""
    top = max(range(len(lp)), key=lambda i: (lp[i].y, -lp[i].x))
    lp = lp[top:] + lp[:top]
    ring = lp + [lp[0]]
    seg = [(ring[i + 1] - ring[i]).length for i in range(len(lp))]
    total = sum(seg) or 1.0
    out, j, acc = [], 0, 0.0
    for k in range(n):
        d = total * k / n
        while j < len(seg) - 1 and acc + seg[j] < d:
            acc += seg[j]
            j += 1
        u = (d - acc) / seg[j] if seg[j] else 0.0
        out.append(ring[j].lerp(ring[j + 1], u))
    return out


def morph_line(sc, prev_text, text, size, y, color, font_path, length):
    """統合版 T23: 前の行と次の行の各文字の輪郭を 64 点にそろえ、12f（easeInOutCubic）で点を
    直線補間で移す。変形の間は輪郭を塗った形（シェイプキー）を見せ、終わったら本来の文字に
    替える（本来の文字の物体は 12f まで隠す）。字数が違うときは何もしない。"""
    a = _glyph_outlines(prev_text, size, font_path)
    b = _glyph_outlines(text, size, font_path)
    if not a or len(a) != len(b):
        return False
    mat = bpy.data.materials.new("t23")
    bs = mat.node_tree.nodes["Principled BSDF"]
    bs.inputs["Base Color"].default_value = (*color, 1)
    bs.inputs["Emission Color"].default_value = (*color, 1)
    bs.inputs["Emission Strength"].default_value = 1.0
    cu = bpy.data.curves.new("t23_shape", "CURVE")
    cu.dimensions = "2D"
    cu.fill_mode = "BOTH"
    for la in a:
        sp = cu.splines.new("POLY")
        sp.points.add(T23_POINTS - 1)
        for q, v in zip(sp.points, la, strict=True):
            q.co = (v.x, v.y, 0.0, 1.0)
        sp.use_cyclic_u = True
    cu.materials.append(mat)
    ob = bpy.data.objects.new("t23_shape", cu)
    sc.collection.objects.link(ob)
    ob.location.y = y
    ref = next((o for o in sc.objects if o.type == "MESH"), None)
    if ref is not None:  # サビの斜め（M3）に合わせる
        ob.rotation_euler = ref.rotation_euler
    ob.shape_key_add(name="Basis")
    key = ob.shape_key_add(name="Next")
    i = 0
    for lb in b:
        for v in lb:
            key.data[i].co = (v.x, v.y, 0.0)
            i += 1
    key.value = 0.0
    key.keyframe_insert("value", frame=1)
    key.value = 1.0
    key.keyframe_insert("value", frame=1 + T23_FRAMES)
    bag = channelbag(ob.data.shape_keys)
    for fc in bag.fcurves if bag is not None else []:
        for kp in fc.keyframe_points:
            kp.interpolation, kp.easing = "CUBIC", "EASE_IN_OUT"
    # 形は変形の間だけ見せ、本来の文字はその後から見せる
    ob.hide_render = False
    ob.keyframe_insert("hide_render", frame=1)
    ob.hide_render = True
    ob.keyframe_insert("hide_render", frame=2 + T23_FRAMES)
    for o in sc.objects:
        if o.type == "MESH":
            o.hide_render = True
            o.keyframe_insert("hide_render", frame=1)
            o.hide_render = False
            o.keyframe_insert("hide_render", frame=2 + T23_FRAMES)
    return True


def center_x(sc, name, frame):
    """文字の物体を、全部の文字が出そろったフレームの形で、左右の中央に寄せる。"""
    ob = sc.objects.get(name)
    if ob is None:
        return
    sc.frame_set(frame)
    dg = sc.view_layers[0].depsgraph
    dg.update()
    ev = ob.evaluated_get(dg)
    xs = [v[0] for v in ev.bound_box]
    if max(xs) - min(xs) > 1e-4:
        dx = (max(xs) + min(xs)) / 2 * ob.scale.x
        ob.location.x -= dx
        sh = sc.objects.get(name + "_shadow")  # 影（M7）も同じだけ寄せる
        if sh is not None:
            sh.location.x -= dx


def make_whoosh(path: Path, sec=0.45, rate=48000):
    """帯域を上下させたノイズを山形の音量で（外部素材なし）。"""
    rnd = random.Random(7)
    n = int(sec * rate)
    frames = bytearray()
    lp = 0.0
    for i in range(n):
        t = i / n
        env = math.sin(math.pi * t) ** 2
        a = 0.04 + 0.5 * math.sin(math.pi * t)
        lp += a * (rnd.uniform(-1, 1) - lp)
        v = int(max(-1, min(1, lp * env * 1.6)) * 32767 * 0.5)
        frames += struct.pack("<hh", v, v)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return path


def line_starts(cues):
    """台本の行ごとの最初の字幕の開始フレーム。"""
    out, seen = [], set()
    for c in cues:
        if c.get("start_frame") is not None and c["segment"] not in seen:
            seen.add(c["segment"])
            out.append(int(c["start_frame"]))
    return out


# ------------------------------------------------------------ 組み立て
def main():
    t0 = time.perf_counter()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.name = "Rich"
    sc.render.resolution_x, sc.render.resolution_y = W, H
    sc.render.resolution_percentage = 100
    sc.render.fps, sc.render.fps_base = FPS_NUM, FPS_DEN
    sc.view_settings.view_transform = "Standard"
    try:
        sc.render.compositor_device = "GPU"
    except Exception:  # noqa: BLE001
        pass
    se = sc.sequence_editor_create()

    cues = A.get("cues") or []
    title, catch = A.get("title") or "", A.get("catch") or ""
    intro_text = catch or title
    INTRO = int(P.get("intro_frames", 0)) if on("intro") and intro_text else 0
    REPORT["intro_frames"] = INTRO
    F0 = INTRO + 1  # 本編の 1 フレーム目（Blender のフレームは 1 始まり）

    base = se.strips.new_movie("base", A["clean_path"], 2, F0, fit_method="FILL")
    se.strips.new_sound("voice", A["clean_path"], 1, F0)
    end = right(base)
    sc.frame_start, sc.frame_end = 1, end - 1
    REPORT["frames"] = end - 1
    k0 = base.transform.scale_x

    marks = []  # 章の境目（全体のフレーム, 名前）
    for f, nm in sorted(A.get("chapters") or []):
        f = F0 + int(f)
        if not marks or f - marks[-1][0] > FPS * 3:
            marks.append((f, nm))
    if not marks or marks[0][0] > F0:
        marks.insert(0, (F0, ""))

    # 1) 冒頭（コールドオープン）
    @part("intro")
    def intro():
        bgt, _gi, go = comp_tree("IntroBG", (("Fader", "NodeSocketFloat"),))
        em = bgt.nodes.new("CompositorNodeEllipseMask")
        em.inputs["Size"].default_value = (0.75, 1.0)
        bl = bgt.nodes.new("CompositorNodeBlur")
        bl.inputs["Size"].default_value = (300 * U, 300 * U)
        m = mix(bgt, "MIX", 1.0, (*[c * 0.3 for c in BASE], 1), (*BASE, 1))
        bgt.links.new(em.outputs[0], bl.inputs["Image"])
        bgt.links.new(bl.outputs[0], m.inputs["Factor"])
        bgt.links.new(m.outputs[2], go.inputs["Image"])
        bg = se.strips.new_effect("intro_bg", "COMPOSITOR", 3, 1, length=INTRO + 8)
        bg.node_group = bgt
        hook, q, rest = intro_text.partition("？")
        hook = hook + ("？" if q else "")
        size = 1.15 if len(hook) <= 12 else max(0.6, 1.15 * 12 / len(hook))
        tsc = kinetic_text_scene(
            "TitleText", hook, size, INK, INTRO + 8, delay=2, dur=14, out_at=INTRO - 16, y=0.55
        )
        ts = se.strips.new_scene("intro_title", tsc, 5, 1)
        set_duration(ts, INTRO + 8)
        ts.blend_type = "ALPHA_OVER"
        use("kinetic_text")
        sub_text = rest.strip() or (title if catch else "")
        if sub_text and INTRO > 40:
            sub = se.strips.new_effect("intro_catch", "TEXT", 6, 28, length=INTRO - 34)
            sub.text = sub_text
            if FONT:
                sub.font = font()
            sub.font_size = 54 * U
            sub.color = (*ACCENT, 1)
            sub.anchor_x = sub.anchor_y = "CENTER"
            sub.alignment_x = "CENTER"
            sub.use_shadow = True
            sub.shadow_color = (0.0, 0.02, 0.08, 0.85)
            sub.shadow_offset = 0.04
            sub.shadow_blur = 0.5  # 統合版 D2
            kp_(sub, "location", 28, (0.5, 0.33))
            kp_(sub, "location", 40, (0.5, 0.37))
            kp_(sub, "blend_alpha", 28, 0.0)
            kp_(sub, "blend_alpha", 38, 1.0)
            kp_(sub, "blend_alpha", INTRO - 14, 1.0)
            kp_(sub, "blend_alpha", INTRO - 6, 0.0)
            use("title_card")
        ln = se.strips.new_effect("intro_line", "COLOR", 7, 18, length=max(8, INTRO - 22))
        ln.color = MAIN
        ln.transform.scale_y = 0.004
        ln.transform.offset_y = -0.05 * H
        kt(ln, "scale_x", 18, 0.0)
        kt(ln, "scale_x", 34, 0.42)
        kt(ln, "scale_x", INTRO - 12, 0.42)
        kt(ln, "scale_x", INTRO - 4, 0.0)
        if on("letterbox"):
            for nm, sign, chn in (("bar_top", 1, 8), ("bar_bot", -1, 9)):
                bar = se.strips.new_effect(nm, "COLOR", chn, 1, length=INTRO + 8)
                bar.color = (0, 0, 0)
                bar.transform.scale_y = 0.5
                kt(bar, "offset_y", 1, sign * H * 0.25)
                kt(bar, "offset_y", 22, sign * H * 0.68)
            use("letterbox")

    if INTRO:
        intro()

    # 2) 章の切り替え: 斜めの色パネルが 3 枚ずれて横切る（＋効果音）
    whoosh = make_whoosh(WORK / "whoosh.wav") if on("sfx") else None
    panel_n = 0
    tr_frames: list[int] = []

    def panel_wipe(center, chn=30):
        nonlocal panel_n
        dur = 16
        k = 1.5 * max(W, H) / min(W, H) if H > W else 1.5
        for j, col in enumerate((BASE, MAIN, INK)):
            s0 = center - dur // 2 + j * 2 - 2
            p = se.strips.new_effect(
                f"panel{panel_n}_{j}", "COLOR", chn + j, max(1, s0), length=dur
            )
            p.color = col
            p.transform.rotation = math.radians(-12)
            p.transform.scale_x = p.transform.scale_y = k
            kt(p, "offset_x", s0, -W * 1.45 * k / 1.5)
            kt(p, "offset_x", s0 + dur, W * 1.45 * k / 1.5)
        if whoosh is not None:
            s = se.strips.new_sound(
                f"whoosh{panel_n}", str(whoosh), 4 + (panel_n % 2), max(1, center - 8)
            )
            s.volume = 0.55
            use("sfx")
        panel_n += 1
        use("transition_shape")

    @part("transitions")
    def transitions():
        cs = ([F0 + 4] if INTRO else []) + [f for f, _ in marks[1:]]
        for f in cs:
            panel_wipe(f)
        return cs

    if on("transitions"):
        tr_frames = transitions() or []

    # 3) 章ラベル（左上）
    @part("chapter_labels")
    def labels():
        limit = int(LIMITS.get("chapter_labels", -1))
        n = 0
        for i, (f, nm) in enumerate(marks):
            f_end = marks[i + 1][0] if i + 1 < len(marks) else end
            dur = f_end - f
            if not nm or dur < FPS * 2 or (limit >= 0 and n >= limit):
                continue
            mx, my = 60 * U, 62 * U
            bar = se.strips.new_effect(f"lab_bar{i}", "COLOR", 20, f + 4, length=dur - 8)
            bar.color = ACCENT
            bar.transform.scale_x = 7.7 * U / W
            bar.transform.offset_x = -W / 2 + mx
            bar.transform.offset_y = H / 2 - my
            kt(bar, "scale_y", f + 4, 0.0)
            kt(bar, "scale_y", f + 12, 54 * U / H)
            t = se.strips.new_effect(f"lab_txt{i}", "TEXT", 21, f + 6, length=dur - 10)
            t.text = nm
            if FONT:
                t.font = font()
            t.font_size = 38 * U
            t.color = (*INK, 1)
            t.anchor_x, t.anchor_y = "LEFT", "CENTER"
            t.alignment_x = "LEFT"
            t.use_shadow = True
            t.shadow_color = (0.0, 0.03, 0.1, 0.8)
            t.shadow_offset = 0.03
            t.shadow_blur = 0.5  # 統合版 D2
            y = 1 - my / H
            x = (mx + 18 * U) / W
            kp_(t, "location", f + 6, (x - 0.02, y))
            kp_(t, "location", f + 18, (x, y))
            kp_(t, "blend_alpha", f + 6, 0.0)
            kp_(t, "blend_alpha", f + 14, 1.0)
            kp_(t, "blend_alpha", f + dur - 14, 1.0)
            kp_(t, "blend_alpha", f + dur - 4, 0.0)
            ul = se.strips.new_effect(f"lab_ul{i}", "COLOR", 22, f + 10, length=dur - 14)
            ul.color = MAIN
            ul.transform.scale_y = 2.7 * U / H
            ul.transform.origin = (0.0, 0.5)
            ul.transform.offset_x = mx + 18 * U
            ul.transform.offset_y = H / 2 - my - 30 * U
            kt(ul, "scale_x", f + 10, 0.0)
            kt(ul, "scale_x", f + 26, 0.16 if W > H else 0.3)
            # 帯と下線も文字と一緒に消す（文字だけ先に消えて、空の帯が残って見えていた）
            for strip, last in ((bar, f + dur - 4), (ul, f + dur - 4)):
                kp_(strip, "blend_alpha", last - 10, 1.0)
                kp_(strip, "blend_alpha", last, 0.0)
            n += 1
        use("chapter_label", n)

    if on("chapter_labels"):
        labels()

    # 4) 章ごとにゆっくり寄る
    @part("ken_burns")
    def push():
        z = float(P.get("ken_burns", 1.03))
        for i, (f, _) in enumerate(marks):
            f_end = marks[i + 1][0] if i + 1 < len(marks) else end
            kt(base, "scale_x", f, k0)
            kt(base, "scale_y", f, k0)
            kt(base, "scale_x", f_end - 1, k0 * z)
            kt(base, "scale_y", f_end - 1, k0 * z)
        use("background_motion", len(marks))

    if on("ken_burns"):
        push()

    # 5) ルック（調整レイヤー＋コンポジタモディファイア）
    @part("look")
    def look():
        adj = se.strips.new_effect("look", "ADJUSTMENT", 25, max(1, F0 - 2), length=end - F0 + 2)
        ng, gi, go = comp_tree("Look")
        N, L = ng.nodes, ng.links
        src = gi.outputs["Image"]
        disp = float(P.get("dispersion", 0.0))
        if disp > 0:
            lens = N.new("CompositorNodeLensdist")
            lens.inputs["Dispersion"].default_value = disp
            L.new(src, lens.inputs["Image"])
            src = lens.outputs[0]
            use("glitch")
        blur = N.new("CompositorNodeBlur")
        blur.inputs["Size"].default_value = (40 * U, 40 * U)
        L.new(src, blur.inputs["Image"])
        diff = mix(ng, "SCREEN", float(P.get("diffusion", 0.08)))
        L.new(src, diff.inputs[6])
        L.new(blur.outputs[0], diff.inputs[7])
        glare = N.new("CompositorNodeGlare")
        glare.inputs["Type"].default_value = "Bloom"
        glare.inputs["Threshold"].default_value = 0.85
        glare.inputs["Strength"].default_value = float(P.get("bloom", 0.3))
        L.new(diff.outputs[2], glare.inputs["Image"])
        em = N.new("CompositorNodeEllipseMask")
        em.inputs["Size"].default_value = (1.35, 1.35)
        vb = N.new("CompositorNodeBlur")
        vb.inputs["Size"].default_value = (260 * U, 260 * U)
        L.new(em.outputs[0], vb.inputs["Image"])
        v = float(P.get("vignette", 0.45))
        inv = mix(ng, "MIX", 1.0, (1 - v, 1 - v, 1 - v * 0.9, 1), (1, 1, 1, 1))
        L.new(vb.outputs[0], inv.inputs["Factor"])
        vig = mix(ng, "MULTIPLY", 1.0)
        L.new(glare.outputs["Image"], vig.inputs[6])
        L.new(inv.outputs[2], vig.inputs[7])
        L.new(vig.outputs[2], go.inputs["Image"])
        adj.modifiers.new("look", "COMPOSITOR").node_group = ng
        use("bloom")

    if on("look"):
        look()

    # 6) 強調: 数値を含む発話の頭でパンチ（＋揺れ・RGB ずらし・薄いフラッシュ）
    emph: list[int] = []

    @part("punch")
    def emphasis():
        limit = int(LIMITS.get("punch", -1))
        hits = []
        unit = r"\d+(?:\.\d+)?\s*(GPa|ギガパスカル|%|％|倍|年|円|万|億|兆|人|件|km|kg)"
        for c in cues:
            if c.get("start_frame") is None or not re.search(unit, c.get("text", "")):
                continue
            f = F0 + int(c["start_frame"]) + 2
            if hits and f - hits[-1] < FPS * 2:
                continue
            if any(abs(f - x) < 20 for x in tr_frames):
                continue
            if limit >= 0 and len(hits) >= limit:
                break
            hits.append(f)
        if not hits:
            return hits
        punch = se.strips.new_effect("punch", "ADJUSTMENT", 26, 1, length=end)
        k = punch.transform.scale_x
        for f in hits:
            for p_, v in ((f - 1, k), (f, k * 1.06), (f + 8, k)):
                kt(punch, "scale_x", p_, v)
                kt(punch, "scale_y", p_, v)
            for prop in ("offset_x", "offset_y"):
                kt(punch, prop, f, 0)
                kt(punch, prop, f + 14, 0)
            if on("glitch"):
                rg = se.strips.new_effect(f"rgb{f}", "ADJUSTMENT", 27, f, length=3)
                ng, gi, go = comp_tree(f"RGB{f}")
                N, L = ng.nodes, ng.links
                sep = N.new("CompositorNodeSeparateColor")
                com = N.new("CompositorNodeCombineColor")
                L.new(gi.outputs["Image"], sep.inputs["Image"])
                for ch, dx in (("Red", 9), ("Green", 0), ("Blue", -9)):
                    if dx:
                        tr = N.new("CompositorNodeTranslate")
                        tr.inputs["X"].default_value = dx * U
                        L.new(sep.outputs[ch], tr.inputs["Image"])
                        L.new(tr.outputs[0], com.inputs[ch])
                    else:
                        L.new(sep.outputs[ch], com.inputs[ch])
                L.new(sep.outputs["Alpha"], com.inputs["Alpha"])
                L.new(com.outputs[0], go.inputs["Image"])
                rg.modifiers.new("rgb", "COMPOSITOR").node_group = ng
                use("glitch")
            if on("flash"):
                fl = se.strips.new_effect(f"flash{f}", "COLOR", 28, f, length=6)
                fl.color = INK
                fl.blend_type = "ADD"
                kp_(fl, "blend_alpha", f, float(P.get("flash_alpha", 0.06)))
                kp_(fl, "blend_alpha", f + 6, 0.0)
                use("flash")
        # 統合版 E5: 揺れは F-Curve の Noise モディファイア（Replace・Scale 2.0・Strength 0.02 の
        # 画面比 = ±0.01・Restrict Frame Range の Blend Out 6f）。打った所の 14f だけ
        bag = channelbag(sc)
        for fc in bag.fcurves if bag is not None else []:
            if "punch" not in fc.data_path or not fc.data_path.endswith(("offset_x", "offset_y")):
                continue
            span = W if fc.data_path.endswith("offset_x") else H
            for j, f in enumerate(hits):
                mod = fc.modifiers.new("NOISE")
                mod.blend_type = "REPLACE"
                mod.scale = 2.0
                mod.strength = 0.02 * span
                mod.phase = 7.0 * j + (0 if fc.data_path.endswith("offset_x") else 3.0)
                mod.use_restricted_range = True
                mod.frame_start, mod.frame_end = f, f + 14
                mod.blend_out = 6.0
        use("shake_punch", len(hits))
        return hits

    if on("punch"):
        emph = emphasis() or []

    # 6.4) 音ハメ（統合版 M1）と、キックの音反応（M2）で画面全体を動かす（MV・リリック）
    # 拍頭（1 小節に 1 回）は 1.018 倍、ピーク・子音は 1.035 倍の脈動。キックは 1.00→1.02 倍。
    # 強調や切り替えの近くの脈動は避ける。主役の文字は動かさない（調整レイヤーで画面全体だけ）
    @part("beat_pulse")
    def beat_pulse():
        busy = list(emph) + list(tr_frames)
        bars = [F0 + int(b) for b in (A.get("bars") or (A.get("beats") or [])[::4])]
        strong = [F0 + int(b) for b in (A.get("peaks") or []) + (A.get("consonants") or [])]
        kick = [float(x) for x in (A.get("kick") or [])]

        def free(b):
            return 1 < b < end - 8 and all(abs(b - x) > 10 for x in busy)

        pulse = {}
        for b in bars:
            if free(b):
                pulse[b] = 1.018
        for b in strong:
            if free(b):
                pulse[b] = 1.035
        if not pulse and not kick:
            return
        vals = [1.0] * (end + 8)
        for i, k in enumerate(kick):
            if 0 <= F0 + i < len(vals):
                vals[F0 + i] = k
        for b, amt in pulse.items():  # 1f 前から立ち上げ、6f で戻す
            for j in range(7):
                f = b + j
                if f < len(vals):
                    vals[f] *= 1 + (amt - 1) * (1 - j / 6)
        bp = se.strips.new_effect("beat_pulse", "ADJUSTMENT", 29, 1, length=end)
        k0 = bp.transform.scale_x
        last = None
        for f in range(1, end):
            v = vals[f]
            if last is None or abs(v - last) > 0.0008 or f in pulse or f + 1 in pulse:
                kt(bp, "scale_x", f, k0 * v)
                kt(bp, "scale_y", f, k0 * v)
                last = v
        use("beat_cut", len(pulse))

    if on("beat_pulse"):
        beat_pulse()

    # 6.5) 歌詞・字幕を動く文字（キネティックタイポグラフィ）で出す（MV・リリック）
    # 字幕を焼き込む代わりに、文節ごとの行を 1 文字ずつ弾ませて出し、終わりで崩して消す。
    # 位置・色を行ごとに変え、画像を置いただけの画面（スライドショー）にしない
    @part("lyrics")
    def lyrics():
        spots = [(0.0, INK), (1.1, INK), (-1.0, ACCENT), (0.5, INK), (-0.4, MAIN)]
        n = 0
        for i, c in enumerate(cues):
            if c.get("start_frame") is None:
                continue
            f0 = F0 + int(c["start_frame"])
            f1 = F0 + int(c.get("end_frame") or (c["start_frame"] + FPS * 2))
            length = max(int(FPS * 0.6), f1 - f0 + 6)
            text = str(c.get("text") or "").strip()
            if not text:
                continue
            rows = [x for x in text.split("\n") if x.strip()]
            longest = max(len(x) for x in rows)
            size = min(1.2, 11.5 / max(1, longest)) * (0.85 if W < H else 1.0)
            y, col = spots[i % len(spots)]
            y *= H / W if W > H else 0.6
            sec = c.get("section") or ""
            if sec in ("verse", "drop"):  # 統合版 M3: A メロは中央（落ちサビの縦書きは未対応）
                y = 0.0
            # 行ごとに別の場面にして縦に並べる（複数行の文字列は中央に揃わないため）
            mo = c.get("motion")
            prev_rows = (
                [x for x in str(cues[i - 1].get("text") or "").split("\n") if x.strip()]
                if i > 0
                else []
            )
            # T23: 変形する行は、本来の文字を変形の後にそのまま見せる（1 文字ずつの入りを重ねない）
            morph_ = bool(mo) and mo[0] == "morph" and len(rows) == 1 and bool(prev_rows)
            for r, row in enumerate(rows):
                ry = y + ((len(rows) - 1) / 2 - r) * size * 1.4
                ksc = kinetic_text_scene(
                    f"Lyric{i}_{r}",
                    row,
                    size,
                    col,
                    length,
                    delay=0 if morph_ else (2 if longest <= 14 else 1),
                    dur=1 if morph_ else 10,
                    out_at=max(8, length - 14),
                    y=ry,
                    font_path=LYRIC_FONT,
                    texture=LYRIC_TEXTURE,
                    outline_light=bool(c.get("motion")) and c["motion"][1] == "outline_light",
                )
                ksc.eevee.taa_render_samples = 1 if DRAFT else 8
                if sec in ("chorus", "chorus_first"):  # 統合版 M3: サビは斜め（−8°）
                    for ob_ in ksc.objects:
                        ob_.rotation_euler.z = math.radians(-8.0)
                center_x(ksc, f"Lyric{i}_{r}", max(2, length - 16))
                alpha_keys = []
                if morph_:  # 統合版 T23
                    if morph_line(ksc, prev_rows[-1], row, size, ry, col,
                                                LYRIC_FONT, length):  # fmt: skip
                        REPORT.setdefault("morphed", 0)
                        REPORT["morphed"] += 1
                if mo:  # 統合版 V5 の改訂
                    out_at_ = max(8, length - 14)
                    beats_ = [F0 + int(b) - f0 + 1 for b in (A.get("beats") or [])
                              if f0 <= F0 + int(b) < f0 + length]  # fmt: skip
                    got = line_motion(ksc, mo[0], mo[1], mo[2], length, out_at_, beats_)
                    alpha_keys = line_alpha(mo[0], mo[2], length, out_at_)[0]
                    for g in got:
                        REPORT.setdefault("motion", {}).setdefault(g, 0)
                        REPORT["motion"][g] += 1
                REPORT.setdefault("sections", {}).setdefault(sec or "none", 0)
                REPORT["sections"][sec or "none"] += 1
                st = se.strips.new_scene(f"lyric{i}_{r}", ksc, 44 + (i % 3) * 2 + r % 2, f0 + r * 3)
                set_duration(st, length - r * 3)
                st.blend_type = "ALPHA_OVER"
                for fr_, a_ in alpha_keys:  # 場面の中のフレーム（1 から）→ 編集のフレーム
                    if fr_ <= length - r * 3:
                        kp_(st, "blend_alpha", f0 + r * 3 + fr_ - 1, a_)
            n += 1
        use("lyric_text", n)
        use("kinetic_text", n)
        return n

    if on("lyrics"):
        lyrics()

    # 7) 終わり: 円が閉じるアイリス
    @part("iris")
    def iris():
        n = 26
        isc = eevee_scene("Iris", n, samples=8)
        ob = bpy.data.objects.new("Iris", bpy.data.meshes.new("Iris"))
        isc.collection.objects.link(ob)
        tr = bpy.data.node_groups.new("Iris", "GeometryNodeTree")
        tr.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
        tr.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
        N, L = tr.nodes, tr.links
        outer = N.new("GeometryNodeCurvePrimitiveCircle")
        outer.inputs["Radius"].default_value = 30
        outer.inputs["Resolution"].default_value = 64
        inner = N.new("GeometryNodeCurvePrimitiveCircle")
        inner.inputs["Resolution"].default_value = 128
        tm = N.new("GeometryNodeInputSceneTime")
        prog = N.new("ShaderNodeMath")
        prog.operation = "DIVIDE"
        prog.use_clamp = True
        prog.inputs[1].default_value = n - 6
        L.new(tm.outputs["Frame"], prog.inputs[0])
        fc = N.new("ShaderNodeFloatCurve")
        c = fc.mapping.curves[0]
        c.points[0].location = (0, 0)
        c.points[1].location = (1, 1)
        c.points.new(0.6, 0.25)
        fc.mapping.update()
        L.new(prog.outputs[0], fc.inputs["Value"])
        rad = N.new("ShaderNodeMapRange")
        rad.inputs["To Min"].default_value = 9.5 * max(1.0, H / W)
        rad.inputs["To Max"].default_value = 0.0
        L.new(fc.outputs[0], rad.inputs["Value"])
        L.new(rad.outputs["Result"], inner.inputs["Radius"])
        join = N.new("GeometryNodeJoinGeometry")
        L.new(outer.outputs["Curve"], join.inputs[0])
        L.new(inner.outputs["Curve"], join.inputs[0])
        fill = N.new("GeometryNodeFillCurve")
        L.new(join.outputs[0], fill.inputs["Curve"])
        mat = bpy.data.materials.new("IrisBlack")
        b = mat.node_tree.nodes["Principled BSDF"]
        b.inputs["Base Color"].default_value = (0, 0, 0, 1)
        b.inputs["Roughness"].default_value = 1.0
        b.inputs["Specular IOR Level"].default_value = 0.0
        setm = N.new("GeometryNodeSetMaterial")
        setm.inputs["Material"].default_value = mat
        L.new(fill.outputs[0], setm.inputs["Geometry"])
        L.new(setm.outputs[0], N.new("NodeGroupOutput").inputs["Geometry"])
        ob.modifiers.new("GN", "NODES").node_group = tr
        s = se.strips.new_scene("iris", isc, 40, end - n)
        set_duration(s, n)
        s.blend_type = "ALPHA_OVER"
        use("transition_shape")

    if on("iris") and end - F0 > FPS * 4:
        iris()

    REPORT["ease"] = {
        "panel": ease(sc, "panel", "EXPO", "EASE_IN_OUT"),
        "lab": ease(sc, "lab_", "EXPO", "EASE_OUT"),
        "intro": ease(sc, "intro_", "EXPO", "EASE_OUT"),
        "bar": ease(sc, "bar_", "EXPO", "EASE_IN_OUT"),
        "punch": ease(sc, "punch", "EXPO", "EASE_OUT"),
        "base": ease(sc, '"base"', "SINE", "EASE_IN_OUT"),
    }
    REPORT["chapters_at"] = marks
    REPORT["emphasis_at"] = emph
    REPORT["strips"] = len(se.strips_all)

    r = sc.render
    r.image_settings.media_type = "VIDEO"
    r.image_settings.file_format = "FFMPEG"
    r.ffmpeg.format = "MPEG4"
    r.ffmpeg.codec = "H264"
    r.ffmpeg.constant_rate_factor = "HIGH"
    r.ffmpeg.audio_codec = "AAC"
    r.ffmpeg.audio_bitrate = 192
    r.ffmpeg.audio_mixrate = 48000
    r.use_sequencer = True
    r.filepath = str(OUT)
    if A.get("plan_only"):  # テスト用: 場面と動きを組むだけで書き出さない
        REPORT["total_sec"] = round(time.perf_counter() - t0, 1)
        return
    t1 = time.perf_counter()
    bpy.ops.render.render(animation=True)
    REPORT["render_sec"] = round(time.perf_counter() - t1, 1)
    REPORT["total_sec"] = round(time.perf_counter() - t0, 1)


try:
    main()
except Exception as e:  # noqa: BLE001
    REPORT["fatal"] = f"{type(e).__name__}: {e}"
    REPORT["fatal_tb"] = traceback.format_exc()[-1500:]
Path(A["result_path"]).write_text(
    json.dumps(REPORT, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
)
if "fatal" in REPORT:
    sys.exit(1)
