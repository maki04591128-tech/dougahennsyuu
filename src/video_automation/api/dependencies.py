from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from video_automation.app.bootstrap import AppContainer
from video_automation.core.events.event_bus import EventBus
from video_automation.core.jobs.job_manager import JobManager
from video_automation.database.database import Database


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_jobs(request: Request) -> JobManager:
    return request.app.state.jobs


def get_bus(request: Request) -> EventBus:
    return request.app.state.bus


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.db.session_factory() as session:
        yield session


def get_container(request: Request) -> AppContainer:
    return request.app.state.container
