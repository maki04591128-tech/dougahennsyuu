"""UI: 用途・デザイン / 声の口調 / 文字起こし のタブ。本物の API（TestClient）につないで確かめる。"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

from fastapi.testclient import TestClient  # noqa: E402

from fakes import FakeProtector  # noqa: E402
from video_automation.api.app import create_app  # noqa: E402
from video_automation.ui.api_client import ApiClient  # noqa: E402


@pytest.fixture
def api(settings, fake):
    app = create_app(settings, http_transport=fake.transport, protector=FakeProtector())
    with TestClient(app, base_url="http://t/api/v1") as tc:
        a = ApiClient("http://t/api/v1", tc)
        a.create_project("UI テスト")
        yield a


def _qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_design_widget_classify_save_check(api):
    from video_automation.ui.design.widget import DesignWidget

    _qapp()
    w = DesignWidget(api)
    w.refresh()
    assert w.project_combo.count() == 1 and w.purpose_combo.count() > 10
    assert w.purpose_combo.currentData() is None and "未設定" in w.profile_label.text()

    w.request_edit.setText("")
    w.classify()
    assert "入力" in w.status_label.text()
    w.request_edit.setText("ゆっくり実況のゲーム実況動画を作りたい")
    w.classify()
    assert w.purpose_combo.currentData() is not None and "判定" in w.status_label.text()

    i = w.purpose_combo.findData("explainer")
    w.purpose_combo.setCurrentIndex(i)
    assert "使わない部品" in w.profile_label.text()
    w.save()
    pid = w.project_combo.currentData()
    assert api.project_design(pid)["purpose"] == "explainer"
    w.check()
    assert w.tree.topLevelItemCount() >= 1 and w.status_label.text()

    # 開き直しても保存した用途が選ばれている
    w.refresh()
    assert w.purpose_combo.currentData() == "explainer"


def test_voice_characters_widget(api):
    from video_automation.ui.voice.characters import VoiceCharactersWidget

    _qapp()
    w = VoiceCharactersWidget(api)
    w.refresh()
    builtin = w.table.rowCount()
    assert builtin >= 3 and "組み込み" in w.table.item(0, 4).text()

    w.speaker_edit.setText("")
    w.save()
    assert "入力" in w.status_label.text()
    w.speaker_edit.setText("テスト声")
    w.first_edit.setText("ぼく")
    w.endings_edit.setText("だよ、, だね ,")
    w.tone_edit.setText("明るく話す")
    w.save()
    assert w.table.rowCount() == builtin + 1
    got = {c["speaker"]: c for c in api.voice_characters()}["テスト声"]
    assert got["endings"] == ["だよ", "だね"] and got["first_person"] == "ぼく"

    row = next(i for i in range(w.table.rowCount()) if w.table.item(i, 0).text() == "テスト声")
    w.table.selectRow(row)
    assert w.tone_edit.text() == "明るく話す"
    w.delete()
    assert w.table.rowCount() == builtin


def test_transcribe_widget_without_model(api):
    from video_automation.ui.transcribe.widget import TranscribeWidget

    _qapp()
    w = TranscribeWidget(api)
    w.refresh()
    assert "使えません" in w.engine_label.text() and not w.run_button.isEnabled()
    assert w.project_combo.count() == 1 and w.asset_combo.count() == 0
    w.run()
    assert "選んで" in w.status_label.text()


def test_transcribe_widget_shows_result():
    from video_automation.ui.transcribe.widget import TranscribeWidget

    _qapp()

    class Fake:
        def __init__(self):
            self.polls = 0

        def transcribe_status(self):
            return {"available": True, "package": True, "model": True}

        def list_projects(self):
            return [{"id": "p1", "name": "P"}]

        def timeline(self, pid):
            return {
                "assets": [
                    {"id": "a1", "name": "song.wav", "kind": "audio", "duration_sec": 65.5},
                    {"id": "i1", "name": "pic.png", "kind": "image"},
                ]
            }

        def transcribe(self, pid, aid, **kw):
            self.kw = (pid, aid, kw)
            return {"id": "j1"}

        def get_job(self, jid):
            self.polls += 1
            if self.polls == 1:
                return {"status": "RUNNING", "progress": 0.4}
            lines = [
                {"text": "一行目", "start_sec": 1.0, "end_sec": 2.5},
                {"text": "二行目", "start_sec": 3.0, "end_sec": 4.0},
            ]
            return {
                "status": "SUCCEEDED",
                "result": {"lines": lines, "duration_sec": 65.5, "weak_lines": [1]},
            }

    f = Fake()
    w = TranscribeWidget(f)
    w.refresh()
    assert w.run_button.isEnabled() and w.asset_combo.count() == 1  # 画像は出さない
    assert "01:05.50" in w.asset_combo.itemText(0)
    w.lyrics_edit.setPlainText("一行目\n二行目")
    w.run()
    assert f.kw == ("p1", "a1", {"title": "", "lyrics": "一行目\n二行目"})
    w._poll()
    assert "40%" in w.status_label.text() and not w.run_button.isEnabled()
    w._poll()
    assert w.table.rowCount() == 2 and w.table.item(1, 2).text() == "二行目"
    assert "推定" in w.status_label.text() and w.run_button.isEnabled()


def test_main_window_has_new_tabs():
    from video_automation.ui.main_window import MainWindow

    _qapp()

    class Fake:
        pass

    w = MainWindow(Fake())
    names = [w.tabs.tabText(i) for i in range(w.tabs.count())]
    assert {"用途・デザイン", "声の口調", "文字起こし"} <= set(names)


def test_every_component_has_a_japanese_name():
    from video_automation.schemas.design import Component
    from video_automation.ui.design.widget import PART

    assert {c.value for c in Component} <= set(PART)
