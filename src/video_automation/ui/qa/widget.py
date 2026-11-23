"""品質検査 UI（Phase 9）。検査を実行し、項目ごとの結果（error / warning / info）を一覧する。"""

from typing import Any

from PySide6.QtCore import QTimer
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from video_automation.ui.async_call import error_text

STATUS_LABEL = {
    "passed": "合格",
    "warnings": "要確認",
    "failed": "要修正",
    "skipped": "対象なし",
}
LEVEL_MARK = {"error": "✖ 要修正", "warning": "⚠ 要確認", "info": "ℹ 参考"}
LEVEL_COLOR = {"error": "#ffd6d6", "warning": "#fff3c4", "info": "#ffffff"}
STATUS_COLOR = {
    "passed": "#d9f2d9",
    "warnings": "#fff3c4",
    "failed": "#ffd6d6",
    "skipped": "#eeeeee",
}


def _sec(v: Any) -> str:
    if v is None:
        return ""
    m, s = divmod(float(v), 60)
    return f"{int(m):02d}:{s:05.2f}"


class QAWidget(QWidget):
    def __init__(self, api: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.api = api
        self._job_id: str | None = None
        self.report: dict[str, Any] | None = None

        self.project_combo = QComboBox()
        self.project_combo.currentIndexChanged.connect(self.load_history)
        self.history_combo = QComboBox()
        self.history_combo.currentIndexChanged.connect(self._history_selected)
        self.run_button = QPushButton("品質検査を実行")
        self.run_button.clicked.connect(self.run)
        self.summary_label = QLabel("")
        self.summary_label.setWordWrap(True)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["項目", "内容", "位置"])
        self.tree.setColumnWidth(0, 200)
        self.tree.setColumnWidth(1, 640)
        self.status_label = QLabel("")
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._poll)

        top = QHBoxLayout()
        top.addWidget(QLabel("プロジェクト"))
        top.addWidget(self.project_combo, 1)
        top.addWidget(QLabel("過去の結果"))
        top.addWidget(self.history_combo, 1)
        top.addWidget(self.run_button)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.summary_label)
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.status_label)

    def refresh(self) -> None:
        try:
            projects = self.api.list_projects()
        except Exception as exc:  # noqa: BLE001
            self.status_label.setText(f"エラー: {error_text(exc)}")
            return
        self.project_combo.blockSignals(True)
        self.project_combo.clear()
        for p in projects:
            self.project_combo.addItem(p["name"], p["id"])
        self.project_combo.blockSignals(False)
        self.load_history()

    def load_history(self, *_: Any) -> None:
        pid = self.project_combo.currentData()
        self.history_combo.blockSignals(True)
        self.history_combo.clear()
        if pid:
            for r in self.api.qa_reports(pid):
                n = r["counts"]
                when = str(r["created_at"])[:19].replace("T", " ")
                self.history_combo.addItem(
                    f"{when} {STATUS_LABEL.get(r['status'], r['status'])}"
                    f"（要修正 {n.get('error', 0)} / 要確認 {n.get('warning', 0)}）",
                    r["id"],
                )
        self.history_combo.blockSignals(False)
        if self.history_combo.count():
            self.history_combo.setCurrentIndex(0)
            self._history_selected()
        else:
            self.tree.clear()
            self.summary_label.setText("まだ検査していません")

    def _history_selected(self, *_: Any) -> None:
        rid = self.history_combo.currentData()
        if rid:
            self.show_report(self.api.qa_report(rid))

    def run(self) -> None:
        pid = self.project_combo.currentData()
        if not pid:
            self.status_label.setText("プロジェクトを選んでください")
            return
        try:
            job = self.api.run_qa(pid)
        except Exception as exc:  # noqa: BLE001
            self.status_label.setText(f"エラー: {error_text(exc)}")
            return
        self._job_id = job["id"]
        self.run_button.setEnabled(False)
        self.status_label.setText("検査中…（音声と動画を測定しています）")
        self._timer.start()

    def _poll(self) -> None:
        if not self._job_id:
            return
        job = self.api.get_job(self._job_id)
        if job["status"] in ("PENDING", "RUNNING"):
            self.status_label.setText(f"検査中… {int(job.get('progress', 0) * 100)}%")
            return
        self._timer.stop()
        self.run_button.setEnabled(True)
        self._job_id = None
        if job["status"] != "SUCCEEDED":
            self.status_label.setText(f"検査に失敗しました: {job.get('error')}")
            return
        self.status_label.setText("検査が終わりました")
        self.load_history()

    def show_report(self, rep: dict[str, Any]) -> None:
        self.report = rep
        self.tree.clear()
        counts = {"error": 0, "warning": 0, "info": 0}
        for sec in rep["sections"]:
            top = QTreeWidgetItem(
                [sec["label"], STATUS_LABEL.get(sec["status"], sec["status"]), ""]
            )
            brush = QBrush(QColor(STATUS_COLOR.get(sec["status"], "#ffffff")))
            for col in range(3):
                top.setBackground(col, brush)
            metrics = ", ".join(
                f"{k}={v}" for k, v in sec.get("metrics", {}).items() if v not in (None, [], "")
            )
            if metrics:
                top.setToolTip(1, metrics)
            for chk in sec["checks"]:
                counts[chk["level"]] = counts.get(chk["level"], 0) + 1
                child = QTreeWidgetItem(
                    [
                        LEVEL_MARK.get(chk["level"], chk["level"]),
                        chk["message"],
                        _sec(chk.get("at_sec")),
                    ]
                )
                cb = QBrush(QColor(LEVEL_COLOR.get(chk["level"], "#ffffff")))
                for col in range(3):
                    child.setBackground(col, cb)
                top.addChild(child)
            self.tree.addTopLevelItem(top)
            top.setExpanded(sec["status"] in ("failed", "warnings"))
        self.summary_label.setText(
            f"総合: {STATUS_LABEL.get(rep['status'], rep['status'])}　"
            f"要修正 {counts['error']} 件 / 要確認 {counts['warning']} 件 / "
            f"参考 {counts['info']} 件"
            f"（検査時間 {rep['duration_ms'] / 1000:.1f}秒）"
        )
