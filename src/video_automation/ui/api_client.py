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

    # ----- Phase 4: 台本・字幕 -----
    def create_script(
        self, project_id: str, text: str, default_style_id: int | None, title: str = ""
    ) -> dict[str, Any]:
        body = {
            "project_id": project_id,
            "text": text,
            "default_style_id": default_style_id,
            "title": title,
        }
        return self._json(self._http.post("/scripts", json=body))

    def script_voice(self, script_id: str) -> dict[str, Any]:
        return self._json(self._http.post(f"/scripts/{script_id}/voice"))

    def subtitles_generate(self, script_id: str, max_chars: int | None = None) -> list[Any]:
        body = {"script_id": script_id, "max_chars_per_line": max_chars}
        return self._json(self._http.post("/subtitles/generate", json=body))

    def subtitles_sync(self, script_id: str) -> list[dict[str, Any]]:
        return self._json(self._http.post("/subtitles/synchronize", json={"script_id": script_id}))

    def subtitles_export(self, script_id: str, fmt: str) -> dict[str, Any]:
        body = {"script_id": script_id, "format": fmt}
        return self._json(self._http.post("/subtitles/export", json=body, timeout=60.0))

    # ----- Phase 5: タイムライン・レンダリング -----
    def list_scripts(self, project_id: str) -> list[dict[str, Any]]:
        return self._json(self._http.get("/scripts", params={"project_id": project_id}))

    def timeline(self, project_id: str) -> dict[str, Any]:
        return self._json(self._http.get(f"/projects/{project_id}/timeline"))

    def timeline_from_script(self, project_id: str, script_id: str) -> dict[str, Any]:
        return self._json(
            self._http.post(
                f"/projects/{project_id}/timeline/from_script", json={"script_id": script_id}
            )
        )

    def list_commands(self, project_id: str, status: str | None = None) -> list[dict[str, Any]]:
        params = {"status": status} if status else None
        return self._json(self._http.get(f"/projects/{project_id}/commands", params=params))

    def decide_command(self, project_id: str, command_id: str, approve: bool) -> dict[str, Any]:
        action = "approve" if approve else "reject"
        return self._json(self._http.post(f"/projects/{project_id}/commands/{command_id}/{action}"))

    def undo(self, project_id: str) -> dict[str, Any]:
        return self._json(self._http.post(f"/projects/{project_id}/undo"))

    def redo(self, project_id: str) -> dict[str, Any]:
        return self._json(self._http.post(f"/projects/{project_id}/redo"))

    def preview_frame(
        self, project_id: str, frame: int, script_id: str | None = None, width: int = 640
    ) -> bytes:
        body = {"frame": frame, "script_id": script_id, "width": width}
        r = self._http.post(f"/projects/{project_id}/preview", json=body, timeout=120.0)
        r.raise_for_status()
        return r.content

    def render_profiles(self) -> list[dict[str, Any]]:
        return self._json(self._http.get("/render/profiles"))

    def render(self, project_id: str, profile: str, script_id: str | None, **kw: Any) -> dict:
        body = {"project_id": project_id, "profile": profile, "script_id": script_id, **kw}
        return self._json(self._http.post("/render", json=body))

    def list_renders(self, project_id: str) -> list[dict[str, Any]]:
        return self._json(self._http.get("/render", params={"project_id": project_id}))

    def get_render(self, render_id: str) -> dict[str, Any]:
        return self._json(self._http.get(f"/render/{render_id}"))

    def decide_render(self, render_id: str, approve: bool) -> dict[str, Any]:
        action = "approve" if approve else "reject"
        return self._json(self._http.post(f"/render/{render_id}/{action}"))

    # ----- Phase 6: 素材・BGM -----
    def asset_sources(self) -> list[dict[str, Any]]:
        return self._json(self._http.get("/asset_sources"))

    def set_source_key(self, name: str, api_key: str) -> dict[str, Any]:
        return self._json(self._http.put(f"/asset_sources/{name}/key", json={"api_key": api_key}))

    def search_assets(self, query: str, media_type: str, **kw: Any) -> dict[str, Any]:
        body = {"query": query, "media_type": media_type, **kw}
        return self._json(self._http.post("/assets/search", json=body, timeout=60.0))

    def asset_thumbnail(self, source: str, source_id: str, media_type: str) -> bytes:
        body = {"source": source, "source_id": source_id, "media_type": media_type}
        r = self._http.post("/assets/thumbnail", json=body, timeout=30.0)
        r.raise_for_status()
        return r.content

    def download_asset(
        self, project_id: str, source: str, source_id: str, media_type: str, acknowledge: bool
    ) -> dict[str, Any]:
        body = {
            "project_id": project_id,
            "source": source,
            "source_id": source_id,
            "media_type": media_type,
            "acknowledge_license": acknowledge,
        }
        return self._json(self._http.post("/assets/download", json=body, timeout=60.0))

    def asset_requests(self, project_id: str) -> list[dict[str, Any]]:
        params = {"project_id": project_id, "status": "PENDING_APPROVAL"}
        return self._json(self._http.get("/assets/requests", params=params))

    def decide_asset_request(self, request_id: str, approve: bool) -> dict[str, Any]:
        action = "approve" if approve else "reject"
        return self._json(self._http.post(f"/assets/requests/{request_id}/{action}"))

    def credits(self, project_id: str) -> dict[str, Any]:
        return self._json(self._http.get(f"/projects/{project_id}/credits"))

    def asset_file(self, asset_id: str) -> bytes:
        r = self._http.get(f"/assets/{asset_id}/file", timeout=60.0)
        r.raise_for_status()
        return r.content

    def music_engines(self) -> dict[str, Any]:
        return self._json(self._http.get("/music/engines"))

    def generate_music(self, project_id: str, prompt: str, **kw: Any) -> dict[str, Any]:
        body = {"project_id": project_id, "prompt": prompt, **kw}
        return self._json(self._http.post("/music/generate", json=body))

    def add_bgm(self, project_id: str, asset_id: str, volume: float) -> dict[str, Any]:
        body = {"asset_id": asset_id, "volume": volume}
        return self._json(self._http.post(f"/projects/{project_id}/timeline/add_bgm", json=body))

    # ----- Phase 7: アニメーション -----
    def motion_presets(self, kind: str | None = None) -> list[dict[str, Any]]:
        params = {"kind": kind} if kind else None
        return self._json(self._http.get("/motion/presets", params=params))

    def submit_command(self, project_id: str, command: dict[str, Any]) -> dict[str, Any]:
        return self._json(
            self._http.post(f"/projects/{project_id}/commands", json={"command": command})
        )

    def set_clip_animation(self, project_id: str, clip_id: str, **kw: Any) -> dict[str, Any]:
        return self.submit_command(
            project_id, {"type": "set_clip_animation", "clip_id": clip_id, **kw}
        )

    def title_card(self, project_id: str, text: str, **kw: Any) -> dict[str, Any]:
        body = {"project_id": project_id, "text": text, **kw}
        return self._json(self._http.post("/assets/title_card", json=body))

    def place_on_track(
        self, project_id: str, asset_id: str, start_frame: int, track_name: str
    ) -> dict[str, Any]:
        """名前のトラック（無ければ作る）に素材を置く。"""
        import uuid

        tl = self.timeline(project_id)
        track = next((t["id"] for t in tl["tracks"] if t["name"] == track_name), None)
        cmds: list[dict[str, Any]] = []
        if track is None:
            track = str(uuid.uuid4())
            cmds.append(
                {"type": "add_track", "kind": "video", "name": track_name, "track_id": track}
            )
        cmds.append(
            {
                "type": "add_clip",
                "track_id": track,
                "asset_id": asset_id,
                "start_frame": start_frame,
            }
        )
        return self.submit_command(project_id, {"type": "batch", "commands": cmds})

    def close(self) -> None:
        self._http.close()
