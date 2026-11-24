import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)


def _png(w=16, h=9):
    import struct
    import zlib

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))

    raw = b"".join(b"\x00" + b"\xff\x00\x00" * w for _ in range(h))
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


PNG = _png()


class FakeApi:
    def __init__(self):
        self.calls = []
        self.render_polls = 0
        self.clips = []

    def list_projects(self):
        return [{"id": "p1", "name": "P", "width": 1920, "height": 1080}]

    def render_profiles(self):
        return [
            {"name": "fhd_landscape", "label": "FHD 横", "width": 1920, "height": 1080},
            {"name": "fhd_portrait", "label": "FHD 縦", "width": 1080, "height": 1920},
        ]

    def timeline(self, pid):
        return {
            "fps_num": 30,
            "fps_den": 1,
            "duration_frames": 90,
            "tracks": [{"id": "t1", "name": "ナレーション"}],
            "assets": [{"id": "a1", "name": "voice.wav", "path": "x"}],
            "clips": self.clips,
        }

    def list_scripts(self, pid):
        return [{"id": "s1", "title": "台本"}]

    def list_commands(self, pid, status=None):
        return [{"id": "c1", "description": "LLMの提案", "command": {"type": "add_clip"}}]

    def list_renders(self, pid):
        return [{"id": "r9", "profile": "fhd_portrait", "status": "PENDING_APPROVAL"}]

    def timeline_from_script(self, pid, sid):
        self.calls.append(("from_script", sid))
        self.clips = [
            {"track_id": "t1", "asset_id": "a1", "start_frame": 0, "end_frame": 90, "volume": 1.0}
        ]
        return {}

    def undo(self, pid):
        self.calls.append(("undo",))

    def redo(self, pid):
        self.calls.append(("redo",))

    def decide_command(self, pid, cid, approve):
        self.calls.append(("cmd", cid, approve))

    def decide_render(self, rid, approve):
        self.calls.append(("render_decide", rid, approve))
        return {"id": rid}

    def preview_frame(self, pid, frame, sid):
        self.calls.append(("preview", frame, sid))
        return PNG

    def render(self, pid, profile, sid, **kw):
        self.calls.append(("render", profile, sid, kw))
        return {"id": "r1"}

    def get_render(self, rid):
        self.render_polls += 1
        if self.render_polls < 2:
            return {"status": "RENDERING", "job_id": "j1"}
        return {
            "status": "SUCCEEDED",
            "output_path": "projects/p1/renders/out.mp4",
            "encoder": "libx264",
        }

    def get_job(self, jid):
        return {"progress": 0.5}


def test_timeline_widget_flow():
    from PySide6.QtWidgets import QApplication

    from video_automation.ui.timeline.widget import TimelineWidget

    app = QApplication.instance() or QApplication([])
    api = FakeApi()
    w = TimelineWidget(api)
    w.refresh()
    assert w.script_combo.currentData() == "s1"
    assert w.pending_list.count() == 2
    w.from_script()
    assert ("from_script", "s1") in api.calls
    assert w.clip_table.rowCount() == 1 and w.clip_table.item(0, 0).text() == "ナレーション"
    assert w.frame_spin.maximum() == 89
    w.frame_spin.setValue(45)
    w.preview()
    assert ("preview", 45, "s1") in api.calls
    assert w.preview_label.pixmap() is not None and not w.preview_label.pixmap().isNull()
    api.preview_frame = lambda *a: b"not a png"
    w.preview()
    assert "画像を読み込めませんでした" in w.status_label.text()
    w.undo_button.click()
    w.redo_button.click()
    assert ("undo",) in api.calls and ("redo",) in api.calls

    w.pending_list.setCurrentRow(0)
    w.decide(True)
    assert ("cmd", "c1", True) in api.calls

    w.profile_combo.setCurrentIndex(1)
    w.burn_check.setChecked(False)
    w.start_render()
    assert api.calls[-1] == (
        "render",
        "fhd_portrait",
        "s1",
        {
            "burn_subtitles": False,
            "loudnorm": True,
            "rich_direction": "auto",
            "credits": "text",
        },
    )
    assert not w.render_button.isEnabled()
    w._poll_render()
    assert "50%" in w.status_label.text()
    w._poll_render()
    assert "書き出し完了" in w.status_label.text() and w.render_button.isEnabled()
    app.processEvents()


def test_main_window_has_timeline_tab():
    from PySide6.QtWidgets import QApplication

    from video_automation.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    w = MainWindow(object())
    labels = [w.tabs.tabText(i) for i in range(w.tabs.count())]
    assert "編集・書き出し" in labels
    app.processEvents()


class AnimApi(FakeApi):
    def __init__(self):
        super().__init__()
        self.clips = [
            {
                "id": "k1",
                "track_id": "t1",
                "asset_id": "a1",
                "start_frame": 0,
                "end_frame": 90,
                "volume": 1.0,
                "animation": {
                    "preset": "pop_in",
                    "keyframes": [{}],
                    "transition_in": {"type": "dissolve"},
                },
            }
        ]
        self.title_polls = 0

    def motion_presets(self, kind=None):
        return [
            {"name": "ken_burns_in", "kind": "camera", "label": "ゆっくりズームイン"},
            {"name": "pop_in", "kind": "element", "label": "ポップイン"},
            {
                "name": "wipe_left",
                "kind": "transition",
                "label": "ワイプ",
                "transition": {"type": "wipe_left", "duration_frames": 12},
            },
            {"name": "text_typewriter", "kind": "text", "label": "タイプライター"},
        ]

    def set_clip_animation(self, pid, cid, **kw):
        self.calls.append(("anim", cid, kw))
        return {}

    def title_card(self, pid, text, **kw):
        self.calls.append(("title", text, kw))
        return {"id": "jt"}

    def get_job(self, jid):
        if jid == "jt":
            self.title_polls += 1
            if self.title_polls < 2:
                return {"status": "RUNNING"}
            return {"status": "SUCCEEDED", "result": {"id": "a-title"}}
        return super().get_job(jid)

    def place_on_track(self, pid, aid, frame, name):
        self.calls.append(("place", aid, frame, name))
        return {}


def test_timeline_widget_animation_controls():
    from PySide6.QtWidgets import QApplication

    from video_automation.ui.timeline.widget import TimelineWidget

    app = QApplication.instance() or QApplication([])
    api = AnimApi()
    w = TimelineWidget(api)
    w.refresh()
    assert w.preset_combo.count() == 2 and w.transition_combo.count() == 1
    assert w.title_anim_combo.currentData() == "typewriter"
    assert w.clip_table.item(0, 5).text() == "pop_in / 入り:dissolve"
    w.apply_preset()  # 未選択
    assert "選択" in w.status_label.text() and not any(c[0] == "anim" for c in api.calls)
    w.clip_table.selectRow(0)
    w.preset_combo.setCurrentIndex(0)
    w.intensity_spin.setValue(1.5)
    w.apply_preset()
    assert api.calls[-1] == ("anim", "k1", {"preset": "ken_burns_in", "intensity": 1.5})
    w.transition_frames.setValue(20)
    w.apply_transition()
    assert api.calls[-1] == (
        "anim",
        "k1",
        {"transition": {"type": "wipe_left", "duration_frames": 20}},
    )
    w.clear_animation()
    assert api.calls[-1] == ("anim", "k1", {"clear": True})

    w.title_edit.setText("第1章")
    w.title_pos_combo.setCurrentIndex(1)
    w.frame_spin.setValue(30)
    w.add_title()
    assert api.calls[-1] == (
        "title",
        "第1章",
        {"animation": "typewriter", "position": "lower_third", "box": True, "duration_sec": 3.0},
    )
    assert not w.title_button.isEnabled()
    w._poll_render()
    w._poll_render()
    assert ("place", "a-title", 30, "テロップ") in api.calls and w.title_button.isEnabled()
    app.processEvents()
