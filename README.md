# VideoAutomation

MCP対応の対話型動画編集自動化システム。仕様は [docs/VIDEO_AUTOMATION_SPEC.md](docs/VIDEO_AUTOMATION_SPEC.md) を参照。

現在の実装範囲:
- **Phase 1（基盤）** — 設定、ログ、SQLite、FastAPI、Job管理、イベントバス、PySide6基盤、MCPテストサーバー
- **Phase 2（LLM・モデル検出）** — Provider共通IF、Ollama / LM Studio / OpenAI互換 / OpenAI・Claude・Gemini Adapter、
  Capability判定（実測プローブ）、推論テスト、モデル設定UI、タスク別割当、ACE-Step（音楽生成Provider）検出

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
