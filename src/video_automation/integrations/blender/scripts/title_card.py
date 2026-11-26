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
