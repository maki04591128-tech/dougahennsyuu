import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)


class FakeApi:
    def __init__(self):
        self.calls = []
        self.msgs = []
        self.pend = [
            {
                "kind": "command",
                "id": "c1",
                "project_id": "p1",
                "title": "動画を配置",
                "details": ["クリップ追加: 「clip.mp4」 を 映像 の 00:00.00〜00:03.00 に配置"],
                "created_at": "2026-11-23T00:00:00Z",
            },
            {
                "kind": "script",
                "id": "s1",
                "project_id": "p1",
                "title": "台本修正案",
                "details": ["追加 A: こんにちは"],
                "created_at": "2026-11-23T00:00:01Z",
            },
        ]
        self.policy = {"asset_download": "required", "unknown_license": "ack"}

    def list_projects(self):
        return [{"id": "p1", "name": "P"}]

    def list_models(self):
        return [
            {"key": "ollama-local::qwen", "display_name": "qwen"},
            {"key": "ollama-local::gemma", "display_name": "gemma"},
        ]

    def conversations(self, pid):
        return [{"id": "k1", "project_id": pid, "title": "前の会話", "created_at": "x"}]

    def conversation(self, cid):
        return {"id": cid, "messages": list(self.msgs)}

    def create_conversation(self, pid, title=""):
        self.calls.append(("new", pid))
        return {"id": "k2"}

    def send_message(self, cid, text, model):
        self.calls.append(("send", cid, text, model))
        self.msgs += [
            {
                "id": "u1",
                "role": "user",
                "content": text,
                "superseded": False,
                "actions": [],
                "questions": [],
                "model_key": None,
            },
            {
                "id": "a1",
                "role": "assistant",
                "content": "配置します<b>",
                "superseded": False,
                "model_key": model or "ollama-local::qwen",
                "questions": ["BGMは？"],
                "actions": [
                    {
                        "tool": "edit_timeline",
                        "ok": True,
                        "summary": "タイムライン変更（承認待ち）",
                        "approval": self.pend[0],
                    },
                    {
                        "tool": "search_assets",
                        "ok": True,
                        "summary": "検索 1 件",
                        "data": {
                            "results": [
                                {
                                    "source": "openverse",
                                    "source_id": "x1",
                                    "license": "cc0",
                                    "title": "t",
                                }
                            ]
                        },
                    },
                    {"tool": "render_project", "ok": False, "summary": "無効", "error": "profile"},
                ],
            },
        ]
        return self.msgs[-1]

    def rerun_message(self, mid, model):
        self.calls.append(("rerun", mid, model))
        return {}

    def approvals(self, pid):
        return list(self.pend)

    def decide_approval(self, kind, iid, approve):
        self.calls.append(("decide", kind, iid, approve))
        self.pend = [p for p in self.pend if p["id"] != iid]
        return {"kind": kind, "id": iid, "status": "APPLIED" if approve else "REJECTED"}

    def history(self, pid):
        return [
            {
                "command_id": "c2",
                "sequence": 2,
                "status": "APPLIED",
                "origin": "llm",
                "description": "動画を配置",
                "changes": ["クリップ追加"],
                "created_at": "x",
            },
            {
                "command_id": "c0",
                "sequence": 1,
                "status": "APPLIED",
                "origin": "user",
                "description": "",
                "changes": ["トラック追加"],
                "created_at": "x",
            },
        ]

    def revert_history(self, pid, cid):
        self.calls.append(("revert", cid))
        return {"undone": ["c2"]}

    def approval_policy(self):
        return [
            {
                "key": "asset_download",
                "label": "素材ダウンロード",
                "mode": self.policy["asset_download"],
                "options": [],
                "configurable": True,
            },
            {
                "key": "unknown_license",
                "label": "ライセンス不明素材の使用",
                "mode": self.policy["unknown_license"],
                "options": [],
                "configurable": True,
            },
            {
                "key": "timeline_change",
                "label": "タイムライン変更",
                "mode": "required",
                "options": [],
                "configurable": False,
            },
        ]

    def set_approval_policy(self, **kw):
        self.calls.append(("policy", kw))
        self.policy.update(kw)
        return self.approval_policy()


def test_assistant_widget_flow():
    from PySide6.QtWidgets import QApplication

    from video_automation.ui.assistant.widget import AssistantWidget

    QApplication.instance() or QApplication([])
    api = FakeApi()
    w = AssistantWidget(api)
    w.runner.enabled = False
    w.refresh()
    assert w.conversation_id == "k1" and w.model_combo.count() == 3
    assert w.approval_list.count() == 2 and "クリップ追加" in w.approval_detail.toPlainText()
    assert "固定" in w.policy_view.toPlainText()

    w.model_combo.setCurrentIndex(2)
    w.input_edit.setPlainText("動画を置いて")
    w.send()
    assert api.calls[-1] == ("send", "k1", "動画を置いて", "ollama-local::gemma")
    html = w.chat_view.toHtml()
    assert "⏳" in html and "✖" in html and "openverse:x1" in html and "BGMは？" in html
    assert "&lt;b&gt;" in w.chat_view.toHtml() or "<b>" not in w.chat_view.toPlainText()

    w.approval_list.setCurrentRow(1)
    w.decide(False)
    assert api.calls[-1] == ("decide", "script", "s1", False)
    assert w.approval_list.count() == 1
    w.approval_list.setCurrentRow(0)
    w.decide(True)
    assert api.calls[-1] == ("decide", "command", "c1", True) and w.approval_list.count() == 0

    assert w.history_list.count() == 2 and "[AI]" in w.history_list.item(0).text()
    w.history_list.setCurrentRow(1)
    w.revert()
    assert api.calls[-1] == ("revert", "c0") and "1 件" in w.status_label.text()

    w.rerun_last()
    assert api.calls[-1] == ("rerun", "u1", "ollama-local::gemma")

    w.policy_download.setCurrentIndex(1)
    w.save_policy()
    assert api.calls[-1] == ("policy", {"asset_download": "auto", "unknown_license": "ack"})
    assert w.policy_download.currentData() == "auto"


def test_assistant_send_creates_conversation_when_none():
    from PySide6.QtWidgets import QApplication

    from video_automation.ui.assistant.widget import AssistantWidget

    QApplication.instance() or QApplication([])
    api = FakeApi()
    api.conversations = lambda pid: []
    w = AssistantWidget(api)
    w.runner.enabled = False
    w.refresh()
    assert w.conversation_id is None
    w.input_edit.setPlainText("こんにちは")
    w.send()
    assert ("new", "p1") in api.calls and api.calls[-1][1] == "k2"
