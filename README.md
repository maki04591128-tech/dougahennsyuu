# VideoAutomation

MCP対応の対話型動画編集自動化システム。仕様は [docs/VIDEO_AUTOMATION_SPEC.md](docs/VIDEO_AUTOMATION_SPEC.md) を参照。

現在の実装範囲:
- **Phase 1（基盤）** — 設定、ログ、SQLite、FastAPI、Job管理、イベントバス、PySide6基盤、MCPテストサーバー
- **Phase 2（LLM・モデル検出）** — Provider共通IF、Ollama / LM Studio / OpenAI互換 / OpenAI・Claude・Gemini Adapter、
  Capability判定（実測プローブ）、推論テスト、モデル設定UI、タスク別割当、ACE-Step（音楽生成Provider）検出
- **Phase 3（音声合成）** — VOICEVOX Nemo / VOICEVOX Engine 対応（エンジン機能を manifest で判定）、話者取得、
  音声生成・保存（WAV・ハッシュ・区間時刻）、読み・アクセント編集、抑揚調整、辞書（DB同期・復元）、試聴、音声タブUI
- **Phase 4（字幕）** — 台本分割（話者・シーン）、日本語改行（句読点・助詞境界・行頭禁則）、
  音声のモーラ時刻への整列、フレーム境界への正規化（半開区間）、ASS / SRT 出力、字幕スタイル、台本・字幕タブUI
- **Phase 5（編集・書き出し）** — 素材取り込み、タイムライン（トラック・クリップ）、編集命令（Undo/Redo・LLM承認）、
  FFmpeg 書き出し（NVENC自動判定・縦横/4K・字幕焼き込み・ラウドネス正規化）、フレーム/動画プレビュー、
  Blender タイトルカード、MCP（mcp_render）、編集・書き出しタブUI
- **Phase 6（素材・音楽生成）** — 素材検索（Pexels / Pixabay / Freesound / Openverse / Wikimedia Commons）、
  安全なダウンロード、ライセンスの正規化・判定・スナップショット保存、クレジット出力、LLM要求の承認、
  ACE-Step 1.5 による BGM 生成・配置、素材・BGMタブUI
- **Phase 7（アニメーション）** — キーフレーム形式のアニメーション定義、要素（拡大・移動・回転・不透明度）と
  カメラ（ズーム・パン）の演出、シーン切替（ディゾルブ・暗転・スライド・ワイプ・ズーム）、
  Blender の文字アニメーション（ポップ・タイプライター・1文字ずつ跳ねる等）と図表、シーン作成、
  モーションプリセット、MCP（mcp_visual）、編集タブの演出・テロップ操作
- **Phase 8（対話型編集）** — 対話型台本作成（全自動 / 片側入力・固定セリフ・1発話の再生成・検証・版と差分）、
  会話による編集指示（計画 → 検証・修正 → 承認待ち）、承認の一元管理とポリシー、編集差分・履歴と取り消し、
  モデルを替えた再実行、MCP（mcp_script・mcp_core の計画）、台本作成（AI）・AIアシスタントタブUI
- **Phase 9（統合・品質検査）** — E2E テスト、品質検査（音声・字幕同期・映像・ライセンス）と品質検査タブ、
  性能レポート、動作環境の確認（`--doctor`）、Windows 配布物（zip＋セットアップ）、利用者マニュアル・運用ガイド

利用者向けの説明は [docs/USER_MANUAL.md](docs/USER_MANUAL.md)、運用・トラブル対応は
[docs/OPERATIONS.md](docs/OPERATIONS.md) を参照。

## Phase 2 の要点

- 自動検出は既定ポートのみ（Ollama `127.0.0.1:11434` / LM Studio `127.0.0.1:1234`）。その他は手動登録。
- 申告Capabilityを推論テスト（basic / streaming / structured / tools / vision）の実測で上書きする。
  - `tool_calls` を返さず本文にJSONを書くモデルは「フォールバック対応（△）」として扱う。
  - 構造化出力はスキーマ形式に加えて値域も検証する。
- VRAM 節約のため既定は同時ロード1モデル（`VA_SINGLE_LOADED_MODEL=true`）。推論テスト前に他モデルをアンロード。
- APIキーは Windows DPAPI で暗号化して保存（平文保存しない）。環境変数 `VA_OPENAI_API_KEY` 等でも指定可。
- 主なAPI: `POST /api/v1/providers/discover`, `POST /providers/register`, `GET /models`,
  `POST /models/test`（Job）, `PUT /models/active`, `GET|PUT /models/task-assignment`, `GET /models/capabilities`
- MCP（mcp_core）: `discover_models`, `list_models`, `select_model`

## Phase 3 の要点

- エンジンは VOICEVOX Nemo（既定 `127.0.0.1:50121`）を優先し、通常の VOICEVOX（`:50021`）も検出する。
  Nemo と通常版の機能が同じとは仮定せず、`/engine_manifest` の `supported_features` で可否を判定し、
  未対応・未確認の機能は API で拒否する。
- 生成音声は `data/projects/<project_id>/audio/<id>.wav` に保存し、DB（`audio_files`）にパス・SHA-256・
  サンプリングレート・長さ・使用クエリ・アクセント句ごとの開始/終了時刻・クレジットを記録する（字幕同期用）。
- VOICEVOX Nemo の音声は「VOICEVOX Nemo」のクレジット表記で商用・非商用利用可（利用規約に従うこと）。
- 主なAPI: `GET /voice/engines`, `GET /voice/speakers`, `POST /voice/preview`（保存しない）,
  `POST /voice/generate`（Job）, `POST /voice/prosody`, `POST /voice/accent_phrases[/recompute]`,
  `GET|POST|PUT|DELETE /voice/dictionary`, `POST /voice/dictionary/restore`
- MCP（mcp_media, `uv run video-automation-mcp-media`）: `list_speakers`, `generate_voice`,
  `adjust_prosody`, `register_pronunciation`

## Phase 4 の要点

- 流れ: `POST /scripts`（台本→セグメント）→ `POST /scripts/{id}/voice`（Job）→ `POST /subtitles/generate`
  → `POST /subtitles/synchronize` → `POST /subtitles/export`（`ass` / `srt`）
- 台本の表記と音声の読みは1対1に対応しないため、字幕各行の読み（モーラ数）を `accent_phrases` で取得し、
  保存済み AudioQuery から求めたモーラ時刻表に整列して表示区間を決める。
- 表示区間はプロジェクト fps のフレーム整数・半開区間 `[start, end)` に正規化し、重なりを除去する。
  セグメントのタイムライン位置（`script_segments.start_frame/end_frame`）は Phase 5 の音声配置に使う。
- 1行の文字数は既定で横長 22 / 縦長 14、最大2行。ASS は解像度に合わせた PlayRes・セーフエリア余白・
  フェード・話者別色に対応。焼き込み（Phase 5）と外部字幕は同じ字幕データから生成する。
- MCP（mcp_media）: `generate_subtitles`, `export_subtitles`

## Phase 5 の要点

- 流れ: `POST /assets/import`（素材をプロジェクトにコピー・SHA-256・ffprobe）→ `POST /projects/{id}/timeline/from_script`
  （台本の音声を区間どおり配置）→ `POST /projects/{id}/commands`（編集命令）→ `POST /projects/{id}/preview`
  → `POST /render`（Job）
- 取り込めるのは `VA_IMPORT_DIRS`（既定: ビデオ・ピクチャ・ミュージック・ダウンロード・デスクトップ）配下の絶対パスのみ。
- 編集命令は判別共用体（`add_track` / `add_clip` / `move_clip` / `trim_clip` / `split_clip` / `delete_clip` /
  `set_clip_props` / `batch`）。適用前に検証（dry-run）し、前後のスナップショットで Undo/Redo する。
  `idempotency_key` で同じ命令の二重適用を防ぐ。
- LLM（MCP）由来の編集・最終書き出しは `PENDING_APPROVAL` になり、UI か `.../approve` で承認するまで反映しない。
- 書き出しプロファイル: FHD 横 / FHD 縦 / 4K 横 / 4K 縦。素材の縦横比が違う場合は fill（切り抜き）/ fit（余白）/
  blur（ぼかし背景）を選べる。エンコーダは `auto` で NVENC が実際に使えるか1フレーム符号化して判定し、
  使えなければ libx264。音声は -14 LUFS に正規化（任意）。字幕は焼き込みと SRT/ASS を同じデータから出力。
- Blender はバックグラウンド起動で、同梱の固定スクリプト（`title_card`）だけを実行する（任意コードは受け付けない）。
  透過付き MOV（PNG コーデック）を素材として登録する。
- MCP（mcp_render, `uv run video-automation-mcp-render`）: `inspect_timeline`, `add_clip`, `trim_clip`,
  `move_clip`, `apply_edit`, `preview_timeline`, `validate_project`, `render_project`, `get_render_status`

## Phase 6 の要点

- 取得元: Openverse・Wikimedia Commons はキー不要。Pexels・Pixabay・Freesound は各サービスで無料の
  APIキーを取得し、「素材・BGM」タブの「APIキー設定」から登録する（Windows DPAPI で暗号化して保存。
  環境変数 `VA_PEXELS_API_KEY` / `VA_PIXABAY_API_KEY` / `VA_FREESOUND_API_KEY` でも可）。
- 検索結果は24時間キャッシュする（Pixabay の利用条件）。取得元ごとのエラー（キー不正・レート制限）は
  他の結果を止めずに `errors` で返す。
- ダウンロードは URL を受け取らず、取得元APIから取り直した URL のみ使う。https のみ・取得元ごとの
  許可ホスト・名前解決後のプライベートアドレス拒否（リダイレクト先も再検査）・サイズ上限・形式検査を行う。
- ライセンスは共通形式に正規化し、取得時点の内容を `asset_licenses` に保存する。CC0 / PD / CC BY /
  CC BY-SA / Pexels / Pixabay は「利用可」。不明・非営利限定（NC）・改変禁止（ND）等は「要確認」で、
  ユーザーが `acknowledge_license=true` で明示した場合のみ取得できる。タイムラインで使うと検証で注意が出る。
- LLM（MCP）からのダウンロードは承認待ち（`/assets/requests/{id}/approve`）。LLM はライセンスの明示承認をできない。
- Freesound は原音の取得に OAuth2 が必要なため、プレビュー音源（MP3 約128kbps）を取得する。
- Pixabay の通常キーで取得できる画像は最大 1280px（フル解像度は Pixabay の承認が必要）。
- `GET /projects/{id}/credits` で動画の概要欄に貼れるクレジット（素材・VOICEVOX・ACE-Step）を出力。
- BGM: ACE-Step 1.5 の `acestep-api`（既定 `127.0.0.1:8001`、別環境・別プロセス）に
  `/release_task` → `/query_result` → `/v1/audio` で依頼する。生成前にロード中の LLM をアンロード
  （VRAM 節約、`VA_MUSIC_UNLOAD_LLM`）。プロンプト・条件・モデル・シードを `music_generations` に記録。
  `POST /projects/{id}/timeline/add_bgm` で BGM トラックを追加（短い曲は繰り返して埋め、端をフェード）。
- MCP（mcp_media）: `search_assets`, `inspect_license`, `download_asset`, `generate_music`, `add_bgm`
- ACE-Step を VRAM 8GB の Windows で起動する場合（実機 RTX 5050 で確認）:
  ```powershell
  $env:ACESTEP_INIT_LLM = "false"   # 補助LM(5Hz LM)を載せない。載せると VAE デコードで VRAM が尽きて停止した
  $env:PYTHONUTF8 = "1"             # ログ出力を cp932 で書けず生成が失敗するのを防ぐ
  uv run acestep-api --host 127.0.0.1 --port 8001
  ```
  この構成で 20秒の BGM を約100秒で生成（初回はモデル読み込みを含む）。本システムは `thinking` を使わない。

## Phase 7 の要点

- アニメーションはクリップごとに `animation`（キーフレーム＋入りの切替）として持ち、編集命令
  `set_clip_animation` で設定する（Undo/Redo・LLM 承認・分割時の引き継ぎに対応）。
  - キーフレームの `frame` はクリップ先頭からの相対フレーム。各プロパティは指定したキーの間を
    `easing`（linear / ease_in / ease_out / ease_in_out / back_out / cubic_* / quart_* / expo_* /
    sine_in_out / hold）で補間し、範囲外は端の値を保つ。
  - 要素: `scale` `x` `y` `rotation` `opacity`、カメラ（素材の中）: `zoom` `pan_x` `pan_y`
  - 入りの切替 `transition_in`: dissolve / dip_to_black / slide_* / wipe_* / zoom_in。
    同じトラックの直前のクリップを切替の長さだけ延長して下に敷く。前が動画で素材の続きが足りないときは
    切替を外してカットにし、書き出しの注意に出す（統合版 E2）。
- 描画は FFmpeg の式に変換する（カメラ = zoompan、回転 = rotate、不透明度・ワイプ = geq を変化する区間だけ、
  位置 = overlay の式、拡大縮小 = scale のフレーム単位評価）。FFmpeg 6.1 では大きさが変わるフレームの後に
  geq・fade を置くと異常終了したため、拡大縮小は必ず最後に置く。
- プリセット（`GET /motion/presets`）: カメラ（ken_burns_in/out, pan_*, zoom_punch）、要素（fade_in/out,
  pop_in_soft / pop_in / pop_express、slide_in_*（画面の 1.7%・3% だけ動く）/ slide_in_*_far（画面の外から）/
  slide_out_*, zoom_enter, spin_in, float, shake）、切替（dissolve 8f・dip_to_black 36f）、文字。
  演出の自動配置では、入り・出の長さ・pop の種類・切替の上限を用途ごとに決める（`DesignProfile.motion`、統合版 2.1）。プリセットは秒単位で
  定義し、適用時にクリップの長さに合わせる（`duration_sec`・`intensity` で調整）。独自プリセットの登録・
  クリップからの保存も可能。
- 文字アニメーション（`POST /assets/title_card`）: pop / slide / zoom / typewriter / bounce / fade / none、
  縁取り・位置（中央・下部・上部）・半透明の帯。Blender は動く部分のフレームだけ描画し、残りは FFmpeg で
  最終フレームを延長してフェード・帯を付ける（描画時間の短縮）。
- 図表（`POST /assets/infographic`）: 横棒グラフ・数値のカウントアップ。
- シーン作成（`POST /projects/{id}/scenes`）: 背景（色・素材）＋タイトル＋カメラ演出＋切替を1回の編集命令で配置。
- MCP（mcp_visual, `uv run video-automation-mcp-visual`）: `create_scene`, `animate_image`, `animate_text`,
  `create_transition`, `create_infographic`, `set_camera_motion`, `list_motion_presets`, `get_job`
- 既存の DB に後の Phase で増えた列（NULL 可）が無い場合は起動時に追加する。

## Phase 8 の要点

- LLM 呼び出しは `LLMGateway` に集約: タスク別割当（または指定モデル）を解決し、JSON Schema 付きで生成
  （Ollama の `format`）→ `<think>` を除いて JSON を取り出し → jsonschema で検証 → 合わなければ誤りを
  伝えて 1 回だけ再試行。VRAM 節約のため他のモデルはアンロードする（`single_loaded_model`）。
- 台本作成（`/scripts/studio` ほか。旧形式の `POST /scripts` はそのまま）
  - モード: `FULL_AI`（全員分を生成）/ `USER_ONE_SIDE`（ユーザー担当キャラクターのセリフを入力し、相手役を
    生成）/ `MANUAL`。キャラクター（役割・性格・口調・話者 ID・抑揚）と設定（テーマ・目的・視聴者・目標尺・
    必須語・禁止事項・参考資料・応答方針・AI 側の分量）を持つ。
  - 固定セリフ: ユーザー入力（USER）・手修正（EDITED）・ロックしたセリフは AI の生成・再生成で変えない。
    全体の再生成では本文で照合して元の行を残し、モデルが落とした行は元の位置へ戻す。
  - 1 発話の再生成（`/dialogue/{turn}/regenerate`）は前後の会話を渡し、候補 1〜3 案。2 案以上は選んで採用。
  - 検証（`/validate`）: 話者・長さ・重複・必須語・禁止語・推定尺（文字数 ÷ 7 字/秒）・ユーザー入力の改変・
    AI 側の分量。`use_llm` で会話のつながり・口調の一貫性も LLM が指摘する。
  - 変更ごとに版を保存し、差分（追加・削除・変更・並べ替え）と復元ができる。生成はプロンプトの版
    （`script.full.v2` 等）・入力・出力を `generation_runs` に記録する。
  - キャラクターの抑揚設定は音声生成（`/scripts/{id}/voice`）に反映する。
- 対話型編集（`/conversations`）: 指示とプロジェクトの状態（ID・時間・素材・台本）を LLM に渡し、操作
  （`edit_timeline` / `generate_script` / `render_project` / `search_assets` / `download_asset`）の計画を
  JSON で受け取る。各操作をスキーマとタイムラインの dry-run で検証し、無効なら誤りを伝えて 1 回だけ修正
  させる。変更を伴う操作はすべて承認待ちになり、自然言語の出力をそのまま実行しない。
  `POST /conversations/messages/{id}/rerun` で別のモデルでやり直す（前の返答の未決定の提案は取り下げ）。
- 承認（`GET /projects/{id}/approvals`, `POST /approvals/{kind}/{id}`）: タイムライン変更・最終レンダリング・
  素材ダウンロード・台本修正案を一覧し、差分を見て承認/却下する。台本修正案は作成後に台本が変わっていれば
  適用しない。ポリシー（`/approvals/policy`）は仕様 §10 で「設定可能」の項目（素材ダウンロード、
  ライセンス不明素材の扱い）だけ変更できる。
- 履歴（`GET /projects/{id}/history`）: 適用・取り消し済みの編集を人が読める差分つきで表示し、
  `POST /projects/{id}/history/revert` で指定の時点まで戻す。
- MCP: mcp_script（`uv run video-automation-mcp-script`）`create_script`, `generate_full_script`,
  `set_user_script`, `generate_counterpart_dialogue`, `regenerate_dialogue`, `lock_dialogue_turn`,
  `validate_script`, `get_script_versions`（生成・登録は承認待ちの修正案になる）。mcp_core に
  `create_project`, `get_project_state`, `plan_edit`。

## Phase 9 の要点

- 品質検査（`POST /projects/{id}/qa` → Job、`GET /qa/{report_id}`、MCP `run_quality_check`）:
  - 音声: ピーク（音割れ）・ラウドネス・セリフ間の音量差・前後/途中の無音・読み上げ速度
  - 字幕: 未同期・重なり・音声区間からのはみ出し・表示時間・読む速さ・行数/文字数・ちらつき
  - 映像: 解像度・fps・コーデック・長さ・音声の有無・ラウドネス（-14 LUFS）・トゥルーピーク・黒画面・静止
  - ライセンス: 未確認・商用不可・改変禁止素材の加工・継承条件・必要なクレジット・記録の無い取り込み素材
  - 測定は FFmpeg の ebur128 / silencedetect / blackdetect / freezedetect を 1 回の実行で行う。
    結果は error（要修正）/ warning（要確認）/ info（参考）で、`qa_reports` に保存する。
- E2E: `tests/integration/test_e2e.py`（偽エンジン＋実 FFmpeg）、`tests/live/test_live_e2e.py`
  （実機。工程ごとの時間・GPU メモリ・RAM を `performance_report.md` に出力）。
- `video-automation --doctor`: FFmpeg・Blender・LLM・音声・ACE-Step・データフォルダを確認する。
- 配布物: `uv run python tools/build_release.py` → `dist/VideoAutomation-<版>-windows.zip`。展開して
  `install.bat`（uv で Python 3.12 と固定版の依存を導入、ショートカット作成、環境確認）。
- 台本・字幕タブで既存の台本（台本作成（AI）で作ったもの）を選んで音声・字幕にできる。

## ローカル LLM のスキル

Claude のスキルと同じ形（`<名前>/SKILL.md`。先頭に `name` と `description`）の決まり・手順を、
ローカル LLM にも使わせる。

- 同梱: `src/video_automation/skills/`（動画のデザイン・演出・VSE・Blender 5 の 5 本）。
- ユーザーのスキル: `data/skills/<名前>/SKILL.md`（設定 `VA_SKILLS_DIR` で変更可）。同じ名前なら
  同梱より優先するので、Claude で直したスキルをそのまま置けば使われる。置いたら `POST /skills/reload`。
- 小さいモデルでも使えるよう、スキル全体ではなく、依頼に関係する節だけを上限
  （`VA_SKILLS_BUDGET_CHARS`、既定 4000 文字）までプロンプトに入れる。節は見出しごとに分け、
  依頼文との語の重なり（BM25）で選ぶ。関係の薄い節は入れない。コード例は省く。
- 使う場面: 演出プランの作成（デザイン・演出の 3 本から、その用途に関係する節）と、
  AI アシスタント（直近の依頼に関係する節）。使った節は結果の `skills` に残る。
- プロンプトが長いときは Ollama の `num_ctx` を自動で広げる（上限 `VA_LLM_MAX_CONTEXT`、既定 16384）。
- API: `GET /skills`・`GET /skills/{名前}`・`POST /skills/search`・`POST /skills/reload`。
  MCP（mcp_core）: `list_skills`・`read_skill`・`search_skills`。
- 使わないときは `VA_SKILLS_ENABLED=false`。

## セットアップ

```powershell
uv sync --extra ui
uv run python tools/fetch_fonts.py   # 演出に使う自由な書体（OFL）を data/fonts に取ってくる
```

書体は SIL Open Font License 1.1 のものだけを google/fonts から取得する（リポジトリには入れない）。
一覧と演出との組み合わせ（曲の方向性・見た目の型など）は `services/rendering/font_catalog.py`、
取得後は `data/fonts/FONTS.md` にも書き出す。無い書体は既定の書体（メイリオなど）で代わりに描く。

絵の無い MV の下書きに使う仮の絵は、利用者が `data/placeholders` に PNG・JPEG を置く（自動では
取ってこない。素材サイトの絵を使うときは、そのサイトの規約を利用者が確かめる。いらすとやは
1 本の動画で 20 点まで）。曲の名前で 1 枚を選ぶので、同じ曲は同じ絵になる。使わないときは
構成の `placeholder` を false にする。本番は `image` で使いたい絵を指定する。

## 起動

```powershell
uv run video-automation            # API起動 + UI表示
uv run video-automation --headless # APIのみ (127.0.0.1:8765)
uv run video-automation-mcp        # MCPテストサーバー (stdio)
```

## テスト

```powershell
uv run pytest
# 実機の Ollama / LM Studio に接続するテスト（任意）
$env:VA_LIVE_TESTS="1"; uv run pytest tests/live -s
```

## 構成

`src/video_automation/` 以下に app / ui / api / core / database / schemas / mcp_servers。
UIはAPIクライアント経由でのみバックエンドに触れる。
