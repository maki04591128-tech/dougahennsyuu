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

## セットアップ

```powershell
uv sync --extra ui
```

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
