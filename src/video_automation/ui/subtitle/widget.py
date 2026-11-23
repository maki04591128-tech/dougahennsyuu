"""台本・字幕UI (Phase 4)。台本入力 → 音声生成 → 字幕生成・同期 → ASS/SRT 出力。"""

from typing import Any

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


def fmt_frame(frame: int | None, fps: float) -> str:
    if frame is None:
        return "-"
    sec = frame / fps
    m, s = divmod(sec, 60)
    return f"{int(m):02d}:{s:06.3f}"


class SubtitleWidget(QWidget):
    def __init__(self, api: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.api = api
        self.script_id: str | None = None
        self._job_id: str | None = None
        self._projects: list[dict[str, Any]] = []

        self.project_combo = QComboBox()
        self.style_combo = QComboBox()
        self.max_chars = QSpinBox()
        self.max_chars.setRange(0, 60)
        self.max_chars.setSpecialValueText("自動")
        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlaceholderText(
            "台本を入力（「話者：セリフ」形式可。空行でシーン区切り）"
        )
        self.create_button = QPushButton("1. 台本を作成")
        self.create_button.clicked.connect(self.create_script)
        self.voice_button = QPushButton("2. 音声を生成")
        self.voice_button.clicked.connect(self.generate_voice)
        self.sync_button = QPushButton("3. 字幕を生成・同期")
        self.sync_button.clicked.connect(self.generate_and_sync)
        self.ass_button = QPushButton("ASS出力")
        self.ass_button.clicked.connect(lambda: self.export("ass"))
        self.srt_button = QPushButton("SRT出力")
        self.srt_button.clicked.connect(lambda: self.export("srt"))
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)

        self.cue_table = QTableWidget(0, 5)
        self.cue_table.setHorizontalHeaderLabels(["#", "開始", "終了", "話者", "字幕"])
        self.cue_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.cue_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.cue_table.horizontalHeader().setStretchLastSection(True)

        form = QFormLayout()
        form.addRow("プロジェクト", self.project_combo)
        form.addRow("既定の話者", self.style_combo)
        form.addRow("1行の最大文字数", self.max_chars)
        btns = QHBoxLayout()
        for b in (
            self.create_button,
            self.voice_button,
            self.sync_button,
            self.ass_button,
            self.srt_button,
        ):
            btns.addWidget(b)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.text_edit)
        layout.addLayout(btns)
        layout.addWidget(self.status_label)
        layout.addWidget(self.cue_table)
        self._set_stage(0)

        self._poll = QTimer(self)
        self._poll.setInterval(700)
        self._poll.timeout.connect(self._poll_job)

    def _set_stage(self, stage: int) -> None:
        self.voice_button.setEnabled(stage >= 1)
        self.sync_button.setEnabled(stage >= 2)
        self.ass_button.setEnabled(stage >= 3)
        self.srt_button.setEnabled(stage >= 3)

    def _error(self, action: str, exc: Exception) -> None:
        detail = ""
        resp = getattr(exc, "response", None)
        if resp is not None:
            try:
                detail = f": {resp.json().get('message', '')}"
            except ValueError:
                pass
        self.status_label.setText(f"{action}に失敗しました ({type(exc).__name__}{detail})")

    def refresh(self) -> None:
        try:
            self._projects = self.api.list_projects()
            self.project_combo.clear()
            for p in self._projects:
                self.project_combo.addItem(f"{p['name']} ({p['width']}x{p['height']})", p["id"])
            self.style_combo.clear()
            for sp in self.api.voice_speakers():
                for st in sp["styles"]:
                    self.style_combo.addItem(f"{sp['name']}（{st['name']}）", st["id"])
        except Exception as exc:
            self._error("読み込み", exc)

    def _fps(self) -> float:
        pid = self.project_combo.currentData()
        p = next((x for x in self._projects if x["id"] == pid), None)
        return p["fps_num"] / p["fps_den"] if p and p.get("fps_num") else 30.0

    def create_script(self) -> None:
        pid, text = self.project_combo.currentData(), self.text_edit.toPlainText().strip()
        if not pid or not text:
            self.status_label.setText("プロジェクトと台本を指定してください")
            return
        try:
            script = self.api.create_script(pid, text, self.style_combo.currentData())
        except Exception as exc:
            self._error("台本作成", exc)
            return
        self.script_id = script["id"]
        self.status_label.setText(f"台本を作成しました（{len(script['segments'])} セグメント）")
        self._set_stage(1)

    def generate_voice(self) -> None:
        if not self.script_id:
            return
        try:
            self._job_id = self.api.script_voice(self.script_id)["id"]
        except Exception as exc:
            self._error("音声生成", exc)
            return
        self.voice_button.setEnabled(False)
        self.status_label.setText("音声を生成中…")
        self._poll.start()

    def _poll_job(self) -> None:
        if not self._job_id:
            self._poll.stop()
            return
        try:
            job = self.api.get_job(self._job_id)
        except Exception as exc:
            self._error("状態取得", exc)
            return
        if job["status"] in ("PENDING", "RUNNING"):
            self.status_label.setText(f"音声を生成中… {int(job.get('progress', 0) * 100)}%")
            return
        self._poll.stop()
        self._job_id = None
        self.voice_button.setEnabled(True)
        if job["status"] != "SUCCEEDED":
            self.status_label.setText(f"音声生成に失敗しました（{job.get('error')}）")
            return
        self.status_label.setText("音声を生成しました")
        self._set_stage(2)

    def generate_and_sync(self) -> None:
        if not self.script_id:
            return
        try:
            self.api.subtitles_generate(self.script_id, self.max_chars.value() or None)
            cues = self.api.subtitles_sync(self.script_id)
        except Exception as exc:
            self._error("字幕の生成・同期", exc)
            return
        self.show_cues(cues)
        self.status_label.setText(f"字幕 {len(cues)} 件を音声に同期しました")
        self._set_stage(3)

    def show_cues(self, cues: list[dict[str, Any]]) -> None:
        fps = self._fps()
        t = self.cue_table
        t.setRowCount(len(cues))
        for r, c in enumerate(cues):
            vals = [
                c["index"] + 1,
                fmt_frame(c["start_frame"], fps),
                fmt_frame(c["end_frame"], fps),
                c["speaker"],
                c["text"].replace("\n", " / "),
            ]
            for col, v in enumerate(vals):
                t.setItem(r, col, QTableWidgetItem(str(v)))

    def export(self, fmt: str) -> None:
        if not self.script_id:
            return
        try:
            r = self.api.subtitles_export(self.script_id, fmt)
        except Exception as exc:
            self._error(f"{fmt.upper()}出力", exc)
            return
        self.status_label.setText(f"{fmt.upper()} を出力しました: data/{r['path']}")
