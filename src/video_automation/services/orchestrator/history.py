"""編集履歴（P8-08）。適用済み・取り消し済みの命令を差分つきで一覧し、指定の時点まで戻す。"""

from sqlalchemy import select

from video_automation.database.database import Database
from video_automation.database.models import EditCommand
from video_automation.schemas.orchestrator import HistoryEntry, RevertResult
from video_automation.schemas.timeline import CommandStatus
from video_automation.services.orchestrator.approvals import ApprovalService
from video_automation.services.timeline.timeline_service import CommandError, TimelineService

_SHOWN = (CommandStatus.APPLIED.value, CommandStatus.UNDONE.value)


class HistoryService:
    def __init__(self, db: Database, timeline: TimelineService, approvals: ApprovalService) -> None:
        self.db = db
        self.timeline = timeline
        self.approvals = approvals

    async def list(self, project_id: str) -> list[HistoryEntry]:
        await self.timeline.get_timeline(project_id)  # 存在確認
        async with self.db.session_factory() as s:
            q = (
                select(EditCommand)
                .where(EditCommand.project_id == project_id, EditCommand.status.in_(_SHOWN))
                .order_by(EditCommand.sequence.desc())
            )
            rows = list((await s.execute(q)).scalars())
        return [
            HistoryEntry(
                command_id=r.id,
                sequence=r.sequence,
                status=r.status,
                origin=r.origin,
                description=r.description,
                changes=await self.approvals.command_details(project_id, r),
                created_at=r.created_at,
            )
            for r in rows
        ]

    async def revert(self, project_id: str, to_command_id: str | None) -> RevertResult:
        """to_command_id を適用した直後の状態まで、新しい順に取り消す。"""
        target = 0
        if to_command_id is not None:
            async with self.db.session_factory() as s:
                row = await s.get(EditCommand, to_command_id)
                if row is None or row.project_id != project_id:
                    raise CommandError(f"command {to_command_id} not found")
                if row.status != CommandStatus.APPLIED.value:
                    raise CommandError(f"command is {row.status}")
                target = row.sequence
        undone: list[str] = []
        for _ in range(1000):
            async with self.db.session_factory() as s:
                q = (
                    select(EditCommand)
                    .where(
                        EditCommand.project_id == project_id,
                        EditCommand.status == CommandStatus.APPLIED.value,
                    )
                    .order_by(EditCommand.sequence.desc())
                )
                latest = (await s.execute(q)).scalars().first()
            if latest is None or latest.sequence <= target:
                break
            undone.append((await self.timeline.undo(project_id)).id)
        return RevertResult(undone=undone)
