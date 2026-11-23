import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)


def _png():
    import struct
    import zlib

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))

    raw = b"".join(b"\x00" + b"\x00\x80\xff" * 8 for _ in range(5))
    ihdr = struct.pack(">IIBBBBB", 8, 5, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


LIC_OK = {"code": "pexels", "name": "Pexels License", "notes": ["任意"]}
LIC_NC = {"code": "cc-by-nc", "name": "CC BY-NC 4.0", "notes": []}


class FakeApi:
    def __init__(self):
        self.calls = []
        self.music_engine = {"available": True, "default_model": "acestep-v15-turbo"}
        self.polls = 0

    def list_projects(self):
        return [{"id": "p1", "name": "P", "width": 1920, "height": 1080}]

    def asset_sources(self):
        return [
            {"name": "pexels", "label": "Pexels", "configured": False},
            {"name": "openverse", "label": "Openverse", "configured": True},
        ]

    def music_engines(self):
        return self.music_engine

    def asset_requests(self, pid):
        return [
            {
                "id": "r1",
                "source": "pexels",
                "title": "夜景",
                "source_id": "1",
                "license_code": "pexels",
                "verdict": "allowed",
            },
            {
                "id": "r2",
                "source": "openverse",
                "title": "",
                "source_id": "x",
                "license_code": "cc-by-nc",
                "verdict": "needs_ack",
            },
        ]

    def search_assets(self, q, mt, **kw):
        self.calls.append(("search", q, mt, kw))
        return {
            "results": [
                {
                    "source": "pexels",
                    "source_id": "101",
                    "media_type": "image",
                    "title": "夜景",
                    "creator": "Hanako",
                    "width": 1920,
                    "height": 1280,
                    "license": LIC_OK,
                    "verdict": "allowed",
                    "attribution": "Photo by Hanako on Pexels",
                    "preview_url": "https://images.pexels.com/x.jpg",
                },
                {
                    "source": "openverse",
                    "source_id": "ov-2",
                    "media_type": "image",
                    "title": "",
                    "creator": None,
                    "duration_sec": None,
                    "license": LIC_NC,
                    "verdict": "needs_ack",
                    "attribution": "x",
                    "preview_url": None,
                },
            ],
            "errors": {"freesound": "rate limited"},
        }

    def asset_thumbnail(self, s, i, mt):
        self.calls.append(("thumb", s, i))
        return _png()

    def download_asset(self, pid, s, i, mt, ack):
        self.calls.append(("download", s, i, ack))
        return {"job_id": "j-dl"}

    def decide_asset_request(self, rid, approve):
        self.calls.append(("decide", rid, approve))
        return {"id": rid, "job_id": "j-dl2" if approve else None}

    def generate_music(self, pid, prompt, **kw):
        self.calls.append(("music", prompt, kw))
        return {"id": "j-music"}

    def get_job(self, jid):
        self.polls += 1
        if jid == "j-music" and self.polls < 2:
            return {"status": "RUNNING", "progress": 0.4}
        if jid == "j-music":
            return {"status": "SUCCEEDED", "result": {"assets": [{"id": "a-bgm"}]}}
        return {"status": "SUCCEEDED", "result": {"name": "夜景"}}

    def add_bgm(self, pid, aid, vol):
        self.calls.append(("bgm", aid, vol))
        return {}

    def credits(self, pid):
        return {"text": "画像:\n- Photo by Hanako on Pexels"}


def test_assets_widget_flow():
    from PySide6.QtWidgets import QApplication

    from video_automation.ui.assets.widget import AssetsWidget, fmt_duration

    app = QApplication.instance() or QApplication([])
    api = FakeApi()
    w = AssetsWidget(api)
    w.refresh()
    assert "APIキー未設定" in w.sources_label.text()
    assert w.music_button.isEnabled() and not w.bgm_button.isEnabled()
    assert w.pending_list.count() == 2 and "要確認" in w.pending_list.item(1).text()

    w.query_edit.setText("夜景")
    w.search()
    assert api.calls[0] == ("search", "夜景", "image", {"allowed_only": True})
    assert w.table.rowCount() == 2 and w.table.item(1, 4).text() == "要確認"
    assert "freesound" in w.status_label.text()

    w.table.selectRow(0)
    assert ("thumb", "pexels", "101") in api.calls
    assert not w.thumb.pixmap().isNull() and "Pexels License" in w.detail.text()
    w.download()
    assert ("download", "pexels", "101", False) in api.calls

    w.table.selectRow(1)  # 非営利限定: 確認チェックなしでは取り込まない
    w.download()
    assert not any(c[:3] == ("download", "openverse", "ov-2") for c in api.calls)
    assert "確認" in w.status_label.text()
    w.ack_check.setChecked(True)
    w.download()
    assert ("download", "openverse", "ov-2", True) in api.calls

    w.pending_list.setCurrentRow(0)
    w.decide(True)
    assert ("decide", "r1", True) in api.calls

    w.music_prompt.setText("calm piano")
    w.music_bpm.setValue(90)
    w.generate_music()
    assert api.calls[-1] == (
        "music",
        "calm piano",
        {"instrumental": True, "duration_sec": 60, "bpm": 90},
    )
    assert not w.music_button.isEnabled()
    for _ in range(4):
        w._poll_jobs()
    assert w.bgm_button.isEnabled() and w._last_music == "a-bgm"
    w.place_bgm()
    assert api.calls[-1] == ("bgm", "a-bgm", 0.25)
    w.show_credits()
    assert "Pexels" in w.credits_text.toPlainText()

    api.music_engine = {"available": False}
    w.refresh()
    assert not w.music_button.isEnabled() and "起動していません" in w.music_status.text()
    assert fmt_duration(75.4) == "1:15" and fmt_duration(None) == "-"
    app.processEvents()


def test_main_window_has_assets_tab():
    from PySide6.QtWidgets import QApplication

    from video_automation.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    w = MainWindow(object())
    assert "素材・BGM" in [w.tabs.tabText(i) for i in range(w.tabs.count())]
    app.processEvents()
