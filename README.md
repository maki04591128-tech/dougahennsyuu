# VideoAutomation

MCP対応の対話型動画編集自動化システム。仕様は [docs/VIDEO_AUTOMATION_SPEC.md](docs/VIDEO_AUTOMATION_SPEC.md) を参照。

現在の実装範囲:
- **Phase 1（基盤）** — 設定、ログ、SQLite、FastAPI、Job管理、イベントバス、PySide6基盤、MCPテストサーバー
- **Phase 2（LLM・モデル検出）** — Provider共通IF、Ollama / LM Studio / OpenAI互換 / OpenAI・Claude・Gemini Adapter、
  Capability判定（実測プローブ）、推論テスト、モデル設定UI、タスク別割当、ACE-Step（音楽生成Provider）検出
- **Phase 3（音声合成）** — VOICEVOX Nemo / VOICEVOX Engine 対応（エンジン機能を manifest で判定）、話者取得、
  音声生成・保存（WAV・ハッシュ・区間時刻）、読み・アクセント編集、抑揚調整、辞書（DB同期・復元）、試聴、音声タブUI

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
