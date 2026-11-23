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
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
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

        self.clip_table = QTableWidget(0, 6)
        self.clip_table.setHorizontalHeaderLabels(
            ["トラック", "開始", "終了", "素材", "音量", "演出"]
        )
        self.clip_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.clip_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
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

        # ---- 演出（Phase 7）----
        self.preset_combo = QComboBox()
        self.intensity_spin = QDoubleSpinBox()
        self.intensity_spin.setRange(0.1, 3.0)
        self.intensity_spin.setSingleStep(0.1)
        self.intensity_spin.setValue(1.0)
        self.preset_button = QPushButton("演出を適用")
        self.preset_button.clicked.connect(self.apply_preset)
        self.transition_combo = QComboBox()
        self.transition_frames = QSpinBox()
        self.transition_frames.setRange(1, 600)
        self.transition_frames.setValue(15)
        self.transition_frames.setSuffix(" フレーム")
        self.transition_button = QPushButton("入りの切替を設定")
        self.transition_button.clicked.connect(self.apply_transition)
        self.clear_anim_button = QPushButton("演出を外す")
        self.clear_anim_button.clicked.connect(self.clear_animation)
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("テロップの文字")
        self.title_anim_combo = QComboBox()
        self.title_pos_combo = QComboBox()
        for label, value in (("中央", "center"), ("下部（帯）", "lower_third"), ("上部", "top")):
            self.title_pos_combo.addItem(label, value)
        self.title_duration = QDoubleSpinBox()
        self.title_duration.setRange(0.5, 30.0)
        self.title_duration.setValue(3.0)
        self.title_duration.setSuffix(" 秒")
        self.title_button = QPushButton("テロップを追加（表示中のフレームに）")
        self.title_button.clicked.connect(self.add_title)
        self._title_job: tuple[str, int] | None = None

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
        anim_box = QGroupBox("演出（選択中のクリップ）")
        af = QFormLayout(anim_box)
        ph = QHBoxLayout()
        ph.addWidget(self.preset_combo, 1)
        ph.addWidget(QLabel("強さ"))
        ph.addWidget(self.intensity_spin)
        ph.addWidget(self.preset_button)
        af.addRow("動き", ph)
        th = QHBoxLayout()
        th.addWidget(self.transition_combo, 1)
        th.addWidget(self.transition_frames)
        th.addWidget(self.transition_button)
        af.addRow("切替", th)
        af.addRow(self.clear_anim_button)
        title_box = QGroupBox("テロップ（Blender）")
        tf = QFormLayout(title_box)
        tf.addRow(self.title_edit)
        th2 = QHBoxLayout()
        th2.addWidget(self.title_anim_combo)
        th2.addWidget(self.title_pos_combo)
        th2.addWidget(self.title_duration)
        tf.addRow(th2)
        tf.addRow(self.title_button)
        left.addWidget(anim_box)
        left.addWidget(title_box)
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
        self.load_presets()
        self.load_project()

    def load_presets(self) -> None:
        if not hasattr(self.api, "motion_presets"):
            return
        try:
            presets = self.api.motion_presets()
        except Exception as exc:
            self._error("プリセット取得", exc)
            return
        self.preset_combo.clear()
        self.transition_combo.clear()
        self.title_anim_combo.clear()
        kinds = {"camera": "カメラ", "element": "動き"}
        for p in presets:
            if p["kind"] in kinds:
                self.preset_combo.addItem(f"{kinds[p['kind']]}: {p['label']}", p["name"])
            elif p["kind"] == "transition":
                frames = (p.get("transition") or {}).get("duration_frames", 15)
                self.transition_combo.addItem(p["label"], (p["name"], frames))
            elif p["kind"] == "text":
                self.title_anim_combo.addItem(p["label"], p["name"].removeprefix("text_"))

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
                self._anim_label(c.get("animation")),
            ]
            for col, v in enumerate(vals):
                t.setItem(r, col, QTableWidgetItem(str(v)))
        dur = tl["duration_frames"]
        self.duration_label.setText(f"長さ {fmt_frame(dur, fps)}（{dur} フレーム）")
        self.frame_spin.setRange(0, max(dur - 1, 0))

    @staticmethod
    def _anim_label(anim: dict | None) -> str:
        if not anim:
            return ""
        parts = []
        if anim.get("preset"):
            parts.append(anim["preset"])
        elif anim.get("keyframes"):
            parts.append(f"キー{len(anim['keyframes'])}")
        if anim.get("transition_in"):
            parts.append(f"入り:{anim['transition_in']['type']}")
        return " / ".join(parts)

    def _selected_clip(self) -> str | None:
        rows = self.clip_table.selectionModel().selectedRows()
        if not rows or self._tl is None:
            self.status_label.setText("クリップを選択してください")
            return None
        return self._tl["clips"][rows[0].row()]["id"]

    def _set_animation(self, action: str, **kw: Any) -> None:
        pid, cid = self._pid(), self._selected_clip()
        if not pid or not cid:
            return
        try:
            self.api.set_clip_animation(pid, cid, **kw)
        except Exception as exc:
            self._error(action, exc)
            return
        self.status_label.setText(f"{action}しました（プレビューで確認できます）")
        self.load_project()

    def apply_preset(self) -> None:
        name = self.preset_combo.currentData()
        if name:
            self._set_animation("演出を適用", preset=name, intensity=self.intensity_spin.value())

    def apply_transition(self) -> None:
        data = self.transition_combo.currentData()
        if data:
            tran = {"type": data[0], "duration_frames": self.transition_frames.value()}
            self._set_animation("切替を設定", transition=tran)

    def clear_animation(self) -> None:
        self._set_animation("演出を外", clear=True)

    def add_title(self) -> None:
        pid, text = self._pid(), self.title_edit.text().strip()
        if not pid or not text:
            self.status_label.setText("テロップの文字を入力してください")
            return
        pos = self.title_pos_combo.currentData()
        try:
            job = self.api.title_card(
                pid,
                text,
                animation=self.title_anim_combo.currentData() or "pop",
                position=pos,
                box=pos == "lower_third",
                duration_sec=self.title_duration.value(),
            )
        except Exception as exc:
            self._error("テロップ作成", exc)
            return
        self._title_job = (job["id"], self.frame_spin.value())
        self.title_button.setEnabled(False)
        self.status_label.setText("テロップを作成中…（Blender）")
        self._poll.start()

    def _poll_title(self) -> bool:
        """テロップ作成の Job を確認する。完了したら配置して True。"""
        if self._title_job is None:
            return True
        job_id, frame = self._title_job
        job = self.api.get_job(job_id)
        if job["status"] in ("PENDING", "RUNNING"):
            return False
        self._title_job = None
        self.title_button.setEnabled(True)
        pid = self._pid()
        if job["status"] != "SUCCEEDED" or not pid:
            self.status_label.setText(f"テロップ作成に失敗しました（{job.get('error')}）")
            return True
        try:
            self.api.place_on_track(pid, job["result"]["id"], frame, "テロップ")
        except Exception as exc:
            self._error("テロップの配置", exc)
            return True
        self.status_label.setText("テロップを配置しました")
        self.load_project()
        return True

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
        try:
            title_done = self._poll_title()
        except Exception as exc:
            self._error("テロップ作成の状態取得", exc)
            title_done = False
        if not self._render_id:
            if title_done:
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
        if title_done:
            self._poll.stop()
        self._render_id = None
        self.render_button.setEnabled(True)
        if r["status"] == "SUCCEEDED":
            self.status_label.setText(f"書き出し完了: {r['output_path']}（{r.get('encoder')}）")
        else:
            self.status_label.setText(
                f"書き出しが {r['status']} で終了しました（{r.get('error')}）"
            )
