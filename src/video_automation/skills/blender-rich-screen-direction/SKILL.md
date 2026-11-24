---
name: "blender-rich-screen-direction"
description: "MV・PV・リリック動画のような凝った画面演出を、他の編集・モーショングラフィックスアプリ（After Effects・Premiere・AviUtl・DaVinci）での定番の使い方と対応づけて、Blender 5.x で作るときに使う。解説動画などに演出を付けるときにも使う。"
---

# Blender で凝った画面演出を作る（他アプリの定番技法との対応）

MV・PV・リリック動画の「凝って見える画面」は、特別な効果ではなく **動きの質・文字の設計・合成（重ね方）・場面に合った効果の選び方** でできている。
このスキルは、After Effects（AE）などで定番の使い方を Blender 5.x の機能に置き換え、Python で再現する方法をまとめる。
コードの基本は `blender-5-scripting`（5.x の API）、`blender-vse-mv-editing`（VSE・拍の検出・書き出し）、演出の考え方は `blender-video-motion-design` を併せて読む。

印: ✓ = Blender 5.3.0 実機で動作を確認、△ = 名前・入力の存在だけ確認（見た目は要確認）

---

## 0. 演出を設計する順番（効果の名前から始めない）
1. **意図を 1 文に**: 何を伝えるか・どんな感情にするか・最初に何を見せるか・ピークと余韻はどこか
2. **4 つの軸に分ける**: 形（図形・文字・素材の輪郭）／空間（2D・2.5D・3D、奥行き、余白）／時間（出る・変わる・止まる・消える・ずらす）／情報（何をどの順で理解させるか）
3. **画面の階層を決める**: 主役（最初に見せる。コントラスト・大きさ・動きのどれかが最も強い）→ 補助（主役より後に動く）→ 背景・装飾（読む邪魔をしない）→ 余白。**同時に動く主役は 1 つ**
4. **2D か 3D かを選ぶ**: 図解・文字・UI は 2D（正対カメラ）、奥行き感だけなら 2.5D（板を並べてカメラを動かす）、物の構造・組み立ては 3D、大量の規則的な配置は Geometry Nodes、仕上げの色・光は VSE とコンポジタ
5. **必要な機能だけを選んで実装 → 静止画と動画で確かめる**。スクリプトが通ったことと、演出が成り立ったことは別

動きを設計するときに決める値:
| 値 | 決めること |
|---|---|
| タイミング | 開始・終了・止める長さ（ホールド） |
| スペーシング | フレームごとの移動量（加速・減速） |
| イージング | 動き始めと止まり方（2.1 のハンドル） |
| ずらし | 要素ごとの開始のずれ（文字 1〜3 フレーム、要素 2〜4 フレーム） |
| 予備動作・行き過ぎ・余韻 | 主動作の前の逆向きの小さな動き／目標を越えて戻る／遅れて収まる |
| 階層 | 主役と従う要素の動く順番 |

---

## 1. 他アプリの機能 → Blender の対応表

| 他アプリでの機能・使い方 | Blender での手段 | 印 |
|---|---|---|
| AE コンポジション／Premiere シーケンス | VSE のシーン（チャンネルが上ほど手前） | ✓ |
| AE プリコンポーズ／ネスト | メタストリップ、または別シーンをシーンストリップとして置く | ✓ |
| AE 調整レイヤー | ADJUSTMENT ストリップ（下の全チャンネルにかかる）＋モディファイア | ✓ |
| AE のエフェクトを重ねる（グロー・ブラー・色） | コンポジタモディファイア（ノードグループを丸ごと 1 つの効果として付ける） | ✓ |
| Premiere のトランジション／AviUtl のシーンチェンジ | コンポジタのトランジションストリップ（入力 2 つ＋進み具合 `effect_fader`） | ✓ |
| AE トラックマット（文字の中に映像） | マスクモディファイア（入力にストリップ＝文字・色・メタ・動画を指定） | ✓ |
| AE キーフレーム＋グラフエディター（速度グラフ） | F カーブのキー・補間・ハンドル（Python で数値指定） | ✓ |
| AE テキストアニメーター＋範囲セレクター（1 文字ずつ） | Geometry Nodes の String to Curves → 文字ごとのインスタンスを Index でずらす | ✓ |
| AE シェイプレイヤー＋トリムパス（線が伸びる） | カーブの `bevel_factor_end`、Grease Pencil の Build モディファイア | ✓ / △ |
| AE ウィグル（wiggle）・カメラシェイク | 乱数で作ったキーを変形に打つ（振幅・周波数・回転・端隠しのズーム） | ✓（キーを打つ方法） |
| AE 3D レイヤー＋カメラ（2.5D・パララックス） | 3D シーン（板ポリに画像を貼って奥行きに並べ、カメラを動かす）をシーンストリップで置く | ✓ |
| AE モーションブラー | 3D シーンはレンダーのモーションブラー。2D の素早い動きは方向ブラー（DBlur ノード） | △ |
| Premiere 速度ランプ／AE タイムリマップ | 動画ストリップのリタイミングキー（動画のみ）、SPEED エフェクト | ✓ |
| AE グロー／アニメの撮影処理（ディフュージョン・透過光） | Glare ノード（Bloom・Fog Glow・Streaks など）、ぼかした複製を SCREEN で重ねる | ✓ |
| 色収差（RGB ずらし） | Lens Distortion ノードの `Dispersion`、または R・G・B を分けて別々に平行移動 | △ |
| グリッチ | ノイズで作ったマスクで「ずらした画像」と元を切り替える＋RGB ずらし＋ピクセル化 | △ |
| DaVinci / Nuke のノード合成 | コンポジタ（ノードグループ）。VSE ではモディファイア／トランジションとして使う | ✓ |
| AE エクスプレッション | Python で値を計算してキーを打つ、またはドライバー | ✓ |

---

## 2. 上手な使い方（技法ごと）

### 2.1 動きの質 ― 最優先（ここが「凝って見える」かの大半を決める）
他アプリの定番: 「イージーイーズだけでは足りない。グラフエディターで速度の山を作る」「キーの間隔を詰めると速い動き」「要素ごとに登場をずらす」。
- **到着の仕方を決める**: 速く入って滑らかに止まる（強い EASE_OUT＝EXPO / QUART）、行き過ぎて戻る（BACK）、弾む（ELASTIC / BOUNCE は乱用しない）
- **速度の山を偏らせる**: ベジェのハンドルを自分で置く。始点のハンドルを長く＝ゆっくり出る、終点のハンドルを長く＝ゆっくり止まる
- **ずらし（ストッガー）**: 同時に出す要素は 2〜4 フレームずつ遅らせる。文字は 1 文字 1〜3 フレーム
- **時間の目安（30fps）**: 登場 8〜15 フレーム、退場は登場より短く 5〜10、強調のパンチ 4〜8、カメラ移動は 1 秒以上
- **止める時間を作る**: 動き続ける画面は読めない。登場の後は 1 秒以上ほぼ静止させる（わずかに寄る程度は可）

ハンドルを数値で指定する（✓）:
```python
def key_bezier(fc, f0, v0, f1, v1, out_infl=0.85, in_infl=0.15):
    """AE の「速度の影響」に相当。out_infl 大＝出だしゆっくり、in_infl 小＝止まりは急。"""
    a = fc.keyframe_points.insert(f0, v0)
    b = fc.keyframe_points.insert(f1, v1)
    for k in (a, b):
        k.interpolation = "BEZIER"
        k.handle_left_type = k.handle_right_type = "FREE"
    d = f1 - f0
    a.handle_right = (f0 + d * out_infl, v0)  # 水平ハンドル＝速度 0 から出る
    b.handle_left = (f1 - d * in_infl, v1)
```
キーを打った F カーブは 5.x ではスロット付きアクションの中にある:
```python
from bpy_extras import anim_utils

bag = anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot)
fc = next(c for c in bag.fcurves if c.data_path.endswith("transform.scale_x"))
```
簡単に済ませるなら `kp.interpolation = "EXPO"; kp.easing = "EASE_OUT"`（✓）。

### 2.2 文字（キネティックタイポグラフィ・リリック）
他アプリの定番: 「フォントは 1 場面 3 つまで」「既定の縁取り・影・斜体は安っぽい（細く・色付きの影・角度を指定）」「読む文字（機能的）と見せる文字（装飾的）を分ける」。
- **配置の型を決める**: 下中央（読ませる）／中央（主張）／縦書き（和の雰囲気）／画面全体（迫力）／不規則（リズム）。1 曲・1 本の中で型を混ぜすぎない
- **強弱を付ける**: 1 行の中で鍵になる語だけ大きく・太く・色を変える。他は小さく細く
- **縁取りは細く**（文字の高さの 2〜4%）、影は真っ黒でなく **背景色を暗く濃くした色**、ずらしは少し
- **読める時間**: 日本語は 1 秒に 4 文字前後。速い曲でも 1 行 1 秒は残す
- **出し方の種類**: 1 文字ずつ（タイプ）、語ごとに弾む、下から押し上がる（マスクで隠して出す）、ぼかしから現れる、拡大から縮んで止まる

VSE の文字ストリップ（読ませる文字向き。✓）: `font, font_size, color, location, anchor_x/y, alignment_x, wrap_width, use_bold, use_italic, use_outline/outline_color/outline_width, use_shadow/shadow_color/shadow_angle/shadow_offset/shadow_blur, use_box/box_color/box_margin/box_roundness, space_line / abs_space_line / use_absolute_line_spacing`

1 文字ずつ動かす（見せる文字向き。Geometry Nodes。✓）:
```python
# String to Curves の出力: "Curve Instances"（1 文字 1 インスタンス）, "Line", "Word", "Pivot Point"
# 文字ごとの進み t = clamp((Frame - Index*遅延) / 長さ) → Float Curve で速度の形（行き過ぎ）を付ける
s2c = N.new("GeometryNodeStringToCurves")
s2c.inputs["String"].default_value = "ワクワクAI"
s2c.inputs["Font"].default_value = bpy.data.fonts.load(r"C:\Windows\Fonts\meiryob.ttc")
idx, tm = N.new("GeometryNodeInputIndex"), N.new("GeometryNodeInputSceneTime")
# (Frame - Index*3)/10 を Math ノードで作り use_clamp=True
fcurve = N.new("ShaderNodeFloatCurve")  # 0→1 の途中に (0.6, 1.12) の点＝行き過ぎて戻る
c = fcurve.mapping.curves[0]
c.points.new(0.6, 1.12)
fcurve.mapping.update()
# → Scale Instances の Scale と、(1-t)*-0.8 を Translate Instances の Y に → Realize → Fill Curve → Set Material
```
- 語単位で動かすなら "Word" 出力（語の番号）を Index の代わりに使う
- 書き出しは透明背景（`film_transparent=True`）の PNG 連番にして VSE で重ねる

### 2.3 合成とマスク（「文字の中に映像」「切り抜いて重ねる」）
他アプリの定番: AE トラックマット、マスクのアニメーションで場面転換、シェイプで画面を区切る。
```python
fill.modifiers.new("matte", "MASK")  # ✓ 文字の形で fill を抜く
mod = fill.modifiers["matte"]
mod.input_mask_type = "STRIP"
mod.input_mask_strip = text_strip
text_strip.mute = True  # マスク用のストリップは表示しない
fill.blend_type = "ALPHA_OVER"
```
- マスクにできるもの: 文字・色（crop で四角）・メタ（色ストリップを組み合わせた図形）・動画（白黒のアニメーション）
- マスクの灰色は半透明になる。ふちをぼかしたい時は白→黒のグラデーションの素材をマスクにする
- 応用: 一部だけぼかす（複製してマスク＋ガウスぼかし）、一部だけ明るくする、画面を斜めに分割して 2 つの映像を見せる
- 合成方法（`blend_type`）の使い分け: 光・粒子・フレア＝SCREEN / ADD、質感（紙・粒子）＝OVERLAY / SOFT_LIGHT、影・汚し＝MULTIPLY

### 2.4 トランジション（場面転換）
他アプリの定番: ウィップパン（素早いずらし＋方向ブラー＋シュッという効果音）、速度ランプ（前の場面の終わりを速く、次の頭を遅く）、マスク／図形で開く、グリッチ、フラッシュ。**使いすぎるとチープになる。1 本で 2〜3 種類、意味のある切れ目だけに。**

コンポジタのトランジションストリップ（5.2+。✓）:
- ノードグループの入力: 1 つ目の色入力＝入力 1（前の場面）、2 つ目＝入力 2（次の場面）、**Float 入力＝進み具合**（Float 入力が無いとフェードが効かない）
- 出力の 1 つ目は色
```python
tr = se.strips.new_effect("wipe", "COMPOSITOR", ch, start, length=n, input1=a, input2=b)
ng = bpy.data.node_groups.new("Wipe", "CompositorNodeTree")
for nm, st in (
    ("Image", "NodeSocketColor"),
    ("Second", "NodeSocketColor"),
    ("Fader", "NodeSocketFloat"),
):
    ng.interface.new_socket(name=nm, in_out="INPUT", socket_type=st)
ng.interface.new_socket(name="Image", in_out="OUTPUT", socket_type="NodeSocketColor")
# …ノードを組む（下の例）…
tr.node_group = ng
tr.use_default_fade = False  # 進み具合をキーで制御（速度の形を自分で決める）
tr.effect_fader = 0
tr.keyframe_insert("effect_fader", frame=start)
tr.effect_fader = 1
tr.keyframe_insert("effect_fader", frame=start + n)
```
組み方の例:
- **図形ワイプ（✓）**: Box Mask（または Ellipse Mask）の `Size` を Fader で広げ → Mix の Factor（A＝前、B＝次）。Box の `Rotation` を付けると斜めに開く
- **ウィップパン（△）**: 前の場面を Translate の X で 0→−幅、次を ＋幅→0（Fader で）＋ 両方に Directional Blur（DBlur、入力 `Samples, Center, Rotation, Scale, Amount, Direction`）を Fader 0.5 で最大に → 0.5 で切り替え。効果音を必ず添える
- **ズームブラー（△）**: Scale で 1→1.3 に寄りながら DBlur の `Scale`（放射状）を上げ、中間で次の場面に
- **グリッチ切り替え（△）**: 中間の 4〜6 フレームだけ RGB ずらし＋ピクセル化＋ノイズマスクで前後を混ぜる
- **速度ランプ（✓ 動画のみ）**: 動画ストリップの `retiming_keys` で切れ目の前を速く、後を遅く（色・文字ストリップにはリタイミングが無い）

### 2.5 カメラ（揺れ・寄り・2.5D）
他アプリの定番: 揺れは「横・縦・回転・ズーム」の 4 つを別々の強さで、端の黒はズームで隠す、速い揺れにはモーションブラー。既定値の揺れはチープ。
```python
import math, random


def shake(strip, f0, f1, amp=12, rot=0.6, freq=3, decay=True, seed=1):
    """調整レイヤーに付けて画面全体を揺らす。amp=画素、rot=度、freq=何フレームごとに目標を変えるか。"""
    rnd = random.Random(seed)
    t = strip.transform
    k0 = t.scale_x
    for f in range(f0, f1 + 1, freq):
        g = (1 - (f - f0) / max(1, f1 - f0)) if decay else 1.0  # 衝撃は減衰させる
        t.offset_x = rnd.uniform(-amp, amp) * g
        t.offset_y = rnd.uniform(-amp, amp) * g
        t.rotation = math.radians(rnd.uniform(-rot, rot) * g)
        t.scale_x = t.scale_y = k0 * (1 + 2 * amp / 1080)  # 端の黒を隠す
        for p in ("offset_x", "offset_y", "rotation", "scale_x", "scale_y"):
            t.keyframe_insert(p, frame=f)
```
- 衝撃（強調・サビ頭）は 6〜12 フレームで減衰、手持ち感は振幅 2〜4 画素で遅く（freq 6〜10）
- 2.5D: 3D シーンに画像を貼った板を奥行き違いに並べ、カメラをゆっくり横移動・前進（手前ほど速く動いて奥行きが出る）。シーンストリップで VSE に置く

### 2.6 光と質感（撮影処理・ルック）
他アプリの定番: アニメの撮影処理（ディフュージョン＝ぼかした複製を薄く重ねる、透過光、入射光）、色収差は **控えめに**、単色化は「彩度 0＋単色を乗算」、周辺減光、フィルムの粒子、走査線。
調整レイヤーにコンポジタモディファイアで付ける（ノード名と入力は ✓、組み合わせの見た目は △）:
| ルック | ノード | 要点 |
|---|---|---|
| 発光（ブルーム） | `CompositorNodeGlare` の Type=Bloom（他に Fog Glow・Streaks・Ghosts・Simple Star・Sun Beams） | `Threshold` 0.7〜0.9、`Strength` 0.3〜0.6。低い閾値は全体が白く飛ぶ |
| ディフュージョン | Blur（Gaussian、大きめ）→ Mix（SCREEN か ADD）で 20〜35% | 明部だけ柔らかく光る。アニメ風の定番 |
| 光の筋（入射光） | Glare の Streaks / Sun Beams（`Sun Position`） | 1 場面に 1 つ |
| 色収差 | `CompositorNodeLensdist` の `Dispersion` 0.01〜0.03（5.3.0 には色収差専用ノードが無い） | 画面の端ほどずれる。強くしない |
| RGB ずらし | `CompositorNodeSeparateColor` → R と B を `CompositorNodeTranslate` で ±数画素 → `CompositorNodeCombineColor` | グリッチ・衝撃の瞬間だけ |
| 周辺減光 | `CompositorNodeEllipseMask` → 大きな Blur → Mix MULTIPLY | 0.4〜0.6 |
| 単色化・2 色化 | 彩度 0（色補正）→ 単色を MULTIPLY、または Color Ramp で 2 色 | 回想・サビ前の静けさ |
| ポスタリゼーション／ピクセル化 | `CompositorNodePosterize` / `CompositorNodePixelate` | グリッチ・レトロ |
| 絵画風 | `CompositorNodeKuwahara` | 背景だけに |
| グリッチの範囲 | Noise / Voronoi 系のテクスチャ → 0.5 で白黒に（Color Ramp 定数）→ Mix の Factor | 範囲をキーで変えて強さを制御 |
| 走査線 | 横縞の画像を Translate の Y で流して OVERLAY | VHS・モニター風 |
| 素材ごとに変える | `CompositorNodeSequencerStripInfo`（ストリップの開始・終了・位置） | モディファイアの中で使う |

**色の決め方**: 原色の既定パレットは使わない。主役の色・強調色 1 つ・背景の 3 色程度で、明るさ（輝度）をそろえる。純白 (1,1,1) は浮くので少し落とす。

### 2.7 時間の使い方
- **拍・発話に合わせる**: 切り替え・文字の登場・揺れは拍か発話の頭に置く（拍は `blender-vse-mv-editing` の検出、発話は字幕の時刻）
- **緩急**: ずっと速いと単調。静かな区間（Aメロ・説明）は動きを少なく、盛り上がり（サビ・結論）で密度を上げる
- **ループ**: 背景の動きは往復（複製して `use_reverse_frames=True` を後ろに置く）で長さを合わせる（✓）

---

## 3. 場面ごとのレシピ（MV・PV・解説動画共通）

| 場面 | 組み合わせ |
|---|---|
| オープニング／題名 | 黒から光（ブルーム）で文字が現れる → 1 文字ずつ（行き過ぎて戻る）→ 1 秒静止 → 黒帯＋ゆっくり寄るカメラ |
| 章・セクションの切り替え | 図形ワイプかウィップパン（効果音付き）→ 章タイトルを左上に小さく（線が伸びて下線） |
| 強調（数値・キーワード・サビ頭） | 4〜8 フレームのズームパンチ＋減衰する揺れ＋白フラッシュ（ADD、0.4〜0.6）＋大きな文字が縮んで止まる＋RGB ずらしを 2〜3 フレーム |
| 説明中・Aメロ | 素材にゆっくり寄る（Ken Burns、10 秒で 5〜10%）、ルックは控えめ、文字は読ませる型（下中央） |
| 回想・余韻 | 単色化・彩度を落とす・周辺減光を強く・動きを遅く |
| クライマックス・サビ | 1〜2 拍ごとのカット、光の素材を SCREEN、文字を画面全体に、2.5D のカメラ前進 |
| エンディング | 動きを止めていく、ブルームを強めて白へフェード、またはマスクで閉じる |

### 3.1 表現の型ごとの作り方（応用 5 分野）
| 型 | 向く用途 | Blender での作り方 | 気を付けること |
|---|---|---|---|
| アイソメトリック（等角図） | 製品の構造・工程・建物・サービスの仕組み | カメラ `type="ORTHO"`、方位 45°・見下ろし約 35.264° を出発点に、軸の見え方をテスト描画で確かめる。部品はコレクションに分け、組み立ては主部品→副部品の順にずらし、着地で小さく収める | 全カットでカメラの角度と `ortho_scale` をそろえる。面の明るさを上・横・前で変えると形が読める。ベベル幅を統一 |
| モーショングラフィックス | 題名・ブランド・抽象的な導入 | 先に「型」を決める（グリッド・線の太さ・角丸・色・基本のイージング・出方と消え方）。図形の変形は頂点の構成が同じならシェイプキー、違えば切り替え。線が伸びるのは Trim Curve かカーブの `bevel_factor_end`。反復は Instance on Points（乱数の Seed は固定） | 同じ動きを使い回す。背景の動きは主役より遅く・小さく・薄く。全部に弾み・回転を付けない |
| インフォグラフィック | データ・比較・数値の説明 | 棒は高さを 0→値へ、折れ線は Trim Curve、円グラフは角度、数値は書式を Python で作る（等幅数字で桁の揺れを防ぐ） | **数値は正確に**（出典・単位・期間を表示、架空の値は「仮データ」と明記）。棒の基準はゼロから。最後に全データを読める静止の時間を作る。色だけで区別しない |
| キネティックタイポグラフィ | 歌詞・キャッチコピー・強調 | 1 文字ずつは String to Curves（2.2）、語ごとは "Word" 出力。重要な語だけ大きさ・太さ・色を変える | 意味の切れ目で区切る（全文字を拍に合わせない）。読む速さ（日本語 1 秒 4 字前後）を超えない。同じ文字はインスタンスを共有するので、形を個別に変えるなら Realize Instances |
| ピクトグラム | 手順・操作説明・注意 | カーブかグリースペンシルで線の太さ・角丸を統一。描かれる演出は Trim Curve。人型は頭・胴・腕・脚に分けて関節で回す | シルエットだけで意味が通じるか。変形中に別の意味に見えないか。小さく白黒で表示しても分かるか |
| モンタージュ | MV・PV・時間経過・比較 | `blender-vse-mv-editing` のショット表とつなぐ 6 つの軸（形・動き・方向・色・音・意味） | カット数を目的にしない。速い区間の後に静かなショットを置く |

組み合わせの例: 製品紹介＝アイソメトリック＋インフォグラフィック＋タイポ、ブランドの導入＝モーショングラフィックス＋タイポ＋モンタージュ、操作説明＝ピクトグラム＋タイポ（カメラは動かさない）。

---

## 4. 品質ルール（安っぽく見せない）
- [ ] 既定値をそのまま使っていない（縁取り・影・揺れ・グロー・色）
- [ ] すべての動きに緩急がある（等速の移動・拡大が無い）。複数の要素はずらして出している
- [ ] 1 場面の主役の動きは 1 つ。トランジションは 2〜3 種類まで
- [ ] フォントは 1 場面 3 つまで。色は 3 色程度で輝度がそろっている
- [ ] 色収差・グリッチ・フラッシュは「瞬間」だけ。常時かけていない
- [ ] 文字は読める長さ出ている。字幕と重なっていない
- [ ] 書き出して 1 秒 1 枚の一覧画像で確認し、白飛び・黒つぶれ・はみ出しが無い
- [ ] どこで止めても構図が成り立つ（動きだけで画面を成立させていない）
- [ ] 「何を足すか」だけでなく「何を動かさないか・見せないか」を決めた

### 4.1 採点の目安（1〜5。用途ごとに重い項目が違う）
| 項目 | 1〜2 | 3 | 4〜5 |
|---|---|---|---|
| 意図 | 効果の目的が不明 | 目的は伝わる | 表現とメッセージが強く一致 |
| 視線 | 散る | 主役は分かる | 読む順が自然 |
| 時間 | 唐突・間延び | おおむね適切 | 動き・停止・余韻が精密 |
| 一貫性 | カットごとに別デザイン | 共通ルールあり | 全体で強い型 |
| 可読性 | 読めない | 必要な情報は読める | 短時間で正確に分かる |
| 節度 | 効果過多 | 必要な効果に限定 | 強調が際立つ |
インフォグラフィックは可読性と正確さ、アイソメトリックは空間の一貫性、モンタージュはカットのつながりとリズムを重く見る。

---

## 5. 実機で分かった注意点（5.3.0）
- **背景実行（`blender -b`）では、`frame_set(f)` の後に `render(write_still=True)` しても 1 フレーム目が描かれることがある。** 確認用の静止画は、範囲を 1 フレームにしてアニメーションとして描く:
  ```python
  sc.frame_start = sc.frame_end = f
  sc.render.filepath = str(out / "check_#")
  bpy.ops.render.render(animation=True, scene=sc.name)
  ```
- 5.3.0 には色収差・センサーノイズ・周辺減光の **専用ノードが無い**（新しいマニュアルには載っている）。Lens Distortion の Dispersion や手組みで代用
- リタイミングキーは動画ストリップだけ（色・文字ストリップには無い）
- コンポジタのトランジションの進み具合は、ノードグループに Float 入力が無いと効かない
- Glare などの設定は 5.x では入力ソケット（`node.inputs["Type"].default_value = "Bloom"`）
- **コンポジタの入口**: `scene.node_tree` は無い。`scene.use_nodes`・`scene.compositing_node_group` は 5.3 で非推奨。5.3 は `scene.compositor_effects`（コレクション、要素に `node_group`）。VSE ではモディファイア／トランジションにノードグループを付ける（こちらは ✓）
- **ストリップの位置と長さ**: `frame_final_start/end/duration`・`frame_start`・`frame_offset_*` は 5.3 で非推奨。`left_handle`・`right_handle`（排他的）・`duration`・`content_start` を使う（詳細は `blender-vse-mv-editing` 2.2）
- アニメーションの F カーブは 5.x の階層アクション（スロット・チャンネルバッグ）の中。古い `action.fcurves` 直接は使わない（2.1 の `anim_utils.action_get_channelbag_for_slot`）
- 存在を確かめたノード: String to Curves・Realize Instances・Instance on Points・Curve to Points・Trim Curve・Curve to Mesh、Glare・Lens Distortion・Color Balance・Defocus・Vector Blur・Ellipse Mask・Cryptomatte・Separate Color。カメラの被写界深度は `cam.dof`（`use_dof, focus_object, focus_distance, aperture_fstop…`）
- 書き出しの前に `image_settings.media_type = "VIDEO"`（動画）、確認用は PNG

## 6. 学習用の無料の情報源
- 仕様の根拠は 5.3 の版を優先: API https://docs.blender.org/api/5.3/ 、マニュアル（日本語）https://docs.blender.org/manual/ja/5.3/ 。`current`・`latest` は 5.3 と違うことがある
- 動きの設計の考え方（視線・一貫性・リズム・停止）: IBM Design Language – Animation https://www.ibm.com/design/language/animation/tips-and-techniques/
- Blender マニュアル（コンポジタを VSE で使う・各ノード）: https://docs.blender.org/manual/en/latest/compositing/
- リリースノート 5.2（コンポジタのエフェクト／トランジションストリップ）: https://developer.blender.org/docs/release_notes/5.2/sequencer/
- コミュニティの VSE 解説（マスク・トランジション・速度）: https://vse-docs.readthedocs.io/
- Blender でのモーショングラフィックスの考え方（Wintermute Digital）: https://wintermutedigital.netlify.app/post/elements_mgfx_1
- 安っぽく見える原因と代替（AviUtl 向けだが考え方は共通）: https://scrapbox.io/leje-campus/脱「AviUtlっぽさ」のすゝめ
- AE のグラフエディターの使い方（速度の山の作り方）: https://www.adobe.com/jp/learn/after-effects/web/adjusting-keyframes-dynamic-movement
- 多くのチュートリアルは AE・Premiere 向け。上の対応表で Blender の手段に置き換える