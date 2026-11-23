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
