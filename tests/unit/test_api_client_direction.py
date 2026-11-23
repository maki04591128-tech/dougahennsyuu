"""UI の API クライアント: 演出プランの作成・適用（Job の完了待ち）。"""

import httpx
import pytest

from video_automation.ui.api_client import ApiClient, JobFailedError


def _client(states):
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, req.url.path))
        if req.url.path == "/direction/plan":
            return httpx.Response(200, json={"plan": {"shots": []}, "lines": 1})
        if req.url.path == "/direction/apply":
            return httpx.Response(202, json={"id": "j1", "status": "PENDING"})
        if req.url.path == "/jobs/j1":
            return httpx.Response(200, json=states.pop(0))
        return httpx.Response(404)

    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://t")
    return ApiClient("http://t", http), seen


def test_direction_apply_waits_for_job():
    api, seen = _client(
        [{"status": "RUNNING"}, {"status": "SUCCEEDED", "result": {"clips": 3, "skipped": []}}]
    )
    assert api.direction_plan("s")["lines"] == 1
    r = api.direction_apply("s", {"shots": []})
    assert r["clips"] == 3 and seen.count(("GET", "/jobs/j1")) == 2


def test_wait_job_raises_on_failure():
    api, _ = _client([{"status": "FAILED", "error": "blender not found"}])
    with pytest.raises(JobFailedError, match="blender"):
        api.wait_job("j1", interval=0)
