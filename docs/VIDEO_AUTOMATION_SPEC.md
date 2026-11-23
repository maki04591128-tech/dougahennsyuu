# 動画編集自動化システム 実装仕様書

- 仕様バージョン: 1.2
- 作成日: 2026-11-23
- 対象: Windows / Python
- 状態: 基本設計確定・Phase 1着手前

## 1. 目的・確定要件

MCP対応の対話型動画編集自動化システムを構築する。LLMが編集計画を作成し、検証済み編集命令をMCP経由で実行する。LLMと編集エンジン、UIと業務ロジックを分離する。

| 項目 | 仕様 |
|---|---|
| OS | Windows 10/11 64-bit |
| UI | PySide6デスクトップアプリ |
| Backend | Python + FastAPI |
| AI制御 | Orchestrator + MCP |
| ローカルLLM検出 | auto：自動検出 + 手動API登録 |
| 優先ローカル環境 | Ollama、LM Studio |
| その他LLM | OpenAI互換API、OpenAI、Claude、Gemini |
| 編集エンジン | Blender + FFmpeg |
| 音声合成 | VOICEVOX Nemo（実機API検証前提） |
| 音楽生成 | ACE-Step 1.5 |
| 字幕 | 焼き込み + 外部字幕、ASS/SRT |
| 出力 | MP4、FHD/4K、16:9/9:16 |
| 編集方式 | 対話型、プレビュー、承認、差分、Undo/Redo |
| 素材 | フリー画像・動画・効果音検索、取得、ライセンス記録 |
| 音声機能 | 抑揚・イントネーション、読み修正、辞書登録 |
| 演出 | 字幕・画像アニメーション、シーン切替 |

### 出力プロファイル

| 名称 | 解像度 | 比率 |
|---|---:|---:|
| FHD Landscape | 1920×1080 | 16:9 |
| FHD Portrait | 1080×1920 | 9:16 |
| 4K Landscape | 3840×2160 | 16:9 |
| 4K Portrait | 2160×3840 | 9:16 |

縦横比変換は単純クロップを標準にせず、主題位置・字幕セーフエリア・焦点位置を考慮した再レイアウトを可能にする。

## 2. ローカルAIモデル

| インストール済みモデル（申告名） | 分類 | 初期用途候補 |
|---|---|---|
| Qwen3.5-9B | テキストLLM候補 | 編集計画、構造化命令、ツール選択 |
| Swallow 8B | テキストLLM候補 | 日本語台本、字幕、文章校正 |
| Gemma 4 E4B | LLM候補 | 台本、対応環境での画像等の内容理解 |
| ACE-Step 1.5 | 音楽生成 | BGM生成 |

モデル名は表示名として扱い、実モデルID・量子化形式・推論エンジンは検出結果を正とする。ACE-StepはLLM Providerに混在させず、Music Generation Providerとして独立管理する。上記のタスク割当は仮設定であり、実機テストで確定する。

### 自動検出

- Ollama：既定ポート11434の /api/tags
- LM Studio：既定ポート1234の /api/v1/models または /v1/models
- llama.cpp、vLLM、LocalAI：登録URLの /v1/models
- その他：URL・API形式を手動登録
- Windowsプロセス検出は補助情報。プロセスの存在だけで推論可能と判定しない。
- 無制限なポートスキャンは行わない。

モデル状態は INSTALLED、SERVER_AVAILABLE、MODEL_DISCOVERED、MODEL_LOADED、INFERENCE_READY に分離する。ロード状態をAPIから確認できない場合はUNKNOWNとする。モデルファイルが存在するだけでは利用可能とみなさない。

### Provider共通機能

discover、list_models、check_connection、get_capabilities、generate、stream、load_model、unload_modelを共通インターフェースとして定義する。Capabilityはテキスト・画像・音声入力、ストリーミング、構造化出力、ツール呼び出し、コンテキスト長等を含め、実機テストで確認する。

UIには接続状態、モデル一覧、実モデルID、ロード状態、推論テスト、メインモデル選択、タスク別割当、自動選択、OpenAI互換API手動登録を設ける。APIキーはWindows DPAPI等で保護し、DBに平文保存しない。各社Web契約とAPI契約は別扱い。

## 3. アーキテクチャ

構成：PySide6 UI → FastAPI → Orchestrator / Provider Manager / MCP Gateway → Project Core / Domain Services → Blender・FFmpeg・VOICEVOX Nemo・ACE-Step・素材API。SQLiteはプロジェクト情報の正本、メディア実体はファイル保存とする。

| コンポーネント | 責務 |
|---|---|
| UI | 表示、入力、承認、プレビュー。DBや外部エンジンを直接操作しない |
| API | 入力検証、Job管理、イベント配信 |
| Orchestrator | 会話、タスク分解、LLM選択、承認 |
| Provider Manager | クラウド・ローカルLLM、音声・音楽Provider |
| MCP Gateway | ツール登録、権限、引数・結果検証 |
| Project Core | プロジェクト、タイムライン、編集命令、履歴の正本 |
| Services | 音声、字幕、素材、アニメーション、レンダリング |
| Integrations | 外部アプリ・APIのバージョン差を吸収 |

### MCPサーバー

| サーバー | 担当 |
|---|---|
| mcp_core | Project、LLM、編集計画 |
| mcp_media | 素材、音声、字幕 |
| mcp_visual | シーン、画像・文字アニメーション |
| mcp_render | タイムライン、Blender、FFmpeg、レンダリング |

初期は4サーバーに集約。MCPは内部サービスを呼び出すアダプターとし、編集ロジックを重複実装しない。

### セキュリティ

- LLMに任意OSコマンド、任意Pythonコード、無制限ファイルアクセスを許可しない。
- 全編集操作を共通Command Schemaで検証する。
- ファイル操作は許可ディレクトリに制限する。
- 高リスク操作はユーザー承認を要求する。
- ローカルAPIは原則localhostにバインドする。
- 素材の取得元・取得日時・ライセンス条件を保存する。
- APIキーやトークンをログに出力しない。

## 4. Pythonディレクトリ構成

~~~text
VideoAutomation/
├── pyproject.toml / uv.lock / README.md / .env.example
├── src/video_automation/
│   ├── app/ (main.py, bootstrap.py, settings.py)
│   ├── ui/ (main_window.py, chat/, timeline/, preview/, llm_settings/, asset_browser/)
│   ├── api/ (app.py, routes/, websocket/, dependencies.py)
│   ├── core/ (orchestrator/, project/, timeline/, commands/, history/, jobs/, events/)
│   ├── providers/ (base.py, registry.py, llm/, audio_generation/, speech/)
│   ├── services/ (model_discovery/, voice/, subtitle/, assets/, animation/, rendering/)
│   ├── integrations/ (blender/, ffmpeg/, voicevox/, acestep/, asset_sources/)
│   ├── mcp_servers/ (core_server.py, media_server.py, visual_server.py, render_server.py)
│   ├── database/ (models.py, repositories/, database.py, migrations/)
│   └── schemas/ (project.py, timeline.py, command.py, provider.py, render.py)
├── tests/ (unit/, integration/, e2e/)
├── scripts/ (setup.ps1, start.ps1, doctor.ps1)
└── data/ (database/, projects/, assets/, cache/, dictionaries/, logs/)
~~~

依存方向：UI→API Client、API→Core/Services、Core→Domain/Schema/抽象Provider、Services→Core抽象/Integrations、Integrations→外部アプリ・API、MCP Servers→Core/Services。PySide6・Blender固有API・FFmpegコマンドをDomain/Coreへ漏らさない。

## 5. データベース設計

SQLite + SQLAlchemy 2.x + Alembic。メディア実体はファイル保存し、DBにはパス・ハッシュ・メタデータを保存する。時間はフレーム整数、FPSは分子・分母（例：30000/1001）で管理する。APIキーは平文保存しない。ライセンスは取得時点の情報をスナップショット保存する。

テーブル：projects、project_settings、scenes、tracks、clips、assets、asset_sources、asset_licenses、scripts、script_segments、speakers、audio_files、voice_settings、pronunciation_dictionary、subtitles、subtitle_styles、animations、transitions、llm_providers、llm_models、llm_task_assignments、conversations、edit_commands、edit_history、render_jobs、render_profiles、system_settings。

Clipは id、track_id、asset_id、start_frame、end_frame、source_in_frame、source_out_frame、layer、transformを持つ。start/endはタイムライン位置、source_in/outは元素材範囲。時間区間は半開区間（endを含まない）に統一する。

主な関係：Project 1:N Scenes/Tracks/Assets/Scripts/Conversations/EditCommands/RenderJobs、Scene 1:N Clips、Track 1:N Clips、Asset 1:N Clips/AssetSources/AssetLicenses、Script 1:N ScriptSegments、ScriptSegment 1:N Subtitles/AudioFiles、Provider 1:N Models。

## 6. REST API

Base URL: http://127.0.0.1:8765/api/v1/

| Method | Endpoint | 用途 |
|---|---|---|
| GET | /health | システム状態 |
| GET | /providers | Provider一覧 |
| POST | /providers/discover | ローカル環境検出 |
| POST | /providers/register | API接続先登録 |
| GET | /models | モデル一覧 |
| POST | /models/test | 推論テスト |
| PUT | /models/active | メインモデル変更 |
| PUT | /models/task-assignment | タスク別割当 |
| GET | /models/capabilities | モデル機能取得 |
| GET/POST | /projects | 一覧・作成 |
| GET/PUT | /projects/{id} | 詳細・設定更新 |
| GET | /projects/{id}/timeline | タイムライン |
| POST | /projects/{id}/commands | 編集命令 |
| POST | /projects/{id}/commands/validate | 命令検証 |
| POST | /projects/{id}/preview | プレビュー |
| POST | /projects/{id}/undo | Undo |
| POST | /projects/{id}/redo | Redo |
| GET | /voice/engines | 音声エンジン |
| GET | /voice/speakers | 話者一覧 |
| POST | /voice/generate | 音声生成 |
| POST | /voice/prosody | 抑揚調整 |
| POST | /voice/dictionary | 読み辞書 |
| POST | /subtitles/generate | 字幕生成 |
| POST | /subtitles/synchronize | 字幕同期 |
| POST | /subtitles/export | ASS/SRT出力 |
| POST | /assets/search | 素材検索 |
| POST | /assets/download | 素材取得 |
| POST | /assets/license/inspect | ライセンス確認 |
| POST | /render | レンダリング開始 |
| GET | /jobs/{id} | Job状態 |
| POST | /jobs/{id}/cancel | Job中止 |
| GET | /render/profiles | 出力プロファイル |

Pydanticで検証。長時間処理はJob IDを返す。Job状態はPENDING/RUNNING/SUCCEEDED/FAILED/CANCELLED。進捗はWebSocketで通知する。編集命令は一意IDと冪等性キーを持つ。エラー形式を統一し、機密情報を含めない。

## 7. MCPツール

- Core：discover_models、select_model、create_project、get_project_state、plan_edit
- Media：search_assets、inspect_license、download_asset、generate_voice、adjust_prosody、register_pronunciation、generate_subtitles、export_subtitles、generate_music
- Visual：create_scene、animate_image、animate_text、create_transition、create_infographic、set_camera_motion
- Render：inspect_timeline、add_clip、trim_clip、move_clip、apply_edit、preview_timeline、validate_project、render_project、get_render_status

実行順：User Request → Orchestrator/LLM → Structured Edit Command → Schema Validation → Permission Check → Project State Validation → Approval Check → Core Execution → History Save → Result/Preview。LLMの自然言語出力をそのまま実行しない。

## 8. 音声・字幕

VOICEVOX Nemoは通常VOICEVOX EngineとAPI・話者・機能が完全一致すると仮定しない。起動時にAPI仕様とCapabilityを検査する。話者一覧、Audio Query相当、読み・アクセント句編集、モーラ長・高さ調整、音声合成、辞書、プレビューを実機で確認し、未対応機能は利用不可にする。

台本セグメントと音声セグメントを共通IDで関連付ける。音声の実時間・区間情報で字幕を同期する。ASSはスタイル・位置・アニメーション、SRTは汎用外部字幕に用いる。焼き込みと外部字幕は同じ字幕データから生成し、表示区間をフレーム境界に正規化する。

## 9. 編集エンジン・音楽生成

- FFmpeg：カット・結合、音声連結・音量・フェード、字幕焼き込み、エンコード、簡易画像ズーム、最終MP4生成。
- Blender：複雑な文字・画像アニメーション、3Dカメラ、モーショングラフィックス、高度な合成、シーン演出。
- Blenderは別プロセス。Python API/VSEのバージョン差をIntegration層で吸収し、バージョン固定と互換テストを行う。LLM生成コードを無制限に実行しない。
- ACE-Step 1.5：音楽生成専用Provider。API・モデルロード・生成状態を検出し、プロンプト・条件・モデル情報・出力ファイル・利用条件を記録する。PyTorch等の依存競合回避のため別環境・別プロセスで実行する。

## 10. 対話型編集と承認

フロー：自然言語指示 → Orchestratorが会話・Project状態取得 → LLMが計画生成 → 構造化Command化 → Schema/権限/状態検証 → 必要な承認 → MCP実行 → 差分・履歴保存 → プレビュー → 修正指示。

| 操作 | 初期承認 |
|---|---|
| 台本修正案 | 必要 |
| 音声試作 | 不要 |
| 音声本生成 | 設定可能 |
| 素材検索 | 不要 |
| 素材ダウンロード | 設定可能 |
| ライセンス不明素材の使用 | 禁止または明示承認 |
| タイムライン変更 | 必要 |
| 字幕変更 | 必要 |
| プレビュー | 不要 |
| プロジェクト上書き | 必要 |
| 最終レンダリング | 必要 |

## 11. 開発フェーズと依存関係

~~~text
Phase 1 基盤
  ├─ Phase 2 LLM・モデル検出
  │    ├─ Phase 3 音声合成
  │    ├─ Phase 6 素材・音楽生成
  │    └─ Phase 8 対話型編集
  ├─ Phase 5 動画編集 ── Phase 7 アニメーション
  └─ Phase 3 音声合成 ── Phase 4 字幕 ── Phase 5 動画編集
Phase 4～8統合 ── Phase 9 品質検査
~~~

Phase 3とPhase 5は基盤完成後に並行開発可能。Phase 4は音声時間情報に依存する。

### Phase 1：基盤構築

| ID | タスク | 成果物 | 依存 |
|---|---|---|---|
| P1-01 | Python/uv環境構築 | 開発環境 | なし |
| P1-02 | Git・ディレクトリ構築 | 初期リポジトリ | P1-01 |
| P1-03 | 設定管理 | Settings | P1-01 |
| P1-04 | ログ基盤 | Logging | P1-01 |
| P1-05 | DB接続・Migration | SQLite/Alembic | P1-01 |
| P1-06 | 共通Schema | Pydantic Models | P1-01 |
| P1-07 | FastAPI基盤 | API Server | P1-03、P1-06 |
| P1-08 | PySide6基盤 | Desktop UI | P1-03 |
| P1-09 | UI/API接続 | API Client | P1-07、P1-08 |
| P1-10 | MCP基盤 | MCP Test Server | P1-06、P1-07 |
| P1-11 | Job管理 | Async Job Manager | P1-07 |
| P1-12 | テスト基盤 | pytest | P1-02 |

Phase 1完了条件：Windowsで起動、PySide6画面表示、localhost:8765応答、UI/API接続、SQLite保存・取得、MCPテストツール実行、ログ・例外処理、pytest成功。

### Phase 2：LLM・モデル検出

| ID | タスク | 成果物 | 依存 |
|---|---|---|---|
| P2-01 | Provider Interface | 共通API | P1 |
| P2-02 | Ollama検出 | Ollama Adapter | P2-01 |
| P2-03 | LM Studio検出 | LM Studio Adapter | P2-01 |
| P2-04 | OpenAI互換API | Generic Adapter | P2-01 |
| P2-05 | OpenAI/Claude/Gemini | Cloud Adapters | P2-01 |
| P2-06 | Capability判定 | Capability Manager | P2-02～05 |
| P2-07 | 推論テスト | Inference Test | P2-06 |
| P2-08 | モデル選択UI | Model Settings | P2-07 |
| P2-09 | タスク別割当 | Task Router | P2-08 |

完了条件：ローカル4モデルについて実環境で検出できるモデルを識別し、利用可能モデルを選択して推論テストできる。ACE-Stepは別Providerとして検出する。

### Phase 3：音声合成

| ID | タスク | 成果物 | 依存 |
|---|---|---|---|
| P3-01 | Nemo API調査 | Engine Capability | P1 |
| P3-02 | 話者取得 | Speaker Manager | P3-01 |
| P3-03 | 音声生成 | Audio Generator | P3-02 |
| P3-04 | 読み・アクセント管理 | Pronunciation Manager | P3-02 |
| P3-05 | 抑揚調整 | Prosody Controller | P3-03 |
| P3-06 | 辞書登録 | Dictionary Manager | P3-04 |
| P3-07 | 音声プレビュー | Audio Preview | P3-03 |
| P3-08 | 音声メタデータ保存 | Audio Repository | P1、P3-03 |

### Phase 4：字幕

| ID | タスク | 成果物 | 依存 |
|---|---|---|---|
| P4-01 | 台本分割 | Script Segmenter | P2 |
| P4-02 | 字幕生成 | Subtitle Generator | P4-01 |
| P4-03 | 音声時間取得 | Timing Analyzer | P3 |
| P4-04 | 字幕同期 | Synchronizer | P4-02、P4-03 |
| P4-05 | ASS出力 | ASS Generator | P4-04 |
| P4-06 | SRT出力 | SRT Generator | P4-04 |
| P4-07 | 字幕スタイル | Subtitle Style | P4-05 |

### Phase 5：動画編集

| ID | タスク | 成果物 | 依存 |
|---|---|---|---|
| P5-01 | FFmpeg接続 | FFmpeg Adapter | P1 |
| P5-02 | Blender接続 | Blender Adapter | P1 |
| P5-03 | タイムライン | Timeline Manager | P1 |
| P5-04 | 映像・音声配置 | Clip Manager | P5-03 |
| P5-05 | カット・トリミング | Editing Commands | P5-04 |
| P5-06 | 16:9/9:16対応 | Layout Manager | P5-03 |
| P5-07 | FHD/4K対応 | Render Profiles | P5-01 |
| P5-08 | 字幕焼き込み | Subtitle Renderer | P4-05、P5-01 |
| P5-09 | 音声ミックス | Audio Mixer | P3、P5-01 |
| P5-10 | MP4出力 | Render Manager | P5-05～09 |

### Phase 6：素材管理・音楽生成

| ID | タスク | 成果物 | 依存 |
|---|---|---|---|
| P6-01 | Asset Provider Interface | Asset Provider | P1 |
| P6-02 | 画像・動画検索 | Media Search | P6-01 |
| P6-03 | 効果音検索 | Sound Search | P6-01 |
| P6-04 | ダウンロード | Asset Downloader | P6-02、03 |
| P6-05 | ライセンス管理 | License Manager | P6-04 |
| P6-06 | 素材管理UI | Asset Browser | P6-04 |
| P6-07 | ACE-Step接続 | Music Provider | P1 |
| P6-08 | BGM生成 | Music Generator | P6-07 |

### Phase 7：アニメーション

| ID | タスク | 成果物 | 依存 |
|---|---|---|---|
| P7-01 | Animation Schema | 定義形式 | P1 |
| P7-02 | 文字アニメーション | Text Animation | P7-01 |
| P7-03 | 画像アニメーション | Image Animation | P7-01 |
| P7-04 | シーン切替 | Transition Manager | P5 |
| P7-05 | カメラ演出 | Camera Motion | P5 |
| P7-06 | プリセット管理 | Motion Presets | P7-02～05 |

### Phase 8：対話型編集

| ID | タスク | 成果物 | 依存 |
|---|---|---|---|
| P8-01 | 会話管理 | Conversation Manager | P2 |
| P8-02 | 編集計画 | Task Planner | P8-01 |
| P8-03 | 命令変換 | Command Generator | P8-02 |
| P8-04 | 命令検証 | Command Validator | P8-03 |
| P8-05 | 承認管理 | Approval Manager | P8-04 |
| P8-06 | MCP実行統合 | Tool Router | P8-05 |
| P8-07 | 差分表示 | Edit Diff | P8-06 |
| P8-08 | Undo/Redo | History Manager | P8-06 |
| P8-09 | LLM切替・再実行 | Model Switch Workflow | P2、P8-01 |

### Phase 9：統合・品質検査

| ID | タスク | 成果物 | 依存 |
|---|---|---|---|
| P9-01 | E2Eテスト | E2E Suite | P8 |
| P9-02 | 音声品質検査 | Audio QA | P3 |
| P9-03 | 字幕同期検査 | Subtitle QA | P4 |
| P9-04 | 映像品質検査 | Video QA | P5、P7 |
| P9-05 | ライセンス検査 | License QA | P6 |
| P9-06 | 性能検査 | Performance Report | 全体 |
| P9-07 | Windows配布物 | Installer | 全体 |
| P9-08 | 運用ドキュメント | User Manual | 全体 |

## 12. Phase 1 実装詳細

### 技術選定

| 項目 | 採用 |
|---|---|
| Python | 3.12.x 64-bit |
| Package Manager | uv |
| UI | PySide6 |
| API | FastAPI + Uvicorn |
| HTTP Client | HTTPX |
| DB | SQLite |
| ORM | SQLAlchemy 2.x |
| Migration | Alembic |
| Validation | Pydantic 2.x |
| Async | asyncio |
| MCP | MCP Python SDK |
| Logging | structlog |
| Test | pytest、pytest-asyncio、pytest-cov |
| Lint/Format | Ruff |
| Type Check | Pyrightまたはmypy |

Windows上で依存関係を解決しuv.lockで固定する。ACE-StepはPyTorch等の依存競合を避けるため別環境・別プロセスにする。Blenderも独自Python環境で動かす。

### 初期セットアップ例（PowerShell）

~~~powershell
mkdir VideoAutomation
cd VideoAutomation
git init
uv init --package --python 3.12
uv add pyside6 fastapi "uvicorn[standard]" httpx sqlalchemy alembic pydantic pydantic-settings aiosqlite structlog mcp
uv add --dev pytest pytest-asyncio pytest-cov ruff mypy
~~~

### Phase 1で作成するファイル

- app/main.py：アプリ起動
- app/bootstrap.py：コンポーネント初期化
- app/settings.py：設定読み込み
- ui/main_window.py：メインウィンドウ
- ui/api_client.py：API通信
- api/app.py：FastAPI初期化
- api/routes/health.py：ヘルスチェック
- core/events/event_bus.py：イベント通知
- core/jobs/job_manager.py：非同期Job管理
- database/database.py：DB接続
- database/models.py：初期テーブル
- mcp_servers/core_server.py：MCPテストサーバー

### 初期API

GET /api/v1/health

~~~json
{
  "status": "ok",
  "application": "VideoAutomation",
  "database": "connected",
  "version": "0.1.0"
}
~~~

POST /api/v1/projects のリクエスト例：

~~~json
{
  "name": "Test Project",
  "width": 1920,
  "height": 1080,
  "fps_num": 30,
  "fps_den": 1
}
~~~

GET /api/v1/projects/{project_id}

初期段階ではプロジェクト基本情報の保存・取得までを実装し、Scene、Track、Clipは後続で追加する。

### 初期DBモデル例

~~~python
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import String, Integer, DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase):
    pass

class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False, default=1920)
    height: Mapped[int] = mapped_column(Integer, nullable=False, default=1080)
    fps_num: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    fps_den: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
~~~

### Phase 1テスト

| テスト | 合格条件 |
|---|---|
| アプリ起動 | PySide6ウィンドウ表示 |
| API起動 | localhost:8765で応答 |
| DB接続 | SQLite接続成功 |
| DB保存・取得 | Projectを保存・取得可能 |
| UI通信 | UIからAPI接続可能 |
| MCP接続 | テストツール呼び出し可能 |
| ログ | 例外・処理結果を記録 |
| テスト | pytest成功 |

## 13. 概算スケジュール

| Phase | 内容 | 推定期間 |
|---|---|---:|
| 1 | 基盤構築 | 1～2週間 |
| 2 | LLM統合 | 1～2週間 |
| 3 | 音声合成 | 1～2週間 |
| 4 | 字幕 | 1週間 |
| 5 | 動画編集 | 2～3週間 |
| 6 | 素材・音楽生成 | 1～2週間 |
| 7 | アニメーション | 2～3週間 |
| 8 | 対話型編集 | 2～3週間 |
| 9 | 統合・品質検査 | 2～4週間 |
| **合計** | | **13～22週間** |

個人開発・既存API利用を前提とした概算。外部エンジンの仕様差、演出品質、UIの作り込み、実機性能で変動する。

## 14. 未確定事項・実装前調査

| 項目 | 未確認内容 | 影響 |
|---|---|---|
| PCハードウェア | CPU、GPU、VRAM、RAM | ローカルLLM同時稼働、ACE-Step性能 |
| 推論サーバー | Ollama/LM Studio導入・稼働状況 | 自動検出の実機テスト |
| Swallow 8B | 正確なモデルID・量子化 | 機能・性能判定 |
| Nemo | 実行方法、API、対応機能 | 発音・抑揚・辞書実装 |
| Blender | バージョン | VSE・Python API互換 |
| 素材サービス | 提供元・APIキー・利用条件 | 検索・取得・ライセンス |

ハードウェア情報が得られるまでは複数モデルの同時ロードを前提にしない。モデル切替・逐次実行を基本とし、VRAMとRAMを測定して同時稼働を判断する。

## 15. 公式ドキュメント

- MCP Python SDK: https://py.sdk.modelcontextprotocol.io/
- OpenAI API: https://platform.openai.com/docs/
- Anthropic API: https://docs.anthropic.com/
- Gemini API: https://ai.google.dev/gemini-api/docs
- Ollama API: https://docs.ollama.com/api
- LM Studio API: https://lmstudio.ai/docs/api
- VOICEVOX Engine: https://github.com/VOICEVOX/voicevox_engine
- Blender: https://docs.blender.org/api/current/
- Blender MCP Lab: https://www.blender.org/lab/mcp-server/
- FFmpeg: https://ffmpeg.org/documentation.html
- ACE-Step: https://github.com/ace-step/ACE-Step

## 16. Phase 1着手順

1. Windows上のPython 3.12・uv環境構築
2. Gitリポジトリとsrc構成の初期化
3. 設定・ログ・例外処理
4. SQLite・SQLAlchemy・Alembic
5. FastAPI起動とヘルスチェック
6. PySide6メインウィンドウ
7. UI/API通信
8. MCPテストサーバー
9. pytestによる統合テスト

Phase 1の完了を確認してからPhase 2以降へ進む。

## 17. 対話型台本作成モード

動画の台本作成では、次の2モードを提供する。プロジェクト作成時または台本作成開始時にユーザーが選択でき、作成途中でもモード変更できる設計とする。

### 17.1 モードA：AIによる全台本自動生成

ユーザーが動画のテーマ・目的・長さ・キャラクター設定・話し方・構成などを指定し、AIが全キャラクターの台本を生成する。

**入力情報**
- 動画テーマ、目的、想定視聴者
- 動画の目標尺、構成、テンポ
- 登場キャラクター、役割、性格、口調、関係性
- 必要に応じて参考資料、禁止事項、必須キーワード
- 出力する台本の粒度（セリフ単位、シーン単位等）

**生成処理**
1. 入力内容から動画構成・シーン案を作成する。
2. キャラクターごとの役割と会話方針を定義する。
3. 全キャラクターのセリフを生成する。
4. 会話の自然さ、情報の正確性、重複、矛盾、尺を検査する。
5. シーン・話者・セリフ単位に構造化して保存する。
6. ユーザーが確認・修正した後、音声合成・字幕生成工程へ渡す。

### 17.2 モードB：片方のキャラクターの台本を入力し、相手役をAIが生成

ユーザーが指定した一方のキャラクターのセリフを入力し、AIがもう一方のキャラクターのセリフを生成する。入力済みセリフは原文を保持し、ユーザーの明示的な指示なしに書き換えない。

**入力情報**
- キャラクターA・Bの設定（名前、役割、性格、口調）
- ユーザーが担当するキャラクター
- ユーザーが入力したセリフ（順序・シーン情報を含められる）
- AI側キャラクターの応答方針（補足、質問、ツッコミ、解説等）
- 必要に応じてテーマ、前提資料、動画尺、会話のテンポ

**生成処理**
1. ユーザー担当キャラクターとAI担当キャラクターを確定する。
2. 入力セリフを順序どおりに固定し、発話意図・文脈・話題を解析する。
3. AI担当キャラクターの応答を、入力セリフごとまたは会話のまとまりごとに生成する。
4. 必要に応じて、相づち・質問・説明・反論・まとめ等を挿入する。
5. 会話の流れ、キャラクター設定、事実関係、重複、尺を検査する。
6. ユーザー入力部分とAI生成部分を明確に区別して表示・保存する。
7. ユーザーが応答だけを再生成・修正できる状態で確定し、音声合成・字幕生成工程へ渡す。

**重要な制約**
- ユーザー入力セリフは固定領域として扱い、AIの再生成対象から除外する。
- AI生成セリフは入力セリフとの対応関係を保持する。
- 1つの入力に対して複数の応答案を生成し、選択できる拡張性を持たせる。
- 入力セリフの順序変更・削除・修正はユーザー操作として記録する。
- 文脈が不足している場合、AIは不足情報を推測で確定せず、質問または仮定を明示する。
- AI側の発話量・説明量・会話比率を調整可能にする。

### 17.3 共通台本データモデル

台本は単一の長文ではなく、シーン・発話ターン・セリフに分割して管理する。

| データ | 主な項目 |
|---|---|
| Script | id、project_id、mode（FULL_AI / USER_ONE_SIDE）、topic、target_duration、status、version |
| Character | id、script_id、name、role、personality、speaking_style、voice_id |
| DialogueTurn | id、script_id、scene_id、sequence、speaker_id、generation_source（USER / AI / EDITED）、reply_to_turn_id、text、locked |
| Scene | id、script_id、sequence、purpose、estimated_duration |
| GenerationRun | id、script_id、model_provider_id、model_id、prompt_version、input_snapshot、output_snapshot、status |

- `generation_source`はユーザー入力・AI生成・生成後の手修正を識別する。
- `locked=true`の発話は、通常のAI再生成・整形処理で変更しない。
- `reply_to_turn_id`でAI応答と元のユーザー入力を関連付ける。複数ターンにまたがる応答では別途context参照を保持する。
- 台本の変更履歴を保存し、過去バージョンへ戻せるようにする。
- 既存のScript / ScriptSegmentテーブルはこの構造に合わせて拡張する。DialogueTurnはScriptSegmentの拡張または独立テーブルとして実装時に選定し、同じ発話データを二重管理しない。

### 17.4 UI要件

- 台本作成開始時に「AIが全台本を作成」「片方の台本を入力して相手役をAI生成」を選択できる。
- モードBではキャラクターA/Bのどちらをユーザーが担当するか選択できる。
- モードBの編集画面は、ユーザー入力セリフとAI生成セリフを視覚的に区別する。
- セリフ単位・会話ブロック単位でAI生成、再生成、編集、固定を操作できる。
- AI生成時に対象範囲を指定でき、固定済みのユーザー入力セリフを変更しない。
- 台本全体のプレビューと、キャラクター別の発話一覧を切り替えられる。
- 各セリフに話者・音声設定・推定読み上げ時間を紐付ける。

### 17.5 MCP / API追加要件

**MCPツール**
- `create_script`：モード・テーマ・キャラクター設定から台本を作成
- `generate_full_script`：全キャラクターの台本を生成
- `set_user_script`：ユーザー担当キャラクターと入力セリフを登録
- `generate_counterpart_dialogue`：相手役のセリフを生成
- `regenerate_dialogue`：指定したAI生成セリフのみ再生成
- `lock_dialogue_turn`：指定セリフを固定
- `validate_script`：会話整合性・キャラクター一貫性・尺を検査
- `get_script_versions`：台本の履歴を取得

**REST API**
- POST `/scripts`：台本作成
- GET `/scripts/{id}`：台本取得
- PUT `/scripts/{id}`：台本設定更新
- POST `/scripts/{id}/generate`：指定モードで生成
- POST `/scripts/{id}/dialogue`：ユーザーセリフ登録
- POST `/scripts/{id}/dialogue/{turn_id}/regenerate`：対象AIセリフ再生成
- PUT `/scripts/{id}/dialogue/{turn_id}/lock`：セリフ固定
- POST `/scripts/{id}/validate`：台本検証
- GET `/scripts/{id}/versions`：履歴取得

### 17.6 開発フェーズへの反映

- Phase 2：台本生成用のLLMタスク定義、構造化出力、モデル別プロンプト管理を追加。
- Phase 4：DialogueTurn単位で字幕を生成し、話者・発話順・音声時間を保持。
- Phase 8：2つの台本モード、セリフ単位の再生成、固定領域、台本差分表示を実装。
- Phase 9：全台本生成と片側入力生成のE2Eテスト、固定セリフ不変テスト、会話整合性テストを追加。

**受け入れ条件**
1. モードAで複数キャラクターの台本を一括生成できる。
2. モードBでユーザーが担当するキャラクターを選び、入力セリフを登録できる。
3. AIが相手役の応答を生成し、入力セリフとの対応関係を保持できる。
4. AI生成部分だけを再生成でき、固定されたユーザー入力セリフが変化しない。
5. 台本を話者別・シーン別に確認・編集でき、音声合成・字幕生成へ引き渡せる。
