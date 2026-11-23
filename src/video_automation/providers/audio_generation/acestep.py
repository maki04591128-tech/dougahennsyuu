"""ACE-Step 1.5 音楽生成Provider（検出のみ。生成は Phase 6）。

LLM Provider に混在させず独立管理する。PyTorch 依存競合を避けるため別環境・別プロセスで
起動されたAPIサーバー（既定 http://127.0.0.1:8001、`acestep-api`）にHTTPで接続する。
"""

from pathlib import Path

import httpx

from video_automation.schemas.provider import MusicProviderStatus


class AceStepProvider:
    def __init__(
        self,
        base_url: str,
        install_path: Path | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.install_path = install_path
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            transport=transport,
            trust_env=False,
            timeout=httpx.Timeout(5.0, connect=2.0),
        )

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
