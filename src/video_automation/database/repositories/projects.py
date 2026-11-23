from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from video_automation.database.models import Project


class ProjectRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, project: Project) -> Project:
        self.session.add(project)
        await self.session.commit()
        return project

    async def get(self, project_id: str) -> Project | None:
        return await self.session.get(Project, project_id)

    async def list(self) -> list[Project]:
        res = await self.session.execute(select(Project).order_by(Project.created_at))
        return list(res.scalars())
