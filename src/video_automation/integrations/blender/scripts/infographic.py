"""Blender 同梱スクリプト：図表（横棒・線・ドーナツ・100点の図・数値のカウントアップ）。

統合版 D3: 棒は1本ずつ3fずらし、各10f easeOutCubic。S10: 線は bevel_factor_end 0→1、
ドーナツは円弧の終わりを 0→割合 × 360°、100点の図は左上から順に色付け（動きは D3 と同じ）。

args: kind("bar"|"line"|"donut"|"dots"|"counter"), title, labels[], values[], unit, decimals,
      width, height, fps,
      frames（動く部分のフレーム数）, font_path, text_color, accent_colors[[RGBA]...],
      output_dir, result_path
"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bpy  # type: ignore[import-not-found]  # noqa: E402
from _common import (  # noqa: E402
    emission,
    load_args,
    load_font,
    rect_mesh,
    render,
    setup_scene,
    text_object,
    visible_size,
    write_result,
)


def ease_out(u: float) -> float:
    u = min(max(u, 0.0), 1.0)
    return 1 - (1 - u) ** 3


STAGGER, GROW = 3, 10  # 統合版 D3: 1 本ずつ 3f ずらし、各 10f


def ease_cubic_out(obj, path: str) -> None:  # noqa: ANN001
    """キーの補間を easeOutCubic に（Blender の CUBIC + EASE_OUT）。"""
    ad = obj.animation_data if hasattr(obj, "animation_data") else None
    if ad is None or ad.action is None:
        return
    for fc in _fcurves(ad.action):
        if fc.data_path == path:
            for k in fc.keyframe_points:
                k.interpolation, k.easing = "CUBIC", "EASE_OUT"


def _fcurves(action):  # noqa: ANN001, ANN202
    """Blender 5 の階層のあるアクション（layers → strips → channelbags）と、以前の fcurves。"""
    if hasattr(action, "fcurves") and len(getattr(action, "fcurves", [])):
        return list(action.fcurves)
    out = []
    for layer in getattr(action, "layers", []):
        for strip in layer.strips:
            for bag in getattr(strip, "channelbags", []):
                out += list(bag.fcurves)
    return out


def ring(name: str, radius: float, width: float, mat):  # noqa: ANN001, ANN201
    """太さ width の円周（2D のカーブ）。始点は真上で時計回り。終わりは bevel_factor_end。"""
    cu = bpy.data.curves.new(name, "CURVE")
    cu.dimensions = "2D"
    cu.fill_mode = "BOTH"
    sp = cu.splines.new("POLY")
    n = 128
    sp.points.add(n)

    for i in range(n + 1):
        t = math.pi / 2 - 2 * math.pi * i / n  # 真上から時計回り
        sp.points[i].co = (radius * math.cos(t), radius * math.sin(t), 0, 1)
    cu.bevel_depth = width / 2
    cu.bevel_mode = "ROUND"
    cu.bevel_resolution = 0
    cu.bevel_factor_mapping_start = cu.bevel_factor_mapping_end = "SPLINE"
    cu.materials.append(mat)
    obj = bpy.data.objects.new(name, cu)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def disc(name: str, radius: float, mat):  # noqa: ANN001, ANN201
    """塗りつぶした円（100 点の図の点・線グラフの点）。"""
    n = 32
    verts = [(0.0, 0.0, 0.0)] + [
        (radius * math.cos(2 * math.pi * i / n), radius * math.sin(2 * math.pi * i / n), 0.0)
        for i in range(n)
    ]
    faces = [(0, 1 + i, 1 + (i + 1) % n) for i in range(n)]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    mesh.materials.append(mat)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def pop(obj, frame: int, length: int = 4) -> None:  # noqa: ANN001
    """frame から length フレームで 0→1 に大きくなって出る。"""
    obj.scale = (1e-3,) * 3
    obj.keyframe_insert("scale", frame=max(1, frame))
    obj.scale = (1, 1, 1)
    obj.keyframe_insert("scale", frame=max(1, frame) + length)
    ease_cubic_out(obj, "scale")


def fmt(v: float, decimals: int, unit: str) -> str:
    return f"{v:,.{decimals}f}{unit}"


def fit_text(obj, max_w: float, max_h: float) -> float:  # noqa: ANN001
    bpy.context.view_layer.update()
    d = obj.dimensions
    s = min(max_w / max(d.x, 1e-3), max_h / max(d.y, 1e-3))
    obj.scale = (s,) * 3
    return s


def line_chart(a, values, labels, vw, vh, top, font, tmat, mats, dec, unit) -> None:  # noqa: ANN001
    """線グラフ（S10）。線を bevel_factor_end 0→1 で描き、点は線が通った時に出す。"""
    n = len(values)
    lo, hi = min(values), max(values)
    span = (hi - lo) or abs(hi) or 1.0
    lo -= span * 0.1
    hi += span * 0.1
    x0, x1 = -vw * 0.4, vw * 0.4
    y0, y1 = -vh / 2 * 0.7, top - vh * 0.08
    pts = [
        (x0 + (x1 - x0) * (i / max(1, n - 1)), y0 + (y1 - y0) * (v - lo) / (hi - lo))
        for i, v in enumerate(values)
    ]
    cu = bpy.data.curves.new("line", "CURVE")
    cu.dimensions = "2D"
    sp = cu.splines.new("POLY")
    sp.points.add(n - 1)
    for i, (x, y) in enumerate(pts):
        sp.points[i].co = (x, y, 0, 1)
    cu.bevel_depth = vh * 0.006
    cu.bevel_factor_mapping_end = "SPLINE"
    cu.materials.append(mats[0])
    line = bpy.data.objects.new("line", cu)
    bpy.context.scene.collection.objects.link(line)
    total = STAGGER * (n - 1) + GROW
    cu.bevel_factor_end = 0.0
    cu.keyframe_insert("bevel_factor_end", frame=1)
    cu.bevel_factor_end = 1.0
    cu.keyframe_insert("bevel_factor_end", frame=1 + total)
    ease_cubic_out(cu, "bevel_factor_end")
    base = rect_mesh("axis", x1 - x0, vh * 0.003, tmat)
    base.location = (x0, y0 - vh * 0.03, 0)
    for i, (x, y) in enumerate(pts):
        f = 1 + round(total * i / max(1, n - 1))
        dot = disc(f"p{i}", vh * 0.013, mats[0])
        dot.location = (x, y, 0.01)
        pop(dot, f)
        if i < len(labels):
            lab = text_object(f"l{i}", labels[i], font, tmat)
            fit_text(lab, (x1 - x0) / max(n, 2) * 0.9, vh * 0.05)
            lab.location = (x, y0 - vh * 0.08, 0)
        if i in (0, n - 1) or values[i] in (min(values), max(values)):
            val = text_object(f"v{i}", fmt(values[i], dec, unit), font, tmat)
            fit_text(val, vw * 0.14, vh * 0.05)
            val.location = (x, y + vh * 0.06, 0)
            sc = val.scale[0]
            pop(val, f)
            for fc in _fcurves(val.animation_data.action):  # 文字の大きさを保って出す
                for k in fc.keyframe_points:
                    k.co[1] *= sc
                    k.handle_left[1] *= sc
                    k.handle_right[1] *= sc


def donut_chart(a, values, labels, vw, vh, top, font, tmat, mats, dec, unit, counters) -> None:  # noqa: ANN001
    """ドーナツ（S10）。各項目の円弧の終わりを 0→割合 × 360° に、1 項目ずつ 3f ずらして 10f。"""
    total = sum(max(0.0, v) for v in values) or 1.0
    r = min(vw, vh) * 0.28
    cy = (top - vh / 2 * 0.85) / 2
    acc = 0.0
    for i, v in enumerate(values):
        frac = max(0.0, v) / total
        seg = ring(f"d{i}", r, r * 0.36, mats[i % len(mats)])

        seg.location = (0, cy, 0)
        seg.rotation_euler = (0, 0, -2 * math.pi * acc)  # 前の項目の終わりから
        cu = seg.data
        start = 1 + i * STAGGER
        cu.bevel_factor_end = 0.0
        cu.keyframe_insert("bevel_factor_end", frame=start)
        cu.bevel_factor_end = frac
        cu.keyframe_insert("bevel_factor_end", frame=start + GROW)
        ease_cubic_out(cu, "bevel_factor_end")
        acc += frac
        if i < len(labels):
            mid = acc - frac / 2
            ang = math.pi / 2 - 2 * math.pi * mid
            lab = text_object(f"l{i}", f"{labels[i]} {round(frac * 100)}%", font, tmat)
            fit_text(lab, vw * 0.2, vh * 0.05)
            lab.location = (math.cos(ang) * r * 1.55, cy + math.sin(ang) * r * 1.4, 0)
    big = max(range(len(values)), key=lambda k: values[k]) if values else 0
    num = text_object(
        "center", fmt(round(max(values[big], 0) / total * 100), 0, "%"), font, mats[big % len(mats)]
    )
    fit_text(num, r * 1.1, r * 0.45)
    num.location = (0, cy, 0)
    counters.append(
        (
            num,
            round(max(values[big], 0) / total * 100),
            1,
            1 + STAGGER * (len(values) - 1) + GROW,
            0,
            "%",
        )
    )


def dots_chart(a, values, labels, vw, vh, top, font, tmat, mats, counters) -> None:  # noqa: ANN001
    """100 点の図（S10）。10×10 の点のうち values[0] 個を、左上から順に色付けする。"""
    k = int(round(min(100, max(0, values[0] if values else 0))))
    size = min(vw * 0.5, (top + vh / 2 * 0.8) * 0.9) / 10
    x0 = -size * 4.5
    y0 = top - vh * 0.06 - size * 0.5
    muted = emission("muted", a.get("muted_color", [0.35, 0.4, 0.48, 1.0]))
    total = STAGGER * 9 + GROW  # 10 行を 1 行ずつ 3f ずらす（D3 と同じ時間の動き）
    for i in range(100):
        r, c = divmod(i, 10)
        x, y = x0 + c * size, y0 - r * size
        base = disc(f"m{i}", size * 0.38, muted)
        base.location = (x, y, 0)
        if i < k:
            on = disc(f"o{i}", size * 0.38, mats[0])
            on.location = (x, y, 0.01)
            pop(on, 1 + round(total * i / max(1, k)))
    if labels:
        lab = text_object("label", labels[0], font, tmat)
        fit_text(lab, vw * 0.8, vh * 0.07)
        lab.location = (0, y0 - 10 * size - vh * 0.02, 0)
    num = text_object("num", f"{k}人", font, mats[0])
    fit_text(num, vw * 0.25, vh * 0.12)
    num.location = (x0 + 10 * size + vw * 0.12, y0 - 4.5 * size, 0)
    counters.append((num, k, 1, 1 + total, 0, "人"))


def main() -> None:
    a = load_args()
    w, h, fps, frames = int(a["width"]), int(a["height"]), int(a["fps"]), int(a["frames"])
    scene = setup_scene(w, h, fps, frames, int(a.get("samples", 8)))
    vw, vh = visible_size(w, h)
    font = load_font(a.get("font_path"))
    tmat = emission("text", a.get("text_color", [1, 1, 1, 1]))
    accents = a.get("accent_colors") or [[0.2, 0.6, 1.0, 1.0]]
    mats = [emission(f"accent{i}", c) for i, c in enumerate(accents)]
    unit, dec = a.get("unit", ""), int(a.get("decimals", 0))
    values = [float(v) for v in a.get("values", [])]
    labels = list(a.get("labels", []))
    end = max(2, frames)
    counters = []  # (text object, target value, start frame, end frame)

    top = vh / 2 * 0.85
    if a.get("title"):
        t = text_object("title", a["title"], font, tmat)
        fit_text(t, vw * 0.8, vh * 0.12)
        t.location = (0, top - vh * 0.06, 0)
        top -= vh * 0.17

    if a.get("kind") == "counter":
        target = values[0] if values else 0.0
        num = text_object("num", fmt(target, dec, unit), font, mats[0])
        s = fit_text(num, vw * 0.7, vh * 0.35)
        num.location = (0, 0, 0)
        counters.append((num, target, 1, end))
        if labels:
            lab = text_object("label", labels[0], font, tmat)
            fit_text(lab, vw * 0.7, vh * 0.1)
            lab.location = (0, -vh * 0.25, 0)
        del s
    elif a.get("kind") == "line":
        line_chart(a, values, labels, vw, vh, top, font, tmat, mats, dec, unit)
    elif a.get("kind") == "donut":
        donut_chart(a, values, labels, vw, vh, top, font, tmat, mats, dec, unit, counters)
    elif a.get("kind") == "dots":
        dots_chart(a, values, labels, vw, vh, top, font, tmat, mats, counters)
    else:
        n = max(1, len(values))
        vmax = max([abs(v) for v in values] + [1e-9])
        area_h = top + vh / 2 * 0.85
        row = area_h / n
        bar_h = row * 0.6
        label_w = vw * 0.25
        x0 = -vw / 2 * 0.9 + label_w
        bar_max = vw * 0.9 - label_w - vw * 0.15
        # ラベル・数値はそれぞれ同じ大きさにそろえる（いちばん長いものに合わせる）
        labs, vals = [], []
        for i, v in enumerate(values):
            lab = text_object(
                f"l{i}", labels[i] if i < len(labels) else "", font, tmat, align="RIGHT"
            )
            labs.append((lab, fit_text(lab, label_w * 0.9, bar_h * 0.62)))
            val = text_object(f"v{i}", fmt(v, dec, unit), font, tmat, align="LEFT")
            vals.append((val, fit_text(val, vw * 0.14, bar_h * 0.55)))
        lab_s = min(sc for _, sc in labs)
        val_s = min(sc for _, sc in vals)
        for i, v in enumerate(values):
            yc = top - row * (i + 0.5)
            lab = labs[i][0]
            lab.scale = (lab_s,) * 3
            lab.location = (x0 - vw * 0.02, yc, 0)
            bar = rect_mesh(f"b{i}", max(bar_max * abs(v) / vmax, 1e-3), bar_h, mats[i % len(mats)])
            bar.location = (x0, yc - bar_h / 2, 0)
            start = 1 + i * STAGGER
            stop = start + GROW
            bar.scale = (1e-3, 1, 1)
            bar.keyframe_insert("scale", frame=start)
            bar.scale = (1, 1, 1)
            bar.keyframe_insert("scale", frame=stop)
            ease_cubic_out(bar, "scale")
            val = vals[i][0]
            val.scale = (val_s,) * 3
            val.location = (x0 + bar_max * abs(v) / vmax + vw * 0.01, yc, 0)
            counters.append((val, v, start, stop))

    def update(sc, *_):  # noqa: ANN001
        f = sc.frame_current
        for c in counters:
            obj, target, s0, s1 = c[:4]
            d, u = (c[4], c[5]) if len(c) > 4 else (dec, unit)
            obj.data.body = fmt(target * ease_out((f - s0) / max(s1 - s0, 1)), d, u)

    bpy.app.handlers.frame_change_pre.append(update)
    files = render(scene, Path(a["output_dir"]))
    write_result(a["result_path"], scene, files, font_loaded=font is not None)


main()
