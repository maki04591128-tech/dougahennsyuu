"""承認管理（P8-05）。タイムライン変更・最終レンダリング・素材ダウンロード・台本修正案の
承認待ちを 1 か所で一覧・決定できるようにする。承認ポリシーは仕様 §10 の表に従い、
「設定可能」とされた項目だけ変更できる（DB の system_settings に保存）。"""

from typing import Any

from sqlalchemy import select

from video_automation.app.settings import Settings
from video_automation.database.database import Database
from video_automation.database.models import (
    Asset,
    AssetRequestRow,
    EditCommand,
    Project,
    SystemSetting,
    Track,
)
from video_automation.schemas.asset_source import AssetRequestStatus
from video_automation.schemas.orchestrator import (
    ApprovalItem,
    ApprovalKind,
    ApprovalResult,
    PolicyEntry,
    PolicyUpdate,
)
from video_automation.schemas.timeline import CommandStatus, RenderStatus
from video_automation.services.assets.asset_source_service import AssetSourceService
from video_automation.services.orchestrator.diff import describe_snapshots
from video_automation.services.rendering.render_service import RenderService
from video_automation.services.script.studio import ScriptStudio
from video_automation.services.timeline.timeline_service import TimelineService

POLICY_KEY = "approval_policy"
# (key, 表示名, 既定, 選択肢, 設定可能)
FIXED: list[tuple[str, str, str, list[str], bool]] = [
    ("script_proposal", "台本修正案", "required", ["required"], False),
    ("voice_preview", "音声試作", "auto", ["auto"], False),
    ("asset_search", "素材検索", "auto", ["auto"], False),
    ("asset_download", "素材ダウンロード", "required", ["required", "auto"], True),
    ("unknown_license", "ライセンス不明素材の使用", "ack", ["ack", "forbidden"], True),
    ("timeline_change", "タイムライン変更", "required", ["required"], False),
    ("subtitle_change", "字幕変更", "required", ["required"], False),
    ("preview", "プレビュー", "auto", ["auto"], False),
    ("final_render", "最終レンダリング", "required", ["required"], False),
]


class ApprovalError(Exception):
    code = "approval_error"


class ApprovalService:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        timeline: TimelineService,
        render: RenderService,
        assets: AssetSourceService,
        studio: ScriptStudio,
    ) -> None:
        self.settings = settings
        self.db = db
        self.timeline = timeline
        self.render = render
        self.assets = assets
        self.studio = studio

    # ---------- ポリシー ----------
    async def load_policy(self) -> None:
        async with self.db.session_factory() as s:
            row = await s.get(SystemSetting, POLICY_KEY)
        saved = (row.value if row else None) or {}
        if saved.get("asset_download") in ("required", "auto"):
            self.settings.llm_asset_download_requires_approval = (
                saved["asset_download"] == "required"
            )
        if saved.get("unknown_license") in ("ack", "forbidden"):
            self.settings.unknown_license_policy = saved["unknown_license"]

    def policy(self) -> list[PolicyEntry]:
        current = {
            "asset_download": "required"
            if self.settings.llm_asset_download_requires_approval
            else "auto",
            "unknown_license": self.settings.unknown_license_policy,
        }
        return [
            PolicyEntry(key=k, label=label, mode=current.get(k, d), options=opts, configurable=conf)
            for k, label, d, opts, conf in FIXED
        ]

    async def set_policy(self, req: PolicyUpdate) -> list[PolicyEntry]:
        values = req.model_dump(exclude_none=True)
        async with self.db.session_factory() as s:
            row = await s.get(SystemSetting, POLICY_KEY)
            merged = {**((row.value if row else None) or {}), **values}
            if row is None:
                s.add(SystemSetting(key=POLICY_KEY, value=merged))
            else:
                row.value = merged
            await s.commit()
        await self.load_policy()
        return self.policy()

    # ---------- 一覧 ----------
    async def _names(self, project_id: str) -> tuple[float, dict[str, str], dict[str, str]]:
        async with self.db.session_factory() as s:
            p = await s.get(Project, project_id)
            fps = (p.fps_num / p.fps_den) if p else 30.0
            assets = {
                a.id: a.name
                for a in (
                    await s.execute(select(Asset).where(Asset.project_id == project_id))
                ).scalars()
            }
            tracks = {
                t.id: t.name
                for t in (
                    await s.execute(select(Track).where(Track.project_id == project_id))
                ).scalars()
            }
        return fps, assets, tracks

    async def command_details(self, project_id: str, row: EditCommand) -> list[str]:
        fps, assets, tracks = await self._names(project_id)
        lines = describe_snapshots(row.before, row.after, fps, assets, tracks)
        return lines or [f"命令: {row.command.get('type')}"]

    async def pending(self, project_id: str) -> list[ApprovalItem]:
        items: list[ApprovalItem] = []
        async with self.db.session_factory() as s:
            q = select(EditCommand).where(
                EditCommand.project_id == project_id,
                EditCommand.status == CommandStatus.PENDING_APPROVAL.value,
            )
            commands = list((await s.execute(q.order_by(EditCommand.created_at))).scalars())
        for c in commands:
            items.append(
                ApprovalItem(
                    kind=ApprovalKind.COMMAND,
                    id=c.id,
                    project_id=project_id,
                    title=c.description or f"タイムライン変更（{c.command.get('type')}）",
                    details=await self.command_details(project_id, c),
                    created_at=c.created_at,
                )
            )
        for r in await self.render.list(project_id):
            if r.status == RenderStatus.PENDING_APPROVAL:
                items.append(
                    ApprovalItem(
                        kind=ApprovalKind.RENDER,
                        id=r.id,
                        project_id=project_id,
                        title=f"最終レンダリング（{r.profile}）",
                        details=[f"出力プロファイル: {r.profile}"],
                        created_at=r.created_at,
                    )
                )
        for a in await self.assets.list_requests(project_id, AssetRequestStatus.PENDING_APPROVAL):
            items.append(
                ApprovalItem(
                    kind=ApprovalKind.ASSET,
                    id=a.id,
                    project_id=project_id,
                    title=f"素材ダウンロード: {a.title}",
                    details=[
                        f"取得元: {a.source} / ID {a.source_id}",
                        f"ライセンス: {a.license_code}（判定 {a.verdict}）",
                    ],
                    created_at=a.created_at,
                )
            )
        for p in await self.studio.proposals(project_id, "PENDING_APPROVAL"):
            details = []
            for ch in p.diff.changes if p.diff else []:
                if ch.kind == "changed":
                    details.append(f"変更 {ch.speaker}: {ch.before} → {ch.after}")
                elif ch.kind == "added":
                    details.append(f"追加 {ch.speaker}: {ch.after}")
                elif ch.kind == "removed":
                    details.append(f"削除 {ch.speaker}: {ch.before}")
                else:
                    details.append(f"並べ替え {ch.speaker}")
            details += [f"AIのメモ: {n}" for n in p.notes or []]
            items.append(
                ApprovalItem(
                    kind=ApprovalKind.SCRIPT,
                    id=p.id,
                    project_id=project_id,
                    title=f"台本修正案: {p.reason}",
                    details=details or ["変更なし"],
                    created_at=p.created_at,
                )
            )
        return sorted(items, key=lambda i: i.created_at)

    # ---------- 決定 ----------
    async def decide(
        self, kind: ApprovalKind, item_id: str, approve: bool, project_id: str | None = None
    ) -> ApprovalResult:
        status: Any
        if kind == ApprovalKind.COMMAND:
            async with self.db.session_factory() as s:
                row = await s.get(EditCommand, item_id)
                if row is None:
                    raise ApprovalError(f"command {item_id} not found")
                pid = row.project_id
            if project_id and pid != project_id:
                raise ApprovalError("project mismatch")
            res = await self.timeline.decide(pid, item_id, approve)
            status = res.status.value
        elif kind == ApprovalKind.RENDER:
            status = (await self.render.decide(item_id, approve)).status.value
        elif kind == ApprovalKind.ASSET:
            status = (await self.assets.decide(item_id, approve)).status.value
        else:
            status = (await self.studio.decide_proposal(item_id, approve)).status
        return ApprovalResult(kind=kind, id=item_id, status=status)

    async def status_of(self, kind: ApprovalKind, item_id: str) -> str | None:
        """承認対象の現在の状態（会話の再実行時に未決定の提案を取り下げるため）。"""
        if kind == ApprovalKind.COMMAND:
            async with self.db.session_factory() as s:
                row = await s.get(EditCommand, item_id)
                return row.status if row else None
        if kind == ApprovalKind.RENDER:
            return (await self.render.get(item_id)).status.value
        if kind == ApprovalKind.ASSET:
            async with self.db.session_factory() as s:
                a = await s.get(AssetRequestRow, item_id)
                return a.status if a else None
        return (await self.studio.proposal(item_id)).status
