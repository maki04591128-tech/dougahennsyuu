"""メインウィンドウ (P1-08)。PySide6。"""

from PySide6.QtWidgets import (
    QLabel,
    QListWidget,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from video_automation.ui.api_client import ApiClient


class MainWindow(QMainWindow):
    def __init__(self, api: ApiClient) -> None:
        super().__init__()
        self.api = api
        self.setWindowTitle("VideoAutomation")
        self.resize(900, 600)

        self.status_label = QLabel("API: 未接続")
        self.project_list = QListWidget()
        self.refresh_button = QPushButton("接続確認 / 更新")
        self.refresh_button.clicked.connect(self.refresh)

        layout = QVBoxLayout()
        layout.addWidget(self.status_label)
        layout.addWidget(self.project_list)
        layout.addWidget(self.refresh_button)
        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)

    def refresh(self) -> None:
        try:
            h = self.api.health()
            self.status_label.setText(f"API: {h['status']} (v{h['version']}, DB {h['database']})")
            self.project_list.clear()
            for p in self.api.list_projects():
                self.project_list.addItem(f"{p['name']} ({p['width']}x{p['height']})")
        except Exception as exc:  # noqa: BLE001
            self.status_label.setText(f"API: 接続失敗 ({type(exc).__name__})")
