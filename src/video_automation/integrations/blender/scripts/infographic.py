"""Blender 同梱スクリプト：簡単なインフォグラフィック（横棒グラフ・数値カウントアップ）。

args: kind("bar"|"counter"), title, labels[], values[], unit, decimals, width, height, fps,
      frames（動く部分のフレーム数）, font_path, text_color, accent_colors[[RGBA]...],
      output_dir, result_path
"""

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


def fmt(v: float, decimals: int, unit: str) -> str:
    return f"{v:,.{decimals}f}{unit}"


def fit_text(obj, max_w: float, max_h: float) -> float:  # noqa: ANN001
    bpy.context.view_layer.update()
    d = obj.dimensions
    s = min(max_w / max(d.x, 1e-3), max_h / max(d.y, 1e-3))
    obj.scale = (s,) * 3
    return s


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
    else:
        n = max(1, len(values))
        vmax = max([abs(v) for v in values] + [1e-9])
        area_h = top + vh / 2 * 0.85
        row = area_h / n
        bar_h = row * 0.6
        label_w = vw * 0.25
        x0 = -vw / 2 * 0.9 + label_w
        bar_max = vw * 0.9 - label_w - vw * 0.15
        stagger = max(1, round(end * 0.3 / n))
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
            start, stop = 1 + i * stagger, end
            bar.scale = (1e-3, 1, 1)
            bar.keyframe_insert("scale", frame=start)
            bar.scale = (1, 1, 1)
            bar.keyframe_insert("scale", frame=max(start + 1, stop))
            val = vals[i][0]
            val.scale = (val_s,) * 3
            val.location = (x0 + bar_max * abs(v) / vmax + vw * 0.01, yc, 0)
            counters.append((val, v, start, max(start + 1, stop)))

    def update(sc, *_):  # noqa: ANN001
        f = sc.frame_current
        for obj, target, s0, s1 in counters:
            obj.data.body = fmt(target * ease_out((f - s0) / max(s1 - s0, 1)), dec, unit)

    bpy.app.handlers.frame_change_pre.append(update)
    files = render(scene, Path(a["output_dir"]))
    write_result(a["result_path"], scene, files, font_loaded=font is not None)


main()
