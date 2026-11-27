"""用途（デザインの型）の UI。指示文から用途を判定し、プロジェクトに設定し、書き出す前の検査を見る。

用途を決めると、その型で使わない部品（別の用途のデザイン）は演出・書き出しで使われない。
"""

from typing import Any

from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from video_automation.ui.async_call import error_text

SEVERITY = {"error": "✖ 要修正", "warning": "⚠ 要確認"}
COLOR = {"error": "#ffd6d6", "warning": "#fff3c4"}
# 部品の名前（画面に出す日本語）
PART = {
    "subtitle": "字幕",
    "speaker_label": "話者名",
    "title_card": "題名",
    "card": "要点カード",
    "chart": "図表",
    "image": "画像",
    "keyword": "要点テロップ",
    "chapter_label": "章ラベル",
    "lower_third": "名前の帯",
    "emphasis_text": "強調テロップ",
    "kinetic_text": "動く文字",
    "lyric_text": "歌詞",
    "background_motion": "背景の動き",
    "transition_cut": "カット・フェード",
    "transition_shape": "図形の転換",
    "transition_whip": "ウィップ",
    "shake_punch": "揺れ・パンチ",
    "flash": "フラッシュ",
    "glitch": "グリッチ",
    "bloom": "光のにじみ",
    "letterbox": "黒帯",
    "beat_cut": "拍でカット",
    "sfx": "効果音",
    "character": "立ち絵",
    "grain": "グレイン・周辺減光・色収差",
    "glow": "グロー",
    "halation": "ハレーション",
}


def _names(items: Any) -> str:
    return "、".join(PART.get(str(c), str(c)) for c in items)


MOTION = ["フェードのみ", "控えめ", "標準", "強め", "拍に同期・強い演出"]


class DesignWidget(QWidget):
    def __init__(self, api: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.api = api
        self.profiles: dict[str, dict[str, Any]] = {}

        self.project_combo = QComboBox()
        self.project_combo.currentIndexChanged.connect(self.load_project)
        self.request_edit = QLineEdit()
        self.request_edit.setPlaceholderText(
            "どんな動画か（例: キャラの掛け合いで物質を解説する動画）"
        )
        self.classify_button = QPushButton("用途を判定")
        self.classify_button.clicked.connect(self.classify)
        self.purpose_combo = QComboBox()
        self.purpose_combo.currentIndexChanged.connect(self._show_profile)
        self.save_button = QPushButton("この用途にする")
        self.save_button.clicked.connect(self.save)
        self.profile_label = QLabel("")
        self.profile_label.setWordWrap(True)
        self.script_combo = QComboBox()
        self.check_button = QPushButton("書き出す前の検査")
        self.check_button.clicked.connect(self.check)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["結果", "内容"])
        self.tree.setColumnWidth(0, 140)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("プロジェクト"))
        row1.addWidget(self.project_combo, 1)
        row2 = QHBoxLayout()
        row2.addWidget(self.request_edit, 1)
        row2.addWidget(self.classify_button)
        row3 = QHBoxLayout()
        row3.addWidget(QLabel("用途"))
        row3.addWidget(self.purpose_combo, 1)
        row3.addWidget(self.save_button)
        row4 = QHBoxLayout()
        row4.addWidget(QLabel("台本（字幕の検査）"))
        row4.addWidget(self.script_combo, 1)
        row4.addWidget(self.check_button)
        layout = QVBoxLayout(self)
        for r in (row1, row2, row3):
            layout.addLayout(r)
        layout.addWidget(self.profile_label)
        layout.addLayout(row4)
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.status_label)

    def _err(self, what: str, exc: BaseException) -> None:
        self.status_label.setText(f"{what}に失敗しました: {error_text(exc)}")

    def refresh(self) -> None:
        try:
            projects = self.api.list_projects()
            profiles = self.api.design_profiles()
        except Exception as exc:  # noqa: BLE001
            self._err("読み込み", exc)
            return
        self.profiles = {p["purpose"]: p for p in profiles}
        self.purpose_combo.blockSignals(True)
        self.purpose_combo.clear()
        self.purpose_combo.addItem("（未設定）", None)
        for p in profiles:
            self.purpose_combo.addItem(p["label"], p["purpose"])
        self.purpose_combo.blockSignals(False)
        self.project_combo.blockSignals(True)
        self.project_combo.clear()
        for p in projects:
            self.project_combo.addItem(p["name"], p["id"])
        self.project_combo.blockSignals(False)
        self.load_project()

    def load_project(self, *_: Any) -> None:
        pid = self.project_combo.currentData()
        self.tree.clear()
        if not pid:
            return
        try:
            d = self.api.project_design(pid)
            scripts = self.api.list_scripts(pid)
        except Exception as exc:  # noqa: BLE001
            self._err("読み込み", exc)
            return
        self._select(d.get("purpose"))
        self.script_combo.clear()
        self.script_combo.addItem("（字幕を検査しない）", None)
        for s in scripts:
            self.script_combo.addItem(s.get("title") or s["id"], s["id"])

    def _select(self, purpose: str | None) -> None:
        i = self.purpose_combo.findData(purpose)
        self.purpose_combo.setCurrentIndex(max(0, i))
        self._show_profile()

    def _show_profile(self, *_: Any) -> None:
        p = self.profiles.get(self.purpose_combo.currentData() or "")
        if p is None:
            self.profile_label.setText(
                "用途が未設定です。決めると、別の用途のデザインが混ざらないように検査します。"
            )
            return
        lim = "、".join(f"{PART.get(k, k)}×{v}" for k, v in (p.get("limited") or {}).items())
        self.profile_label.setText(
            f"{p['summary']}\n動き: {MOTION[min(4, int(p['motion_level']))]}　"
            f"使える部品: {_names(p['allowed'])}"
            + (f"\n回数の上限: {lim}" if lim else "")
            + f"\n使わない部品: {_names(p['denied'])}"
        )

    def classify(self) -> None:
        text = self.request_edit.text().strip()
        if not text:
            self.status_label.setText("どんな動画かを入力してください")
            return
        try:
            r = self.api.classify_purpose(text)
        except Exception as exc:  # noqa: BLE001
            self._err("判定", exc)
            return
        self._select(r["purpose"])
        label = self.profiles.get(r["purpose"], {}).get("label", r["purpose"])
        msg = f"判定: {label}（確からしさ {r['confidence']:.0%}）"
        if r.get("needs_confirmation"):
            others = [
                self.profiles.get(c["purpose"], {}).get("label", c["purpose"])
                for c in r.get("candidates", [])[1:3]
            ]
            msg += f"。自信がありません。ほかの候補: {'、'.join(others)}"
        if r.get("note"):
            msg += f"\n{r['note']}"
        self.status_label.setText(msg + "\n合っていれば「この用途にする」を押してください")

    def save(self) -> None:
        pid = self.project_combo.currentData()
        if not pid:
            return
        try:
            d = self.api.set_project_design(pid, self.purpose_combo.currentData())
        except Exception as exc:  # noqa: BLE001
            self._err("保存", exc)
            return
        self.status_label.setText(f"用途を保存しました: {self.purpose_combo.currentText()}")
        self._select(d.get("purpose"))

    def check(self) -> None:
        pid = self.project_combo.currentData()
        if not pid:
            return
        try:
            r = self.api.design_check(pid, self.script_combo.currentData())
        except Exception as exc:  # noqa: BLE001
            self._err("検査", exc)
            return
        self.tree.clear()
        for v in r["violations"]:
            item = QTreeWidgetItem([SEVERITY.get(v["severity"], v["severity"]), v["message"]])
            brush = QBrush(QColor(COLOR.get(v["severity"], "#ffffff")))
            for col in range(2):
                item.setBackground(col, brush)
                item.setForeground(col, QBrush(QColor("#000000")))  # 暗いテーマでも読める
            self.tree.addTopLevelItem(item)
        used = "、".join(f"{PART.get(k, k)}×{n}" for k, n in (r.get("used") or {}).items())
        if not r["violations"]:
            self.tree.addTopLevelItem(QTreeWidgetItem(["✔ 問題なし", "用途の型に合っています"]))
        self.status_label.setText(
            ("書き出せます" if r["ok"] else "このままでは書き出しが止まります")
            + (f"（使った部品: {used}）" if used else "")
        )
