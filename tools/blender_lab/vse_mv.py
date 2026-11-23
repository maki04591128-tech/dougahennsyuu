"""Blender 5.x のシーケンサー（動画編集）で短い MV を自動で組み立てる。

音楽のビートを検出し、ビートに合わせたカット・トランジション・フラッシュ・ズームパンチ・
歌詞テキスト・色補正（調整レイヤー＋モディファイア）・コンポジタモディファイア・
3D シーンのストリップ・レターボックスを使う。

blender -b --factory-startup -P vse_mv.py -- <out_dir> <lab_dir> <font_path>
  lab_dir: bgm.wav があるフォルダ。素材は <out_dir>/../va_blender_lab/*.png と
           <out_dir>/../va_demo_out/wbn_explainer_v3.mp4 を使う（無ければ飛ばす）
結果: <out_dir>/mv_demo.mp4、<out_dir>/vse_mv.json（各機能の成否・ビート・時間）
"""

import json
import sys
import time
import traceback
from pathlib import Path

import bpy

ARGS = sys.argv[sys.argv.index("--") + 1 :]
OUT = Path(ARGS[0])
LAB = Path(ARGS[1])
FONT = ARGS[2] if len(ARGS) > 2 else ""
DESK = OUT.parent
OUT.mkdir(parents=True, exist_ok=True)
REPORT: dict = {"features": {}}
FPS = 30
W, H = 1920, 1080


def feature(name):
    """機能ごとに成否を記録するデコレータ（失敗しても MV は作り続ける）。"""

    def deco(fn):
        def run(*a, **k):
            try:
                out = fn(*a, **k)
                REPORT["features"][name] = {"ok": True, "info": out}
                return out
            except Exception as e:  # noqa: BLE001
                REPORT["features"][name] = {
                    "ok": False,
                    "error": f"{type(e).__name__}: {e}",
                    "tb": traceback.format_exc()[-900:],
                }
                return None

        return run

    return deco


# ------------------------------------------------------------ ビート検出
@feature("beat_detection")
def detect_beats(path: Path, fps: int):
    """aud（Blender 同梱の音声ライブラリ）で波形を読み、numpy で拍を推定する。"""
    import aud
    import numpy as np

    snd = aud.Sound(str(path))
    rate = int(snd.specs[0])
    x = snd.data().astype(np.float32)
    mono = x.mean(axis=1) if x.ndim == 2 else x
    hop = 512
    n = len(mono) // hop
    frames = mono[: n * hop].reshape(n, hop)
    energy = np.log1p(100 * np.sqrt((frames**2).mean(axis=1)))
    onset = np.maximum(0.0, np.diff(energy, prepend=energy[0]))
    onset = (onset - onset.mean()) / (onset.std() + 1e-9)
    # テンポ: 自己相関の 70〜180 BPM の山
    ac = np.correlate(onset, onset, mode="full")[n - 1 :]
    sec_per_hop = hop / rate
    lags = np.arange(len(ac))
    bpm = 60.0 / np.maximum(lags * sec_per_hop, 1e-9)
    ok = (bpm >= 70) & (bpm <= 180)
    lag = int(lags[ok][np.argmax(ac[ok])])
    tempo = 60.0 / (lag * sec_per_hop)
    # 位相: 拍の位置の onset の合計が最大になるずれ
    best, phase = -1e9, 0
    for p in range(lag):
        s = onset[p::lag].sum()
        if s > best:
            best, phase = s, p
    beats = [round((p * sec_per_hop) * fps) for p in range(phase, n, lag)]
    # 強いアクセント（上位 15% の onset）
    thr = float(np.quantile(onset, 0.97))
    accents = sorted({round(i * sec_per_hop * fps) for i in np.where(onset > thr)[0]})
    dur = len(mono) / rate
    return {
        "tempo": round(float(tempo), 1),
        "beats": beats,
        "accents": accents[:40],
        "duration_sec": round(dur, 2),
    }


# ------------------------------------------------------------ 補助
def key_tr(strip, prop, frame, value):
    """transform（scale_x / offset_y など）にキー。"""
    setattr(strip.transform, prop, value)
    strip.transform.keyframe_insert(prop, frame=frame)


def set_interp_all(id_block, interpolation="BEZIER", easing="AUTO"):
    from bpy_extras import anim_utils

    ad = id_block.animation_data
    if ad is None or ad.action is None:
        return
    bag = anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot)
    if bag is None:
        return
    for fc in bag.fcurves:
        for kp in fc.keyframe_points:
            kp.interpolation, kp.easing = interpolation, easing


def fade(strip, frames_in=8, frames_out=8, peak=1.0):
    s0, s1 = strip.frame_final_start, strip.frame_final_end
    strip.blend_alpha = 0.0
    strip.keyframe_insert("blend_alpha", frame=s0)
    strip.blend_alpha = peak
    strip.keyframe_insert("blend_alpha", frame=s0 + frames_in)
    if frames_out:
        strip.keyframe_insert("blend_alpha", frame=s1 - frames_out)
        strip.blend_alpha = 0.0
        strip.keyframe_insert("blend_alpha", frame=s1)


# ------------------------------------------------------------ 組み立て
def main():
    t_start = time.perf_counter()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.name = "MV"
    sc.render.resolution_x, sc.render.resolution_y = W, H
    sc.render.resolution_percentage = 100
    sc.render.fps = FPS
    sc.view_settings.view_transform = "Standard"
    se = sc.sequence_editor_create()
    music = LAB / "bgm.wav"
    info = detect_beats(music, FPS) or {
        "tempo": 120,
        "beats": list(range(0, 600, 15)),
        "accents": [],
        "duration_sec": 20,
    }
    REPORT["beats"] = info
    end = int(info["duration_sec"] * FPS)
    sc.frame_start, sc.frame_end = 1, end
    beats = [b for b in info["beats"] if b < end]
    for i, b in enumerate(beats):  # ビートをマーカーに（編集時の目印）
        sc.timeline_markers.new(f"b{i}", frame=max(1, b))

    # 1) 音楽（フェードイン・アウト）
    @feature("sound_strip_volume_fade")
    def add_music():
        s = se.strips.new_sound("music", str(music), 1, 1)
        s.volume = 0.0
        s.keyframe_insert("volume", frame=1)
        s.volume = 1.0
        s.keyframe_insert("volume", frame=15)
        s.keyframe_insert("volume", frame=end - 30)
        s.volume = 0.0
        s.keyframe_insert("volume", frame=end)
        return {"duration": s.frame_final_duration}

    add_music()

    # 2) 素材を集める
    stills = [
        p
        for p in [
            DESK / "va_blender_lab" / n
            for n in (
                "perf_1080_b.png",
                "molecule_f40.png",
                "gn_grid_f45.png",
                "bar3d_f60.png",
                "curve_draw_f35.png",
                "toon_freestyle.png",
                "title_3d_f40.png",
                "molecule_f1.png",
            )
        ]
        if p.is_file()
    ]
    movie = DESK / "va_demo_out" / "wbn_explainer_v3.mp4"
    REPORT["sources"] = {"stills": [p.name for p in stills], "movie": movie.is_file()}

    # 3D シーン（ジオメトリノードの波）をストリップとして使う
    @feature("scene_strip_3d")
    def make_3d_scene():
        s3 = bpy.data.scenes.new("Waves3D")
        s3.render.engine = "BLENDER_EEVEE"
        s3.eevee.taa_render_samples = 16
        s3.render.resolution_x, s3.render.resolution_y = W, H
        s3.view_settings.view_transform = "Standard"
        s3.frame_start, s3.frame_end = 1, 200
        w = bpy.data.worlds.new("W")
        s3.world = w
        w.node_tree.nodes["Background"].inputs["Color"].default_value = (0.01, 0.01, 0.03, 1)
        me = bpy.data.meshes.new("Base")
        ob = bpy.data.objects.new("Waves", me)
        s3.collection.objects.link(ob)
        tree = bpy.data.node_groups.new("Waves", "GeometryNodeTree")
        tree.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
        tree.interface.new_socket(
            name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry"
        )
        N, L = tree.nodes, tree.links
        out = N.new("NodeGroupOutput")
        grid = N.new("GeometryNodeMeshGrid")
        for k_, v_ in (("Size X", 14), ("Size Y", 14), ("Vertices X", 28), ("Vertices Y", 28)):
            grid.inputs[k_].default_value = v_
        cube = N.new("GeometryNodeMeshCube")
        cube.inputs["Size"].default_value = (0.4, 0.4, 1.0)
        inst = N.new("GeometryNodeInstanceOnPoints")
        pos, tm = N.new("GeometryNodeInputPosition"), N.new("GeometryNodeInputSceneTime")
        noise = N.new("ShaderNodeTexNoise")
        noise.noise_dimensions = "4D"
        noise.inputs["Scale"].default_value = 0.3
        mul = N.new("ShaderNodeMath")
        mul.operation = "MULTIPLY"
        mul.inputs[1].default_value = 3.5
        comb = N.new("ShaderNodeCombineXYZ")
        comb.inputs["X"].default_value = 1
        comb.inputs["Y"].default_value = 1
        mat = bpy.data.materials.new("Bars")
        bsdf = mat.node_tree.nodes["Principled BSDF"]
        bsdf.inputs["Base Color"].default_value = (0.9, 0.3, 0.8, 1)
        bsdf.inputs["Emission Color"].default_value = (1.0, 0.25, 0.7, 1)
        bsdf.inputs["Emission Strength"].default_value = 0.6
        setm = N.new("GeometryNodeSetMaterial")
        setm.inputs["Material"].default_value = mat
        L.new(grid.outputs["Mesh"], inst.inputs["Points"])
        L.new(cube.outputs["Mesh"], inst.inputs["Instance"])
        L.new(pos.outputs["Position"], noise.inputs["Vector"])
        L.new(tm.outputs["Seconds"], noise.inputs["W"])
        L.new(noise.outputs["Fac"], mul.inputs[0])
        L.new(mul.outputs["Value"], comb.inputs["Z"])
        L.new(comb.outputs["Vector"], inst.inputs["Scale"])
        L.new(inst.outputs["Instances"], setm.inputs["Geometry"])
        L.new(setm.outputs["Geometry"], out.inputs["Geometry"])
        ob.modifiers.new("Waves", "NODES").node_group = tree
        cd = bpy.data.cameras.new("Cam")
        cam = bpy.data.objects.new("Cam", cd)
        s3.collection.objects.link(cam)
        cam.location = (13, -13, 9)
        tgt = bpy.data.objects.new("Target", None)
        s3.collection.objects.link(tgt)
        c = cam.constraints.new("TRACK_TO")
        c.target = tgt
        c.track_axis, c.up_axis = "TRACK_NEGATIVE_Z", "UP_Y"
        s3.camera = cam
        ld = bpy.data.lights.new("Sun", "SUN")
        ld.energy = 3
        lo = bpy.data.objects.new("Sun", ld)
        lo.rotation_euler = (0.6, 0.2, 0.5)
        s3.collection.objects.link(lo)
        return s3

    scene3d = make_3d_scene()

    # 3) ビートに合わせてカット（2 拍ごと）。チャンネル 2 と 3 を交互に使い、重ねて切り替える
    cut_every = 2
    cuts = beats[::cut_every]
    if not cuts or cuts[0] > 1:
        cuts = [1, *cuts]
    cuts.append(end + 1)
    clips = []
    src_i = 0
    overlap = 6  # クロスフェードの重なり（フレーム）

    @feature("cut_on_beats")
    def build_cuts():
        nonlocal src_i
        made = []
        for i in range(len(cuts) - 1):
            a, b = cuts[i], cuts[i + 1]
            ch = 2 + (i % 2)
            start = max(1, a - (overlap if i else 0))
            length = max(4, b - start)
            kind = i % 4
            st = None
            if kind == 1 and movie.is_file():
                st = se.strips.new_movie(f"mov{i}", str(movie), ch, start, fit_method="FILL")
                # 解説動画の分子の画像の辺り（約 37 秒）から使う。頭を切ると表示開始も
                # 後ろへずれるので、frame_start を戻して置き直す
                off = 37 * FPS + i * 10
                st.frame_offset_start = off
                st.frame_start = start - off
                st.frame_final_duration = length
                # 元の動画の字幕帯（下 約32%）を crop で切り、残りが画面を覆うよう
                # 拡大して中心を合わせる（拡大だけでは字幕の上端が残った）
                src_h = 1080
                try:
                    src_h = st.elements[0].orig_height or 1080
                except Exception:
                    pass
                cut = int(src_h * 0.32)
                st.crop.min_y = cut
                vis = (src_h - cut) / src_h  # 残る高さの割合
                k = 1.04 / vis
                st.transform.scale_x *= k
                st.transform.scale_y *= k
                # 残る部分の中心は元の中心より cut/2 上にある。出力の画素単位で下げる
                st.transform.offset_y = -(cut / 2) * (H / src_h) * k
            elif kind == 3 and scene3d is not None:
                st = se.strips.new_scene(f"scene{i}", scene3d, ch, start)
                st.frame_final_duration = length
            elif stills:
                p = stills[src_i % len(stills)]
                src_i += 1
                st = se.strips.new_image(f"img{i}", str(p), ch, start, fit_method="FILL")
                st.frame_final_duration = length
                # ケンバーンズ（ゆっくり寄る・横に流れる）
                # fit_method で決まった拡大率を基準にする（1.0 で上書きすると小さくなる）
                d = 1 if i % 2 else -1
                k0 = st.transform.scale_x
                key_tr(st, "scale_x", start, k0)
                key_tr(st, "scale_y", start, k0)
                key_tr(st, "offset_x", start, -40 * d)
                key_tr(st, "scale_x", start + length, k0 * 1.12)
                key_tr(st, "scale_y", start + length, k0 * 1.12)
                key_tr(st, "offset_x", start + length, 40 * d)
            if st is None:
                continue
            if i:  # 前のクリップの上にフェードで重ねる（クロスフェード）
                st.blend_type = "ALPHA_OVER"
                st.blend_alpha = 0.0
                st.keyframe_insert("blend_alpha", frame=start)
                st.blend_alpha = 1.0
                st.keyframe_insert("blend_alpha", frame=start + overlap)
            made.append(st)
        return [f"{s.name}:{s.type}@{s.frame_final_start}-{s.frame_final_end}" for s in made]

    built = build_cuts() or []
    clips = [se.strips[n.split(":")[0]] for n in built if n.split(":")[0] in se.strips]

    # 4) ワイプのトランジション（エフェクトストリップ）を 1 か所
    @feature("wipe_transition_effect")
    def wipe():
        pairs = [(a, b) for a, b in zip(clips, clips[1:], strict=False) if a.channel != b.channel]
        a, b = pairs[len(pairs) // 2]
        f0 = b.frame_final_start
        f1 = min(a.frame_final_end, b.frame_final_start + overlap)
        if f1 - f0 < 2:
            return "skip"
        eff = se.strips.new_effect("wipe", "WIPE", 9, f0, length=f1 - f0, input1=a, input2=b)
        eff.transition_type = "CLOCK"
        return {"frames": [f0, f1], "props": [p.identifier for p in eff.bl_rna.properties][:60]}

    wipe()

    # 5) 速度の変化（スピード制御エフェクトでスローモーション）
    @feature("speed_control_effect")
    def speed():
        movs = [s for s in clips if s.type == "MOVIE"]
        if not movs:
            return "no movie"
        m = movs[0]
        eff = se.strips.new_effect(
            "speed", "SPEED", 10, m.frame_final_start, length=m.frame_final_duration, input1=m
        )
        props = {
            p.identifier: getattr(eff, p.identifier, None)
            for p in eff.bl_rna.properties
            if "speed" in p.identifier
        }
        if "speed_control" in props:
            eff.speed_control = "MULTIPLY"
        if "speed_factor" in props:
            eff.speed_factor = 0.5  # 半分の速さ（スローモーション）
        # 設定「後」の値を記録する（以前は設定前の値を記録していた）
        return {k: str(getattr(eff, k)) for k in props}

    speed()

    # 6) 調整レイヤー: 全体の色補正・ビートでズームパンチ
    @feature("adjustment_layer_grade_and_punch")
    def adjustment():
        adj = se.strips.new_effect("grade", "ADJUSTMENT", 20, 1, length=end)
        cb = adj.modifiers.new("teal_orange", "COLOR_BALANCE")
        cbd = cb.color_balance
        cbd.lift = (0.96, 1.0, 1.06)  # 影を青緑寄りに
        cbd.gain = (1.08, 1.0, 0.92)  # 明るい所を暖色に
        cu = adj.modifiers.new("contrast", "CURVES")
        c = cu.curve_mapping.curves[3]  # 合成（C）カーブで S 字
        c.points.new(0.25, 0.2)
        c.points.new(0.75, 0.82)
        cu.curve_mapping.update()
        n = 0
        for b in beats:
            if b + 6 >= end:
                break
            key_tr(adj, "scale_x", b, 1.05)
            key_tr(adj, "scale_y", b, 1.05)
            key_tr(adj, "scale_x", b + 6, 1.0)
            key_tr(adj, "scale_y", b + 6, 1.0)
            n += 1
        set_interp_all(sc, "QUAD", "EASE_OUT")  # 画像のパンも含め、ビート後に素早く戻る
        return {"punches": n, "modifiers": [m.type for m in adj.modifiers]}

    adjustment()

    # 7) コンポジタモディファイア（5.0+）: グロー＋周辺減光
    @feature("compositor_modifier")
    def comp_mod():
        adj = se.strips["grade"]
        tree = bpy.data.node_groups.new("MV_Look", "CompositorNodeTree")
        tree.interface.new_socket(name="Image", in_out="INPUT", socket_type="NodeSocketColor")
        tree.interface.new_socket(name="Image", in_out="OUTPUT", socket_type="NodeSocketColor")
        gi = tree.nodes.new("NodeGroupInput")
        go = tree.nodes.new("NodeGroupOutput")
        glare = tree.nodes.new("CompositorNodeGlare")
        glare.inputs["Type"].default_value = "Bloom"
        glare.inputs["Threshold"].default_value = 0.75
        glare.inputs["Strength"].default_value = 0.6
        tree.links.new(gi.outputs["Image"], glare.inputs["Image"])
        last = glare.outputs["Image"]
        names = sorted(n for n in dir(bpy.types) if n.startswith("CompositorNode"))
        vign = None
        if "CompositorNodeEllipseMask" in names:
            em = tree.nodes.new("CompositorNodeEllipseMask")
            ins = {s.name: s for s in em.inputs}
            if "Size" in ins:
                ins["Size"].default_value = (1.3, 1.3)
            blur = tree.nodes.new("CompositorNodeBlur")
            for s in blur.inputs:
                if s.name == "Size" and hasattr(s, "default_value"):
                    try:
                        s.default_value = (200, 200)
                    except TypeError:
                        s.default_value = 200
            mix = tree.nodes.new("ShaderNodeMix")
            mix.data_type = "RGBA"
            mix.blend_type = "MULTIPLY"
            mix.inputs["Factor"].default_value = 0.6
            tree.links.new(em.outputs[0], blur.inputs["Image"])
            tree.links.new(last, mix.inputs[6])
            tree.links.new(blur.outputs[0], mix.inputs[7])
            last = mix.outputs[2]
            vign = {"ellipse_inputs": list(ins), "blur_inputs": [s.name for s in blur.inputs]}
        tree.links.new(last, go.inputs["Image"])
        m = adj.modifiers.new("look", "COMPOSITOR")
        m.node_group = tree
        return {
            "class": type(m).__name__,
            "props": [p.identifier for p in m.bl_rna.properties][:30],
            "vignette": vign,
            "chroma_nodes": [n for n in names if "Chrom" in n or "Lens" in n],
        }

    comp_mod()

    # 8) 強い拍でフラッシュ（白い色ストリップを加算で重ね、すぐ消す）
    @feature("flash_on_accents")
    def flashes():
        acc = [a for a in info.get("accents", []) if 10 < a < end - 10]
        picked = []
        for a in acc:
            if not picked or a - picked[-1] > FPS * 1.5:
                picked.append(a)
        for i, a in enumerate(picked[:8]):
            fl = se.strips.new_effect(f"flash{i}", "COLOR", 21, a, length=8)
            fl.color = (1, 1, 1)
            fl.blend_type = "ADD"
            fl.blend_alpha = 0.55
            fl.keyframe_insert("blend_alpha", frame=a)
            fl.blend_alpha = 0.0
            fl.keyframe_insert("blend_alpha", frame=a + 8)
        return {"flashes": picked[:8]}

    flashes()

    # 9) レターボックス（映画風の黒帯）
    @feature("letterbox")
    def letterbox():
        out = []
        for name, oy in (("bar_top", H / 2 - H * 0.06), ("bar_bottom", -(H / 2 - H * 0.06))):
            bar = se.strips.new_effect(
                name, "COLOR", 22 if name == "bar_top" else 23, 1, length=end
            )
            bar.color = (0, 0, 0)
            bar.transform.scale_y = 0.12
            bar.transform.offset_y = oy
            out.append(name)
        return out

    letterbox()

    # 10) 文字（タイトル・歌詞）。縁取り・影・箱、フェードと横移動
    font = bpy.data.fonts.load(FONT) if FONT and Path(FONT).is_file() else None

    @feature("text_strips_lyrics")
    def texts():
        lines = [
            ("ワクワクAI", 0, 4, "title"),
            ("光をつなぐ ことばの粒", 4, 8, "lyric"),
            ("ビートに乗せて 走り出す", 8, 12, "lyric"),
            ("ひとつの 想いを", 12, 14, "lyric"),
            ("いま 未来へ", 14, 16, "lyric"),
        ]
        made = []
        for j, (txt, b0, b1, kind) in enumerate(lines):
            if b1 >= len(beats):
                break
            f0, f1 = max(1, beats[b0]), beats[b1]
            t = se.strips.new_effect(f"text{j}", "TEXT", 24 + (j % 2), f0, length=f1 - f0)
            t.text = txt
            if font:
                t.font = font
            t.use_outline = True
            t.outline_color = (0, 0, 0, 1)
            t.outline_width = 0.06
            t.use_shadow = True
            t.shadow_color = (0, 0, 0, 0.7)
            t.shadow_blur = 0.3
            t.color = (1, 1, 1, 1)
            t.anchor_x = t.anchor_y = "CENTER"
            t.alignment_x = "CENTER"
            if kind == "title":
                t.font_size = 150
                t.use_bold = True
                t.location = (0.5, 0.55)
                t.color = (1.0, 0.85, 0.3, 1)
            else:
                t.font_size = 72
                t.location = (0.5, 0.17)
                t.use_box = True
                t.box_color = (0, 0, 0, 0.45)
                t.box_margin = 0.015
                t.box_roundness = 0.4
            fade(t, 6, 6)
            # 少し横から入る
            t.location = (t.location[0] - 0.03, t.location[1])
            t.keyframe_insert("location", frame=f0)
            t.location = (t.location[0] + 0.03, t.location[1])
            t.keyframe_insert("location", frame=f0 + 10)
            made.append(f"{txt}@{f0}-{f1}")
        set_interp_all(sc, "QUAD", "EASE_OUT")
        return made

    texts()

    # 11) 透明素材を SCREEN で重ねる（光の演出）
    @feature("overlay_blend_screen")
    def overlay():
        p = DESK / "va_blender_lab" / "overlay_rgba.png"
        if not p.is_file() or len(beats) < 20:
            return "skip"
        f0, f1 = beats[16], beats[19]
        o = se.strips.new_image("overlay", str(p), 26, f0, fit_method="FIT")
        o.frame_final_duration = f1 - f0
        o.blend_type = "SCREEN"
        fade(o, 6, 6, 0.9)
        return [f0, f1]

    overlay()

    # 12) グローのエフェクトストリップ（光る部分をにじませる）を 1 クリップに
    @feature("glow_effect_strip")
    def glow():
        imgs = [s for s in clips if s.type == "IMAGE"]
        if not imgs:
            return "skip"
        s = imgs[min(2, len(imgs) - 1)]
        g = se.strips.new_effect(
            "glow", "GLOW", 11, s.frame_final_start, length=s.frame_final_duration, input1=s
        )
        g.threshold = 0.85  # 明るい所だけにじませる（低いと画面が白く飛ぶ）
        g.clamp = 1.0
        g.boost_factor = 0.5
        g.blur_radius = 8
        return s.name

    glow()

    # 書き出し（H.264 + AAC）
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
    r.filepath = str(OUT / "mv_demo.mp4")
    bpy.ops.wm.save_as_mainfile(filepath=str(OUT / "mv_demo.blend"))
    REPORT["strips"] = sorted(f"{s.channel}:{s.name}:{s.type}" for s in se.strips_all)
    t0 = time.perf_counter()
    bpy.ops.render.render(animation=True)
    REPORT["render_sec"] = round(time.perf_counter() - t0, 1)
    REPORT["total_sec"] = round(time.perf_counter() - t_start, 1)
    REPORT["output_exists"] = (OUT / "mv_demo.mp4").is_file()


try:
    main()
except Exception as e:  # noqa: BLE001
    REPORT["fatal"] = f"{type(e).__name__}: {e}"
    REPORT["fatal_tb"] = traceback.format_exc()[-1500:]
(OUT / "vse_mv.json").write_text(
    json.dumps(REPORT, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
)
print("vse mv done")
