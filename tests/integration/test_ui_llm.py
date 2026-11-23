import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)

MODEL = {
    "key": "ollama-local::qwen3-swallow-8b-local:latest",
    "provider_id": "ollama-local",
    "model_id": "qwen3-swallow-8b-local:latest",
    "display_name": "qwen3-swallow-8b-local:latest",
    "quantization": "Q4_K_M",
    "state": "INFERENCE_READY",
    "loaded": False,
    "is_active": True,
    "capabilities": {
        "context_length": 40960,
        "image_input": False,
        "tool_calling": False,
        "tool_calling_fallback": True,
        "structured_output": True,
    },
    "last_test": {"tokens_per_sec": 55.0},
}


class FakeApi:
    def __init__(self):
        self.assigned = []
        self.registered = []
        self.active = None

    def health(self):
        return {"status": "ok", "version": "0.8.0", "database": "connected"}

    def list_projects(self):
        return []

    def list_providers(self):
        return [
            {
                "provider_id": "ollama-local",
                "name": "Ollama",
                "type": "ollama",
                "base_url": "http://127.0.0.1:11434",
                "available": True,
                "models_count": 1,
                "server_version": "0.40.16",
            }
        ]

    def discover(self):
        return {
            "providers": self.list_providers(),
            "models": [MODEL],
            "music": [
                {"name": "ACE-Step 1.5", "available": False, "base_url": "http://127.0.0.1:8001"}
            ],
        }

    def list_models(self):
        return [MODEL]

    def get_task_assignments(self):
        return [
            {
                "task": "chat",
                "mode": "auto",
                "model_key": None,
                "resolved_model_key": MODEL["key"],
                "reason": None,
            }
        ]

    def set_task_assignment(self, task, key):
        self.assigned.append((task, key))
        return self.get_task_assignments()

    def set_active_model(self, key):
        self.active = key

    def register_provider(self, *args):
        self.registered.append(args)

    def test_model(self, key):
        return {"id": "job1"}

    def get_job(self, job_id):
        return {
            "status": "SUCCEEDED",
            "result": {
                "success": True,
                "tokens_per_sec": 55.0,
                "sample_text": "こんにちは",
                "probes": [{"name": "basic", "ok": True}, {"name": "vision", "ok": False}],
            },
        }


def test_llm_settings_widget_flow():
    from PySide6.QtWidgets import QApplication

    from video_automation.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    api = FakeApi()
    w = MainWindow(api)
    w.refresh()
    s = w.llm_settings
    assert s.provider_table.rowCount() == 1 and s.model_table.rowCount() == 1
    assert s.model_table.item(0, 8).text() == "△"  # ツール: フォールバックのみ
    assert s.model_table.item(0, 11).text() == "★"

    s.discover()
    assert "ACE-Step" in s.music_label.text() and "未接続" in s.music_label.text()

    combo = s.task_table.cellWidget(0, 1)
    combo.setCurrentIndex(1)
    assert api.assigned == [("chat", MODEL["key"])]

    s.model_table.selectRow(0)
    s.set_active_selected()
    assert api.active == MODEL["key"]
    s.test_selected()
    s._poll_job()
    assert "成功" in s.test_label.text() and "vision:×" in s.test_label.text()

    s.reg_name.setText("llama.cpp")
    s.reg_url.setText("http://127.0.0.1:8080")
    s.reg_key.setText("sk-x")
    s.register_provider()
    assert api.registered[0][0] == "openai_compatible" and s.reg_key.text() == ""
    assert app is not None
