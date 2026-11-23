import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)


def _detail(segs, version=2, notes=None):
    return {
        "id": "s1",
        "project_id": "p1",
        "title": "T",
        "mode": "USER_ONE_SIDE",
        "status": "DRAFT",
        "version": version,
        "settings": {
            "topic": "AI",
            "target_duration_sec": 30,
            "must_keywords": ["自動化"],
            "banned": [],
            "response_policy": "質問",
            "ai_ratio": 0.5,
            "reference": "",
        },
        "characters": [
            {
                "id": "c1",
                "name": "A",
                "role": "",
                "personality": "",
                "speaking_style": "",
                "is_user": True,
                "voice": {"style_id": 3, "speed_scale": 1.2},
            },
            {
                "id": "c2",
                "name": "B",
                "role": "",
                "personality": "",
                "speaking_style": "",
                "is_user": False,
                "voice": {"style_id": None},
            },
        ],
        "scenes": [],
        "segments": segs,
        "estimated_total_sec": 4.0,
        "notes": notes or [],
    }


def _seg(i, speaker, text, src="AI", locked=False):
    return {
        "id": f"t{i}",
        "scene_index": 0,
        "sequence": i,
        "speaker": speaker,
        "text": text,
        "generation_source": src,
        "reply_to_segment_id": None,
        "locked": locked,
        "voice_style_id": None,
        "audio_file_id": None,
        "start_frame": None,
        "end_frame": None,
        "estimated_sec": 1.0,
    }


class FakeApi:
    def __init__(self):
        self.calls = []
        self.segs = [_seg(0, "A", "こんにちは", "USER", True), _seg(1, "B", "どうも")]

    def list_projects(self):
        return [{"id": "p1", "name": "P"}]

    def studio_create(self, pid, **kw):
        self.calls.append(("create", pid, kw))
        return _detail([], 1)

    def studio_update(self, sid, **kw):
        self.calls.append(("update", kw))
        return _detail(self.segs, 3)

    def studio_generate(self, sid, instruction="", model_key=None):
        self.calls.append(("generate", instruction))
        return _detail(self.segs, 2, ["視聴者を初心者と仮定"])

    def studio_add_lines(self, sid, lines, replace=False):
        self.calls.append(("lines", lines))
        return _detail(self.segs)

    def studio_update_turn(self, sid, tid, **kw):
        self.calls.append(("edit", tid, kw))
        self.segs[1] = {**self.segs[1], **kw, "generation_source": "EDITED"}
        return _detail(self.segs)

    def studio_lock(self, sid, tid, locked):
        self.calls.append(("lock", tid, locked))
        return _detail(self.segs)

    def studio_delete_turn(self, sid, tid):
        return _detail(self.segs)

    def studio_regenerate(self, sid, tid, n, ins):
        self.calls.append(("regen", tid, n))
        return {"turn_id": tid, "candidates": ["案1", "案2", "案3"], "generation_run_id": "r"}

    def studio_apply(self, sid, tid, text):
        self.calls.append(("apply", tid, text))
        return _detail(self.segs)

    def studio_validate(self, sid, use_llm=False):
        return {
            "valid": False,
            "estimated_total_sec": 4,
            "target_duration_sec": 30,
            "issues": [
                {"level": "error", "code": "banned_word", "message": "禁止", "turn_id": "t1"}
            ],
        }

    def studio_versions(self, sid):
        return [
            {"version": 1, "reason": "作成", "turn_count": 0},
            {"version": 2, "reason": "生成", "turn_count": 2},
        ]

    def studio_diff(self, sid, v, to=None):
        return {
            "from_version": v,
            "to_version": 2,
            "settings_changed": False,
            "characters_changed": False,
            "changes": [
                {
                    "kind": "changed",
                    "turn_id": "t1",
                    "speaker": "B",
                    "before": "x",
                    "after": "どうも",
                }
            ],
        }

    def studio_restore(self, sid, v):
        self.calls.append(("restore", v))
        return _detail(self.segs, 4)


def test_script_studio_widget_flow():
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from video_automation.ui.script_studio.widget import ScriptStudioWidget

    QApplication.instance() or QApplication([])
    api = FakeApi()
    w = ScriptStudioWidget(api)
    w.run_async = False
    w.refresh()
    w.mode_combo.setCurrentIndex(1)  # USER_ONE_SIDE
    w.topic_edit.setText("AI")
    w.keywords_edit.setText("自動化、字幕")
    w.char_table.item(0, 4).setCheckState(Qt.CheckState.Checked)
    w.char_table.item(0, 5).setText("3")
    w.create_script()
    _, pid, kw = api.calls[0]
    assert pid == "p1" and kw["mode"] == "USER_ONE_SIDE"
    assert kw["settings"]["must_keywords"] == ["自動化", "字幕"]
    assert kw["characters"][0] == {
        "name": "ずんだもん",
        "role": "解説役",
        "personality": "",
        "speaking_style": "",
        "is_user": True,
        "voice": {"style_id": 3},
        "color": None,
    }

    w.lines_edit.setPlainText("こんにちは\nB：割り込み\n\n")
    w.add_lines()
    assert api.calls[-1] == (
        "lines",
        [{"text": "こんにちは"}, {"speaker": "B", "text": "割り込み"}],
    )
    w.generate()
    assert "初心者" in w.notes_label.text()
    assert w.turn_table.rowCount() == 2 and w.turn_table.item(0, 5).text() == "🔒"
    assert w.turn_table.item(0, 4).text() == "ユーザー"
    assert w.version_combo.count() == 2

    # 固定・ユーザー入力は再生成しない
    w.turn_table.setCurrentCell(0, 3)
    w.regenerate()
    assert not any(c[0] == "regen" for c in api.calls)
    w.turn_table.setCurrentCell(1, 3)
    w.regenerate()
    assert w.candidate_list.count() == 3
    w.candidate_list.setCurrentRow(1)
    w.apply_candidate()
    assert api.calls[-1] == ("apply", "t1", "案2") and w.candidate_list.count() == 0

    w.turn_table.setCurrentCell(1, 3)
    w.turn_table.item(1, 3).setText("手で直した")
    w.save_turn_edit()
    assert api.calls[-1] == ("edit", "t1", {"text": "手で直した"})
    assert w.turn_table.item(1, 4).text() == "手修正"
    w.turn_table.setCurrentCell(1, 0)
    w.toggle_lock()
    assert api.calls[-1] == ("lock", "t1", True)

    w.validate(False)
    assert w.issue_list.item(0).text().startswith("✖ [#2]")
    w.version_combo.setCurrentIndex(0)
    w.show_diff()
    assert "~ B: x → どうも" in w.diff_view.toPlainText()
    w.version_combo.setCurrentIndex(0)
    w.restore()
    assert api.calls[-1] == ("restore", 1)

    # 設定保存では UI に無い抑揚設定を保持する
    w.save_settings()
    upd = next(c for c in reversed(api.calls) if c[0] == "update")[1]
    assert upd["characters"][0]["voice"] == {"style_id": 3, "speed_scale": 1.2}
    # 抑揚の強さ（列 6）
    w.char_table.item(1, 6).setText("1.5")
    w.save_settings()
    upd = next(c for c in reversed(api.calls) if c[0] == "update")[1]
    assert upd["characters"][1]["voice"]["expressiveness"] == 1.5
    # 読みチェック（AI）
    api.check_readings = lambda sid: {
        "checked": 2,
        "issues": [{"surface": "六方晶", "reading": "ロッポウショウ", "text": "六方晶の結晶"}],
    }
    w.check_readings()
    assert "六方晶 → ロッポウショウ" in w.issue_list.item(0).text()
    assert "1 件" in w.status_label.text()
    # 演出プラン（AI）→ 確認 → 適用
    assert not w.direction_apply_button.isEnabled()
    plan = {
        "title": "w-BN",
        "subtitle": "",
        "shots": [
            {"kind": "card", "from_line": 1, "heading": "疑問", "body": ["硬さとは"]},
            {
                "kind": "chart",
                "from_line": 2,
                "to_line": 3,
                "heading": "強度",
                "labels": ["w-BN", "ダイヤ"],
                "values": [114, 97],
                "unit": "GPa",
            },
            {"kind": "keyword", "from_line": 2, "text": "114GPa"},
            {"kind": "image", "from_line": 3, "query": "diamond"},
        ],
        "notes": ["3: 発話に無い語のテロップ「x」を外しました"],
    }
    api.direction_plan = lambda sid: {"plan": plan, "model_key": "m", "lines": 3}
    applied = []
    api.direction_apply = lambda sid, p: (
        applied.append(p)
        or {
            "command_id": "c",
            "status": "APPLIED",
            "clips": 7,
            "skipped": ["3: 画像の素材が未選択"],
        }
    )
    w.plan_direction()
    text = w.diff_view.toPlainText()
    assert "[1] カード: 疑問：硬さとは" in text and "[2〜3] 図表: 強度（w-BN 114GPa" in text
    assert "[2] テロップ: 114GPa" in text and "[3] 画像: 検索語: diamond" in text
    assert "[整えた点]" in text and w.direction_apply_button.isEnabled()
    w.apply_direction()
    assert applied == [plan] and "7 クリップ" in w.status_label.text()
    assert "画像の素材が未選択" in w.diff_view.toPlainText()


def test_script_studio_async_runner_reports_errors():
    from PySide6.QtWidgets import QApplication

    from video_automation.ui.script_studio.widget import ScriptStudioWidget

    app = QApplication.instance() or QApplication([])
    api = FakeApi()

    def boom(*a, **k):
        raise RuntimeError("LLM が見つかりません")

    api.studio_generate = boom
    w = ScriptStudioWidget(api)
    w.show_detail(_detail(api.segs))
    w.generate()
    assert not w.generate_button.isEnabled()
    import time

    for _ in range(50):
        app.processEvents()
        w._poll()
        if not w._pending:
            break
        time.sleep(0.05)
    assert w.generate_button.isEnabled()
    assert "LLM が見つかりません" in w.status_label.text()
