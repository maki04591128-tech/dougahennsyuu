from fastapi import APIRouter, Depends, HTTPException

from video_automation.api.dependencies import get_jobs
from video_automation.core.jobs.job_manager import JobManager
from video_automation.schemas.common import JobRead

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{job_id}", response_model=JobRead)
async def get_job(job_id: str, jobs: JobManager = Depends(get_jobs)):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return job


@router.post("/{job_id}/cancel", response_model=JobRead)
async def cancel_job(job_id: str, jobs: JobManager = Depends(get_jobs)):
    job = await jobs.cancel(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return job
