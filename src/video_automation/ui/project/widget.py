"""プロジェクトの UI。接続の確認、プロジェクトの一覧・新規作成、PC のファイルの取り込み。"""

from pathlib import Path
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from video_automation.ui.async_call import error_text

# 画面の大きさ（名前, 幅, 高さ）
SIZES = [
    ("横長 1920×1080（YouTube など）", 1920, 1080),
    ("縦長 1080×1920（ショート）", 1080, 1920),
    ("横長 3840×2160（4K）", 3840, 2160),
]
KIND = {"video": "動画", "audio": "音声", "image": "画像"}


class ProjectWidget(QWidget):
    # プロジェクトを作ったとき（ほかのタブの一覧も新しくする）
    projects_changed = Signal()

    def __init__(self, api: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.api = api
        self.projects: list[dict[str, Any]] = []

        self.status_label = QLabel("API: 未接続")
        self.project_list = QListWidget()
        self.project_list.currentRowChanged.connect(self.load_assets)
        self.refresh_button = QPushButton("接続確認 / 更新")

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("例: 富士山の豆知識")
        self.size_combo = QComboBox()
        for label, w, h in SIZES:
            self.size_combo.addItem(label, (w, h))
        self.fps_combo = QComboBox()
        for fps in (30, 60, 24):
            self.fps_combo.addItem(f"{fps} fps", fps)
        self.create_button = QPushButton("プロジェクトを作成")
        self.create_button.clicked.connect(self.create_project)

        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("取り込むファイル（動画・音声・画像）のパス")
        self.browse_button = QPushButton("参照…")
        self.browse_button.clicked.connect(self.browse)
        self.import_button = QPushButton("選んだプロジェクトに取り込む")
        self.import_button.clicked.connect(self.import_file)
        self.asset_list = QListWidget()
        self.message_label = QLabel("")
        self.message_label.setWordWrap(True)

        new_box = QGroupBox("新しいプロジェクト")
        nf = QFormLayout(new_box)
        nf.addRow("名前", self.name_edit)
        nf.addRow("画面の大きさ", self.size_combo)
        nf.addRow("フレームレート", self.fps_combo)
        nf.addRow(self.create_button)

        imp_box = QGroupBox(
            "素材ファイルの取り込み（ビデオ・ピクチャ・ミュージック・ダウンロード・デスクトップ）"
        )
        il = QVBoxLayout(imp_box)
        row = QHBoxLayout()
        row.addWidget(self.path_edit, 1)
        row.addWidget(self.browse_button)
        row.addWidget(self.import_button)
        il.addLayout(row)
        il.addWidget(QLabel("取り込んだ素材"))
        il.addWidget(self.asset_list)

        left = QVBoxLayout()
        left.addWidget(self.status_label)
        left.addWidget(QLabel("プロジェクト"))
        left.addWidget(self.project_list, 1)
        left.addWidget(self.refresh_button)
        right = QVBoxLayout()
        right.addWidget(new_box)
        right.addWidget(imp_box, 1)
        body = QHBoxLayout()
        body.addLayout(left, 1)
        body.addLayout(right, 1)
        layout = QVBoxLayout(self)
        layout.addLayout(body, 1)
        layout.addWidget(self.message_label)

    def show_projects(self, projects: list[dict[str, Any]], select: str | None = None) -> None:
        if select is None:  # 一覧を新しくしても、選んでいたプロジェクトはそのまま
            select = self.project_id()
        self.projects = projects
        self.project_list.blockSignals(True)
        self.project_list.clear()
        for p in projects:
            self.project_list.addItem(f"{p['name']}（{p['width']}×{p['height']}）")
        self.project_list.blockSignals(False)
        if projects:
            idx = next((i for i, p in enumerate(projects) if p.get("id") == select), 0)
            self.project_list.setCurrentRow(idx)

    def project_id(self) -> str | None:
        row = self.project_list.currentRow()
        return self.projects[row].get("id") if 0 <= row < len(self.projects) else None

    def create_project(self) -> None:
        name = self.name_edit.text().strip()
        if not name:
            self.message_label.setText("プロジェクトの名前を入力してください")
            return
        w, h = self.size_combo.currentData()
        try:
            p = self.api.create_project(
                name, width=w, height=h, fps_num=self.fps_combo.currentData(), fps_den=1
            )
            projects = self.api.list_projects()
        except Exception as exc:  # noqa: BLE001
            self.message_label.setText(f"作成できませんでした: {error_text(exc)}")
            return
        self.name_edit.clear()
        self.show_projects(projects, p.get("id"))
        self.message_label.setText(f"「{p['name']}」を作りました。ほかのタブでも選べます")
        self.projects_changed.emit()

    def browse(self) -> None:
        start = str(Path.home() / "Videos")
        path, _ = QFileDialog.getOpenFileName(
            self,
            "取り込むファイル",
            start,
            "動画・音声・画像 (*.mp4 *.mov *.mkv *.webm *.wav *.mp3 *.m4a *.flac *.png *.jpg"
            " *.jpeg *.webp);;すべて (*.*)",
        )
        if path:
            self.path_edit.setText(path)

    def import_file(self) -> None:
        pid = self.project_id()
        path = self.path_edit.text().strip().strip('"')
        if not pid or not path:
            self.message_label.setText("プロジェクトを選び、ファイルのパスを入力してください")
            return
        try:
            a = self.api.import_asset(pid, path)
        except Exception as exc:  # noqa: BLE001
            self.message_label.setText(f"取り込めませんでした: {error_text(exc)}")
            return
        self.path_edit.clear()
        self.message_label.setText(
            f"取り込みました: {a['name']}（{KIND.get(a['kind'], a['kind'])}）"
        )
        self.load_assets()

    def load_assets(self, *_: Any) -> None:
        self.asset_list.clear()
        pid = self.project_id()
        if not pid or not hasattr(self.api, "list_assets"):
            return
        try:
            assets = self.api.list_assets(pid)
        except Exception as exc:  # noqa: BLE001
            self.message_label.setText(f"素材の一覧を読めません: {error_text(exc)}")
            return
        for a in assets:
            dur = a.get("duration_sec")
            extra = f" {dur:.1f} 秒" if dur else ""
            self.asset_list.addItem(f"[{KIND.get(a['kind'], a['kind'])}] {a['name']}{extra}")
