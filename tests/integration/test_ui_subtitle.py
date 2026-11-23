import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)


class FakeApi:
    def __init__(self):
        self.calls = []

    def list_projects(self):
        return [
            {"id": "p1", "name": "P", "width": 1920, "height": 1080, "fps_num": 30, "fps_den": 1}
        ]

    def voice_speakers(self):
        return [{"name": "女声1", "styles": [{"id": 10005, "name": "ノーマル"}]}]

    def create_script(self, pid, text, style, title=""):
        self.calls.append(("create", pid, style))
        return {"id": "s1", "segments": [{}, {}]}

    def script_voice(self, sid):
        return {"id": "j1"}

    def get_job(self, jid):
        return {"status": "SUCCEEDED"}

    def subtitles_generate(self, sid, max_chars):
        self.calls.append(("gen", max_chars))
        return []

    def subtitles_sync(self, sid):
        return [
            {
                "index": 0,
                "start_frame": 3,
                "end_frame": 90,
                "speaker": "A",
                "text": "一行目\n二行目",
            }
        ]

    def subtitles_export(self, sid, fmt):
        return {"path": f"projects/p1/subtitles/s1.{fmt}"}


def test_subtitle_widget_flow():
    from PySide6.QtWidgets import QApplication

    from video_automation.ui.subtitle.widget import SubtitleWidget, fmt_frame

    app = QApplication.instance() or QApplication([])
    api = FakeApi()
    w = SubtitleWidget(api)
    w.refresh()
    assert not w.voice_button.isEnabled()
    w.create_script()
    assert "指定してください" in w.status_label.text()
    w.text_edit.setPlainText("A：こんにちは。")
    w.create_script()
    assert api.calls[0] == ("create", "p1", 10005) and w.voice_button.isEnabled()
    w.generate_voice()
    w._poll_job()
    assert w.sync_button.isEnabled()
    w.generate_and_sync()
    assert api.calls[-1] == ("gen", None)
    assert w.cue_table.item(0, 1).text() == "00:00.100"
    assert w.cue_table.item(0, 4).text() == "一行目 / 二行目"
    w.export("ass")
    assert "s1.ass" in w.status_label.text()
    assert fmt_frame(None, 30) == "-" and fmt_frame(1830, 30) == "01:01.000"
    assert app is not None


def test_subtitle_widget_selects_existing_script():
    from PySide6.QtWidgets import QApplication

    from video_automation.ui.subtitle.widget import SubtitleWidget

    QApplication.instance() or QApplication([])
    api = FakeApi()
    api.list_scripts = lambda pid: [
        {
            "id": "s9",
            "title": "AI台本",
            "mode": "FULL_AI",
            "segments": [{"audio_file_id": "a"}, {"audio_file_id": "b"}],
        },
        {"id": "s8", "title": "", "mode": "MANUAL", "segments": [{"audio_file_id": None}]},
    ]
    api.subtitle_cues = lambda sid: [
        {"index": 0, "start_frame": 3, "end_frame": 30, "speaker": "A", "text": "あ"}
    ]
    w = SubtitleWidget(api)
    w.refresh()
    assert w.script_combo.count() == 3
    w.script_combo.setCurrentIndex(1)
    assert w.script_id == "s9" and w.ass_button.isEnabled() and w.cue_table.rowCount() == 1
    w.script_combo.setCurrentIndex(2)
    assert w.script_id == "s8" and w.voice_button.isEnabled() and not w.sync_button.isEnabled()
    w.script_combo.setCurrentIndex(0)
    assert w.script_id is None and not w.voice_button.isEnabled()
