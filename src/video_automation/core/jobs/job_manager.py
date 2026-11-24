"""非同期Job管理 (P1-11)。PENDING/RUNNING/SUCCEEDED/FAILED/CANCELLED。"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from video_automation.app.logging import get_logger
from video_automation.core.events.event_bus import EventBus
from video_automation.schemas.common import JobRead, JobStatus

log = get_logger("jobs")
ProgressFn = Callable[[float], Awaitable[None]]
JobFn = Callable[[ProgressFn], Awaitable[Any]]


class JobManager:
    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._jobs: dict[str, JobRead] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}

    async def _emit(self, job: JobRead) -> None:
        await self._bus.publish({"type": "job", "job": job.model_dump(mode="json")})

    def submit(self, kind: str, fn: JobFn) -> JobRead:
        job = JobRead(id=str(uuid4()), kind=kind, status=JobStatus.PENDING)
        self._jobs[job.id] = job
        self._tasks[job.id] = asyncio.create_task(self._run(job, fn))
        return job

    async def _run(self, job: JobRead, fn: JobFn) -> None:
        job.status = JobStatus.RUNNING
        await self._emit(job)

        async def progress(value: float, message: str | None = None) -> None:
            job.progress = max(0.0, min(1.0, value))
            if message is not None:
                job.message = message[:200]
            await self._emit(job)

        try:
            job.result = await fn(progress)
            job.progress = 1.0
            job.status = JobStatus.SUCCEEDED
        except asyncio.CancelledError:
            job.status = JobStatus.CANCELLED
            await self._emit(job)
            raise
        except Exception as exc:
            log.error("job_failed", job_id=job.id, kind=job.kind, exc_info=True)
            job.status = JobStatus.FAILED
            # 詳細は返さない（機密保護）。利用者向けと明示された例外のみ本文を付ける
            job.error = type(exc).__name__
            if getattr(exc, "public_message", False):
                job.error += f": {str(exc)[:300]}"
        await self._emit(job)

    def get(self, job_id: str) -> JobRead | None:
        return self._jobs.get(job_id)

    async def cancel(self, job_id: str) -> JobRead | None:
        job = self._jobs.get(job_id)
        task = self._tasks.get(job_id)
        if job is None:
            return None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        return job

    async def wait(self, job_id: str) -> JobRead:
        task = self._tasks[job_id]
        try:
            await task
        except asyncio.CancelledError:
            pass
        return self._jobs[job_id]
