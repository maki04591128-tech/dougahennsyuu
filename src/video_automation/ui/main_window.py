"""メインウィンドウ (P1-08)。

タブ: プロジェクト / LLM設定 / 音声 / 台本作成（AI） / 台本・字幕 / 素材・BGM /
編集・書き出し / AIアシスタント / 品質検査 / 用途・デザイン / 声の口調 / 文字起こし。"""

from typing import Any

from PySide6.QtWidgets import (
    QMainWindow,
    QTabWidget,
)

from video_automation.ui.assets.widget import AssetsWidget
from video_automation.ui.assistant.widget import AssistantWidget
from video_automation.ui.design.widget import DesignWidget
from video_automation.ui.llm_settings.widget import LlmSettingsWidget
from video_automation.ui.project.widget import ProjectWidget
from video_automation.ui.qa.widget import QAWidget
from video_automation.ui.script_studio.widget import ScriptStudioWidget
from video_automation.ui.subtitle.widget import SubtitleWidget
from video_automation.ui.timeline.widget import TimelineWidget
from video_automation.ui.transcribe.widget import TranscribeWidget
from video_automation.ui.voice.characters import VoiceCharactersWidget
from video_automation.ui.voice.widget import VoiceWidget


class MainWindow(QMainWindow):
    def __init__(self, api: Any) -> None:
        super().__init__()
        self.api = api
        self.setWindowTitle("VideoAutomation")
        self.resize(1200, 800)

        project_tab = ProjectWidget(api)
        self.project = project_tab
        # 互換: 以前の属性名（テストや拡張から参照される）
        self.status_label = project_tab.status_label
        self.project_list = project_tab.project_list
        self.refresh_button = project_tab.refresh_button
        self.refresh_button.clicked.connect(self.refresh)
        project_tab.projects_changed.connect(self.refresh)

        self.llm_settings = LlmSettingsWidget(api)
        self.tabs = QTabWidget()
        self.tabs.addTab(project_tab, "プロジェクト")
        self.tabs.addTab(self.llm_settings, "LLM設定")
        self.voice = VoiceWidget(api)
        self.tabs.addTab(self.voice, "音声")
        self.studio = ScriptStudioWidget(api)
        self.tabs.addTab(self.studio, "台本作成（AI）")
        self.subtitles = SubtitleWidget(api)
        self.tabs.addTab(self.subtitles, "台本・字幕")
        self.assets = AssetsWidget(api)
        self.tabs.addTab(self.assets, "素材・BGM")
        self.timeline = TimelineWidget(api)
        self.tabs.addTab(self.timeline, "編集・書き出し")
        self.assistant = AssistantWidget(api)
        self.tabs.addTab(self.assistant, "AIアシスタント")
        self.qa = QAWidget(api)
        self.tabs.addTab(self.qa, "品質検査")
        self.design = DesignWidget(api)
        self.tabs.addTab(self.design, "用途・デザイン")
        self.voice_characters = VoiceCharactersWidget(api)
        self.tabs.addTab(self.voice_characters, "声の口調")
        self.transcribe = TranscribeWidget(api)
        self.tabs.addTab(self.transcribe, "文字起こし")
        self.setCentralWidget(self.tabs)
        self.tabs.currentChanged.connect(self._on_tab)

    def _on_tab(self, index: int) -> None:
        """タブを開いたとき、ほかのタブでの変更（演出の配置など）を読み直す。"""
        w = self.tabs.widget(index)
        if w is self.timeline:
            self.timeline.load_project()

    def refresh(self) -> None:
        try:
            h = self.api.health()
            self.status_label.setText(f"API: {h['status']} (v{h['version']}, DB {h['database']})")
            self.project.show_projects(self.api.list_projects())
        except Exception as exc:
            self.status_label.setText(f"API: 接続失敗 ({type(exc).__name__})")
            return
        if hasattr(self.api, "list_providers"):
            self.llm_settings.refresh()
        if hasattr(self.api, "voice_engines"):
            self.voice.refresh()
        if hasattr(self.api, "studio_create"):
            self.studio.refresh()
        if hasattr(self.api, "create_script"):
            self.subtitles.refresh()
        if hasattr(self.api, "asset_sources"):
            self.assets.refresh()
        if hasattr(self.api, "timeline"):
            self.timeline.refresh()
        if hasattr(self.api, "send_message"):
            self.assistant.refresh()
        if hasattr(self.api, "run_qa"):
            self.qa.refresh()
        if hasattr(self.api, "design_profiles"):
            self.design.refresh()
        if hasattr(self.api, "voice_characters"):
            self.voice_characters.refresh()
        if hasattr(self.api, "transcribe_status"):
            self.transcribe.refresh()
