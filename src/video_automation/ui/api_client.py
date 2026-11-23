"""UI用APIクライアント (P1-09)。UIはDB/外部エンジンに直接触れずここ経由で通信する。"""

from typing import Any

import httpx


class ApiClient:
    def __init__(self, base_url: str, client: httpx.Client | None = None) -> None:
        self._http = client or httpx.Client(base_url=base_url, timeout=10.0)

    def health(self) -> dict[str, Any]:
        r = self._http.get("/health")
        r.raise_for_status()
        return r.json()

    def create_project(self, name: str, **kwargs: Any) -> dict[str, Any]:
        r = self._http.post("/projects", json={"name": name, **kwargs})
        r.raise_for_status()
        return r.json()

    def list_projects(self) -> list[dict[str, Any]]:
        r = self._http.get("/projects")
        r.raise_for_status()
        return r.json()

    # ----- Phase 2: LLM -----
    def _json(self, r: httpx.Response) -> Any:
        r.raise_for_status()
        return r.json() if r.content else None

    def list_providers(self) -> list[dict[str, Any]]:
        return self._json(self._http.get("/providers"))

    def discover(self) -> dict[str, Any]:
        return self._json(self._http.post("/providers/discover", timeout=60.0))

    def register_provider(
        self, type_: str, name: str, base_url: str, api_key: str | None = None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"type": type_, "name": name, "base_url": base_url}
        if api_key:
            body["api_key"] = api_key
        return self._json(self._http.post("/providers/register", json=body))

    def delete_provider(self, provider_id: str) -> None:
        self._json(self._http.delete(f"/providers/{provider_id}"))

    def list_models(self) -> list[dict[str, Any]]:
        return self._json(self._http.get("/models"))

    def test_model(self, model_key: str, probes: list[str] | None = None) -> dict[str, Any]:
        return self._json(
            self._http.post("/models/test", json={"model_key": model_key, "probes": probes})
        )

    def get_job(self, job_id: str) -> dict[str, Any]:
        return self._json(self._http.get(f"/jobs/{job_id}"))

    def set_active_model(self, model_key: str) -> dict[str, Any]:
        return self._json(self._http.put("/models/active", json={"model_key": model_key}))

    def get_task_assignments(self) -> list[dict[str, Any]]:
        return self._json(self._http.get("/models/task-assignment"))

    def set_task_assignment(self, task: str, model_key: str | None) -> list[dict[str, Any]]:
        return self._json(
            self._http.put("/models/task-assignment", json={"task": task, "model_key": model_key})
        )

    # ----- Phase 3: 音声 -----
    def voice_engines(self) -> list[dict[str, Any]]:
        return self._json(self._http.get("/voice/engines"))

    def voice_speakers(self) -> list[dict[str, Any]]:
        return self._json(self._http.get("/voice/speakers"))

    def voice_preview(self, text: str, style_id: int, prosody: dict[str, Any]) -> bytes:
        r = self._http.post(
            "/voice/preview",
            json={"text": text, "style_id": style_id, "prosody": prosody},
            timeout=120.0,
        )
        r.raise_for_status()
        return r.content

    def voice_generate(
        self, text: str, style_id: int, prosody: dict[str, Any], project_id: str | None = None
    ) -> dict[str, Any]:
        body = {
            "project_id": project_id,
            "items": [{"text": text, "style_id": style_id, "prosody": prosody}],
        }
        return self._json(self._http.post("/voice/generate", json=body))

    def voice_dictionary(self) -> list[dict[str, Any]]:
        return self._json(self._http.get("/voice/dictionary"))

    def voice_add_word(
        self, surface: str, pronunciation: str, accent_type: int, priority: int = 5
    ) -> dict[str, Any]:
        body = {
            "surface": surface,
            "pronunciation": pronunciation,
            "accent_type": accent_type,
            "priority": priority,
        }
        return self._json(self._http.post("/voice/dictionary", json=body))

    def voice_delete_word(self, word_id: str) -> None:
        self._json(self._http.delete(f"/voice/dictionary/{word_id}"))

    def close(self) -> None:
        self._http.close()
