# Blender 5.x 検証ラボ

Blender の機能を自動化で使うための調査・検証スクリプト（改善項目⑥）。
スキル `blender-5-scripting` / `blender-video-motion-design` のコード例は、ここのレシピで実機確認したもの。

- `probe.py` — Blender の API を実機で調べる（エンジン名、色変換、動画の書き出し形式、コンポジタ、Glare ノードの入力、アニメーション API、ノードの種類など）
- `recipes.py` — 演出レシピを実行して静止画・動画を書き出し、成否と所要時間を `recipes.json` に記録する
  - 立体タイトル＋グロー、ジオメトリノードの背景、分子模型、3D 棒グラフ、Grease Pencil、透明背景の素材、動画の直接書き出し、1080p の速度、線が伸びる演出、1 文字ずつ出す文字、トゥーン＋輪郭線、カメラの揺れ、シーケンサー

```
blender -b --factory-startup -P probe.py -- <出力フォルダ>
blender -b --factory-startup -P recipes.py -- <出力フォルダ> <フォントのパス> [レシピ名]
```

確認結果（Blender 5.3.0 / Windows / RTX 5050）: 13 レシピすべて成功。
1080p・64 サンプルの立体文字＋被写界深度＋グローで約 0.52 秒／フレーム。

## 動画編集機能（VSE）で MV / PV を作る

- `vse_probe.py` — VSE の API を実機で調べる（ストリップ・エフェクト・合成方法・モディファイアの種類、テキスト／音／速度／グローの設定項目、コンポジタモディファイア、リタイミング）。結果は `vse_probe.json`
- `vse_mv.py` — 曲から拍を検出し（Blender 同梱の aud + numpy）、VSE だけで 20 秒の MV を組んで書き出す。成否は `vse_mv.json`
  - 拍のマーカー、音量フェード、3D シーンストリップ、2 拍ごとのカットとクロスフェード、ワイプ、スロー（SPEED・MULTIPLY）、調整レイヤー（カラーバランス・カーブ・拍ごとのズーム）、コンポジタモディファイア（ブルーム・周辺減光）、アクセントでのフラッシュ、黒帯、歌詞と題名の文字、SCREEN 合成の光、グロー

```
blender -b --factory-startup -P vse_probe.py -- <出力フォルダ>
blender -b --factory-startup -P vse_mv.py -- <出力フォルダ> <素材フォルダ> <フォントのパス>
```

確認結果（Blender 5.3.0）: 13 機能すべて成功、84 BPM を検出、20 秒 1080p の書き出しに約 96 秒。
手順とコードはスキル `blender-vse-mv-editing` にまとめた。

## 凝った画面演出の機能確認

- `mg_probe.py` — 演出に使う機能を実機で確かめる。確認する機能は次のとおり。
  - コンポジタのノードと入力名
  - コンポジタのトランジションストリップ（図形ワイプ、`effect_fader` のキー）
  - マスクモディファイアで文字の形に抜く処理（トラックマット）
  - リタイミング
  - Geometry Nodes で 1 文字ずつ出す文字
  - Grease Pencil の線
  - 文字ストリップの項目

```
blender -b --factory-startup -P mg_probe.py -- <出力フォルダ> <フォントのパス>
```

確認結果（Blender 5.3.0）:
- 動くことを確認したもの: 図形ワイプのトランジション、文字の形に抜く処理、1 文字ずつ弾む文字
- 5.3.0 には色収差・センサーノイズ・周辺減光の専用ノードが無い
- 背景実行では `frame_set` の後に `write_still` で描いても 1 フレーム目になることがある。範囲を 1 フレームにした動画描画で回避する
- 手順と他アプリとの対応はスキル `blender-rich-screen-direction` にまとめた
