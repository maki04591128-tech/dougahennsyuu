from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from video_automation.api.dependencies import get_session
from video_automation.database.models import Project
from video_automation.database.repositories.projects import ProjectRepository
from video_automation.schemas.project import ProjectCreate, ProjectRead

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post("", response_model=ProjectRead, status_code=201)
async def create_project(body: ProjectCreate, session: AsyncSession = Depends(get_session)):
    project = await ProjectRepository(session).add(Project(**body.model_dump()))
    return project


@router.get("", response_model=list[ProjectRead])
async def list_projects(session: AsyncSession = Depends(get_session)):
    return await ProjectRepository(session).list()


@router.get("/{project_id}", response_model=ProjectRead)
async def get_project(project_id: str, session: AsyncSession = Depends(get_session)):
    project = await ProjectRepository(session).get(project_id)
    if project is None:
        raise HTTPException(404, "project not found")
    return project
