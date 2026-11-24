"""UI用APIクライアント (P1-09)。UIはDB/外部エンジンに直接触れずここ経由で通信する。"""

from typing import Any

import httpx


class JobFailedError(RuntimeError):
    """サーバーの Job が失敗・中止・時間切れになった。"""


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

    def voice_preview(self, text: str, style_id: int, prosody: dict[str, Any], **kw: Any) -> bytes:
        """kw: reading_text（読み上げ用の文）、expressiveness（抑揚づけ）、emphasis。"""
        r = self._http.post(
            "/voice/preview",
            json={"text": text, "style_id": style_id, "prosody": prosody, **kw},
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

    def subtitle_cues(self, script_id: str) -> list[dict[str, Any]]:
        return self._json(self._http.get(f"/subtitles/{script_id}"))

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

    # ----- Phase 8: 対話型台本作成 -----
    LLM_TIMEOUT = 600.0  # ローカル LLM の生成は数分かかることがある

    def studio_create(self, project_id: str, **kw: Any) -> dict[str, Any]:
        return self._json(self._http.post("/scripts/studio", json={"project_id": project_id, **kw}))

    def studio_detail(self, script_id: str) -> dict[str, Any]:
        return self._json(self._http.get(f"/scripts/{script_id}/detail"))

    def studio_update(self, script_id: str, **kw: Any) -> dict[str, Any]:
        return self._json(self._http.put(f"/scripts/{script_id}", json=kw))

    def studio_generate(self, script_id: str, instruction: str = "", model_key: str | None = None):
        body = {"instruction": instruction, "model_key": model_key}
        return self._json(
            self._http.post(f"/scripts/{script_id}/generate", json=body, timeout=self.LLM_TIMEOUT)
        )

    def studio_add_lines(
        self, script_id: str, lines: list[dict[str, Any]], replace: bool = False
    ) -> dict[str, Any]:
        body = {"lines": lines, "replace": replace}
        return self._json(self._http.post(f"/scripts/{script_id}/dialogue", json=body))

    def studio_update_turn(self, script_id: str, turn_id: str, **kw: Any) -> dict[str, Any]:
        return self._json(self._http.put(f"/scripts/{script_id}/dialogue/{turn_id}", json=kw))

    def studio_delete_turn(self, script_id: str, turn_id: str) -> dict[str, Any]:
        return self._json(self._http.delete(f"/scripts/{script_id}/dialogue/{turn_id}"))

    def studio_lock(self, script_id: str, turn_id: str, locked: bool) -> dict[str, Any]:
        url = f"/scripts/{script_id}/dialogue/{turn_id}/lock"
        return self._json(self._http.put(url, json={"locked": locked}))

    def studio_regenerate(
        self, script_id: str, turn_id: str, candidates: int = 1, instruction: str = ""
    ) -> dict[str, Any]:
        url = f"/scripts/{script_id}/dialogue/{turn_id}/regenerate"
        body = {"candidates": candidates, "instruction": instruction}
        return self._json(self._http.post(url, json=body, timeout=self.LLM_TIMEOUT))

    def studio_apply(self, script_id: str, turn_id: str, text: str) -> dict[str, Any]:
        url = f"/scripts/{script_id}/dialogue/{turn_id}/apply"
        return self._json(self._http.post(url, json={"text": text}))

    def studio_validate(self, script_id: str, use_llm: bool = False) -> dict[str, Any]:
        return self._json(
            self._http.post(
                f"/scripts/{script_id}/validate",
                json={"use_llm": use_llm},
                timeout=self.LLM_TIMEOUT,
            )
        )

    def studio_versions(self, script_id: str) -> list[dict[str, Any]]:
        return self._json(self._http.get(f"/scripts/{script_id}/versions"))

    def studio_diff(self, script_id: str, from_version: int, to_version: int | None = None):
        params: dict[str, Any] = {"from_version": from_version}
        if to_version is not None:
            params["to_version"] = to_version
        return self._json(self._http.get(f"/scripts/{script_id}/versions/diff", params=params))

    def studio_restore(self, script_id: str, version: int) -> dict[str, Any]:
        return self._json(self._http.post(f"/scripts/{script_id}/versions/{version}/restore"))

    # ----- Phase 8: AI アシスタント・承認・履歴 -----
    def conversations(self, project_id: str) -> list[dict[str, Any]]:
        return self._json(self._http.get("/conversations", params={"project_id": project_id}))

    def create_conversation(self, project_id: str, title: str = "") -> dict[str, Any]:
        body = {"project_id": project_id, "title": title}
        return self._json(self._http.post("/conversations", json=body))

    def conversation(self, conversation_id: str) -> dict[str, Any]:
        return self._json(self._http.get(f"/conversations/{conversation_id}"))

    def send_message(
        self, conversation_id: str, text: str, model_key: str | None = None
    ) -> dict[str, Any]:
        return self._json(
            self._http.post(
                f"/conversations/{conversation_id}/messages",
                json={"text": text, "model_key": model_key},
                timeout=self.LLM_TIMEOUT,
            )
        )

    def rerun_message(self, message_id: str, model_key: str | None = None) -> dict[str, Any]:
        return self._json(
            self._http.post(
                f"/conversations/messages/{message_id}/rerun",
                json={"model_key": model_key},
                timeout=self.LLM_TIMEOUT,
            )
        )

    def approvals(self, project_id: str) -> list[dict[str, Any]]:
        return self._json(self._http.get(f"/projects/{project_id}/approvals"))

    def decide_approval(self, kind: str, item_id: str, approve: bool) -> dict[str, Any]:
        url = f"/approvals/{kind}/{item_id}"
        return self._json(self._http.post(url, json={"approve": approve}))

    def approval_policy(self) -> list[dict[str, Any]]:
        return self._json(self._http.get("/approvals/policy"))

    def set_approval_policy(self, **kw: Any) -> list[dict[str, Any]]:
        return self._json(self._http.put("/approvals/policy", json=kw))

    def history(self, project_id: str) -> list[dict[str, Any]]:
        return self._json(self._http.get(f"/projects/{project_id}/history"))

    def revert_history(self, project_id: str, to_command_id: str | None) -> dict[str, Any]:
        url = f"/projects/{project_id}/history/revert"
        return self._json(self._http.post(url, json={"to_command_id": to_command_id}))

    # ----- Phase 9: 品質検査 -----
    def run_qa(self, project_id: str, render_id: str | None = None) -> dict[str, Any]:
        body = {"render_id": render_id}
        return self._json(self._http.post(f"/projects/{project_id}/qa", json=body))

    def qa_reports(self, project_id: str) -> list[dict[str, Any]]:
        return self._json(self._http.get(f"/projects/{project_id}/qa"))

    def qa_report(self, report_id: str) -> dict[str, Any]:
        return self._json(self._http.get(f"/qa/{report_id}"))

    # ----- 読みのルール（読み間違い対策） -----
    def readings(self, project_id: str | None = None) -> list[dict[str, Any]]:
        params = {"project_id": project_id} if project_id else {}
        return self._json(self._http.get("/voice/readings", params=params))

    def add_reading(
        self, surface: str, reading: str, project_id: str | None = None
    ) -> dict[str, Any]:
        body = {"surface": surface, "reading": reading, "project_id": project_id}
        return self._json(self._http.post("/voice/readings", json=body))

    def update_reading(self, rule: dict[str, Any], **kw: Any) -> dict[str, Any]:
        keys = ("surface", "reading", "project_id", "enabled", "note")
        body = {k: rule.get(k) for k in keys} | kw
        return self._json(self._http.put(f"/voice/readings/{rule['id']}", json=body))

    def delete_reading(self, rule_id: str) -> None:
        self._json(self._http.delete(f"/voice/readings/{rule_id}"))

    def speech_text(self, text: str, project_id: str | None = None) -> str:
        params = {"text": text} | ({"project_id": project_id} if project_id else {})
        return self._json(self._http.post("/voice/readings/preview", params=params))["speech"]

    # ---------- 演出プラン ----------
    def direction_plan(self, script_id: str, model_key: str | None = None) -> dict[str, Any]:
        return self._json(
            self._http.post(
                "/direction/plan",
                json={"script_id": script_id, "model_key": model_key},
                timeout=self.LLM_TIMEOUT,
            )
        )

    # ----- 立ち絵 -----
    def tachie_packs(self) -> list[dict[str, Any]]:
        return list(self._json(self._http.get("/characters/packs")))

    def tachie_import(self, path: str) -> dict[str, Any]:
        """PSD（zip）から立ち絵パックを作る（Job の終わりまで待つ）。"""
        job = self._json(self._http.post("/characters/packs/import", json={"path": path}))
        return dict(self.wait_job(job["id"], timeout=900))

    def tachie_apply(self, script_id: str) -> dict[str, Any]:
        job = self._json(self._http.post("/characters/apply", json={"script_id": script_id}))
        return dict(self.wait_job(job["id"], timeout=3600))

    def wait_job(self, job_id: str, timeout: float = 1800.0, interval: float = 1.0) -> Any:
        """Job の終了を待って結果を返す（失敗は例外）。UI のワーカースレッドから呼ぶ。"""
        import time

        end = time.monotonic() + timeout
        while True:
            j = self.get_job(job_id)
            if j["status"] == "SUCCEEDED":
                return j.get("result")
            if j["status"] in ("FAILED", "CANCELLED"):
                raise JobFailedError(j.get("error") or j["status"])
            if time.monotonic() > end:
                raise JobFailedError("job timed out")
            time.sleep(interval)

    def direction_apply(self, script_id: str, plan: dict[str, Any]) -> dict[str, Any]:
        job = self._json(
            self._http.post("/direction/apply", json={"script_id": script_id, "plan": plan})
        )
        result: dict[str, Any] = self.wait_job(job["id"])
        return result

    def check_readings(self, script_id: str, model_key: str | None = None) -> dict[str, Any]:
        return self._json(
            self._http.post(
                f"/scripts/{script_id}/readings/check",
                json={"model_key": model_key, "apply": True},
                timeout=self.LLM_TIMEOUT,
            )
        )

    def close(self) -> None:
        self._http.close()

    # ----- デザインの型（用途） -----
    def design_profiles(self) -> list[dict[str, Any]]:
        return self._json(self._http.get("/design/profiles"))

    def classify_purpose(self, text: str) -> dict[str, Any]:
        return self._json(self._http.post("/design/classify", json={"text": text}))

    def project_design(self, project_id: str) -> dict[str, Any]:
        return self._json(self._http.get(f"/projects/{project_id}/design"))

    def set_project_design(
        self, project_id: str, purpose: str | None, extras: list[str] | None = None
    ) -> dict[str, Any]:
        body = {"purpose": purpose, "extras": extras or []}
        return self._json(self._http.put(f"/projects/{project_id}/design", json=body))

    def design_check(self, project_id: str, script_id: str | None = None) -> dict[str, Any]:
        params = {"script_id": script_id} if script_id else {}
        return self._json(self._http.get(f"/projects/{project_id}/design/check", params=params))

    # ----- 声ごとの口調 -----
    def voice_characters(self) -> list[dict[str, Any]]:
        return self._json(self._http.get("/voice/characters"))

    def put_voice_character(self, speaker: str, **kw: Any) -> dict[str, Any]:
        body = {"speaker": speaker, **kw}
        return self._json(self._http.put(f"/voice/characters/{speaker}", json=body))

    def delete_voice_character(self, speaker: str) -> None:
        self._http.delete(f"/voice/characters/{speaker}").raise_for_status()

    # ----- 文字起こし -----
    def transcribe_status(self) -> dict[str, Any]:
        return self._json(self._http.get("/transcribe/status"))

    def transcribe(self, project_id: str, asset_id: str, **kw: Any) -> dict[str, Any]:
        body = {"project_id": project_id, "asset_id": asset_id, **kw}
        return self._json(self._http.post("/transcribe", json=body))

    # ----- 素材の取り込み（PC のファイル） -----
    def import_asset(self, project_id: str, path: str) -> dict[str, Any]:
        body = {"project_id": project_id, "path": path}
        return self._json(self._http.post("/assets/import", json=body, timeout=120.0))

    def list_assets(self, project_id: str) -> list[dict[str, Any]]:
        return self._json(self._http.get("/assets", params={"project_id": project_id}))
