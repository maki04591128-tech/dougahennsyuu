"""タイムライン・レンダリングUI (Phase 5)。

台本からタイムラインを組み、フレームを確認し、承認待ちの編集命令/レンダリングを捌き、
プロファイルを選んで書き出す。UI は API 経由でのみ操作する。
"""

from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from video_automation.ui.subtitle.widget import fmt_frame

_ACTIVE = ("PENDING_APPROVAL", "QUEUED", "RENDERING")


class TimelineWidget(QWidget):
    def __init__(self, api: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.api = api
        self._tl: dict[str, Any] | None = None
        self._render_id: str | None = None

        self.project_combo = QComboBox()
        self.project_combo.currentIndexChanged.connect(self.load_project)
        self.script_combo = QComboBox()
        self.profile_combo = QComboBox()
        self.burn_check = QCheckBox("字幕を焼き込む")
        self.burn_check.setChecked(True)
        self.loudnorm_check = QCheckBox("音量を -14 LUFS に揃える")
        self.loudnorm_check.setChecked(True)

        self.from_script_button = QPushButton("台本から配置")
        self.from_script_button.clicked.connect(self.from_script)
        self.undo_button = QPushButton("元に戻す")
        self.undo_button.clicked.connect(lambda: self._history("undo"))
        self.redo_button = QPushButton("やり直す")
        self.redo_button.clicked.connect(lambda: self._history("redo"))

        self.clip_table = QTableWidget(0, 5)
        self.clip_table.setHorizontalHeaderLabels(["トラック", "開始", "終了", "素材", "音量"])
        self.clip_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.clip_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.clip_table.horizontalHeader().setStretchLastSection(True)
        self.duration_label = QLabel("")

        self.frame_spin = QSpinBox()
        self.frame_spin.setRange(0, 0)
        self.preview_button = QPushButton("このフレームを表示")
        self.preview_button.clicked.connect(self.preview)
        self.preview_label = QLabel()
        self.preview_label.setMinimumSize(320, 180)
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.pending_list = QListWidget()
        self.approve_button = QPushButton("承認")
        self.approve_button.clicked.connect(lambda: self.decide(True))
        self.reject_button = QPushButton("却下")
        self.reject_button.clicked.connect(lambda: self.decide(False))

        self.render_button = QPushButton("書き出し")
        self.render_button.clicked.connect(self.start_render)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)

        form = QFormLayout()
        form.addRow("プロジェクト", self.project_combo)
        form.addRow("台本（字幕）", self.script_combo)
        top = QHBoxLayout()
        for b in (self.from_script_button, self.undo_button, self.redo_button):
            top.addWidget(b)
        pv = QHBoxLayout()
        pv.addWidget(QLabel("フレーム"))
        pv.addWidget(self.frame_spin)
        pv.addWidget(self.preview_button)
        pending_btns = QHBoxLayout()
        pending_btns.addWidget(self.approve_button)
        pending_btns.addWidget(self.reject_button)
        rform = QFormLayout()
        rform.addRow("プロファイル", self.profile_combo)
        rform.addRow(self.burn_check)
        rform.addRow(self.loudnorm_check)

        left = QVBoxLayout()
        left.addLayout(form)
        left.addLayout(top)
        left.addWidget(self.duration_label)
        left.addWidget(self.clip_table)
        left.addWidget(QLabel("承認待ち（LLMからの編集・書き出し）"))
        left.addWidget(self.pending_list)
        left.addLayout(pending_btns)
        right = QVBoxLayout()
        right.addLayout(pv)
        right.addWidget(self.preview_label, 1)
        right.addLayout(rform)
        right.addWidget(self.render_button)
        right.addWidget(self.status_label)
        root = QHBoxLayout(self)
        root.addLayout(left, 3)
        root.addLayout(right, 2)

        self._poll = QTimer(self)
        self._poll.setInterval(1000)
        self._poll.timeout.connect(self._poll_render)

    # ---------- 共通 ----------
    def _pid(self) -> str | None:
        return self.project_combo.currentData()

    def _fps(self) -> float:
        return self._tl["fps_num"] / self._tl["fps_den"] if self._tl else 30.0

    def _error(self, action: str, exc: Exception) -> None:
        detail = ""
        resp = getattr(exc, "response", None)
        if resp is not None:
            try:
                detail = f": {resp.json().get('message', '')}"
            except ValueError:
                pass
        self.status_label.setText(f"{action}に失敗しました ({type(exc).__name__}{detail})")

    # ---------- 読み込み ----------
    def refresh(self) -> None:
        try:
            projects = self.api.list_projects()
            profiles = self.api.render_profiles()
        except Exception as exc:
            self._error("読み込み", exc)
            return
        self.project_combo.blockSignals(True)
        self.project_combo.clear()
        for p in projects:
            self.project_combo.addItem(f"{p['name']} ({p['width']}x{p['height']})", p["id"])
        self.project_combo.blockSignals(False)
        self.profile_combo.clear()
        for pr in profiles:
            self.profile_combo.addItem(f"{pr['label']} ({pr['width']}x{pr['height']})", pr["name"])
        self.load_project()

    def load_project(self) -> None:
        pid = self._pid()
        if not pid:
            return
        try:
            self._tl = self.api.timeline(pid)
            scripts = self.api.list_scripts(pid)
            pending = self.api.list_commands(pid, "PENDING_APPROVAL")
            renders = self.api.list_renders(pid)
        except Exception as exc:
            self._error("タイムライン取得", exc)
            return
        self.script_combo.clear()
        self.script_combo.addItem("（字幕なし）", None)
        for s in scripts:
            self.script_combo.addItem(s.get("title") or s["id"][:8], s["id"])
        if scripts:
            self.script_combo.setCurrentIndex(len(scripts))
        self._show_timeline()
        self._show_pending(pending, [r for r in renders if r["status"] == "PENDING_APPROVAL"])

    def _show_timeline(self) -> None:
        tl = self._tl
        if tl is None:
            return
        fps = self._fps()
        tracks = {t["id"]: t["name"] for t in tl["tracks"]}
        assets = {a["id"]: a["name"] for a in tl["assets"]}
        t = self.clip_table
        t.setRowCount(len(tl["clips"]))
        for r, c in enumerate(tl["clips"]):
            vals = [
                tracks.get(c["track_id"], c["track_id"]),
                fmt_frame(c["start_frame"], fps),
                fmt_frame(c["end_frame"], fps),
                assets.get(c["asset_id"], c["asset_id"]),
                f"{c['volume']:.2f}",
            ]
            for col, v in enumerate(vals):
                t.setItem(r, col, QTableWidgetItem(str(v)))
        dur = tl["duration_frames"]
        self.duration_label.setText(f"長さ {fmt_frame(dur, fps)}（{dur} フレーム）")
        self.frame_spin.setRange(0, max(dur - 1, 0))

    def _show_pending(self, commands: list[dict], renders: list[dict]) -> None:
        self.pending_list.clear()
        for c in commands:
            label = c.get("description") or c["command"].get("type", "")
            item = QListWidgetItem(f"編集: {label}")
            item.setData(Qt.ItemDataRole.UserRole, ("command", c["id"]))
            self.pending_list.addItem(item)
        for r in renders:
            item = QListWidgetItem(f"書き出し: {r['profile']}")
            item.setData(Qt.ItemDataRole.UserRole, ("render", r["id"]))
            self.pending_list.addItem(item)

    # ---------- 編集 ----------
    def from_script(self) -> None:
        pid, sid = self._pid(), self.script_combo.currentData()
        if not pid or not sid:
            self.status_label.setText("台本を選択してください")
            return
        try:
            self.api.timeline_from_script(pid, sid)
        except Exception as exc:
            self._error("台本からの配置", exc)
            return
        self.status_label.setText("台本の音声をタイムラインに配置しました")
        self.load_project()

    def _history(self, action: str) -> None:
        pid = self._pid()
        if not pid:
            return
        try:
            getattr(self.api, action)(pid)
        except Exception as exc:
            self._error("元に戻す" if action == "undo" else "やり直し", exc)
            return
        self.load_project()

    def decide(self, approve: bool) -> None:
        item = self.pending_list.currentItem()
        pid = self._pid()
        if item is None or not pid:
            return
        kind, ident = item.data(Qt.ItemDataRole.UserRole)
        try:
            if kind == "command":
                self.api.decide_command(pid, ident, approve)
            else:
                r = self.api.decide_render(ident, approve)
                if approve:
                    self._watch(r["id"])
        except Exception as exc:
            self._error("承認" if approve else "却下", exc)
            return
        self.status_label.setText("承認しました" if approve else "却下しました")
        self.load_project()

    # ---------- プレビュー ----------
    def preview(self) -> None:
        pid = self._pid()
        if not pid:
            return
        try:
            png = self.api.preview_frame(
                pid, self.frame_spin.value(), self.script_combo.currentData()
            )
        except Exception as exc:
            self._error("プレビュー", exc)
            return
        pix = QPixmap()
        if not pix.loadFromData(png, "PNG"):
            self.status_label.setText("プレビュー画像を読み込めませんでした")
            return
        self.preview_label.setPixmap(
            pix.scaled(
                self.preview_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    # ---------- 書き出し ----------
    def start_render(self) -> None:
        pid = self._pid()
        if not pid:
            return
        try:
            r = self.api.render(
                pid,
                self.profile_combo.currentData(),
                self.script_combo.currentData(),
                burn_subtitles=self.burn_check.isChecked(),
                loudnorm=self.loudnorm_check.isChecked(),
            )
        except Exception as exc:
            self._error("書き出し", exc)
            return
        self._watch(r["id"])

    def _watch(self, render_id: str) -> None:
        self._render_id = render_id
        self.render_button.setEnabled(False)
        self.status_label.setText("書き出し中…")
        self._poll.start()

    def _poll_render(self) -> None:
        if not self._render_id:
            self._poll.stop()
            return
        try:
            r = self.api.get_render(self._render_id)
            if r["status"] in _ACTIVE:
                progress = 0.0
                if r.get("job_id"):
                    progress = self.api.get_job(r["job_id"]).get("progress", 0.0)
                self.status_label.setText(f"書き出し中… {int(progress * 100)}%")
                return
        except Exception as exc:
            self._error("状態取得", exc)
            return
        self._poll.stop()
        self._render_id = None
        self.render_button.setEnabled(True)
        if r["status"] == "SUCCEEDED":
            self.status_label.setText(f"書き出し完了: {r['output_path']}（{r.get('encoder')}）")
        else:
            self.status_label.setText(
                f"書き出しが {r['status']} で終了しました（{r.get('error')}）"
            )
