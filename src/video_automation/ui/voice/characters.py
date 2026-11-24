"""声ごとの口調の UI。話者（例: ずんだもん）ごとに一人称・語尾・話し方を登録する。

登録した口調は、台本作成でその声を選び「口調」を空にしたときに使われる。
組み込みの口調は上書き（自分の設定を優先）でき、削除すると組み込みに戻る。
"""

from typing import Any

from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from video_automation.ui.async_call import error_text


class VoiceCharactersWidget(QWidget):
    def __init__(self, api: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.api = api
        self.items: list[dict[str, Any]] = []

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["声", "一人称", "語尾", "話し方", "出典"])
        self.table.setColumnWidth(3, 360)
        self.table.itemSelectionChanged.connect(self._selected)
        self.speaker_edit = QLineEdit()
        self.speaker_edit.setPlaceholderText("エンジンの話者名（例: ずんだもん）")
        self.first_edit = QLineEdit()
        self.endings_edit = QLineEdit()
        self.endings_edit.setPlaceholderText("「、」区切り（例: のだ、なのだ）")
        self.tone_edit = QLineEdit()
        self.save_button = QPushButton("保存")
        self.save_button.clicked.connect(self.save)
        self.delete_button = QPushButton("削除（組み込みに戻す）")
        self.delete_button.clicked.connect(self.delete)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)

        form = QFormLayout()
        form.addRow("声", self.speaker_edit)
        form.addRow("一人称", self.first_edit)
        form.addRow("語尾", self.endings_edit)
        form.addRow("話し方", self.tone_edit)
        btns = QHBoxLayout()
        btns.addWidget(self.save_button)
        btns.addWidget(self.delete_button)
        layout = QVBoxLayout(self)
        layout.addWidget(self.table, 1)
        layout.addLayout(form)
        layout.addLayout(btns)
        layout.addWidget(self.status_label)

    def refresh(self) -> None:
        try:
            self.items = self.api.voice_characters()
        except Exception as exc:  # noqa: BLE001
            self.status_label.setText(f"エラー: {error_text(exc)}")
            return
        self.table.setRowCount(len(self.items))
        for i, c in enumerate(self.items):
            src = c.get("source") or ""
            if c.get("builtin"):
                src = "組み込み" + (f": {src}" if src else "")
            vals = [
                c["speaker"],
                c.get("first_person", ""),
                "、".join(c.get("endings") or []),
                c.get("tone", ""),
                src,
            ]
            for col, v in enumerate(vals):
                self.table.setItem(i, col, QTableWidgetItem(v))

    def _selected(self) -> None:
        row = self.table.currentRow()
        if not 0 <= row < len(self.items):
            return
        c = self.items[row]
        self.speaker_edit.setText(c["speaker"])
        self.first_edit.setText(c.get("first_person", ""))
        self.endings_edit.setText("、".join(c.get("endings") or []))
        self.tone_edit.setText(c.get("tone", ""))

    def save(self) -> None:
        sp = self.speaker_edit.text().strip()
        if not sp:
            self.status_label.setText("声（話者名）を入力してください")
            return
        endings = [
            e.strip() for e in self.endings_edit.text().replace(",", "、").split("、") if e.strip()
        ][:8]
        try:
            self.api.put_voice_character(
                sp,
                first_person=self.first_edit.text().strip(),
                endings=endings,
                tone=self.tone_edit.text().strip(),
            )
        except Exception as exc:  # noqa: BLE001
            self.status_label.setText(f"保存に失敗しました: {error_text(exc)}")
            return
        self.status_label.setText(f"「{sp}」の口調を保存しました")
        self.refresh()

    def delete(self) -> None:
        sp = self.speaker_edit.text().strip()
        if not sp:
            return
        try:
            self.api.delete_voice_character(sp)
        except Exception as exc:  # noqa: BLE001
            self.status_label.setText(f"削除に失敗しました: {error_text(exc)}")
            return
        self.status_label.setText(f"「{sp}」の自分の設定を削除しました")
        self.refresh()
