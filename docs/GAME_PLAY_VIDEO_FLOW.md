# AIが遊ぶ → 動画にする → YouTubeに上げる（ゲーム実況の流れ）

対象: 「わくわく☆生活シミュレーター♪」（リポジトリ gameyou）を Claude が遊び、その録画とプレイ記録・感想から
この動画編集ツールで実況動画を作り、YouTube に上げる。2026-11-27 作成。

## 1. 全体の流れ

| 段 | すること | 誰・何で | 状態 |
|---|---|---|---|
| 1 遊ぶ | 開発用ビルドを `play_launch` で起動し、`play_*` の道具で遊ぶ。遊ぶ間は Discord で配信（gameyou `kb/1_guide/claude_play.md` §5） | Claude ＋ MCP `wakuwakuPlay` | 【断定】あり |
| 2 録画 | ゲームの窓を録画する（映像＋ゲームの音） | 未定（§3 の決めること） | 【未確定】なし |
| 3 プレイ記録 | 遊びながら「録画の頭からの時刻・起きたこと・感想」を残す | Claude | 【未確定】なし（書式は §2.1） |
| 4 台本 | プレイ記録から実況の掛け合いを書く | 未定（§3） | 一部あり（ツールの台本作成 AI） |
| 5 動画にする | `/build/plan`（指示＋`screen_recording`＋`reference`）→ 構成の確認 → `/build/{id}/run` | このツール | **今回の手直しで用途「ゲーム実況」で作れる**（§2.2） |
| 6 題名・説明・サムネイル | 題名・説明（クレジット `GET /projects/{id}/credits` を含む）・タグ・サムネイル画像 | Claude ＋ このツール | 【未確定】サムネイルの作り方は未定 |
| 7 上げる | `tools/youtube_upload.py upload … --confirm`。既定は**非公開**、公開は本人が YouTube Studio で行う | このツール | **今回追加**（§2.3） |

## 2. 今回入れたもの

### 2.1 プレイ記録の書式（`reference` に入れる）
```
プレイ記録（時刻は録画の頭から）:
[0:12] 迷宮の1階に入った。暗くて敵の姿が見えない — 感想: 文字だけの画面なのに緊張する
[3:40] スライムを3匹倒してレベル2 — 感想: …
```
- 見出し `プレイ記録（時刻は録画の頭から）:` があると、録画を画像 AI に読ませる処理を省き、この記録を台本の参考に使う
  （`services/build/recording_reader.py` `PLAY_LOG_HEAD`、`service.py` `_read_recording`）
- `reference` は 20,000 字まで

### 2.2 録画つきでもゲーム実況のまま作る（`services/build/service.py`）
- 以前: 画面録画を渡すと用途が必ず「操作説明」になり、操作箇所へのズーム・録画の音は消音だった
- 今: 指示文から用途が「ゲーム実況」と判定されたとき（「ゲーム実況」「実況」「プレイ動画」「攻略」を含む）は
  ゲーム実況のまま。録画は寄らずに全体を見せ、ゲームの音を声の下に小さく残す（`GAME_REC_VOLUME = 0.25`、約 −12 dB）
- 画像 AI に読ませるときの問いもゲーム向け（`PROMPT_GAME`）
- テスト: `tests/integration/test_build_api.py::test_game_play_from_recording_and_play_log`

### 2.3 YouTube への投稿（`integrations/youtube/`、`tools/youtube_upload.py`）
- 許可: OAuth 2.0 デスクトップアプリ方式（ループバック＋PKCE）。スコープは `youtube.upload` だけ。
  更新用の鍵は DPAPI で暗号化して `data/youtube/token.bin`（平文で置かない）
- 上げ方: 再開できる方式（resumable）。5xx・429・回線切れは、届いた位置を聞いて残りから送り直す（最大 5 回）
- 既定: 非公開・カテゴリ「ゲーム」(20)・子ども向けでない・`containsSyntheticMedia=false`（ゲーム画面と立ち絵だけなら
  現実と見分けにくい合成ではない【推測】。実写風の生成映像を入れるときは true にする）
- `--confirm` が無いと確認だけ（何を上げるかを表示して終わる）
- 結果は `<動画>.youtube.json`（id・URL・Studio の編集 URL・公開状態）
- テスト: `tests/unit/test_youtube.py`（通信は偽物）

本人が PC で 1 回だけ行うこと:
1. Google Cloud でプロジェクトを作り、「YouTube Data API v3」を有効にする
2. OAuth 同意画面（外部・テストユーザーに自分の Google アカウントを追加）
3. 認証情報 →「OAuth クライアント ID」→ 種類「デスクトップ アプリ」→ JSON をダウンロードして
   `data/youtube/client_secret.json` に置く
4. `uv run python tools/youtube_upload.py auth` → ブラウザで許可 → `YOUTUBE_AUTH_OK`

注意（2026-11-27 時点で調べたこと）:
- 【未確定】監査を受けていない API プロジェクトから上げた動画は非公開に固定される、と書かれた公式ページと、
  固定されないと書かれた公式ページ（REST リファレンス）が両方ある。固定される場合は本人も公開に変えられないので、
  そのときは §3 Q3 の別の上げ方（Studio から上げる）に切り替える。最初の 1 本で確かめる
- クォータ: `videos.insert` は 1 日 100 回の別枠（1 回 1 単位）とする説明と 1,600 単位とする古い表が混在。
  どちらでも 1 日数本なら足りる【推測】
- カスタムサムネイルはチャンネルの電話番号確認が要る（できなければ `thumbnail_note` に理由が出る。動画は上がる）

## 3. まだ無いもの・決めること

| # | 項目 | 内容 | 案 |
|---|---|---|---|
| G1 | 録画 | ゲームの窓を録る仕組みが無い。FFmpeg の `gdigrab` は窓の映像だけで、Windows のゲーム音（再生中の音）は録れない | A: OBS（obs-websocket で開始・停止を操作、音も録れる）/ B: FFmpeg `gdigrab`（音なし、BGM は後で足す） |
| G2 | 時刻合わせ | 今の「動画をつくる」は台詞の声を頭から順に並べ、録画も頭から流すだけ。長いプレイでは台詞と画面の出来事がずれる | 台詞ごとに録画の時刻を持たせ、その時刻に声を置く。録画は見どころだけ切り出して詰める（プレイ記録の時刻で切る） |
| G3 | 長さ | 「動画をつくる」は 30 分（1,800 秒）まで。1 回のプレイが長いときは見どころだけにする | G2 の切り出しで 10〜20 分にする |
| G4 | 台本を書く者 | 遊んだ Claude 本人が書くか、このツールの台本作成 AI（ローカル LLM）に記録を渡して書かせるか | §4 Q1 |
| G5 | つなぎ方 | このツールは PC の 127.0.0.1:8765 で動く。クラウドの Claude からは直接届かない | このツールの MCP を Claude デスクトップアプリに登録する（`wakuwakuPlay` と同じ方式） |
| G6 | プレイ記録を残す道具 | 録画の開始時刻からの経過秒で記録を書く道具が無い | `wakuwakuPlay` に `play_record_start` / `play_mark` / `play_record_stop` を足す（gameyou 側） |
| G7 | サムネイル | 作り方が未定 | このツールの題名カード（Blender）でプレイ画面の 1 枚＋題字 |

## 4. 決まったこと（本人 2026-11-27）
| 問い | 答え | この流れでの形 |
|---|---|---|
| Q1 台本を書く者 | 「Claudeが書く」 | 遊んだ Claude がプレイ記録から自分の台詞を書く |
| Q2 録画 | 「OBS」 | OBS＋obs-websocket で開始・停止し、映像とゲームの音を録る（G1 = A） |
| Q3 投稿 | 「APIで非公開に上げる」 | `tools/youtube_upload.py`。公開は本人が Studio で。1 本目で非公開に固定されないか確かめる |
| Q4 話し手 | 「voicevox2人の掛け合いで実況及び機能解説役のclaudeと質問兼ガヤのQwenでやり取りしてほしいです」 | VOICEVOX の 2 人。Claude 役（実況・機能の解説）の台詞は Claude が書き、Qwen 役（質問・ガヤ）の台詞はローカル LLM の Qwen が書く。台本の作り方は「片側入力」（`USER_ONE_SIDE`: Claude の台詞を固定し、相手役を `generate_counterpart_dialogue` で作らせる） |

## 5. 作ったもの（2026-11-27 の 2 回目）
本人 2026-11-27「全てclaudeで進めて下さい」「長いプレイに関しては動画をカット編集し見どころのみを集めて15分までに凝縮してください」

| 部分 | 場所 | 中身 |
|---|---|---|
| OBS | PC の `%LOCALAPPDATA%\va_obs\obs`（ポータブル版 33.0.3、管理者権限なし。zip の SHA-256 を GitHub の値と照合） | WebSocket は 127.0.0.1:4455、パスワードは `%USERPROFILE%\.va_obs\ws_password.txt`。初回の自動構成ウィザードは出さない |
| OBS の操作 | `integrations/obs/client.py` | obs-websocket 5 の同期クライアント（認証つき） |
| 録画とプレイ記録 | `services/capture/session.py` | 場面 `VA_Game` に「ゲームの窓」（window_capture・同じ exe）と「ゲームの音」（wasapi_process_output_capture）だけを置き、デスクトップ音声・マイクは消音。記録の時刻は OBS の録画時間。保存先 `%USERPROFILE%\Videos\va_capture\<日時>\`（`session.json`） |
| 見どころ | `services/capture/highlights.py` | 録画が 15 分を超えるとき: 最初の 12 秒＋印の前後（大事さ 1: −3/+6 秒、2: −5/+10、3 と見どころ: −8/+18）。近い区間（3 秒以内）はつなぐ。収まらなければ「大事さ²の合計 ÷ 長さ」の高い区間から選び、時刻の順に並べる。つなぎ目は 0.25 秒のフェード。印は詰めた後の時刻に付け直す |
| 台詞の時刻合わせ | `ScriptSegment.anchor_sec`、`subtitle_service.synchronize` | 時刻つきのセリフはその時刻より前に置かない。3 秒以上遅れたセリフは「動画をつくる」の注意に出る |
| 掛け合い | `BuildRequest.play_lines`・`cast`、`BuildRunRequest.script_model` | 1 人目「Claude」（実況・機能の解説、声 四国めたん）のセリフは固定、2 人目「Qwen」（質問・ガヤ、声 ずんだもん）のセリフはローカル LLM が作る（台本の形は片側入力） |
| MCP | `mcp_servers/game_video_server.py`（Claude デスクトップアプリに `gameVideo` として登録） | `capture_start` / `capture_mark` / `capture_stop` / `capture_status` / `make_highlights` / `build_game_video` / `youtube_auth` / `youtube_upload` / `job_status`。時間のかかる処理は裏の仕事にして job id を返す。VOICEVOX・Ollama が止まっていれば起こす |

### 遊んで動画にする手順（Claude）
1. `play_launch`（開発用ビルド）→ Discord で配信（gameyou の `kb/1_guide/claude_play.md` §5）→ `capture_start`
2. 遊びながら `capture_mark`（出来事 event・見どころ highlight・感想 comment・場面 scene、大事さ 1〜3）
3. `capture_stop` → `make_highlights` → `job_status` で待つ（返る `marks` は詰めた後の時刻）
4. 返った記録を見て、Claude のセリフを `[{at_sec, text}]` で書く（1 つの区間の長さを超えないように短く）
5. `build_game_video(video=…_highlights.mp4, play_lines=…, reference=返った reference, script_model=<Qwen のモデル>)` → `job_status`
6. 題名・説明・タグを書き、`youtube_upload(confirm=True)`（既定は非公開）
7. 配信を止め、開いた窓を閉じる

## 6. 次に作るもの（順）
1. G6＋G1: OBS の録画の開始・停止とプレイ記録の印（`play_record_start` / `play_mark` / `play_record_stop`、gameyou の `wakuwakuPlay`）
2. G2＋G3: 台詞ごとの録画時刻で声を置き、見どころだけ切り出して詰める（このツールの「動画をつくる」）。
   片側入力（Claude の台詞は固定・Qwen が相手役）を「動画をつくる」から使えるようにする
3. G5: このツールの MCP を Claude デスクトップアプリに登録する
4. G7: サムネイル
5. 1 本通しで作って非公開で上げ、確かめる
