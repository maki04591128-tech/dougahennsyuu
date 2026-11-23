"""モデル設定UI (P2-08)。接続状態・モデル一覧・実モデルID・ロード状態・推論テスト・
メインモデル選択・タスク別割当・OpenAI互換API手動登録。UIはAPIクライアント経由でのみ操作する。"""

from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

TASK_LABELS = {
    "edit_plan": "編集計画",
    "structured_command": "構造化命令",
    "tool_selection": "ツール選択",
    "script_generation": "台本生成",
    "subtitle": "字幕",
    "proofreading": "文章校正",
    "image_understanding": "画像理解",
    "chat": "対話",
}
REGISTER_TYPES = ["openai_compatible", "openai", "anthropic", "gemini", "ollama", "lmstudio"]
MODEL_COLUMNS = [
    "表示名",
    "実モデルID",
    "Provider",
    "量子化",
    "状態",
    "ロード",
    "最大ctx",
    "画像",
    "ツール",
    "構造化",
    "tok/s",
    "メイン",
]


def _mark(v: Any) -> str:
    return {True: "○", False: "×", None: "?"}.get(v, str(v))


class LlmSettingsWidget(QWidget):
    def __init__(self, api: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.api = api
        self._models: list[dict[str, Any]] = []
        self._job_id: str | None = None
        self._updating = False

        # ---- providers ----
        self.provider_table = QTableWidget(0, 6)
        self.provider_table.setHorizontalHeaderLabels(
            ["名前", "種別", "URL", "状態", "モデル数", "詳細"]
        )
        self._setup_table(self.provider_table)
        self.discover_button = QPushButton("ローカル環境を検出")
        self.discover_button.clicked.connect(self.discover)
        self.delete_provider_button = QPushButton("選択した登録先を削除")
        self.delete_provider_button.clicked.connect(self.delete_selected_provider)
        self.music_label = QLabel("音楽生成 (ACE-Step): 未確認")

        # ---- register ----
        self.reg_type = QComboBox()
        self.reg_type.addItems(REGISTER_TYPES)
        self.reg_name = QLineEdit()
        self.reg_url = QLineEdit()
        self.reg_url.setPlaceholderText("http://127.0.0.1:8080  /  https://api.openai.com/v1")
        self.reg_key = QLineEdit()
        self.reg_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.reg_key.setPlaceholderText("任意（DPAPIで暗号化保存）")
        self.register_button = QPushButton("登録")
        self.register_button.clicked.connect(self.register_provider)
        reg_box = QGroupBox("API接続先の手動登録")
        form = QFormLayout(reg_box)
        form.addRow("種別", self.reg_type)
        form.addRow("名前", self.reg_name)
        form.addRow("URL", self.reg_url)
        form.addRow("APIキー", self.reg_key)
        form.addRow(self.register_button)

        # ---- models ----
        self.model_table = QTableWidget(0, len(MODEL_COLUMNS))
        self.model_table.setHorizontalHeaderLabels(MODEL_COLUMNS)
        self._setup_table(self.model_table)
        self.test_button = QPushButton("推論テスト")
        self.test_button.clicked.connect(self.test_selected)
        self.active_button = QPushButton("メインモデルに設定")
        self.active_button.clicked.connect(self.set_active_selected)
        self.test_label = QLabel("")
        self.test_label.setWordWrap(True)

        # ---- tasks ----
        self.task_table = QTableWidget(0, 4)
        self.task_table.setHorizontalHeaderLabels(["タスク", "割当", "使用モデル", "備考"])
        self._setup_table(self.task_table)

        self.status_label = QLabel("")
        top = QHBoxLayout()
        top.addWidget(self.discover_button)
        top.addWidget(self.delete_provider_button)
        top.addStretch()
        model_btns = QHBoxLayout()
        model_btns.addWidget(self.test_button)
        model_btns.addWidget(self.active_button)
        model_btns.addStretch()

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("推論サーバー / API"))
        layout.addLayout(top)
        layout.addWidget(self.provider_table)
        layout.addWidget(self.music_label)
        layout.addWidget(reg_box)
        layout.addWidget(QLabel("モデル"))
        layout.addWidget(self.model_table)
        layout.addLayout(model_btns)
        layout.addWidget(self.test_label)
        layout.addWidget(QLabel("タスク別割当"))
        layout.addWidget(self.task_table)
        layout.addWidget(self.status_label)

        self._poll = QTimer(self)
        self._poll.setInterval(1000)
        self._poll.timeout.connect(self._poll_job)

    @staticmethod
    def _setup_table(t: QTableWidget) -> None:
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        t.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        t.horizontalHeader().setStretchLastSection(True)

    def _error(self, action: str, exc: Exception) -> None:
        self.status_label.setText(f"{action}に失敗しました ({type(exc).__name__})")

    # ----- 表示 -----
    def refresh(self) -> None:
        try:
            self._fill_providers(self.api.list_providers())
            self._models = self.api.list_models()
            self._fill_models()
            self._fill_tasks(self.api.get_task_assignments())
        except Exception as exc:
            self._error("読み込み", exc)

    def _fill_providers(self, providers: list[dict[str, Any]]) -> None:
        t = self.provider_table
        t.setRowCount(len(providers))
        for r, p in enumerate(providers):
            state = "接続OK" if p.get("available") else "未接続"
            detail = p.get("server_version") or p.get("api_format") or p.get("error") or ""
            for c, v in enumerate(
                [p["name"], p["type"], p["base_url"], state, p.get("models_count", 0), detail]
            ):
                item = QTableWidgetItem(str(v))
                item.setData(Qt.ItemDataRole.UserRole, p["provider_id"])
                t.setItem(r, c, item)

    def _fill_models(self) -> None:
        t = self.model_table
        t.setRowCount(len(self._models))
        for r, m in enumerate(self._models):
            caps = m.get("capabilities") or {}
            tool = caps.get("tool_calling")
            tool_txt = "△" if not tool and caps.get("tool_calling_fallback") else _mark(tool)
            tps = (m.get("last_test") or {}).get("tokens_per_sec")
            row = [
                m["display_name"],
                m["model_id"],
                m["provider_id"],
                m.get("quantization") or "",
                m["state"],
                _mark(m.get("loaded")),
                caps.get("context_length") or "",
                _mark(caps.get("image_input")),
                tool_txt,
                _mark(caps.get("structured_output")),
                tps or "",
                "★" if m.get("is_active") else "",
            ]
            for c, v in enumerate(row):
                item = QTableWidgetItem(str(v))
                item.setData(Qt.ItemDataRole.UserRole, m["key"])
                t.setItem(r, c, item)

    def _fill_tasks(self, rows: list[dict[str, Any]]) -> None:
        self._updating = True
        t = self.task_table
        t.setRowCount(len(rows))
        for r, a in enumerate(rows):
            t.setItem(r, 0, QTableWidgetItem(TASK_LABELS.get(a["task"], a["task"])))
            combo = QComboBox()
            combo.addItem("自動選択", None)
            for m in self._models:
                combo.addItem(m["display_name"], m["key"])
            if a.get("model_key"):
                idx = combo.findData(a["model_key"])
                combo.setCurrentIndex(max(idx, 0))
            combo.currentIndexChanged.connect(
                lambda _i, task=a["task"], cb=combo: self._assign(task, cb.currentData())
            )
            t.setCellWidget(r, 1, combo)
            resolved = a.get("resolved_model_key") or "（なし）"
            t.setItem(r, 2, QTableWidgetItem(resolved))
            t.setItem(r, 3, QTableWidgetItem(a.get("reason") or ""))
        self._updating = False

    # ----- 操作 -----
    def _selected_key(self, table: QTableWidget) -> str | None:
        row = table.currentRow()
        item = table.item(row, 0) if row >= 0 else None
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def discover(self) -> None:
        self.status_label.setText("検出中…")
        try:
            res = self.api.discover()
        except Exception as exc:
            self._error("検出", exc)
            return
        self._fill_providers(res.get("providers", []))
        music = res.get("music") or []
        if music:
            m = music[0]
            state = "接続OK" if m["available"] else "未接続"
            self.music_label.setText(f"音楽生成 ({m['name']}): {state} {m['base_url']}")
        self._models = res.get("models", [])
        self._fill_models()
        try:
            self._fill_tasks(self.api.get_task_assignments())
        except Exception as exc:
            self._error("割当の読み込み", exc)
            return
        self.status_label.setText(f"検出完了: モデル {len(self._models)} 件")

    def register_provider(self) -> None:
        name, url = self.reg_name.text().strip(), self.reg_url.text().strip()
        if not name or not url:
            self.status_label.setText("名前とURLを入力してください")
            return
        try:
            self.api.register_provider(
                self.reg_type.currentText(), name, url, self.reg_key.text() or None
            )
        except Exception as exc:
            self._error("登録", exc)
            return
        finally:
            self.reg_key.clear()
        self.status_label.setText(f"登録しました: {name}")
        self.discover()

    def delete_selected_provider(self) -> None:
        pid = self._selected_key(self.provider_table)
        if not pid:
            return
        try:
            self.api.delete_provider(pid)
        except Exception as exc:
            self._error("削除", exc)
            return
        self.refresh()

    def set_active_selected(self) -> None:
        key = self._selected_key(self.model_table)
        if not key:
            self.status_label.setText("モデルを選択してください")
            return
        try:
            self.api.set_active_model(key)
        except Exception as exc:
            self._error("メインモデル設定", exc)
            return
        self.refresh()

    def _assign(self, task: str, model_key: str | None) -> None:
        if self._updating:
            return
        try:
            self._fill_tasks(self.api.set_task_assignment(task, model_key))
        except Exception as exc:
            self._error("タスク割当", exc)

    def test_selected(self) -> None:
        key = self._selected_key(self.model_table)
        if not key:
            self.status_label.setText("モデルを選択してください")
            return
        try:
            job = self.api.test_model(key)
        except Exception as exc:
            self._error("推論テスト開始", exc)
            return
        self._job_id = job["id"]
        self.test_button.setEnabled(False)
        self.test_label.setText(
            f"推論テスト実行中: {key}（初回はモデルのロードに30秒程度かかります）"
        )
        self._poll.start()

    def _poll_job(self) -> None:
        if not self._job_id:
            self._poll.stop()
            return
        try:
            job = self.api.get_job(self._job_id)
        except Exception as exc:
            self._error("テスト状態取得", exc)
            return
        if job["status"] in ("PENDING", "RUNNING"):
            self.test_label.setText(f"推論テスト実行中… {int(job.get('progress', 0) * 100)}%")
            return
        self._poll.stop()
        self._job_id = None
        self.test_button.setEnabled(True)
        self.show_test_result(job)

    def show_test_result(self, job: dict[str, Any]) -> None:
        if job["status"] != "SUCCEEDED":
            self.test_label.setText(f"推論テスト失敗: {job.get('error') or job['status']}")
            return
        res = job["result"]
        probes = " / ".join(f"{p['name']}:{_mark(p['ok'])}" for p in res["probes"])
        self.test_label.setText(
            f"{'成功' if res['success'] else '失敗'}  {res.get('tokens_per_sec') or '-'} tok/s  "
            f"[{probes}]\n{res.get('sample_text') or ''}"
        )
        self.refresh()
