import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

REPORT = {
    "id": "r1",
    "project_id": "p1",
    "render_id": "x",
    "script_id": "s",
    "status": "failed",
    "duration_ms": 1500,
    "created_at": "2026-11-23T00:00:00Z",
    "sections": [
        {
            "name": "audio",
            "label": "音声品質",
            "status": "passed",
            "checks": [],
            "metrics": {"voiced": 4},
        },
        {
            "name": "video",
            "label": "映像品質",
            "status": "failed",
            "metrics": {},
            "checks": [
                {"level": "error", "code": "resolution", "message": "解像度が違います"},
                {"level": "warning", "code": "black", "message": "黒画面", "at_sec": 63.5},
            ],
        },
    ],
}


class FakeApi:
    def __init__(self):
        self.calls = []
        self.reports = []
        self.job = {"id": "j1", "status": "RUNNING", "progress": 0.4}

    def list_projects(self):
        return [{"id": "p1", "name": "P"}]

    def qa_reports(self, pid):
        return self.reports

    def qa_report(self, rid):
        return REPORT

    def run_qa(self, pid, render_id=None):
        self.calls.append(("run", pid))
        return {"id": "j1"}

    def get_job(self, jid):
        return self.job


def test_qa_widget_runs_and_shows_report():
    from PySide6.QtWidgets import QApplication

    from video_automation.ui.qa.widget import QAWidget

    QApplication.instance() or QApplication([])
    api = FakeApi()
    w = QAWidget(api)
    w.refresh()
    assert w.summary_label.text() == "まだ検査していません"
    w.run()
    assert api.calls == [("run", "p1")] and not w.run_button.isEnabled()
    w._poll()
    assert "40%" in w.status_label.text()
    api.job = {"id": "j1", "status": "SUCCEEDED"}
    api.reports = [
        {
            "id": "r1",
            "status": "failed",
            "render_id": "x",
            "counts": {"error": 1, "warning": 1, "info": 0},
            "created_at": "2026-11-23T00:00:00Z",
        }
    ]
    w._poll()
    assert w.run_button.isEnabled() and w.history_combo.count() == 1
    assert "要修正" in w.history_combo.itemText(0)
    assert w.tree.topLevelItemCount() == 2
    video = w.tree.topLevelItem(1)
    assert video.text(1) == "要修正" and video.isExpanded()
    assert video.child(1).text(2) == "01:03.50"
    assert not w.tree.topLevelItem(0).isExpanded()
    assert "要修正 1 件 / 要確認 1 件" in w.summary_label.text()
