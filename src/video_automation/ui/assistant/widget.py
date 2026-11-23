"""AI アシスタント UI（Phase 8 Part A）。

会話で編集を指示 → AI の計画と実行結果を表示 → 承認待ちの変更を差分つきで確認して承認/却下。
別のモデルでの再実行、編集履歴の確認と指定時点までの取り消し、承認ポリシーの設定ができる。
"""

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from video_automation.ui.async_call import AsyncCaller, error_text

KIND_LABEL = {"command": "編集", "render": "書き出し", "asset": "素材", "script": "台本"}


def _esc(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")


class AssistantWidget(QWidget):
    def __init__(self, api: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.api = api
        self.conversation_id: str | None = None
        self.messages: list[dict[str, Any]] = []
        self.pending: list[dict[str, Any]] = []
        self.history_rows: list[dict[str, Any]] = []
        self.runner = AsyncCaller(self, self._error, self._set_busy)

        self.project_combo = QComboBox()
        self.project_combo.currentIndexChanged.connect(self._project_changed)
        self.conv_combo = QComboBox()
        self.conv_combo.currentIndexChanged.connect(self._conv_changed)
        self.new_conv_button = QPushButton("新しい会話")
        self.new_conv_button.clicked.connect(self.new_conversation)
        self.model_combo = QComboBox()

        self.chat_view = QTextBrowser()
        self.input_edit = QPlainTextEdit()
        self.input_edit.setPlaceholderText(
            "編集の指示（例: 冒頭3秒に動画を置いてフェードインして）"
        )
        self.input_edit.setMaximumHeight(80)
        self.send_button = QPushButton("送信")
        self.send_button.clicked.connect(self.send)
        self.rerun_button = QPushButton("選んだモデルで最後の指示をやり直す")
        self.rerun_button.clicked.connect(self.rerun_last)

        self.approval_list = QListWidget()
        self.approval_list.currentRowChanged.connect(self._show_approval)
        self.approval_detail = QPlainTextEdit()
        self.approval_detail.setReadOnly(True)
        self.approve_button = QPushButton("承認")
        self.approve_button.clicked.connect(lambda: self.decide(True))
        self.reject_button = QPushButton("却下")
        self.reject_button.clicked.connect(lambda: self.decide(False))
        self.refresh_button = QPushButton("更新")
        self.refresh_button.clicked.connect(self.reload_side)

        self.history_list = QListWidget()
        self.revert_button = QPushButton("選んだ時点まで戻す")
        self.revert_button.clicked.connect(self.revert)

        self.policy_download = QComboBox()
        self.policy_download.addItem("承認が必要", "required")
        self.policy_download.addItem("承認なしで取り込む", "auto")
        self.policy_license = QComboBox()
        self.policy_license.addItem("明示承認で使用可", "ack")
        self.policy_license.addItem("使用しない", "forbidden")
        self.policy_save = QPushButton("ポリシーを保存")
        self.policy_save.clicked.connect(self.save_policy)
        self.policy_view = QPlainTextEdit()
        self.policy_view.setReadOnly(True)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)

        top = QHBoxLayout()
        for w in (
            QLabel("プロジェクト"),
            self.project_combo,
            QLabel("会話"),
            self.conv_combo,
            self.new_conv_button,
            QLabel("モデル"),
            self.model_combo,
        ):
            top.addWidget(w)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.addWidget(self.chat_view, 1)
        il = QHBoxLayout()
        il.addWidget(self.input_edit)
        bl = QVBoxLayout()
        bl.addWidget(self.send_button)
        bl.addWidget(self.rerun_button)
        il.addLayout(bl)
        ll.addLayout(il)

        side = QTabWidget()
        ap = QWidget()
        al = QVBoxLayout(ap)
        al.addWidget(self.approval_list)
        al.addWidget(self.approval_detail)
        ab = QHBoxLayout()
        for b in (self.approve_button, self.reject_button, self.refresh_button):
            ab.addWidget(b)
        al.addLayout(ab)
        side.addTab(ap, "承認待ち")
        hp = QWidget()
        hl = QVBoxLayout(hp)
        hl.addWidget(self.history_list)
        hl.addWidget(self.revert_button)
        side.addTab(hp, "編集履歴")
        pp = QWidget()
        pl = QVBoxLayout(pp)
        pl.addWidget(QLabel("AIによる素材ダウンロード"))
        pl.addWidget(self.policy_download)
        pl.addWidget(QLabel("ライセンス不明・要確認の素材"))
        pl.addWidget(self.policy_license)
        pl.addWidget(self.policy_save)
        pl.addWidget(self.policy_view)
        side.addTab(pp, "承認ポリシー")

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(left)
        split.addWidget(side)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(split, 1)
        layout.addWidget(self.status_label)

    # ---------- 共通 ----------
    def _error(self, exc: BaseException) -> None:
        self.status_label.setText(f"エラー: {error_text(exc)[:300]}")
        if self.conversation_id:  # 送信した指示は保存済み（やり直しできる）
            try:
                self.messages = self.api.conversation(self.conversation_id)["messages"]
                self.render_chat()
            except Exception:  # noqa: BLE001
                pass

    def _set_busy(self, busy: bool) -> None:
        for b in (self.send_button, self.rerun_button):
            b.setEnabled(not busy)
        if busy:
            self.status_label.setText("AIが計画中…（ローカルLLMでは数分かかることがあります）")

    @property
    def project_id(self) -> str | None:
        return self.project_combo.currentData()

    def refresh(self) -> None:
        try:
            projects = self.api.list_projects()
            models = self.api.list_models()
        except Exception as exc:  # noqa: BLE001
            self._error(exc)
            return
        self.model_combo.clear()
        self.model_combo.addItem("自動（タスク割当）", None)
        for m in models:
            self.model_combo.addItem(m.get("display_name") or m["key"], m["key"])
        self.project_combo.blockSignals(True)
        self.project_combo.clear()
        for p in projects:
            self.project_combo.addItem(p["name"], p["id"])
        self.project_combo.blockSignals(False)
        self._project_changed()
        self.load_policy()

    def _project_changed(self, *_: Any) -> None:
        pid = self.project_id
        self.conv_combo.blockSignals(True)
        self.conv_combo.clear()
        if pid:
            for c in self.api.conversations(pid):
                self.conv_combo.addItem(c["title"] or "（無題）", c["id"])
        self.conv_combo.blockSignals(False)
        if self.conv_combo.count():
            self.conv_combo.setCurrentIndex(self.conv_combo.count() - 1)
        self._conv_changed()
        self.reload_side()

    def _conv_changed(self, *_: Any) -> None:
        self.conversation_id = self.conv_combo.currentData()
        self.messages = []
        if self.conversation_id:
            self.messages = self.api.conversation(self.conversation_id)["messages"]
        self.render_chat()

    def new_conversation(self) -> None:
        pid = self.project_id
        if not pid:
            self.status_label.setText("プロジェクトを選んでください")
            return

        def done(c: dict[str, Any]) -> None:
            self.conv_combo.addItem("（新しい会話）", c["id"])
            self.conv_combo.setCurrentIndex(self.conv_combo.count() - 1)

        self.runner.run_now(lambda: self.api.create_conversation(pid), done)

    # ---------- 会話 ----------
    def render_chat(self) -> None:
        html = []
        for m in self.messages:
            if m["superseded"]:
                continue
            if m["role"] == "user":
                html.append(f"<p><b>あなた:</b> {_esc(m['content'])}</p>")
                continue
            model = (m.get("model_key") or "").split("::")[-1]
            html.append(f"<p><b>AI</b> <small>({_esc(model)})</small>: {_esc(m['content'])}</p>")
            items = []
            for a in m.get("actions", []):
                if a.get("approval"):
                    mark = "⏳"
                elif a["ok"]:
                    mark = "✔"
                else:
                    mark = "✖"
                line = f"{mark} {_esc(a['summary'])}"
                if a.get("error"):
                    line += f" — {_esc(a['error'][:200])}"
                if a.get("data", {}) and a["data"].get("results"):
                    hits = ", ".join(
                        f"{h['source']}:{h['source_id']}（{h['license']}）"
                        for h in a["data"]["results"][:5]
                    )
                    line += f"<br><small>{_esc(hits)}</small>"
                items.append(f"<li>{line}</li>")
            for q in m.get("questions", []):
                items.append(f"<li>❓ {_esc(q)}</li>")
            if items:
                html.append("<ul>" + "".join(items) + "</ul>")
        self.chat_view.setHtml("".join(html))
        bar = self.chat_view.verticalScrollBar()
        bar.setValue(bar.maximum())

    def send(self) -> None:
        text = self.input_edit.toPlainText().strip()
        if not text:
            return
        if not self.conversation_id:
            pid = self.project_id
            if not pid:
                self.status_label.setText("プロジェクトを選んでください")
                return
            c = self.api.create_conversation(pid)
            self.conv_combo.blockSignals(True)
            self.conv_combo.addItem(text[:40], c["id"])
            self.conv_combo.setCurrentIndex(self.conv_combo.count() - 1)
            self.conv_combo.blockSignals(False)
            self.conversation_id = c["id"]
        cid, model = self.conversation_id, self.model_combo.currentData()
        self.messages.append(
            {"role": "user", "content": text, "superseded": False, "actions": [], "questions": []}
        )
        self.render_chat()
        self.input_edit.clear()
        self.runner.call(lambda: self.api.send_message(cid, text, model), self._after_reply)

    def _after_reply(self, _: dict[str, Any]) -> None:
        assert self.conversation_id
        self.messages = self.api.conversation(self.conversation_id)["messages"]
        self.render_chat()
        self.reload_side()
        n = len(self.pending)
        self.status_label.setText(f"承認待ち {n} 件" if n else "完了")

    def rerun_last(self) -> None:
        last = next(
            (m for m in reversed(self.messages) if m["role"] == "user" and m.get("id")), None
        )
        if last is None:
            self.status_label.setText("やり直す指示がありません")
            return
        model = self.model_combo.currentData()
        self.runner.call(lambda: self.api.rerun_message(last["id"], model), self._after_reply)

    # ---------- 承認・履歴 ----------
    def reload_side(self) -> None:
        pid = self.project_id
        self.approval_list.clear()
        self.history_list.clear()
        self.pending, self.history_rows = [], []
        if not pid:
            return
        try:
            self.pending = self.api.approvals(pid)
            self.history_rows = self.api.history(pid)
        except Exception as exc:  # noqa: BLE001
            self._error(exc)
            return
        for a in self.pending:
            self.approval_list.addItem(f"[{KIND_LABEL.get(a['kind'], a['kind'])}] {a['title']}")
        if self.pending:
            self.approval_list.setCurrentRow(0)
        else:
            self.approval_detail.clear()
        for h in self.history_rows:
            mark = "↶" if h["status"] == "UNDONE" else ""
            who = "AI" if h["origin"] == "llm" else "手動"
            text = f"#{h['sequence']} {mark}[{who}] {h['description'] or '編集'}"
            item = QListWidgetItem(text)
            item.setToolTip("\n".join(h["changes"]))
            self.history_list.addItem(item)

    def _show_approval(self, row: int) -> None:
        if 0 <= row < len(self.pending):
            a = self.pending[row]
            self.approval_detail.setPlainText("\n".join([a["title"], "", *a["details"]]))

    def decide(self, approve: bool) -> None:
        row = self.approval_list.currentRow()
        if not 0 <= row < len(self.pending):
            self.status_label.setText("承認待ちの項目を選んでください")
            return
        a = self.pending[row]

        def done(r: dict[str, Any]) -> None:
            self.status_label.setText(f"{'承認' if approve else '却下'}しました（{r['status']}）")
            self.reload_side()

        self.runner.run_now(lambda: self.api.decide_approval(a["kind"], a["id"], approve), done)

    def revert(self) -> None:
        row = self.history_list.currentRow()
        pid = self.project_id
        if not pid or not 0 <= row < len(self.history_rows):
            self.status_label.setText("戻す時点を選んでください")
            return
        h = self.history_rows[row]

        def done(r: dict[str, Any]) -> None:
            self.status_label.setText(f"{len(r['undone'])} 件の編集を取り消しました")
            self.reload_side()

        self.runner.run_now(lambda: self.api.revert_history(pid, h["command_id"]), done)

    def load_policy(self) -> None:
        try:
            pol = self.api.approval_policy()
        except Exception as exc:  # noqa: BLE001
            self._error(exc)
            return
        by = {p["key"]: p for p in pol}
        for combo, key in (
            (self.policy_download, "asset_download"),
            (self.policy_license, "unknown_license"),
        ):
            i = combo.findData(by.get(key, {}).get("mode"))
            if i >= 0:
                combo.setCurrentIndex(i)
        labels = {
            "required": "承認が必要",
            "auto": "承認不要",
            "ack": "明示承認",
            "forbidden": "禁止",
        }
        self.policy_view.setPlainText(
            "\n".join(
                f"{p['label']}: {labels.get(p['mode'], p['mode'])}"
                + ("" if p["configurable"] else "（固定）")
                for p in pol
            )
        )

    def save_policy(self) -> None:
        self.runner.run_now(
            lambda: self.api.set_approval_policy(
                asset_download=self.policy_download.currentData(),
                unknown_license=self.policy_license.currentData(),
            ),
            self._policy_saved,
        )

    def _policy_saved(self, _: Any) -> None:
        self.load_policy()
        self.status_label.setText("ポリシーを保存しました")
