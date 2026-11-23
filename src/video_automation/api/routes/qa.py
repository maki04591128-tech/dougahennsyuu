"""品質検査 API（Phase 9）。検査は時間がかかるため Job として実行する。"""

from fastapi import APIRouter, Depends

from video_automation.api.dependencies import get_container
from video_automation.app.bootstrap import AppContainer
from video_automation.schemas.common import JobRead
from video_automation.schemas.qa import QAReportRead, QAReportSummary, QARequest

projects = APIRouter(prefix="/projects", tags=["qa"])
reports = APIRouter(prefix="/qa", tags=["qa"])


@projects.post("/{project_id}/qa", response_model=JobRead, status_code=202)
async def run_qa(project_id: str, body: QARequest, c: AppContainer = Depends(get_container)):
    await c.timeline.get_timeline(project_id)  # 存在確認
    return c.qa.submit(project_id, body)


@projects.get("/{project_id}/qa", response_model=list[QAReportSummary])
async def list_reports(project_id: str, c: AppContainer = Depends(get_container)):
    return await c.qa.list_reports(project_id)


@reports.get("/{report_id}", response_model=QAReportRead)
async def get_report(report_id: str, c: AppContainer = Depends(get_container)):
    return await c.qa.get(report_id)
