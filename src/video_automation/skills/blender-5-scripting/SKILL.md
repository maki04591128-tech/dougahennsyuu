---
name: "blender-5-scripting"
description: "Blender 5.x（5.0〜5.3）をPythonで自動操作して3D・文字・図表・合成・動画を作るときに使う。5.0の破壊的変更と実機5.3で確認済みのコード例をまとめている。"
---

# Blender 5.x スクリプト操作

Blender を画面操作ではなく Python（bpy）で動かし、静止画・連番・動画を作るための手順と注意点。
コード例はすべて **Blender 5.3.0（Windows, Python 3.13, RTX 5050）で実際に実行して確認済み**（検証スクリプトはリポジトリ dougahennsyuu の `tools/blender_lab/`）。

学習データや古いチュートリアルは Blender 2.8〜4.x の書き方が大半で、5.0 で消えた API を使いがち。
**書く前に下の「5.0 の破壊的変更」を必ず確認する。** 迷ったら推測せず、後述の「API を実機で調べる」で確かめる。

## 実行のしかた

```
blender -b --factory-startup -P script.py -- <引数...>
```
- `-b` 画面なし、`--factory-startup` ユーザー設定・アドオンを読まない（再現性のため必須）
- スクリプト側の引数は `sys.argv[sys.argv.index("--") + 1:]`
- Windows で端末が使えない環境では、bat（＋ps1）をデスクトップに置いて実行し、標準出力をファイルに保存する（`windows-bat-runner` スキル）。ログは最後の数十行だけ残すと読みやすい
- Steam 版の場所の例: `D:\Steam\steamapps\common\Blender\blender.exe`。バージョンは `blender --factory-startup --version`
- 結果は JSON（成否・所要時間・エラーの traceback）と画像で残し、**画像を実際に見て**構図を確認する。エラーが出なくても「文字がはみ出す」「カメラに何も映らない」はよくある

## 5.0 の破壊的変更（4.x のコードが動かない所）

| 項目 | 4.x まで | 5.x |
|---|---|---|
| コンポジタ | `scene.use_nodes = True` → `scene.node_tree` | `scene.node_tree` は削除。`bpy.data.node_groups.new(名前, "CompositorNodeTree")` を `scene.compositing_node_group` に設定。出力は `NodeGroupOutput` ＋ `tree.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")`。有効化は `scene.render.use_compositing` |
| ノードの設定値 | `glare.glare_type = 'BLOOM'` など | 多くが**入力ソケット**に移動。Glare は `inputs["Type"]`（メニュー、値は表示名 `"Bloom"`）、`"Threshold"`、`"Strength"`、`"Size"`、`"Quality"` |
| アニメーション | `action.fcurves` / `action.groups` | 削除（4.4 から「スロット付きアクション」）。`bpy_extras.anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot).fcurves`。新規作成は `action_ensure_channelbag_for_slot`。`obj.keyframe_insert()` はそのまま使える |
| 動画の書き出し | `image_settings.file_format = 'FFMPEG'` | **先に** `image_settings.media_type = "VIDEO"`、それから `file_format = "FFMPEG"`（逆順は enum エラー）。静止画に戻すときは `media_type = "IMAGE"` が先 |
| EEVEE のエンジン名 | 4.2〜4.4 は `BLENDER_EEVEE_NEXT` | `BLENDER_EEVEE`（5.x） |
| シーケンサー | `sequence_editor.sequences` | `sequence_editor.strips` / `strips_all` |
| Grease Pencil | `bpy.data.grease_pencils`（旧） | 4.3 から v3（属性ベース）。注釈は `bpy.data.annotations` に分離 |
| モーションブラー | `scene.eevee.use_motion_blur` | 4.2 から `scene.render.use_motion_blur` / `motion_blur_shutter`（EEVEE・Cycles 共通） |
| String to Curves | `node.font = フォント` | `node.inputs["Font"].default_value = フォント` |
| Python | 3.11 | 5.1 から 3.13 |

既定値の落とし穴（5.3 で確認）
- 色の変換（View Transform）の既定は **AgX**。図・文字・ロゴのように**指定した色をそのまま出したい**ときは `scene.view_settings.view_transform = "Standard"`。写実的な絵は AgX のまま
- `enum_items` で選択肢を調べると、エンジン名や View Transform は動的に決まるため一部しか出ない。値を設定して例外が出ないかで確認する
- 空のシーン（`read_factory_settings(use_empty=True)`）では、ワールド・Freestyle の線のスタイルなどが無い。作ってから使う

## 動作確認済みの共通部品

```python
import bpy, math, sys, time
from pathlib import Path
from bpy_extras import anim_utils
from mathutils import Vector


def new_scene(w=1920, h=1080, samples=32, transparent=False):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.render.engine = "BLENDER_EEVEE"
    sc.render.resolution_x, sc.render.resolution_y = w, h
    sc.render.resolution_percentage = 100
    sc.render.fps = 30
    sc.eevee.taa_render_samples = samples
    sc.view_settings.view_transform = "Standard"  # 図・文字向け。写実なら "AgX"
    sc.render.image_settings.media_type = "IMAGE"
    sc.render.image_settings.file_format = "PNG"
    sc.render.film_transparent = transparent  # 透明背景（重ねる素材用）
    sc.render.image_settings.color_mode = "RGBA" if transparent else "RGB"
    return sc


def link(obj):
    bpy.context.scene.collection.objects.link(obj)
    return obj


def world(color=(0.02, 0.03, 0.06), strength=1.0):
    w = bpy.data.worlds.new("World")
    bpy.context.scene.world = w
    bg = w.node_tree.nodes.get("Background")
    if bg is None:
        bg = w.node_tree.nodes.new("ShaderNodeBackground")
        out = w.node_tree.nodes.new("ShaderNodeOutputWorld")
        w.node_tree.links.new(bg.outputs["Background"], out.inputs["Surface"])
    bg.inputs["Color"].default_value = (*color, 1)
    bg.inputs["Strength"].default_value = strength


def material(name, color, metallic=0.0, rough=0.4, emission=None, strength=0.0):
    m = bpy.data.materials.new(name)
    bsdf = m.node_tree.nodes.get("Principled BSDF")  # 新規マテリアルに最初からある
    bsdf.inputs["Base Color"].default_value = (*color, 1)
    bsdf.inputs["Metallic"].default_value = metallic
    bsdf.inputs["Roughness"].default_value = rough
    if emission is not None:  # 光らせる（グローと組み合わせる）
        bsdf.inputs["Emission Color"].default_value = (*emission, 1)
        bsdf.inputs["Emission Strength"].default_value = strength
    return m


def camera(loc, target=(0, 0, 0), lens=50, dof=None):
    data = bpy.data.cameras.new("Cam")
    data.lens = lens
    cam = link(bpy.data.objects.new("Cam", data))
    cam.location = loc
    tgt = link(bpy.data.objects.new("Target", None))
    tgt.location = target
    c = cam.constraints.new("TRACK_TO")
    c.target = tgt
    c.track_axis, c.up_axis = "TRACK_NEGATIVE_Z", "UP_Y"
    if dof:  # 被写界深度（F値が小さいほどぼける）
        data.dof.use_dof = True
        data.dof.focus_object = tgt
        data.dof.aperture_fstop = dof
    bpy.context.scene.camera = cam
    return cam, tgt


def light(kind="AREA", loc=(4, -4, 6), energy=800, size=4.0, color=(1, 1, 1)):
    d = bpy.data.lights.new(kind, kind)
    d.energy = energy
    d.color = color
    if kind == "AREA":
        d.size = size
    ob = link(bpy.data.objects.new(kind, d))
    ob.location = loc
    ob.rotation_euler = (Vector((0, 0, 0)) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
    return ob


def three_point():  # キー・フィル・輪郭光
    light("AREA", (4, -5, 5), 900, 5)
    light("AREA", (-5, -3, 2), 300, 6, (0.7, 0.8, 1.0))
    light("AREA", (0, 6, 4), 700, 3, (0.6, 0.8, 1.0))


def ease(idb, interpolation="BEZIER", easing="AUTO"):
    """オブジェクトやカーブ等のキーの補間を一括変更（5.x の channelbag 経由）。"""
    ad = idb.animation_data
    bag = anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot)
    for fc in bag.fcurves:
        for kp in fc.keyframe_points:
            kp.interpolation, kp.easing = interpolation, easing
    return bag


def set_input(node, name, value):
    """5.x はノードの設定がソケット。メニューは表示名（例 "Bloom"）で設定できる。"""
    node.inputs[name].default_value = value


def bloom(strength=0.8, threshold=0.9):
    sc = bpy.context.scene
    tree = bpy.data.node_groups.new("Comp", "CompositorNodeTree")
    sc.compositing_node_group = tree
    sc.render.use_compositing = True
    rl = tree.nodes.new("CompositorNodeRLayers")
    g = tree.nodes.new("CompositorNodeGlare")
    out = tree.nodes.new("NodeGroupOutput")
    tree.interface.new_socket(name="Image", in_out="OUTPUT", socket_type="NodeSocketColor")
    tree.links.new(rl.outputs["Image"], g.inputs["Image"])
    tree.links.new(g.outputs["Image"], out.inputs["Image"])
    set_input(g, "Type", "Bloom")
    set_input(g, "Threshold", threshold)
    set_input(g, "Strength", strength)


def text(body, size=1.0, extrude=0.0, bevel=0.0, mat=None, font_path=None, align="CENTER"):
    cu = bpy.data.curves.new("T", "FONT")
    cu.body = body
    if font_path:
        cu.font = bpy.data.fonts.load(font_path)  # 日本語は必ずフォント指定（.ttc 可）
    cu.size, cu.extrude, cu.bevel_depth, cu.bevel_resolution = size, extrude, bevel, 3
    cu.align_x, cu.align_y = align, "CENTER"
    ob = link(bpy.data.objects.new("Text", cu))
    if mat:
        ob.data.materials.append(mat)
    ob.rotation_euler = (math.radians(90), 0, 0)  # 正面（-Y 方向のカメラ）に向ける
    return ob


def fit_width(obj, cam, frac=0.8):
    """obj の幅を、カメラから見た画面幅の frac 倍にする。はみ出し防止に必ず使う。"""
    bpy.context.view_layer.update()
    w = obj.dimensions.x or 1e-6
    dist = (cam.matrix_world.translation - obj.matrix_world.translation).length
    k = 2 * dist * math.tan(cam.data.angle_x / 2) * frac / w
    obj.scale = tuple(v * k for v in obj.scale)
    return k


def render_still(path, frame=1):
    sc = bpy.context.scene
    sc.frame_set(frame)
    sc.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
```

## レシピ（どれも 5.3 で確認済み）

### 立体タイトル（押し出し・面取り・弾む登場・グロー）
```python
sc = new_scene()
sc.frame_end = 40
world((0.01, 0.015, 0.04))
three_point()
cam, _ = camera((0, -9, 0.6), (0, 0, -0.2), 50, dof=2.8)
t = text(
    "ウルツァイト窒化ホウ素",
    0.9,
    0.12,
    0.02,
    material("Title", (0.9, 0.95, 1.0), 0.8, 0.25),
    r"C:\Windows\Fonts\meiryob.ttc",
)
k = fit_width(t, cam, 0.8)
t.scale = (0.01,) * 3
t.keyframe_insert("scale", frame=1)
t.scale = (k, k, k)
t.keyframe_insert("scale", frame=18)
ease(t, "BACK", "EASE_OUT")  # 少し行き過ぎて戻る
bloom(0.8, 0.9)
```
- 補間の種類: `CONSTANT LINEAR BEZIER SINE QUAD CUBIC QUART QUINT EXPO CIRC BACK BOUNCE ELASTIC`、緩急: `AUTO EASE_IN EASE_OUT EASE_IN_OUT`
- 文字が画面からはみ出す失敗が最も多い。カメラを作ってから `fit_width` で合わせる

### ジオメトリノード（格子の箱がノイズで波打つ背景）
```python
tree = bpy.data.node_groups.new("Waves", "GeometryNodeTree")
tree.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
tree.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
N, L = tree.nodes, tree.links
out = N.new("NodeGroupOutput")
grid = N.new("GeometryNodeMeshGrid")  # 入力: "Size X" "Size Y" "Vertices X" "Vertices Y"
cube = N.new("GeometryNodeMeshCube")
cube.inputs["Size"].default_value = (0.32, 0.32, 1.0)
inst = N.new("GeometryNodeInstanceOnPoints")  # "Points" "Instance" "Scale"
pos, t = (
    N.new("GeometryNodeInputPosition"),
    N.new("GeometryNodeInputSceneTime"),
)  # "Seconds" "Frame"
noise = N.new("ShaderNodeTexNoise")
noise.noise_dimensions = "4D"  # W を時間で動かす
mul = N.new("ShaderNodeMath")
mul.operation = "MULTIPLY"
mul.inputs[1].default_value = 3.0
comb = N.new("ShaderNodeCombineXYZ")
comb.inputs["X"].default_value = comb.inputs["Y"].default_value = 1
setm = N.new("GeometryNodeSetMaterial")
setm.inputs["Material"].default_value = material(
    "Bars", (0.2, 0.6, 1), 0.3, 0.3, (0.1, 0.4, 1), 1.5
)
L.new(grid.outputs["Mesh"], inst.inputs["Points"])
L.new(cube.outputs["Mesh"], inst.inputs["Instance"])
L.new(pos.outputs["Position"], noise.inputs["Vector"])
L.new(t.outputs["Seconds"], noise.inputs["W"])
L.new(noise.outputs["Fac"], mul.inputs[0])
L.new(mul.outputs["Value"], comb.inputs["Z"])
L.new(comb.outputs["Vector"], inst.inputs["Scale"])
L.new(inst.outputs["Instances"], setm.inputs["Geometry"])
L.new(setm.outputs["Geometry"], out.inputs["Geometry"])
ob = link(bpy.data.objects.new("Waves", bpy.data.meshes.new("Base")))
ob.modifiers.new("Waves", "NODES").node_group = tree
```
- ノードは RNA 名（`GeometryNode...`、`ShaderNode...`、`FunctionNode...`）で作る。5.3 のジオメトリノードは 286 種
- ソケット名が分からないときは `[s.name for s in node.inputs]` を出力して確かめる

### 文字を 1 文字ずつ出す（ジオメトリノード）
`FunctionNodeInputString`（`.string`）→ `FunctionNodeSliceString`（"Length" にシーン時間の "Frame"÷2）→ `GeometryNodeStringToCurves`（`inputs["Font"]`、"Size"）→ `GeometryNodeRealizeInstances` → `GeometryNodeFillCurve` →（`GeometryNodeExtrudeMesh` "Offset Scale" で厚み、元の面と `GeometryNodeJoinGeometry`）→ `GeometryNodeSetMaterial`。String to Curves は**左端が原点**なので位置で調整する。

### 分子・構造模型（球と棒、リンク複製、ターンテーブル）
- `bpy.ops.mesh.primitive_uv_sphere_add(...)` で原型を作り `ob.copy()` で複製（メッシュ共有で軽い）。色を変える原子だけ `ob.data = ob.data.copy()`
- 棒: `primitive_cylinder_add(depth=1)` を中点に置き `scale.z = 長さ`、`rotation_euler = (q - p).to_track_quat("Z", "Y").to_euler()`
- 滑らかに: `mesh.shade_smooth()`（5.x はメッシュのメソッド）
- 回転: 親の空オブジェクトの `rotation_euler.z` に 1 周分のキー、`ease(root, "LINEAR")`

### 3D 棒グラフ（伸びる棒・数値・回り込むカメラ）
- 立方体の頂点を `co.z += 0.5` して原点を底に。`scale.z` を 0.001→高さでキー、`ease(bar, "EXPO", "EASE_OUT")`、棒ごとに開始を 6 フレームずらす
- 数値・項目名は `text()`。カメラの位置にもキーを打ち `ease(cam, "SINE", "EASE_IN_OUT")`
- 画面に収まらないときは、カメラを遠ざける・`lens` を小さくする（例: 距離 13、40mm）

### 線が伸びて描かれる（矢印・軌跡）
`curves.new(..., "CURVE")`、`splines.new("BEZIER")`、`bezier_points.add(n-1)`、各点 `handle_*_type = "AUTO"`、`bevel_depth` で太さ、`use_fill_caps = True`。`curve.bevel_factor_end` を 0→1 でキー（カーブのデータにキーを打つので `ease(curve_data, ...)`）。発光マテリアル＋グローで光の線になる。

### 手書き風の線（Grease Pencil v3）
```python
gp = bpy.data.grease_pencils.new("GP")
ob = link(bpy.data.objects.new("GP", gp))
mat = bpy.data.materials.new("Ink")
bpy.data.materials.create_gpencil_data(mat)
mat.grease_pencil.color = (0.85, 0.1, 0.1, 1)
gp.materials.append(mat)
dr = gp.layers.new("Lines").frames.new(1).drawing
dr.add_strokes([21, 3])  # 点の数ごとにストロークを作る
for p, co in zip(dr.strokes[0].points, coords):
    p.position = co
    p.radius = 0.04
```
点を追加・削除した後は `dr.strokes[n]` を取り直す（古い参照は無効）。

### トゥーン調＋輪郭線
- マテリアル: Diffuse BSDF →「Shader to RGB」→ カラーランプ（`interpolation = "CONSTANT"`、2〜3 段）→ Emission → 出力（EEVEE のみ）
- 輪郭線: `render.use_freestyle = True`、`line_thickness_mode = "ABSOLUTE"`、`line_thickness = 2`。空のシーンでは `freestyle_settings.linesets[0].linestyle` が None なので `bpy.data.linestyles.new()` を割り当てる
- 5.3 では EEVEE に「マテリアルライティング」ノード（`ShaderNodeLightEvaluation`、`ShaderNodeLightAccumulation`、`ShaderNodeLightFalloff`、`ShaderNodeLightInfo`、`ShaderNodeShadowRaycast` など）が追加され、ライトごとのトゥーン表現ができる（EEVEE 専用、まだ新しいので使う前に実機で試す）

### カメラの揺れ（手持ち風）
キーを 1 つ打ち、その F カーブに `fc.modifiers.new("NOISE")`（`scale` で周期、`strength` で振れ幅）。

### 重ねる素材（透明背景）
`new_scene(transparent=True)` で RGBA の PNG。動画編集側で上に重ねる（テロップ・数値・図）。

### 動画を直接書き出す
```python
s = sc.render.image_settings
s.media_type = "VIDEO"  # 必ず先
s.file_format = "FFMPEG"
sc.render.ffmpeg.format = "MPEG4"
sc.render.ffmpeg.codec = "H264"
sc.render.ffmpeg.constant_rate_factor = "HIGH"  # LOSSLESS / PERC_LOSSLESS / HIGH / MEDIUM / LOW ...
sc.render.filepath = "out.mp4"
bpy.ops.render.render(animation=True)
```
長い書き出しは PNG 連番の方が安全（途中で止まっても続きから書ける）。透過付き動画は PNG 連番か、FFmpeg 側で QuickTime（PNG/ProRes 4444）にする。

### シーケンサー（動画編集）
`se = sc.sequence_editor_create()`、`se.strips.new_movie(名前, パス, チャンネル, 開始)`、`se.strips.new_effect(名前, "TEXT", チャンネル, 開始, length=フレーム数)`、`strip.text` / `strip.font_size`。書き出しは上と同じ。

## 書き出し時間の目安（RTX 5050 8GB、EEVEE）
- 960×540・16 サンプル: 1 フレーム約 0.16〜0.2 秒（最初の 1 枚はシェーダーのコンパイルで 0.7〜3.5 秒）
- 1920×1080・64 サンプル・立体文字＋被写界深度＋グロー: 約 0.52 秒／フレーム → 2 分（3600 フレーム）で約 30 分
- Freestyle は重い（1 枚 4 秒台）。必要な場面だけ使う
- 時間を減らす: サンプル数、解像度、動かない部分は 1 枚だけ描いて動画編集側で伸ばす、透明素材を分けて描く

## API を実機で調べる
推測で書かず、小さな調査スクリプトで確かめる（雛形はリポジトリ dougahennsyuu の `tools/blender_lab/probe.py`）:
- 値の候補: `[i.identifier for i in T.bl_rna.properties["prop"].enum_items]`（動的な enum は一部しか出ない）
- 型・プロパティの有無: `hasattr(scene, "compositing_node_group")`、`sorted(p.identifier for p in bpy.types.SceneEEVEE.bl_rna.properties)`
- ノードの種類: `sorted(n for n in dir(bpy.types) if n.startswith("GeometryNode"))`
- ノードのソケット: `[(s.name, s.type) for s in node.inputs]`（メニューは `s.type == "MENU"`、現在値は表示名）
- 失敗したら traceback 全文を保存し、1 か所ずつ直して再実行する

## 情報源（無料）
- Blender 5.0 Python API の変更: https://developer.blender.org/docs/release_notes/5.0/python_api/
- API リファレンス（版ごと）: https://docs.blender.org/api/5.3/ 、変更履歴 https://docs.blender.org/api/current/change_log.html
- マニュアル: https://docs.blender.org/manual/en/latest/
- スロット付きアクションへの移行: https://developer.blender.org/docs/release_notes/4.4/upgrading/slotted_actions/
- Grease Pencil v3 への移行: https://developer.blender.org/docs/release_notes/4.3/grease_pencil_migration/
- リリースノート: https://developer.blender.org/docs/release_notes/5.3/