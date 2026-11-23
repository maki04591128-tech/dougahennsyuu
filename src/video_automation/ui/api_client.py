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

    def close(self) -> None:
        self._http.close()
