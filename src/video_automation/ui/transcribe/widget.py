"""文字起こしの UI。取り込んだ音声・動画から字幕（または歌詞の時刻）を作る。

歌詞・台本の文があれば貼り付ける（文はそのまま使い、時刻だけを文字起こしから取る）。
モデルはローカルのものだけを使う（自動でダウンロードしない）。
"""

from typing import Any

from PySide6.QtCore import QTimer
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from video_automation.ui.async_call import error_text

MEDIA_KINDS = ("audio", "video")


def _t(sec: float) -> str:
    m, s = divmod(float(sec), 60)
    return f"{int(m):02d}:{s:05.2f}"


class TranscribeWidget(QWidget):
    def __init__(self, api: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.api = api
        self._job_id: str | None = None
        self.result: dict[str, Any] | None = None

        self.engine_label = QLabel("")
        self.engine_label.setWordWrap(True)
        self.project_combo = QComboBox()
        self.project_combo.currentIndexChanged.connect(self.load_assets)
        self.asset_combo = QComboBox()
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("台本の題名（空なら素材の名前）")
        self.lyrics_edit = QPlainTextEdit()
        self.lyrics_edit.setPlaceholderText(
            "歌詞・台本の文があれば貼り付け（1 行 1 字幕）。空なら聞き取った文を使います"
        )
        self.run_button = QPushButton("文字起こし")
        self.run_button.clicked.connect(self.run)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["開始", "終了", "文"])
        self.table.setColumnWidth(2, 600)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._poll)

        row = QHBoxLayout()
        row.addWidget(QLabel("プロジェクト"))
        row.addWidget(self.project_combo, 1)
        row.addWidget(QLabel("音声・動画"))
        row.addWidget(self.asset_combo, 1)
        row2 = QHBoxLayout()
        row2.addWidget(self.title_edit, 1)
        row2.addWidget(self.run_button)
        layout = QVBoxLayout(self)
        layout.addWidget(self.engine_label)
        layout.addLayout(row)
        layout.addWidget(self.lyrics_edit)
        layout.addLayout(row2)
        layout.addWidget(self.table, 2)
        layout.addWidget(self.status_label)

    def refresh(self) -> None:
        try:
            st = self.api.transcribe_status()
            projects = self.api.list_projects()
        except Exception as exc:  # noqa: BLE001
            self.status_label.setText(f"エラー: {error_text(exc)}")
            return
        if st.get("available"):
            self.engine_label.setText("文字起こし: 使えます（faster-whisper・ローカルのモデル）")
        else:
            why = []
            if not st.get("package"):
                why.append("faster-whisper が入っていません（uv sync --extra transcribe）")
            if not st.get("model"):
                why.append("モデルの場所が未設定か、model.bin がありません（VA_WHISPER_MODEL_DIR）")
            self.engine_label.setText("文字起こし: 使えません。" + "／".join(why))
        self.run_button.setEnabled(bool(st.get("available")))
        self.project_combo.blockSignals(True)
        self.project_combo.clear()
        for p in projects:
            self.project_combo.addItem(p["name"], p["id"])
        self.project_combo.blockSignals(False)
        self.load_assets()

    def load_assets(self, *_: Any) -> None:
        self.asset_combo.clear()
        pid = self.project_combo.currentData()
        if not pid:
            return
        try:
            assets = self.api.timeline(pid).get("assets", [])
        except Exception as exc:  # noqa: BLE001
            self.status_label.setText(f"エラー: {error_text(exc)}")
            return
        for a in assets:
            if a.get("kind") in MEDIA_KINDS:
                dur = a.get("duration_sec")
                self.asset_combo.addItem(a["name"] + (f"（{_t(dur)}）" if dur else ""), a["id"])

    def run(self) -> None:
        pid, aid = self.project_combo.currentData(), self.asset_combo.currentData()
        if not pid or not aid:
            self.status_label.setText("プロジェクトと音声・動画を選んでください")
            return
        kw: dict[str, Any] = {"title": self.title_edit.text().strip()}
        lyrics = self.lyrics_edit.toPlainText().strip()
        if lyrics:
            kw["lyrics"] = lyrics
        try:
            job = self.api.transcribe(pid, aid, **kw)
        except Exception as exc:  # noqa: BLE001
            self.status_label.setText(f"エラー: {error_text(exc)}")
            return
        self._job_id = job["id"]
        self.run_button.setEnabled(False)
        self.status_label.setText("文字起こし中…")
        self._timer.start()

    def _poll(self) -> None:
        if not self._job_id:
            return
        try:
            job = self.api.get_job(self._job_id)
        except Exception as exc:  # noqa: BLE001
            self.status_label.setText(f"エラー: {error_text(exc)}")
            return
        if job["status"] in ("PENDING", "RUNNING"):
            self.status_label.setText(f"文字起こし中… {int(job.get('progress', 0) * 100)}%")
            return
        self._timer.stop()
        self._job_id = None
        self.run_button.setEnabled(True)
        if job["status"] != "SUCCEEDED":
            self.status_label.setText(f"文字起こしに失敗しました: {job.get('error')}")
            return
        self.show_result(job["result"])

    def show_result(self, r: dict[str, Any]) -> None:
        self.result = r
        weak = set(r.get("weak_lines") or [])
        self.table.setRowCount(len(r["lines"]))
        for i, ln in enumerate(r["lines"]):
            cells = [_t(ln["start_sec"]), _t(ln["end_sec"]), ln["text"]]
            for col, v in enumerate(cells):
                it = QTableWidgetItem(v)
                if i in weak:
                    it.setBackground(QBrush(QColor("#fff3c4")))
                    it.setForeground(QBrush(QColor("#000000")))
                    it.setToolTip("聞き取った文とほとんど一致しなかったため、時刻は推定です")
                self.table.setItem(i, col, it)
        msg = f"{len(r['lines'])} 行の台本を作りました（{_t(r['duration_sec'])}）。"
        if weak:
            msg += f"黄色の {len(weak)} 行は時刻が推定です。"
        self.status_label.setText(msg + "「台本・字幕」タブで字幕を作れます")
