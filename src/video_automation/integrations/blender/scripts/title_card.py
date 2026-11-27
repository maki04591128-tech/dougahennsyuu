"""Blender 同梱スクリプト：タイトル文字（テロップ）のアニメーションを PNG 連番で書き出す。

blender --background --factory-startup --python title_card.py -- args.json
args: text, width, height, fps, frames（動く部分のフレーム数。以降は呼び出し側で静止して延長）,
      font_path, text_color[RGBA], outline_color[RGBA] / outline(bool), size(倍率),
      position("center"|"lower_third"|"top"), animation, output_dir, result_path
animation: pop / slide / zoom / typewriter / bounce / fade / none
  fade / none は1フレームだけ描画し、フェードは呼び出し側（FFmpeg）で付ける。
結果の text_box は最終状態の文字の範囲（ピクセル、左上原点）。帯（背景板）の描画に使う。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

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

    if anim in ("typewriter", "bounce") and len(text.replace("\n", "")) > 1 and "\n" not in text:
        # 1文字ずつのオブジェクト。位置は先頭からの部分文字列の幅で決める
        widths = []
        for i in range(1, len(text) + 1):
            o = text_object("m", text[:i], font, mat, align="LEFT")
            bpy.context.view_layer.update()
            widths.append(o.dimensions.x)
            bpy.data.objects.remove(o, do_unlink=True)
        total = widths[-1]
        stagger = max(1, round((end - 1) * 0.7 / max(len(text) - 1, 1)))
        for i, ch in enumerate(text):
            left = widths[i - 1] if i else 0.0
            cx = ((left + widths[i]) / 2 - total / 2) * scale
            o = text_object(f"c{i}", ch, font, mat)
            o.parent = root
            if outline and ch.strip():
                add_outline(o, outline_mat, ow)
            start = 1 + i * stagger
            if anim == "typewriter":
                # その文字の番が来たら1フレームで現れる（補間の種類に依らないよう、
                # 直前のフレームで 0・当該フレームで原寸のキーを打つ）
                if start > 1:
                    key(o, "scale", 1, (0, 0, 0))
                    key(o, "scale", start - 1, (0, 0, 0))
                key(o, "scale", start, (scale,) * 3)
                o.location = (cx, 0, 0)
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
        main_obj = o
        box_w, box_h = dims.x * scale, dims.y * scale
    del main_obj

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
