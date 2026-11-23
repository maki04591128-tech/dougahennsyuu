"""解説動画に MV/PV 風の凝った画面演出を付ける（Blender 5.x の VSE）。

元の映像（字幕なし）に、台本・字幕の時刻から自動で演出を付けて書き出す。字幕は最後に別途焼き込む
（演出で画面が動いても字幕は動かず読みやすいまま）。

  blender -b --factory-startup -P rich_direction.py -- <rich_dir> <plan.txt> <font> [mode]
    rich_dir: clean.mp4（字幕なし）, cues.json（字幕の時刻）, subs.ass があるフォルダ
    plan.txt: 演出プラン（タイトル・ひとこと・カードの章立て）
    mode: video（既定。rich_nosub.mp4 を書き出す）/ stills（確認用に要所のフレームだけ PNG）
出力: rich_nosub.mp4, subs_shifted.ass（冒頭のタイトル分だけずらした字幕）, rich.blend, rich.json

演出の部品（スキル blender-rich-screen-direction の対応表に沿う）
  - 冒頭タイトル: 放射グラデーション背景＋1 文字ずつ弾む題名（Geometry Nodes）＋ひとこと＋黒帯が開く
  - 章の切り替え: 斜めの色パネルが 3 枚ずれて横切る図形トランジション＋シュッという効果音
  - 章ラベル: 左上に小さく。縦の色帯＋文字が滑り込み、下線が伸びる
  - 強調（数値の発話）: ズームパンチ＋減衰する揺れ＋RGB ずらし 3 フレーム＋薄いフラッシュ
  - 説明中: 章ごとにゆっくり寄る（Ken Burns）
  - ルック: ディフュージョン（ぼかしを SCREEN で薄く）＋ブルーム＋周辺減光＋わずかな色収差
  - 終わり: 円が閉じるアイリス
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

import bpy

ARGS = sys.argv[sys.argv.index("--") + 1 :]
RICH = Path(ARGS[0])
PLAN = Path(ARGS[1])
FONT = ARGS[2] if len(ARGS) > 2 else ""
MODE = ARGS[3] if len(ARGS) > 3 else "video"
W, H, FPS = 1920, 1080, 30
INTRO = 96  # 冒頭タイトルの長さ（フレーム）
REPORT: dict = {"parts": {}, "mode": MODE}

# 配色: 背景の紺・主役の水色・強調の金（輝度をそろえ、純白は使わない）
NAVY = (0.035, 0.06, 0.13)
CYAN = (0.23, 0.72, 0.86)
GOLD = (1.0, 0.78, 0.32)
INK = (0.93, 0.95, 0.98)


def part(name):
    def deco(fn):
        def run(*a, **k):
            try:
                out = fn(*a, **k)
                REPORT["parts"][name] = {"ok": True, "info": out}
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


# ------------------------------------------------------------ 入力の読み取り
def read_plan(path: Path):
    """演出プランから、題名・ひとこと・章（開始行と名前）を取り出す。"""
    title, catch, chapters = "", "", []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("タイトル:"):
            title = line.split(":", 1)[1].strip()
        elif line.startswith("ひとこと:"):
            catch = line.split(":", 1)[1].strip()
        m = re.match(r"\[(\d+)(?:〜\d+)?\]\s*(カード|図表):\s*(.+)", line)
        if m:
            name = m.group(3)
            name = name.split("：", 1)[0] if m.group(2) == "カード" else name.split("（", 1)[0]
            chapters.append((int(m.group(1)), name.strip()))
    return title, catch, chapters


def line_starts(cues):
    """台本の行（発話）ごとの開始フレーム。字幕は 1 行が複数に分かれるので最初のものを使う。"""
    starts, seen = [], set()
    for c in sorted(cues, key=lambda c: c["index"]):
        if c["script_segment_id"] not in seen and c["start_frame"] is not None:
            seen.add(c["script_segment_id"])
            starts.append(c["start_frame"])
    return starts


# ------------------------------------------------------------ アニメーションの補助
def channelbag(sc):
    from bpy_extras import anim_utils

    ad = sc.animation_data
    if ad is None or ad.action is None:
        return None
    return anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot)


def ease(sc, name_part, interpolation, easing="AUTO"):
    """名前に name_part を含むストリップのキーの補間をまとめて変える。"""
    bag = channelbag(sc)
    if bag is None:
        return 0
    n = 0
    for fc in bag.fcurves:
        if f'["{name_part}' in fc.data_path or name_part in fc.data_path:
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


# ------------------------------------------------------------ 部品: 1 文字ずつ弾む文字（3D シーン）
def kinetic_text_scene(name, text, size, color, length, delay=2, dur=12, out_at=None, y=0.0):
    """Geometry Nodes で 1 文字ずつ「下から拡大しつつ行き過ぎて止まる」。

    out_at 以降は順に縮んで消える。
    """
    sc = bpy.data.scenes.new(name)
    sc.render.resolution_x, sc.render.resolution_y = W, H
    sc.render.fps = FPS
    sc.frame_start, sc.frame_end = 1, length
    sc.render.engine = "BLENDER_EEVEE"
    sc.eevee.taa_render_samples = 16
    sc.render.film_transparent = True
    sc.view_settings.view_transform = "Standard"
    me = bpy.data.meshes.new(name)
    ob = bpy.data.objects.new(name, me)
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
        s2c.inputs["Font"].default_value = bpy.data.fonts.load(FONT, check_existing=True)
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

    # 入り: t_in = clamp((Frame - Index*delay) / dur)
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
    s_in = curve.outputs[0]
    scale = s_in
    if out_at:
        # 出: 1 - clamp((Frame - out_at - Index*1) / 8)（EXPO 風に二乗）
        t_out = math_node(
            "DIVIDE",
            math_node(
                "SUBTRACT",
                tm.outputs["Frame"],
                math_node("ADD", math_node("MULTIPLY", idx.outputs[0], 1.0), out_at),
            ),
            8,
            True,
        )
        keep = math_node("SUBTRACT", 1.0, math_node("POWER", t_out, 2.0))
        scale = math_node("MULTIPLY", s_in, keep)
    # 下から上がる: (1 - s_in) * -0.6 * size
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
    if not aligned:  # 中央揃えが使えないときは文字数から左へずらす（全角は 1 文字 ≒ size）
        ob.location.x = -len(text) * size * 0.5
    REPORT.setdefault("text_align", {})[name] = aligned
    cd = bpy.data.cameras.new(name)
    cd.type = "ORTHO"
    cd.ortho_scale = 16.0  # 画面の横幅 = 16
    cam = bpy.data.objects.new(name + "_cam", cd)
    cam.location = (0, 0, 10)
    sc.collection.objects.link(cam)
    sc.camera = cam
    return sc


# ------------------------------------------------------------ 効果音（シュッ）を作る
def make_whoosh(path: Path, sec=0.45, rate=48000):
    """帯域を上下させたノイズを山形の音量で。外部素材なし。"""
    rnd = random.Random(7)
    n = int(sec * rate)
    frames = bytearray()
    lp = 0.0
    for i in range(n):
        t = i / n
        env = math.sin(math.pi * t) ** 2
        a = 0.04 + 0.5 * math.sin(math.pi * t)  # 低域通過の係数を動かして「シュッ」
        lp += a * (rnd.uniform(-1, 1) - lp)
        v = int(max(-1, min(1, lp * env * 1.6)) * 32767 * 0.5)
        frames += struct.pack("<hh", v, v)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return path


# ------------------------------------------------------------ 組み立て
def main():
    t0 = time.perf_counter()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.name = "Rich"
    sc.render.resolution_x, sc.render.resolution_y = W, H
    sc.render.resolution_percentage = 100
    sc.render.fps = FPS
    sc.view_settings.view_transform = "Standard"
    try:
        sc.render.compositor_device = "GPU"
    except Exception:  # noqa: BLE001
        pass
    se = sc.sequence_editor_create()

    title, catch, chapters = read_plan(PLAN)
    cues = json.loads((RICH / "cues.json").read_text(encoding="utf-8"))
    starts = line_starts(cues)
    REPORT["plan"] = {"title": title, "catch": catch, "chapters": chapters, "lines": len(starts)}

    # 1) 元の映像と音（冒頭タイトルの分だけ後ろへ）
    clean = str(RICH / "clean.mp4")
    base = se.strips.new_movie("base", clean, 2, INTRO + 1, fit_method="FILL")
    snd = se.strips.new_sound("voice", clean, 1, INTRO + 1)
    end = base.frame_final_end
    sc.frame_start, sc.frame_end = 1, end - 1
    k0 = base.transform.scale_x
    REPORT["length"] = {"frames": end - 1, "base_scale": k0, "sound": snd.frame_final_duration}

    def at(line):  # 台本の行（1 始まり）→ 全体のフレーム
        i = max(0, min(len(starts) - 1, line - 1))
        return INTRO + 1 + starts[i]

    # 章の境目（最初の章は本編の頭から）
    marks = [(INTRO + 1, chapters[0][1] if chapters else "")]
    for ln, nm in chapters[1:]:
        f = at(ln)
        if f - marks[-1][0] > FPS * 3:  # 近すぎる境目はまとめる
            marks.append((f, nm))
    REPORT["chapters_at"] = marks

    # 2) 冒頭タイトル
    @part("intro_title")
    def intro():
        bgt, gi, go = comp_tree("IntroBG", (("Fader", "NodeSocketFloat"),))
        em = bgt.nodes.new("CompositorNodeEllipseMask")
        em.inputs["Size"].default_value = (0.75, 1.0)
        bl = bgt.nodes.new("CompositorNodeBlur")
        bl.inputs["Size"].default_value = (300, 300)
        m = mix(bgt, "MIX", 1.0, (0.004, 0.006, 0.014, 1), (*[c * 1.1 for c in NAVY], 1))
        bgt.links.new(em.outputs[0], bl.inputs["Image"])
        bgt.links.new(bl.outputs[0], m.inputs["Factor"])
        bgt.links.new(m.outputs[2], go.inputs["Image"])
        bg = se.strips.new_effect("intro_bg", "COMPOSITOR", 3, 1, length=INTRO + 8)
        bg.node_group = bgt
        # 題名と、ひとこと
        # 元の動画の冒頭に題名カードがあるので、ここでは「ひとこと」を
        # 問いかけとして大きく出す（コールドオープン）
        hook, _, rest = catch.partition("？")
        hook = hook + ("？" if _ else "")
        tsc = kinetic_text_scene(
            "TitleText", hook, 1.15, INK, INTRO + 8, delay=2, dur=14, out_at=INTRO - 16, y=0.55
        )
        ts = se.strips.new_scene("intro_title", tsc, 5, 1)
        ts.frame_final_duration = INTRO + 8
        ts.blend_type = "ALPHA_OVER"
        sub = se.strips.new_effect("intro_catch", "TEXT", 6, 28, length=INTRO - 34)
        sub.text = rest.strip() or title
        if FONT:
            sub.font = bpy.data.fonts.load(FONT, check_existing=True)
        sub.font_size = 54
        sub.color = (*GOLD, 1)
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
        # 細い線が中央から左右に伸びる（題名とひとことの間）
        ln = se.strips.new_effect("intro_line", "COLOR", 7, 18, length=INTRO - 22)
        ln.color = CYAN
        ln.transform.scale_y = 0.004
        ln.transform.offset_y = -0.05 * H
        kt(ln, "scale_x", 18, 0.0)
        kt(ln, "scale_x", 34, 0.42)
        kt(ln, "scale_x", INTRO - 12, 0.42)
        kt(ln, "scale_x", INTRO - 4, 0.0)
        # 黒帯: 最初は画面の 1/3 ずつ覆い、開いて細い帯で止まる
        for nm, sign, chn in (("bar_top", 1, 8), ("bar_bot", -1, 9)):
            bar = se.strips.new_effect(nm, "COLOR", chn, 1, length=INTRO + 8)
            bar.color = (0, 0, 0)
            bar.transform.scale_y = 0.5
            # 高さ H/2 の帯。中心が ±H/4 で画面を全部覆い、
            # ±(H/2 + H/4 - 0.07H) で上下 7% の細い帯になる
            kt(bar, "offset_y", 1, sign * H * 0.25)
            kt(bar, "offset_y", 22, sign * H * 0.68)
        return {"title": title, "catch": catch}

    intro()

    # 3) 章の切り替え: 斜めの色パネルが 3 枚ずれて横切る（切れ目を隠す）＋効果音
    whoosh = make_whoosh(RICH / "whoosh.wav")
    panel_n = 0

    def panel_wipe(center, chn=30):
        nonlocal panel_n
        cols = (NAVY, CYAN, INK)
        dur = 16
        for j, col in enumerate(cols):
            s0 = center - dur // 2 + j * 2 - 2
            p = se.strips.new_effect(f"panel{panel_n}_{j}", "COLOR", chn + j, s0, length=dur)
            p.color = col
            p.transform.rotation = math.radians(-12)
            p.transform.scale_x = p.transform.scale_y = 1.5
            kt(p, "offset_x", s0, -W * 1.45)
            kt(p, "offset_x", s0 + dur, W * 1.45)
        s = se.strips.new_sound(f"whoosh{panel_n}", str(whoosh), 4 + (panel_n % 2), center - 8)
        s.volume = 0.55
        panel_n += 1

    @part("chapter_transitions")
    def transitions():
        cs = [INTRO + 4] + [f for f, _ in marks[1:]]
        for f in cs:
            panel_wipe(f)
        return cs

    tr_frames = transitions()

    # 4) 章ラベル（左上）＋章ごとのゆっくり寄るカメラ
    @part("chapter_labels")
    def labels():
        made = []
        for i, (f, nm) in enumerate(marks):
            f_end = marks[i + 1][0] if i + 1 < len(marks) else end
            dur = f_end - f
            if not nm or dur < FPS * 2:
                continue
            bar = se.strips.new_effect(f"lab_bar{i}", "COLOR", 20, f + 4, length=dur - 8)
            bar.color = GOLD
            bar.transform.scale_x = 0.004
            bar.transform.scale_y = 0.05
            bar.transform.offset_x = -W / 2 + 60
            bar.transform.offset_y = H / 2 - 62
            kt(bar, "scale_y", f + 4, 0.0)
            kt(bar, "scale_y", f + 12, 0.05)
            t = se.strips.new_effect(f"lab_txt{i}", "TEXT", 21, f + 6, length=dur - 10)
            t.text = nm
            if FONT:
                t.font = bpy.data.fonts.load(FONT, check_existing=True)
            t.font_size = 38
            t.color = (*INK, 1)
            t.anchor_x, t.anchor_y = "LEFT", "CENTER"
            t.alignment_x = "LEFT"
            t.use_shadow = True
            t.shadow_color = (0.0, 0.03, 0.1, 0.8)
            t.shadow_offset = 0.03
            t.shadow_blur = 0.3
            y = 1 - 62 / H
            kp_(t, "location", f + 6, (78 / W - 0.02, y))
            kp_(t, "location", f + 18, (78 / W, y))
            kp_(t, "blend_alpha", f + 6, 0.0)
            kp_(t, "blend_alpha", f + 14, 1.0)
            kp_(t, "blend_alpha", f + dur - 14, 1.0)
            kp_(t, "blend_alpha", f + dur - 4, 0.0)
            ul = se.strips.new_effect(f"lab_ul{i}", "COLOR", 22, f + 10, length=dur - 14)
            ul.color = CYAN
            ul.transform.scale_y = 0.0025
            ul.transform.origin = (0.0, 0.5)
            ul.transform.offset_x = 78  # origin を左端にしたので、左端の位置＝offset_x
            ul.transform.offset_y = H / 2 - 92
            kt(ul, "scale_x", f + 10, 0.0)
            kt(ul, "scale_x", f + 26, 0.16)
            made.append(nm)
        return made

    labels()

    @part("ken_burns_per_chapter")
    def push():
        for i, (f, _) in enumerate(marks):
            f_end = marks[i + 1][0] if i + 1 < len(marks) else end
            kt(base, "scale_x", f, k0)
            kt(base, "scale_y", f, k0)
            kt(base, "scale_x", f_end - 1, k0 * 1.045)
            kt(base, "scale_y", f_end - 1, k0 * 1.045)
        return len(marks)

    push()

    # 5) ルック（調整レイヤー＋コンポジタモディファイア）
    @part("look_diffusion_bloom_vignette_dispersion")
    def look():
        adj = se.strips.new_effect("look", "ADJUSTMENT", 25, INTRO - 2, length=end - INTRO + 2)
        ng, gi, go = comp_tree("Look")
        N, L = ng.nodes, ng.links
        lens = N.new("CompositorNodeLensdist")
        lens.inputs["Dispersion"].default_value = 0.012
        L.new(gi.outputs["Image"], lens.inputs["Image"])
        blur = N.new("CompositorNodeBlur")
        blur.inputs["Size"].default_value = (40, 40)
        L.new(lens.outputs[0], blur.inputs["Image"])
        diff = mix(ng, "SCREEN", 0.1)
        L.new(lens.outputs[0], diff.inputs[6])
        L.new(blur.outputs[0], diff.inputs[7])
        glare = N.new("CompositorNodeGlare")
        glare.inputs["Type"].default_value = "Bloom"
        glare.inputs["Threshold"].default_value = 0.85
        glare.inputs["Strength"].default_value = 0.35
        L.new(diff.outputs[2], glare.inputs["Image"])
        em = N.new("CompositorNodeEllipseMask")
        em.inputs["Size"].default_value = (1.35, 1.35)
        vb = N.new("CompositorNodeBlur")
        vb.inputs["Size"].default_value = (260, 260)
        L.new(em.outputs[0], vb.inputs["Image"])
        inv = mix(ng, "MIX", 1.0, (0.55, 0.55, 0.6, 1), (1, 1, 1, 1))
        L.new(vb.outputs[0], inv.inputs["Factor"])
        vig = mix(ng, "MULTIPLY", 1.0)
        L.new(glare.outputs["Image"], vig.inputs[6])
        L.new(inv.outputs[2], vig.inputs[7])
        L.new(vig.outputs[2], go.inputs["Image"])
        adj.modifiers.new("look", "COMPOSITOR").node_group = ng
        cb = adj.modifiers.new("grade", "COLOR_BALANCE").color_balance
        cb.lift = (0.97, 1.0, 1.04)
        cb.gain = (1.03, 1.0, 0.97)
        return "ok"

    look()

    # 6) 強調: 数値を含む発話の頭でパンチ＋揺れ＋RGB ずらし＋薄いフラッシュ
    punch = se.strips.new_effect("punch", "ADJUSTMENT", 26, 1, length=end)

    @part("emphasis_numbers")
    def emphasis():
        hits = []
        for c in sorted(cues, key=lambda c: c["index"]):
            if c["start_frame"] is None or not re.search(
                r"\d+\s*(GPa|ギガパスカル|%|％|倍|年)", c["text"]
            ):
                continue
            f = INTRO + 1 + c["start_frame"] + 2
            if hits and f - hits[-1] < FPS * 2:
                continue
            if any(abs(f - x) < 20 for x in tr_frames):
                continue
            hits.append(f)
        k = punch.transform.scale_x
        rnd = random.Random(3)
        for f in hits:
            kt(punch, "scale_x", f - 1, k)
            kt(punch, "scale_y", f - 1, k)
            kt(punch, "scale_x", f, k * 1.06)
            kt(punch, "scale_y", f, k * 1.06)
            kt(punch, "scale_x", f + 8, k)
            kt(punch, "scale_y", f + 8, k)
            for j, ff in enumerate(range(f, f + 13, 2)):  # 減衰する揺れ（横・縦・回転）
                g = 1 - j / 7
                kt(punch, "offset_x", ff, rnd.uniform(-10, 10) * g)
                kt(punch, "offset_y", ff, rnd.uniform(-7, 7) * g)
                kt(punch, "rotation", ff, math.radians(rnd.uniform(-0.5, 0.5) * g))
            kt(punch, "offset_x", f + 14, 0)
            kt(punch, "offset_y", f + 14, 0)
            kt(punch, "rotation", f + 14, 0)
            # RGB ずらし（3 フレームだけの調整レイヤー）
            rg = se.strips.new_effect(f"rgb{f}", "ADJUSTMENT", 27, f, length=3)
            ng, gi, go = comp_tree(f"RGB{f}")
            N, L = ng.nodes, ng.links
            sep = N.new("CompositorNodeSeparateColor")
            com = N.new("CompositorNodeCombineColor")
            L.new(gi.outputs["Image"], sep.inputs["Image"])
            for ch, dx in (("Red", 9), ("Green", 0), ("Blue", -9)):
                if dx:
                    tr = N.new("CompositorNodeTranslate")
                    tr.inputs["X"].default_value = dx
                    L.new(sep.outputs[ch], tr.inputs["Image"])
                    L.new(tr.outputs[0], com.inputs[ch])
                else:
                    L.new(sep.outputs[ch], com.inputs[ch])
            L.new(sep.outputs["Alpha"], com.inputs["Alpha"])
            L.new(com.outputs[0], go.inputs["Image"])
            rg.modifiers.new("rgb", "COMPOSITOR").node_group = ng
            fl = se.strips.new_effect(f"flash{f}", "COLOR", 28, f, length=6)
            fl.color = INK
            fl.blend_type = "ADD"
            kp_(fl, "blend_alpha", f, 0.06)  # ADD は暗い背景を大きく持ち上げるので弱く
            kp_(fl, "blend_alpha", f + 6, 0.0)
        return hits

    emph = emphasis()

    # 7) 終わり: 円が閉じるアイリス
    #    （Geometry Nodes で外側の大きな円と内側の円を Fill Curve ＝ 穴の開いた黒い面）
    @part("ending_iris")
    def iris():
        n = 26
        isc = bpy.data.scenes.new("Iris")
        isc.render.resolution_x, isc.render.resolution_y = W, H
        isc.render.fps = FPS
        isc.frame_start, isc.frame_end = 1, n
        isc.render.engine = "BLENDER_EEVEE"
        isc.eevee.taa_render_samples = 8
        isc.render.film_transparent = True
        isc.view_settings.view_transform = "Standard"
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
        fc = N.new("ShaderNodeFloatCurve")  # 最初はゆっくり、最後に素早く閉じる
        c = fc.mapping.curves[0]
        c.points[0].location = (0, 0)
        c.points[1].location = (1, 1)
        c.points.new(0.6, 0.25)
        fc.mapping.update()
        L.new(prog.outputs[0], fc.inputs["Value"])
        rad = N.new("ShaderNodeMapRange")
        rad.inputs["To Min"].default_value = 9.5
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
        cd = bpy.data.cameras.new("IrisCam")
        cd.type = "ORTHO"
        cd.ortho_scale = 16.0
        cam = bpy.data.objects.new("IrisCam", cd)
        cam.location = (0, 0, 10)
        isc.collection.objects.link(cam)
        isc.camera = cam
        s = se.strips.new_scene("iris", isc, 40, end - n)
        s.frame_final_duration = n
        s.blend_type = "ALPHA_OVER"
        return end - n

    iris()

    # 補間: パネルは EXPO の加減速、文字・線は EXPO で止まる、揺れは直線的、寄りは等速に近い SINE
    REPORT["ease"] = {
        "panel": ease(sc, "panel", "EXPO", "EASE_IN_OUT"),
        "lab": ease(sc, "lab_", "EXPO", "EASE_OUT"),
        "intro": ease(sc, "intro_", "EXPO", "EASE_OUT"),
        "bar": ease(sc, "bar_", "EXPO", "EASE_IN_OUT"),
        "punch": ease(sc, "punch", "EXPO", "EASE_OUT"),
        "base": ease(sc, '"base"', "SINE", "EASE_IN_OUT"),
        "iris": ease(sc, "iris", "EXPO", "EASE_IN"),
    }

    # 字幕を冒頭の分だけ後ろへずらす
    @part("shift_subtitles")
    def shift_ass():
        src = RICH / "subs.ass"
        dt = INTRO / FPS

        def sh(m):
            h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
            t = h * 3600 + mi * 60 + s + dt
            return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"

        out_lines = []
        for ln in src.read_text(encoding="utf-8-sig").splitlines():
            if ln.startswith("Dialogue:"):
                head, rest = ln.split(":", 1)
                parts = rest.split(",", 3)
                parts[1] = re.sub(r"(\d+):(\d+):(\d+(?:\.\d+)?)", sh, parts[1])
                parts[2] = re.sub(r"(\d+):(\d+):(\d+(?:\.\d+)?)", sh, parts[2])
                ln = head + ":" + ",".join(parts)
            out_lines.append(ln)
        (RICH / "subs_shifted.ass").write_text("\n".join(out_lines), encoding="utf-8")
        return dt

    shift_ass()

    r = sc.render
    bpy.ops.wm.save_as_mainfile(filepath=str(RICH / "rich.blend"))
    REPORT["strips"] = len(se.strips_all)
    t1 = time.perf_counter()
    if MODE == "stills":
        r.image_settings.file_format = "PNG"
        checks = [10, 40, INTRO + 4, INTRO + 30] + [
            f + d for f in tr_frames[1:4] for d in (-3, 1, 30)
        ]
        checks += [f + d for f in emph[:2] for d in (0, 2, 10)] + [end - 14, end - 3]
        done = []
        for f in sorted(set(c for c in checks if 1 <= c < end)):
            sc.frame_start = sc.frame_end = f
            r.filepath = str(RICH / "stills" / "f_#####")
            bpy.ops.render.render(animation=True)
            done.append(f)
        REPORT["stills"] = done
    else:
        r.image_settings.media_type = "VIDEO"
        r.image_settings.file_format = "FFMPEG"
        r.ffmpeg.format = "MPEG4"
        r.ffmpeg.codec = "H264"
        r.ffmpeg.constant_rate_factor = "HIGH"
        r.ffmpeg.audio_codec = "AAC"
        r.ffmpeg.audio_bitrate = 192
        r.ffmpeg.audio_mixrate = 48000
        r.use_sequencer = True
        r.filepath = str(RICH / "rich_nosub.mp4")
        bpy.ops.render.render(animation=True)
    REPORT["render_sec"] = round(time.perf_counter() - t1, 1)
    REPORT["total_sec"] = round(time.perf_counter() - t0, 1)


try:
    main()
except Exception as e:  # noqa: BLE001
    REPORT["fatal"] = f"{type(e).__name__}: {e}"
    REPORT["fatal_tb"] = traceback.format_exc()[-1500:]
(RICH / "rich.json").write_text(
    json.dumps(REPORT, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
)
print("rich direction done")
