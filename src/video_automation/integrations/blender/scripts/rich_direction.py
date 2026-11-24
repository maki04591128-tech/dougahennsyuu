"""書き出し済みの映像（字幕なし）に MV/PV 風の画面演出を付けて書き出す（Blender 5.x の VSE）。

RenderService から JSON 引数で呼ばれる同梱スクリプト。字幕は付けない（呼び出し側が最後に焼き込む）。
どの演出を使うか（parts）は、呼び出し側が「演出の強さ」と「用途で許された部品」から決めて渡す。
ここでは渡された部品だけを作る。

引数（JSON）:
  clean_path, out_path, result_path, font, width, height, fps_num, fps_den
  title, catch: 冒頭（コールドオープン）の文字
  chapters: [[開始フレーム(0 始まり), 章の名前], ...]
  cues: [{"segment": 台本の行 id, "text": 字幕, "start_frame": 0 始まり, "end_frame"}, ...]
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
U = min(W, H) / 1080  # 1080p を基準にした大きさの倍率
FPS_NUM, FPS_DEN = int(A.get("fps_num", 30)), int(A.get("fps_den", 1))
FPS = FPS_NUM / FPS_DEN
FONT = A.get("font") or ""
PARTS = {k: bool(v) for k, v in (A.get("parts") or {}).items()}
P = A.get("params") or {}
LIMITS = A.get("limits") or {}
OUT = Path(A["out_path"])
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
    sc.eevee.taa_render_samples = samples
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
def kinetic_text_scene(name, text, size, color, length, delay=2, dur=12, out_at=None, y=0.0):
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
    if FONT:
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

    t_in = math_node(
        "DIVIDE",
        math_node("SUBTRACT", tm.outputs["Frame"], math_node("MULTIPLY", idx.outputs[0], delay)),
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
            math_node("SUBTRACT", tm.outputs["Frame"], math_node("ADD", idx.outputs[0], out_at)),
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
    L.new(sci.outputs[0], rlz.inputs[0])
    L.new(rlz.outputs[0], fill.inputs["Curve"])
    L.new(fill.outputs[0], setm.inputs["Geometry"])
    L.new(setm.outputs[0], out.inputs["Geometry"])
    mat = bpy.data.materials.new(name)
    b = mat.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*color, 1)
    b.inputs["Emission Color"].default_value = (*color, 1)
    b.inputs["Emission Strength"].default_value = 1.0
    setm.inputs["Material"].default_value = mat
    ob.modifiers.new("GN", "NODES").node_group = tr
    ob.location.y = y
    if not aligned:
        ob.location.x = -len(text) * size * 0.5
    return sc


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
        ob.location.x -= (max(xs) + min(xs)) / 2 * ob.scale.x


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
            sub.shadow_blur = 0.25
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
            t.shadow_blur = 0.3
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
        rnd = random.Random(3)
        for f in hits:
            for p_, v in ((f - 1, k), (f, k * 1.06), (f + 8, k)):
                kt(punch, "scale_x", p_, v)
                kt(punch, "scale_y", p_, v)
            for j, ff in enumerate(range(f, f + 13, 2)):
                g = 1 - j / 7
                kt(punch, "offset_x", ff, rnd.uniform(-10, 10) * g * U)
                kt(punch, "offset_y", ff, rnd.uniform(-7, 7) * g * U)
                kt(punch, "rotation", ff, math.radians(rnd.uniform(-0.5, 0.5) * g))
            for prop in ("offset_x", "offset_y", "rotation"):
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
        use("shake_punch", len(hits))
        return hits

    if on("punch"):
        emph = emphasis() or []

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
            # 行ごとに別の場面にして縦に並べる（複数行の文字列は中央に揃わないため）
            for r, row in enumerate(rows):
                ry = y + ((len(rows) - 1) / 2 - r) * size * 1.4
                ksc = kinetic_text_scene(
                    f"Lyric{i}_{r}",
                    row,
                    size,
                    col,
                    length,
                    delay=2 if longest <= 14 else 1,
                    dur=10,
                    out_at=max(8, length - 14),
                    y=ry,
                )
                ksc.eevee.taa_render_samples = 8
                center_x(ksc, f"Lyric{i}_{r}", max(2, length - 16))
                st = se.strips.new_scene(f"lyric{i}_{r}", ksc, 44 + (i % 3) * 2 + r % 2, f0 + r * 3)
                set_duration(st, length - r * 3)
                st.blend_type = "ALPHA_OVER"
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
