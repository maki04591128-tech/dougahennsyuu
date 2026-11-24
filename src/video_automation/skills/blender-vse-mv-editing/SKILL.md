---
name: "blender-vse-mv-editing"
description: "Blender 5.x の動画編集機能（Video Sequencer, VSE）をPythonで操作してMV/PVを組むときに使う。拍の検出、拍でのカット、切り替え、色補正、文字、書き出しを実機5.3で確認済みのコードでまとめている。"
---

# Blender 5.3 の VSE で MV・PV・モンタージュを組む

対象は Blender 5.3.0（Windows 実機で確認）。印: ✓ = 5.3.0 で動作を確認、△ = 名前・関数の存在だけ確認。
凝った画面演出は `blender-rich-screen-direction`、5.x 全般の API は `blender-5-scripting`、用途ごとに使ってよい演出は `video-design-by-purpose` を併せて読む。

## 0. 作業の順番（コードより先に決める）
1. **演出の意図を 1 文にする**: 何を伝え、どんな感情にし、最初に何を見せるか。効果の名前から始めない
2. **時間の設計**: 全体の尺・fps、区切り（導入→盛り上げ→ピーク→余韻→終わり）、各ショットの開始・終了フレーム
3. **ショット表を作る**（データにする。下の 3.2）→ そこからストリップを生成 → 生成後にフレーム範囲・チャンネル・素材を検証
4. **粗編集 → 仕上げ**: カット・尺・構成を先に決め、色・光・文字は後
5. **確かめる**: 代表フレームの静止画、全尺の低解像度書き出し、音だけ・映像だけでも構成が成り立つか

## 1. 最初に環境を確かめる（読み取りだけ）
```python
import bpy, sys

print(bpy.app.version_string, sys.version)  # 5.3.0 以外なら下の API を確かめ直す
sc = bpy.context.scene
print(sc.render.resolution_x, sc.render.resolution_y, sc.render.fps, sc.render.fps_base)
se = sc.sequence_editor
print(se is not None and (hasattr(se, "strips"), hasattr(se, "strips_all")))
```
- 実効 fps は `fps / fps_base`（29.97 は fps=30, fps_base=1.001）。秒→フレームは `round(秒 * 実効fps)`
- 古い記事の `sequences` / `sequences_all` は使わない。5.x は `strips`（最上位）と `strips_all`（メタの中も含む）✓

## 2. 5.3.0 の API（実機で確認した事実）

### 2.1 ストリップの作成（`se.strips.*`）✓
`new_movie, new_sound, new_image, new_scene, new_clip, new_mask, new_meta, new_effect, remove`
```python
se = sc.sequence_editor_create()
mov = se.strips.new_movie(
    "SHOT_010", path, channel=2, frame_start=1, fit_method="FIT"
)  # FIT/FILL/STRETCH/ORIGINAL
snd = se.strips.new_sound("SHOT_010_音", path, channel=1, frame_start=1)  # 動画の音は別ストリップ
col = se.strips.new_effect("flash", "COLOR", 21, 100, length=8)
eff = se.strips.new_effect(
    "wipe", "WIPE", 9, f0, length=n, input1=a, input2=b
)  # 2 入力のエフェクト
```
- 動画の fps がシーンと違うと、映像と音の長さがずれる。**空のシーンならシーンを動画の fps・解像度に合わせてから置き直す**（`mov.fps`、`mov.elements[0].orig_width/orig_height`）✓
- 生成物は名前の接頭辞やカスタムプロパティで印を付け、再実行で二重に作らない

### 2.2 位置と長さ（5.3 で名前が変わった）✓
| 使う | 意味 | 5.3 で非推奨（6.0 で削除予定） |
|---|---|---|
| `left_handle` | 表示の始まり（タイムライン上のフレーム） | `frame_final_start` |
| `right_handle` | 表示の終わり（このフレームからは出ない＝排他的） | `frame_final_end` |
| `duration` | 表示の長さ（`right_handle - left_handle`） | `frame_final_duration` |
| `content_start` | 素材そのものが始まる位置（動かすとストリップごと移動） | `frame_start` |
| `left_handle_offset` / `right_handle_offset` | 素材の頭・お尻を削った量 | `frame_offset_start` / `frame_offset_end` |
| `content_duration`（読み取りのみ） | 素材の長さ | — |
- 古い名前は 5.3 でも動くが DeprecationWarning が出る。新しいコードは上の列を使う
- 複数の版で動かすなら `getattr(s, "right_handle", None)` で確かめ、無ければ `frame_final_end` に戻す
- シーンの最後は `scene.frame_end = right_handle - 1`（`frame_end` は含む側）
- ストリップの関数: `split`（分割）、`swap`、`move_to_meta`、`strip_elem_from_frame` △

### 2.3 編集の演算子（5.3 にある。UI の文脈が要るものがある）△
`bpy.ops.sequencer.ripple_delete / ripple_trim / box_blade / split / slip / retiming_key_add / retiming_segment_speed_set / retiming_transition_add / retiming_freeze_frame_add / export_subtitles`、`bpy.ops.wm.otio_export / otio_import`（OpenTimelineIO）
- 背景実行（`-b`）では演算子が動かないことが多い。自動化はデータ API（2.1・2.2）を優先し、演算子は UI でだけ使う
- **リップル削除**は他のチャンネルの範囲にも影響し得る。編集用の複製シーンで試してから使う
- OpenTimelineIO の受け渡しは初期対応。書き出した後に相手のソフトでカット位置・尺・音を照合する。Python の `opentimelineio`・`OpenEXR` モジュールは import できる ✓

### 2.4 コンポジタ（5.3 で入口が変わった）✓
- `scene.node_tree` は無い。`scene.use_nodes` と `scene.compositing_node_group` は 5.3 で非推奨（`compositing_node_group` は「有効なエフェクトのノードグループ」を返す互換用）
- 5.3 の入口は **`scene.compositor_effects`**（コレクション。`new / remove / clear`、要素は `node_group, enable_for_render, enable_for_preview, is_active`）
- VSE の中で使うなら、ストリップのモディファイア `strip.modifiers.new("look", "COMPOSITOR")` にノードグループ（`bpy.data.node_groups.new(名前, "CompositorNodeTree")`）を付ける ✓。トランジションは `new_effect(..., "COMPOSITOR", ...)`（入力 2 つ＋進み具合 `effect_fader`）✓
- Glare の設定は入力ソケット: `Image, Type, Quality, Threshold, Smoothness, Clamp, Maximum, Strength, Saturation, Tint, Size, Streaks, Streaks Angle, Iterations, Fade, Color Modulation, Diagonal, Sun Position, Jitter, Kernel…` ✓

### 2.5 キーフレームと F カーブ（5.x の階層アクション）✓
```python
from bpy_extras import anim_utils


def set_interp_all(id_block, interpolation="BEZIER", easing="AUTO"):
    ad = id_block.animation_data
    if ad is None or ad.action is None:
        return
    bag = anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot)
    for fc in bag.fcurves if bag else []:
        for kp in fc.keyframe_points:
            kp.interpolation, kp.easing = interpolation, easing
```
ストリップの変形は `strip.transform`（`offset_x/offset_y/scale_x/scale_y/rotation`）にキーを打つ。

## 3. 編集の組み立て

### 3.1 チャンネルの割り当て（初期の型。作品に合わせて変える）
| ch | 用途 |
|---|---|
| 1 | 音楽 |
| 2〜3 | 映像（ベース・カットイン） |
| 9〜10 | トランジション・速度 |
| 20 | 調整レイヤー（色・光） |
| 21 | フラッシュなど瞬間の効果 |
| 22〜23 | 黒帯・枠 |
| 24〜 | 文字（題名・歌詞） |
上ほど手前に重なる。

### 3.2 ショット表から並べる（データ → ストリップ → 検証）
```python
shots = [  # 開始は拍・意味の切れ目から決める。長さは 2〜3 種類を混ぜる
    {"name": "SHOT_010", "path": r"D:\clips\a.mp4", "start": 1, "dur": 72, "ch": 2, "role": "全景"},
    {
        "name": "SHOT_020",
        "path": r"D:\clips\b.mp4",
        "start": 73,
        "dur": 36,
        "ch": 3,
        "role": "寄り",
    },
]
for s in shots:
    if s["name"] in se.strips:  # 再実行で増やさない
        continue
    m = se.strips.new_movie(s["name"], s["path"], s["ch"], s["start"], fit_method="FILL")
    m.right_handle = m.left_handle + s["dur"]
    m["va_generated"] = True
for s in shots:  # 検証
    m = se.strips[s["name"]]
    assert m.left_handle == s["start"] and m.duration == s["dur"], m.name
```

### 3.3 拍を検出する（Blender 同梱の aud + numpy。外部の追加なし）✓
```python
def detect_beats(path, fps):
    import aud, numpy as np

    snd = aud.Sound(str(path))
    rate = int(snd.specs[0])
    x = snd.data().astype(np.float32)
    mono = x.mean(axis=1) if x.ndim == 2 else x
    hop = 512
    n = len(mono) // hop
    energy = np.log1p(100 * np.sqrt((mono[: n * hop].reshape(n, hop) ** 2).mean(axis=1)))
    onset = np.maximum(0.0, np.diff(energy, prepend=energy[0]))
    onset = (onset - onset.mean()) / (onset.std() + 1e-9)
    ac = np.correlate(onset, onset, mode="full")[n - 1 :]
    sec = hop / rate
    lags = np.arange(len(ac))
    bpm = 60.0 / np.maximum(lags * sec, 1e-9)
    ok = (bpm >= 70) & (bpm <= 180)
    lag = int(lags[ok][np.argmax(ac[ok])])
    phase = max(range(lag), key=lambda p: onset[p::lag].sum())
    beats = [round(p * sec * fps) for p in range(phase, n, lag)]
    accents = sorted({round(i * sec * fps) for i in np.where(onset > np.quantile(onset, 0.97))[0]})
    return {"tempo": 60.0 / (lag * sec), "beats": beats, "accents": accents}
```
- 拍はマーカーにする: `sc.timeline_markers.new(f"b{i}", frame=b)`
- **推定であって正確な拍ではない**。テンポの揺れ・無音の前奏・変拍子は手で直す
- BPM から計算するときは拍ごとに丸める（`round(i * 60 / BPM * fps)`）。一定の整数間隔を足し続けるとずれが積もる

### 3.4 リズムの付け方（全部を拍に合わせない）
- 音楽を 小節・拍／フレーズ（4・8 小節）／区切り（A・サビ・ブレイク）／音色（キック・ボーカル）に分けて見る
- 強い拍・フレーズの頭に大きな画面変化、弱い拍は動きの続きや細部
- カットは拍だけでなく **動作のピーク・意味の切れ目・視線** で決める。あえて外すカットで緊張を作る
- 密度: 導入は長め → 盛り上げで短く → ピーク → 余韻で動きとカットを減らす。速いカットは遅いショットの後で効く
- 確かめ方: 音を消しても映像の流れが分かるか、映像を隠しても音の流れが成り立つか

### 3.5 モンタージュでカットをつなぐ 6 つの軸
| 軸 | 例 | 実装 |
|---|---|---|
| 形（グラフィックマッチ） | 円→レンズ | 構図・大きさ・位置を合わせて切る |
| 動き（マッチ・オン・アクション） | 手を振り下ろす→機械が動く | 動作の方向・速さをそろえ、動作の途中で切る |
| 方向 | 左→右の移動を保つ | 移動方向を反転させない |
| 色 | 赤い光→赤い部品 | 調整レイヤーで色を寄せる |
| 音（サウンドブリッジ） | 次の場面の音を先に鳴らす（J カット）・前の音を残す（L カット） | 音のストリップを映像と別に置く |
| 意味（連想） | 都市→回路→神経 | 形・動き・言葉の連想 |

### 3.6 速度（スピードランプ）
- 動画ストリップだけがリタイミングキーを持つ（色・文字には無い）✓。UI ではリタイミングキー・速度の遷移・フリーズを使う
- データ API では `SPEED` エフェクト（`speed_factor` など）✓
- 設計の目安: 予備動作 70〜85% → 動作 180〜300% → インパクト 30〜60% → 余韻 70〜100%。元の撮影 fps より遅くしても新しい情報は増えない（カクつく）
- 音のアタックを速度の山かインパクトに合わせる

## 4. 確認済みの部品（`tools/blender_lab/vse_mv.py`。20 秒 1080p の書き出し約 96 秒）
| 部品 | 要点 | 印 |
|---|---|---|
| 音楽のフェード | `snd.volume` にキー（頭 15 フレーム・終わり 30 フレーム） | ✓ |
| 3D シーンをストリップに | `new_scene`（Geometry Nodes の波など）。Eevee のサンプル 16 で軽く | ✓ |
| 2 拍ごとのカット＋クロスフェード | `blend_type="ALPHA_OVER"`＋`blend_alpha` にキー | ✓ |
| ワイプ | `new_effect("wipe","WIPE",…, input1, input2)` | ✓ |
| スロー | `SPEED` エフェクト | ✓ |
| 調整レイヤーの色 | `ADJUSTMENT` ストリップ＋`COLOR_BALANCE`・`CURVES` モディファイア | ✓ |
| ルック（ブルーム・周辺減光） | `COMPOSITOR` モディファイア＋ノードグループ | ✓ |
| アクセントのフラッシュ | `COLOR` を `ADD`、8 フレーム。**暗い背景では強さ 0.06 程度**（強いと全体が白く浮く） | ✓ |
| 黒帯 | `COLOR`（黒）を上下に、`transform` で位置 | ✓ |
| 歌詞・題名 | `TEXT` ストリップ | ✓ |
| 光の素材を重ねる | `blend_type="SCREEN"` | ✓ |

## 5. 書き出し
- 動画: `sc.render.image_settings.media_type = "VIDEO"`、FFmpeg の H.264。確認用の静止画は PNG
- 背景実行で 1 フレームだけ描くときは、`frame_set` の後の `write_still` が 1 フレーム目になることがある → `sc.frame_start = sc.frame_end = f` にして `render(animation=True)` ✓
- 出力ファイルを最後まで再生し、尺・音・解像度・fps を確かめる

## 6. 品質の点検
- [ ] カットに意味がある（拍だけで決めていない）。尺に 2〜3 種類の変化がある
- [ ] 同じ画角・動きが続いていない。動きの方向が意図せず反転していない
- [ ] ピークの前後に対比（静けさ・余白）がある
- [ ] 映像と音の fps・長さが合っている（読み込んだ直後に `duration` を比べる）
- [ ] 黒フレーム・素材の欠け・音の途切れが無い
- [ ] 古い API（`sequences`・`frame_final_*`・`scene.node_tree`）を使っていない

## 7. 資料（5.3 の版を優先する。`current`・`latest` は 5.3 と違うことがある）
- Python API 5.3: https://docs.blender.org/api/5.3/ （`bpy.types.SequenceEditor`・`bpy.types.Strip`、変更履歴 change_log）
- マニュアル 5.3（日本語）: https://docs.blender.org/manual/ja/5.3/ （動画編集・リタイミング）
- リリースノート 5.3 の VSE: https://developer.blender.org/docs/release_notes/5.3/sequencer/
- 公式資料に無いことは「未確認」とし、実機の RNA（`bl_rna.properties`）で確かめる