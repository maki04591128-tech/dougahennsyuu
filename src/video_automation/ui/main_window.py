"""メインウィンドウ (P1-08)。タブ: プロジェクト / LLM設定 / 音声 / 台本・字幕。"""

from typing import Any

from PySide6.QtWidgets import (
    QLabel,
    QListWidget,
    QMainWindow,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from video_automation.ui.llm_settings.widget import LlmSettingsWidget
from video_automation.ui.subtitle.widget import SubtitleWidget
from video_automation.ui.voice.widget import VoiceWidget


class MainWindow(QMainWindow):
    def __init__(self, api: Any) -> None:
        super().__init__()
        self.api = api
        self.setWindowTitle("VideoAutomation")
        self.resize(1200, 800)

        self.status_label = QLabel("API: 未接続")
        self.project_list = QListWidget()
        self.refresh_button = QPushButton("接続確認 / 更新")
        self.refresh_button.clicked.connect(self.refresh)

        project_tab = QWidget()
        layout = QVBoxLayout(project_tab)
        layout.addWidget(self.status_label)
        layout.addWidget(self.project_list)
        layout.addWidget(self.refresh_button)

        self.llm_settings = LlmSettingsWidget(api)
        self.tabs = QTabWidget()
        self.tabs.addTab(project_tab, "プロジェクト")
        self.tabs.addTab(self.llm_settings, "LLM設定")
        self.voice = VoiceWidget(api)
        self.tabs.addTab(self.voice, "音声")
        self.subtitles = SubtitleWidget(api)
        self.tabs.addTab(self.subtitles, "台本・字幕")
        self.setCentralWidget(self.tabs)

    def refresh(self) -> None:
        try:
            h = self.api.health()
            self.status_label.setText(f"API: {h['status']} (v{h['version']}, DB {h['database']})")
            self.project_list.clear()
            for p in self.api.list_projects():
                self.project_list.addItem(f"{p['name']} ({p['width']}x{p['height']})")
        except Exception as exc:
            self.status_label.setText(f"API: 接続失敗 ({type(exc).__name__})")
            return
        if hasattr(self.api, "list_providers"):
            self.llm_settings.refresh()
        if hasattr(self.api, "voice_engines"):
            self.voice.refresh()
        if hasattr(self.api, "create_script"):
            self.subtitles.refresh()
