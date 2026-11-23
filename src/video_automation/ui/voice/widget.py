"""音声設定UI (P3-07 プレビュー / P3-05 抑揚 / P3-06 辞書)。UIはAPI経由でのみ操作する。"""

import tempfile
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

# (キー, ラベル, 最小, 最大, 既定, 刻み)
PROSODY_FIELDS = [
    ("speed_scale", "話速", 0.5, 2.0, 1.0, 0.05),
    ("pitch_scale", "音高", -0.15, 0.15, 0.0, 0.01),
    ("intonation_scale", "抑揚", 0.0, 2.0, 1.0, 0.05),
    ("volume_scale", "音量", 0.0, 2.0, 1.0, 0.05),
    ("pause_length_scale", "句読点の間", 0.0, 2.0, 1.0, 0.05),
]


class VoiceWidget(QWidget):
    def __init__(self, api: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.api = api
        self.last_preview: bytes | None = None
        self._player: Any = None
        self._job_id: str | None = None

        self.engine_label = QLabel("音声エンジン: 未確認")
        self.engine_label.setWordWrap(True)
        self.style_combo = QComboBox()
        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlaceholderText("読み上げるテキスト（1000文字まで）")
        self.text_edit.setMaximumHeight(90)

        self.prosody_inputs: dict[str, QDoubleSpinBox] = {}
        pro_box = QGroupBox("抑揚・話速")
        pro_form = QFormLayout(pro_box)
        for key, label, lo, hi, default, step in PROSODY_FIELDS:
            sb = QDoubleSpinBox()
            sb.setRange(lo, hi)
            sb.setSingleStep(step)
            sb.setDecimals(2)
            sb.setValue(default)
            self.prosody_inputs[key] = sb
            pro_form.addRow(label, sb)

        self.preview_button = QPushButton("試聴（保存しない）")
        self.preview_button.clicked.connect(self.preview)
        self.generate_button = QPushButton("音声を生成して保存")
        self.generate_button.clicked.connect(self.generate)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)

        # 辞書
        self.dict_table = QTableWidget(0, 4)
        self.dict_table.setHorizontalHeaderLabels(
            ["表記", "読み（カタカナ）", "アクセント型", "優先度"]
        )
        self.dict_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.dict_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.word_surface = QLineEdit()
        self.word_pron = QLineEdit()
        self.word_accent = QSpinBox()
        self.word_accent.setRange(0, 50)
        self.add_word_button = QPushButton("辞書に登録")
        self.add_word_button.clicked.connect(self.add_word)
        self.del_word_button = QPushButton("選択を削除")
        self.del_word_button.clicked.connect(self.delete_word)
        dict_box = QGroupBox("読み辞書")
        dl = QVBoxLayout(dict_box)
        dl.addWidget(self.dict_table)
        row = QHBoxLayout()
        for w in (
            QLabel("表記"),
            self.word_surface,
            QLabel("読み"),
            self.word_pron,
            QLabel("アクセント"),
            self.word_accent,
            self.add_word_button,
            self.del_word_button,
        ):
            row.addWidget(w)
        dl.addLayout(row)

        btns = QHBoxLayout()
        btns.addWidget(self.preview_button)
        btns.addWidget(self.generate_button)
        btns.addStretch()
        layout = QVBoxLayout(self)
        layout.addWidget(self.engine_label)
        form = QFormLayout()
        form.addRow("話者", self.style_combo)
        layout.addLayout(form)
        layout.addWidget(self.text_edit)
        layout.addWidget(pro_box)
        layout.addLayout(btns)
        layout.addWidget(self.status_label)
        layout.addWidget(dict_box)

        self._poll = QTimer(self)
        self._poll.setInterval(500)
        self._poll.timeout.connect(self._poll_job)

    def _error(self, action: str, exc: Exception) -> None:
        detail = ""
        resp = getattr(exc, "response", None)
        if resp is not None:
            try:
                detail = f": {resp.json().get('message', '')}"
            except ValueError:
                pass
        self.status_label.setText(f"{action}に失敗しました ({type(exc).__name__}{detail})")

    def prosody(self) -> dict[str, float]:
        return {k: round(sb.value(), 3) for k, sb in self.prosody_inputs.items()}

    def refresh(self) -> None:
        try:
            engines = self.api.voice_engines()
            ok = [e for e in engines if e.get("available")]
            if not ok:
                self.engine_label.setText(
                    "音声エンジン: 未接続（VOICEVOX Nemo を起動してください）"
                )
                return
            e = ok[0]
            self.engine_label.setText(
                f"音声エンジン: {e.get('name')} v{e.get('version')}  {e.get('terms_note') or ''}"
            )
            self.style_combo.clear()
            for sp in self.api.voice_speakers():
                for st in sp["styles"]:
                    self.style_combo.addItem(f"{sp['name']}（{st['name']}）", st["id"])
            self._fill_dict(self.api.voice_dictionary())
        except Exception as exc:
            self._error("音声エンジン情報の取得", exc)

    def _fill_dict(self, words: list[dict[str, Any]]) -> None:
        t = self.dict_table
        t.setRowCount(len(words))
        for r, w in enumerate(words):
            for c, v in enumerate(
                [w["surface"], w["pronunciation"], w["accent_type"], w["priority"]]
            ):
                item = QTableWidgetItem(str(v))
                item.setData(Qt.ItemDataRole.UserRole, w["id"])
                t.setItem(r, c, item)

    def _input(self) -> tuple[str, int] | None:
        text = self.text_edit.toPlainText().strip()
        style = self.style_combo.currentData()
        if not text or style is None:
            self.status_label.setText("話者とテキストを指定してください")
            return None
        return text, int(style)

    def preview(self) -> None:
        inp = self._input()
        if inp is None:
            return
        try:
            self.last_preview = self.api.voice_preview(inp[0], inp[1], self.prosody())
        except Exception as exc:
            self._error("試聴", exc)
            return
        self.status_label.setText(f"試聴音声を取得しました（{len(self.last_preview):,} bytes）")
        self._play(self.last_preview)

    def _play(self, wav: bytes) -> None:  # pragma: no cover - 音声デバイス依存
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        except ImportError:
            return
        path = Path(tempfile.gettempdir()) / "video_automation_preview.wav"
        path.write_bytes(wav)
        if self._player is None:
            self._player = QMediaPlayer(self)
            self._audio_out = QAudioOutput(self)
            self._player.setAudioOutput(self._audio_out)
        self._player.setSource(QUrl.fromLocalFile(str(path)))
        self._player.play()

    def generate(self) -> None:
        inp = self._input()
        if inp is None:
            return
        try:
            job = self.api.voice_generate(inp[0], inp[1], self.prosody())
        except Exception as exc:
            self._error("音声生成", exc)
            return
        self._job_id = job["id"]
        self.generate_button.setEnabled(False)
        self.status_label.setText("音声を生成中…")
        self._poll.start()

    def _poll_job(self) -> None:
        if not self._job_id:
            self._poll.stop()
            return
        try:
            job = self.api.get_job(self._job_id)
        except Exception as exc:
            self._error("生成状態の取得", exc)
            return
        if job["status"] in ("PENDING", "RUNNING"):
            return
        self._poll.stop()
        self._job_id = None
        self.generate_button.setEnabled(True)
        if job["status"] != "SUCCEEDED":
            self.status_label.setText(f"音声生成に失敗しました（{job.get('error')}）")
            return
        a = job["result"][0]
        self.status_label.setText(
            f"保存しました: {a['path']}（{a['duration_sec']:.2f}秒） クレジット: {a.get('credit')}"
        )

    def add_word(self) -> None:
        try:
            self.api.voice_add_word(
                self.word_surface.text().strip(),
                self.word_pron.text().strip(),
                self.word_accent.value(),
            )
            self._fill_dict(self.api.voice_dictionary())
        except Exception as exc:
            self._error("辞書登録", exc)
            return
        self.word_surface.clear()
        self.word_pron.clear()
        self.status_label.setText("辞書に登録しました")

    def delete_word(self) -> None:
        row = self.dict_table.currentRow()
        item = self.dict_table.item(row, 0) if row >= 0 else None
        if item is None:
            return
        try:
            self.api.voice_delete_word(item.data(Qt.ItemDataRole.UserRole))
            self._fill_dict(self.api.voice_dictionary())
        except Exception as exc:
            self._error("辞書削除", exc)
