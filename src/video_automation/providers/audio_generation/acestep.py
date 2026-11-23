"""ACE-Step 1.5 音楽生成Provider（検出: Phase 2 / 生成: Phase 6）。

LLM Provider に混在させず独立管理する。PyTorch 依存競合を避けるため別環境・別プロセスで
起動されたAPIサーバー（既定 http://127.0.0.1:8001、`acestep-api`）にHTTPで接続する。
"""

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from video_automation.schemas.provider import MusicProviderStatus


class AceStepError(Exception):
    # メッセージに機密（APIキー等）を含めないため、Job のエラー表示に使ってよい
    public_message = True
    code = "music_engine_error"


class AceStepUnavailableError(AceStepError):
    code = "music_engine_unavailable"


# /query_result の status（docs/en/API.md）
TASK_RUNNING, TASK_SUCCEEDED, TASK_FAILED = 0, 1, 2


class AceStepProvider:
    def __init__(
        self,
        base_url: str,
        install_path: Path | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        api_key: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.install_path = install_path
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            transport=transport,
            trust_env=False,
            timeout=httpx.Timeout(5.0, connect=2.0),
            headers=headers,
        )

    # ---------- 生成 API（応答は {data, code, error, timestamp, extra} で包まれる） ----------
    async def _call(self, method: str, path: str, **kw: Any) -> Any:
        try:
            r = await self._client.request(method, path, **kw)
        except httpx.HTTPError as exc:
            raise AceStepUnavailableError(f"ACE-Step: {type(exc).__name__}") from exc
        if r.status_code == 401:
            raise AceStepError("ACE-Step: API key rejected (401)")
        if r.status_code >= 400:
            detail = ""
            try:
                detail = str(r.json().get("detail", ""))[:200]
            except ValueError:
                pass
            raise AceStepError(f"ACE-Step: HTTP {r.status_code} {detail}".strip())
        try:
            body = r.json()
        except ValueError as exc:
            raise AceStepError("ACE-Step: invalid JSON") from exc
        if isinstance(body, dict) and "data" in body and "code" in body:
            if body.get("code") not in (200, None) or body.get("error"):
                raise AceStepError(f"ACE-Step: {body.get('error') or body.get('code')}")
            return body["data"]
        return body

    async def health(self) -> dict[str, Any]:
        return await self._call("GET", "/health")

    async def models(self) -> dict[str, Any]:
        return await self._call("GET", "/v1/models")

    async def release_task(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._call("POST", "/release_task", json=body, timeout=60.0)

    async def query_result(self, task_ids: list[str]) -> list[dict[str, Any]]:
        data = await self._call(
            "POST", "/query_result", json={"task_id_list": task_ids}, timeout=30.0
        )
        out = []
        for item in data or []:
            res = item.get("result")
            if isinstance(res, str) and res:
                try:
                    res = json.loads(res)
                except ValueError:
                    res = []
            out.append({**item, "result": res or []})
        return out

    async def download(self, file_ref: str) -> bytes:
        """結果の file（"/v1/audio?path=..."）を取得する。他のパス・ホストは拒否。"""
        u = urlsplit(file_ref)
        if u.netloc and f"{u.scheme}://{u.netloc}" != self.base_url:
            raise AceStepError("ACE-Step: unexpected audio host")
        if u.path != "/v1/audio":
            raise AceStepError("ACE-Step: unexpected audio path")
        try:
            r = await self._client.get(f"{u.path}?{u.query}", timeout=120.0)
        except httpx.HTTPError as exc:
            raise AceStepUnavailableError(f"ACE-Step: {type(exc).__name__}") from exc
        if r.status_code >= 400:
            raise AceStepError(f"ACE-Step: audio download HTTP {r.status_code}")
        return r.content

    def _installed(self) -> bool | None:
        if self.install_path is None:
            return None
        p = self.install_path
        return p.is_dir() and (p / "pyproject.toml").is_file()

    async def check_connection(self) -> MusicProviderStatus:
        status = MusicProviderStatus(
            base_url=self.base_url,
            available=False,
            installed=self._installed(),
            install_path=str(self.install_path) if self.install_path else None,
        )
        endpoints: list[str] = []
        try:
            for path in ("/health", "/docs", "/openapi.json"):
                r = await self._client.get(path)
                if r.status_code < 400:
                    endpoints.append(path)
            if "/openapi.json" in endpoints:
                spec = (await self._client.get("/openapi.json")).json()
                endpoints = sorted(set(endpoints) | set((spec.get("paths") or {}).keys()))
        except httpx.HTTPError as exc:
            status.error = type(exc).__name__
            return status
        except ValueError:
            pass
        status.available = bool(endpoints)
        status.endpoints = endpoints
        if not endpoints:
            status.error = "no ACE-Step endpoint responded"
        return status

    async def aclose(self) -> None:
        await self._client.aclose()
