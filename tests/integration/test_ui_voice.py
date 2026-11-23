import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)


class FakeVoiceApi:
    def __init__(self):
        self.words = []
        self.preview_args = None

    def voice_engines(self):
        return [
            {
                "engine_id": "voicevox-nemo",
                "available": True,
                "name": "VOICEVOX Nemo Engine",
                "version": "0.26.0",
                "terms_note": "クレジット表記が必要",
            }
        ]

    def voice_speakers(self):
        return [{"name": "女声1", "styles": [{"id": 10005, "name": "ノーマル"}]}]

    def voice_dictionary(self):
        return self.words

    def voice_preview(self, text, style_id, prosody):
        self.preview_args = (text, style_id, prosody)
        return b"RIFF....WAVE"

    def voice_generate(self, text, style_id, prosody, project_id=None):
        return {"id": "j1"}

    def get_job(self, job_id):
        return {
            "status": "SUCCEEDED",
            "result": [
                {
                    "path": "projects/_shared/audio/a.wav",
                    "duration_sec": 1.5,
                    "credit": "VOICEVOX Nemo",
                }
            ],
        }

    def voice_add_word(self, surface, pron, accent, priority=5):
        self.words.append(
            {
                "id": "w1",
                "surface": surface,
                "pronunciation": pron,
                "accent_type": accent,
                "priority": priority,
            }
        )

    def voice_delete_word(self, word_id):
        self.words = [w for w in self.words if w["id"] != word_id]


def test_voice_widget_flow(monkeypatch):
    from PySide6.QtWidgets import QApplication

    from video_automation.ui.voice.widget import VoiceWidget

    app = QApplication.instance() or QApplication([])
    api = FakeVoiceApi()
    w = VoiceWidget(api)
    monkeypatch.setattr(w, "_play", lambda wav: None)
    w.refresh()
    assert "Nemo" in w.engine_label.text() and w.style_combo.count() == 1

    w.preview()  # テキスト未入力
    assert "指定してください" in w.status_label.text()
    w.text_edit.setPlainText("こんにちは")
    w.prosody_inputs["speed_scale"].setValue(1.3)
    w.preview()
    assert api.preview_args[1] == 10005 and api.preview_args[2]["speed_scale"] == 1.3

    w.generate()
    w._poll_job()
    assert "1.50秒" in w.status_label.text() and "VOICEVOX Nemo" in w.status_label.text()

    w.word_surface.setText("動画")
    w.word_pron.setText("ドウガ")
    w.add_word()
    assert w.dict_table.rowCount() == 1
    w.dict_table.selectRow(0)
    w.delete_word()
    assert w.dict_table.rowCount() == 0
    assert app is not None
