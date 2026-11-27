"""Blender 同梱スクリプト：動く背景（3D のモーショングラフィックス。継ぎ目なく繰り返せる）。

単色・静止画の背景は画面が止まって見える。3D の形をゆっくり動かした背景にする。
- iso_blocks: アイソメトリック（正投影・(54.736°, 0, 45°)）の箱の床が波打つ（解説・講義・操作説明）
- floating:   低ポリゴンの形が浮かんで回る（MV・PV・PR）
照明は Workbench の studio（面の向きで明るさが変わるだけ）。ブルーム等の撮影処理は付けない。
動きは周期 frames の整数倍の周期だけにしているので、最後のフレームの次が最初のフレームにつながる。

args: style, width, height, fps, frames, colors{base, base2, main, accent}（[R,G,B,A] リニア）,
      seed, output_dir, result_path
"""

import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bpy  # type: ignore[import-not-found]  # noqa: E402
from _common import load_args, write_result  # noqa: E402


def _scene(w: int, h: int, fps: int, frames: int):  # noqa: ANN202
    scene = bpy.context.scene
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    scene.render.engine = "BLENDER_WORKBENCH"
    shading = scene.display.shading
    shading.light = "STUDIO"
    shading.color_type = "OBJECT"
    for attr, val in (("show_cavity", False), ("show_shadows", False), ("show_xray", False)):
        if hasattr(shading, attr):
            setattr(shading, attr, val)
    if hasattr(scene.display, "render_aa"):
        scene.display.render_aa = "8"
    scene.render.resolution_x, scene.render.resolution_y = w, h
    scene.render.resolution_percentage = 50  # 背景は少しぼかして使うので半分の解像度で十分
    scene.render.fps = fps
    scene.frame_start, scene.frame_end = 1, frames
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.view_settings.view_transform = "Standard"
    return scene


def _cube_mesh():  # noqa: ANN202
    """底面が z=0 の 1x1x1 の箱（z の拡大で上へ伸びる）。"""
    v = [(x, y, z) for z in (0, 1) for y in (-0.5, 0.5) for x in (-0.5, 0.5)]
    f = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    m = bpy.data.meshes.new("cube")
    m.from_pydata(v, [], f)
    m.update()
    return m


def _ico_mesh():  # noqa: ANN202
    """低ポリゴンの正二十面体。"""
    t = (1 + 5**0.5) / 2
    v = [
        (-1, t, 0), (1, t, 0), (-1, -t, 0), (1, -t, 0),
        (0, -1, t), (0, 1, t), (0, -1, -t), (0, 1, -t),
        (t, 0, -1), (t, 0, 1), (-t, 0, -1), (-t, 0, 1),
    ]  # fmt: skip
    f = [
        (0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11),
        (1, 5, 9), (5, 11, 4), (11, 10, 2), (10, 7, 6), (7, 1, 8),
        (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8), (3, 8, 9),
        (4, 9, 5), (2, 4, 11), (6, 2, 10), (8, 6, 7), (9, 8, 1),
    ]  # fmt: skip
    s = 1 / math.hypot(1, t)
    m = bpy.data.meshes.new("ico")
    m.from_pydata([(a * s, b * s, c * s) for a, b, c in v], [], f)
    m.update()
    return m


def _obj(name: str, mesh, color) -> object:  # noqa: ANN001
    o = bpy.data.objects.new(name, mesh)
    o.color = tuple(color)
    bpy.context.scene.collection.objects.link(o)
    return o


def _camera(ortho: bool, scale: float, rot, loc):  # noqa: ANN001, ANN202
    cd = bpy.data.cameras.new("cam")
    cd.type = "ORTHO" if ortho else "PERSP"
    if ortho:
        cd.ortho_scale = scale
    else:
        cd.lens = 50
    cam = bpy.data.objects.new("cam", cd)
    cam.rotation_euler = rot
    cam.location = loc
    bpy.context.scene.collection.objects.link(cam)
    bpy.context.scene.camera = cam
    return cam


def _mix(a, b, u: float):  # noqa: ANN001, ANN202
    return [a[i] + (b[i] - a[i]) * u for i in range(3)] + [1.0]


def iso_blocks(a: dict, frames: int, rng: random.Random):  # noqa: ANN201
    c = a["colors"]
    n = 24  # 正投影の菱形の床で、画面の四隅まで埋まる数
    mesh = _cube_mesh()
    cells = []
    for i in range(n):
        for j in range(n):
            r = rng.random()
            col = c["accent"] if r < 0.025 else c["main"] if r < 0.14 else c["base2"]
            col = _mix(col, c["base"], 0.25 + 0.35 * rng.random()) if r >= 0.025 else col
            o = _obj(f"b{i}_{j}", mesh, col)
            o.location = ((i - (n - 1) / 2) * 1.08, (j - (n - 1) / 2) * 1.08, 0)
            o.scale = (1, 1, 0.3)
            cells.append((o, i, j))
    # アイソメトリック: 正投影で (54.736°, 0, 45°) から見る
    rot = (math.radians(54.736), 0, math.radians(45))
    d = 30
    loc = (
        d * math.sin(rot[0]) * math.sin(rot[2]),
        -d * math.sin(rot[0]) * math.cos(rot[2]),
        d * math.cos(rot[0]),
    )
    _camera(True, 13.5, rot, loc)

    def step(f: int) -> None:
        ph = 2 * math.pi * f / frames
        for o, i, j in cells:
            k = 0.5 + 0.5 * math.sin(ph + 0.42 * (i + j))
            o.scale.z = 0.25 + 1.1 * k * k

    return step


def floating(a: dict, frames: int, rng: random.Random):  # noqa: ANN201
    c = a["colors"]
    ico, cube = _ico_mesh(), _cube_mesh()
    items = []
    for k in range(22):
        col = [c["main"], c["accent"], c["base2"], c["base2"]][k % 4]
        o = _obj(f"f{k}", ico if k % 3 else cube, col)
        s = 0.35 + 1.1 * rng.random() ** 2
        o.scale = (s, s, s)
        base = (rng.uniform(-9, 9), rng.uniform(-5, 5), rng.uniform(-6, 2))
        o.location = base
        spin = (rng.choice((-1, 1)), rng.choice((-1, 1)), rng.choice((-1, 0, 1)))
        items.append((o, base, spin, rng.random() * 2 * math.pi))
    _camera(False, 0, (0, 0, 0), (0, 0, 22))

    def step(f: int) -> None:
        ph = 2 * math.pi * f / frames
        for o, base, spin, p in items:
            o.location = (base[0], base[1] + 0.35 * math.sin(ph + p), base[2])
            o.rotation_euler = (spin[0] * ph + p, spin[1] * ph, spin[2] * ph)

    return step


def liquid(a: dict, frames: int, rng: random.Random):  # noqa: ANN201
    """統合版 M6: メタボールの液体。主色と強調色の 2 つの群れが、くっついたり離れたりして漂う。

    動きは frames で 1 周する（継ぎ目なく繰り返す）。"""
    c = a["colors"]
    groups = []
    for name, col, n in (("Liq", c["main"], 6), ("Acc", c["accent"], 3)):
        mb = bpy.data.metaballs.new(name)
        mb.resolution = 0.18
        mb.render_resolution = 0.12
        mb.threshold = 0.6
        o = bpy.data.objects.new(name, mb)
        o.color = tuple(col)
        bpy.context.scene.collection.objects.link(o)
        els = []
        for _ in range(n):
            e = mb.elements.new()
            e.radius = 1.2 + 1.4 * rng.random()
            base = (rng.uniform(-7, 7), rng.uniform(-3.5, 3.5))
            amp = (rng.uniform(1.0, 2.6), rng.uniform(0.6, 1.6))
            els.append((e, base, amp, rng.random() * 2 * math.pi, rng.choice((1, 2))))
        groups.append(els)
    _camera(True, 18.0, (0, 0, 0), (0, 0, 20))

    def step(f: int) -> None:
        ph = 2 * math.pi * f / frames
        for els in groups:
            for e, base, amp, p, k in els:
                e.co = (
                    base[0] + amp[0] * math.sin(k * ph + p),
                    base[1] + amp[1] * math.cos(ph + 1.3 * p),
                    0.0,
                )

    return step


STYLES = {"floating": floating, "liquid": liquid}


def main() -> None:
    a = load_args()
    w, h, fps, frames = int(a["width"]), int(a["height"]), int(a["fps"]), int(a["frames"])
    scene = _scene(w, h, fps, frames)
    rng = random.Random(int(a.get("seed", 7)))
    step = STYLES.get(a.get("style", ""), iso_blocks)(a, frames, rng)
    out = Path(a["output_dir"])
    out.mkdir(parents=True, exist_ok=True)
    files = []
    for f in range(frames):
        step(f)
        scene.render.filepath = str(out / f"frame_{f + 1:04d}.png")
        bpy.ops.render.render(write_still=True)
        files.append(f"frame_{f + 1:04d}.png")
    write_result(a["result_path"], scene, files, style=a.get("style", "iso_blocks"))


main()
