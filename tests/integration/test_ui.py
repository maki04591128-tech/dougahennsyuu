import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)


def test_main_window_shows_api_status():
    from PySide6.QtWidgets import QApplication

    from video_automation.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])

    class FakeApi:
        def health(self):
            return {"status": "ok", "version": "0.1.0", "database": "connected"}

        def list_projects(self):
            return [{"name": "P", "width": 1920, "height": 1080}]

    w = MainWindow(FakeApi())
    w.refresh()
    assert "ok" in w.status_label.text() and w.project_list.count() == 1
    assert app is not None
