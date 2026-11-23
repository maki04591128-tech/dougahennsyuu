"""会話管理・編集計画・命令変換と検証・ツール実行（P8-01〜04, 06, 09）。

フロー（仕様 §7・§10）: ユーザーの指示 → 会話とプロジェクト状態を LLM に渡す → 構造化された
計画（actions）→ スキーマ検証 → ツールごとの検証（タイムラインは dry-run）→ 無効なら 1 回だけ
誤りを伝えて修正させる → 実行（変更を伴うものは承認待ち）→ 結果を会話に保存。
LLM の自然言語出力をそのまま実行しない。
"""

import json
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select

from video_automation.app.logging import get_logger
from video_automation.database.database import Database
from video_automation.database.models import (
    Asset,
    Clip,
    Conversation,
    ConversationMessage,
    EditCommand,
    Project,
    Script,
    ScriptSegment,
    Track,
)
from video_automation.schemas.asset_source import (
    AssetDownloadRequest,
    AssetSearchRequest,
    MediaType,
    SourceName,
)
from video_automation.schemas.orchestrator import (
    ActionResult,
    ApprovalItem,
    ApprovalKind,
    ConversationCreate,
    ConversationDetail,
    ConversationRead,
    MessageRead,
)
from video_automation.schemas.provider import ChatMessage, TaskType
from video_automation.schemas.script_studio import ProposalRead, ScriptGenerateRequest
from video_automation.schemas.timeline import (
    CommandOrigin,
    CommandRequest,
    EditCommandBody,
    RenderProfileName,
    RenderRequest,
)
from video_automation.services.assets.asset_source_service import AssetSourceService
from video_automation.services.llm_tasks.gateway import LLMError, LLMGateway
from video_automation.services.orchestrator import prompts as P
from video_automation.services.orchestrator.approvals import ApprovalService
from video_automation.services.orchestrator.diff import fmt_time
from video_automation.services.rendering.render_service import RenderService
from video_automation.services.script.studio import ScriptStudio
from video_automation.services.timeline.timeline_service import TimelineService

log = get_logger("orchestrator")
HISTORY_TURNS = 10


class OrchestratorError(Exception):
    code = "orchestrator_error"
    public_message = True


class ConversationNotFoundError(OrchestratorError):
    code = "conversation_not_found"


# ---------- ツールの引数（P8-03 命令変換） ----------
class EditTimelineArgs(BaseModel):
    description: str = Field(default="", max_length=500)
    command: EditCommandBody


class GenerateScriptArgs(BaseModel):
    script_id: str
    instruction: str = Field(default="", max_length=2000)


class RenderArgs(BaseModel):
    profile: RenderProfileName = RenderProfileName.FHD_LANDSCAPE
    script_id: str | None = None


class SearchArgs(BaseModel):
    query: str = Field(min_length=1, max_length=100)
    media_type: MediaType = MediaType.IMAGE


class DownloadArgs(BaseModel):
    source: SourceName
    source_id: str = Field(min_length=1, max_length=200)
    media_type: MediaType


class ToolInputError(Exception):
    """検証で弾かれた（修正の再試行対象）。"""


class ToolRouter:
    """計画の action を検証して各サービスへ振り分ける（P8-06）。変更を伴う操作は承認待ちにする。"""

    def __init__(
        self,
        timeline: TimelineService,
        studio: ScriptStudio,
        render: RenderService,
        assets: AssetSourceService,
        approvals: ApprovalService,
    ) -> None:
        self.timeline = timeline
        self.studio = studio
        self.render = render
        self.assets = assets
        self.approvals = approvals

    async def validate(self, project_id: str, tool: str, args: dict[str, Any]) -> Any:
        """スキーマ・権限・状態の検証。実行せずに問題点を ToolInputError で返す。"""
        try:
            if tool == "edit_timeline":
                norm = normalize_edit_args(args)
                a: Any = EditTimelineArgs.model_validate(norm)
                if extra := unknown_fields(norm.get("command"), a.command):
                    # 知らない項目は黙って捨てず、指示が反映されない原因として修正させる
                    raise ToolInputError("unknown fields: " + ", ".join(extra))
                v = await self.timeline.validate_command(
                    project_id, CommandRequest(command=a.command, origin=CommandOrigin.LLM)
                )
                errors = [i.message for i in v.issues if i.level == "error"]
                if not v.valid:
                    raise ToolInputError("; ".join(errors) or "invalid command")
                return a
            if tool == "generate_script":
                a = GenerateScriptArgs.model_validate(args)
                d = await self.studio.detail(a.script_id)
                if d.project_id != project_id:
                    raise ToolInputError(f"script {a.script_id} is not in this project")
                return a
            if tool == "render_project":
                a = RenderArgs.model_validate(args)
                if a.script_id and (await self.studio.detail(a.script_id)).project_id != project_id:
                    raise ToolInputError(f"script {a.script_id} is not in this project")
                return a
            if tool == "search_assets":
                return SearchArgs.model_validate(args)
            if tool == "download_asset":
                return DownloadArgs.model_validate(args)
        except ValidationError as exc:
            raise ToolInputError(_short_errors(exc)) from exc
        except ToolInputError:
            raise
        except Exception as exc:  # 存在しない ID など
            if getattr(exc, "code", None) or isinstance(exc, LookupError):
                raise ToolInputError(str(exc)[:300]) from exc
            raise
        raise ToolInputError(f"unknown tool: {tool}")

    async def execute(
        self, project_id: str, tool: str, a: Any, model_key: str | None = None
    ) -> ActionResult:
        """model_key: ユーザーがこの指示に選んだモデル（台本生成などの LLM 処理にも使う）。"""
        if isinstance(a, EditTimelineArgs):
            row = await self.timeline.submit(
                project_id,
                CommandRequest(
                    command=a.command, origin=CommandOrigin.LLM, description=a.description
                ),
            )
            async with self.timeline.db.session_factory() as s:
                ec = await s.get(EditCommand, row.id)
                assert ec is not None
                details = await self.approvals.command_details(project_id, ec)
            item = ApprovalItem(
                kind=ApprovalKind.COMMAND,
                id=row.id,
                project_id=project_id,
                title=a.description or "タイムライン変更",
                details=details,
                created_at=row.created_at,
            )
            return ActionResult(
                tool=tool, ok=True, summary="タイムライン変更（承認待ち）", approval=item
            )
        if isinstance(a, GenerateScriptArgs):
            p = await self.studio.generate(
                a.script_id,
                ScriptGenerateRequest(instruction=a.instruction, model_key=model_key),
                propose=True,
            )
            assert isinstance(p, ProposalRead)
            n = len(p.diff.changes) if p.diff else 0
            item = ApprovalItem(
                kind=ApprovalKind.SCRIPT,
                id=p.id,
                project_id=project_id,
                title=f"台本修正案: {p.reason}",
                details=[f"{n} 件の変更"] + [f"AIのメモ: {x}" for x in p.notes or []],
                created_at=p.created_at,
            )
            return ActionResult(
                tool=tool, ok=True, summary="台本の生成案（承認待ち）", approval=item
            )
        if isinstance(a, RenderArgs):
            r = await self.render.request(
                RenderRequest(
                    project_id=project_id,
                    profile=a.profile,
                    script_id=a.script_id,
                    origin=CommandOrigin.LLM,
                )
            )
            item = ApprovalItem(
                kind=ApprovalKind.RENDER,
                id=r.id,
                project_id=project_id,
                title=f"最終レンダリング（{r.profile}）",
                details=[f"出力プロファイル: {r.profile}"],
                created_at=r.created_at,
            )
            return ActionResult(
                tool=tool, ok=True, summary="レンダリング（承認待ち）", approval=item
            )
        if isinstance(a, SearchArgs):
            res = await self.assets.search(
                AssetSearchRequest(query=a.query, media_type=a.media_type, per_page=5)
            )
            hits = [
                {
                    "source": h.source.value,
                    "source_id": h.source_id,
                    "title": h.title[:80],
                    "license": h.license.code,
                }
                for h in res.results[:5]
            ]
            return ActionResult(
                tool=tool,
                ok=True,
                summary=f"「{a.query}」の検索結果 {len(res.results)} 件",
                data={"results": hits, "errors": res.errors},
            )
        assert isinstance(a, DownloadArgs)
        d = await self.assets.download(
            AssetDownloadRequest(
                project_id=project_id,
                source=a.source,
                source_id=a.source_id,
                media_type=a.media_type,
                origin="llm",
            )
        )
        if d.status == "PENDING_APPROVAL" and d.request_id:
            item = ApprovalItem(
                kind=ApprovalKind.ASSET,
                id=d.request_id,
                project_id=project_id,
                title=f"素材ダウンロード: {a.source.value}/{a.source_id}",
                details=[f"ライセンス判定: {d.license.verdict.value}"],
                created_at=_now(),
            )
            return ActionResult(
                tool=tool, ok=True, summary="素材ダウンロード（承認待ち）", approval=item
            )
        return ActionResult(
            tool=tool, ok=True, summary="素材ダウンロードを開始", data={"job_id": d.job_id}
        )


def _now() -> Any:
    from datetime import UTC, datetime

    return datetime.now(UTC)


def _short_errors(exc: ValidationError) -> str:
    parts = []
    for e in exc.errors()[:5]:
        loc = ".".join(str(x) for x in e["loc"])
        parts.append(f"{loc}: {e['msg']}")
    return "; ".join(parts)


class Orchestrator:
    def __init__(
        self, db: Database, llm: LLMGateway, router: ToolRouter, approvals: ApprovalService
    ) -> None:
        self.db = db
        self.llm = llm
        self.router = router
        self.approvals = approvals

    # ---------- 会話 ----------
    async def create(self, req: ConversationCreate) -> ConversationRead:
        async with self.db.session_factory() as s:
            if await s.get(Project, req.project_id) is None:
                raise ConversationNotFoundError(f"project {req.project_id}")
            row = Conversation(project_id=req.project_id, title=req.title)
            s.add(row)
            await s.commit()
            return ConversationRead(
                id=row.id, project_id=row.project_id, title=row.title, created_at=row.created_at
            )

    async def list_conversations(self, project_id: str) -> list[ConversationRead]:
        async with self.db.session_factory() as s:
            q = select(Conversation).where(Conversation.project_id == project_id)
            return [
                ConversationRead(
                    id=r.id, project_id=r.project_id, title=r.title, created_at=r.created_at
                )
                for r in (await s.execute(q.order_by(Conversation.created_at))).scalars()
            ]

    async def get(self, conversation_id: str) -> ConversationDetail:
        async with self.db.session_factory() as s:
            conv = await s.get(Conversation, conversation_id)
            if conv is None:
                raise ConversationNotFoundError(f"conversation {conversation_id}")
            msgs = (
                await s.execute(
                    select(ConversationMessage)
                    .where(ConversationMessage.conversation_id == conversation_id)
                    .order_by(ConversationMessage.sequence)
                )
            ).scalars()
            return ConversationDetail(
                id=conv.id,
                project_id=conv.project_id,
                title=conv.title,
                created_at=conv.created_at,
                messages=[_msg(m) for m in msgs],
            )

    async def _add(self, conversation_id: str, **values: Any) -> ConversationMessage:
        async with self.db.session_factory() as s:
            n = (
                await s.execute(
                    select(func.count())
                    .select_from(ConversationMessage)
                    .where(ConversationMessage.conversation_id == conversation_id)
                )
            ).scalar_one()
            row = ConversationMessage(conversation_id=conversation_id, sequence=n, **values)
            s.add(row)
            await s.commit()
            return row

    # ---------- 指示 → 計画 → 実行 ----------
    async def send(
        self, conversation_id: str, text: str, model_key: str | None = None
    ) -> MessageRead:
        conv = await self.get(conversation_id)
        user = await self._add(conversation_id, role="user", content=text)
        if not conv.title:
            async with self.db.session_factory() as s:
                c = await s.get(Conversation, conversation_id)
                assert c is not None
                c.title = text[:40]
                await s.commit()
        return await self._respond(conv.project_id, conversation_id, user.id, model_key)

    async def rerun(self, message_id: str, model_key: str | None = None) -> MessageRead:
        """同じ指示を別のモデル（または同じモデル）でやり直す（P8-09）。
        以前の返答は superseded にし、その返答が出した未決定の承認待ちは取り下げる。"""
        async with self.db.session_factory() as s:
            m = await s.get(ConversationMessage, message_id)
            if m is None:
                raise ConversationNotFoundError(f"message {message_id}")
            user_id = m.id if m.role == "user" else m.reply_to
            if user_id is None:
                raise OrchestratorError("message has no user instruction")
            conv = await s.get(Conversation, m.conversation_id)
            assert conv is not None
            olds = list(
                (
                    await s.execute(
                        select(ConversationMessage).where(
                            ConversationMessage.reply_to == user_id,
                            ConversationMessage.superseded.is_(False),
                        )
                    )
                ).scalars()
            )
            for o in olds:
                o.superseded = True
            await s.commit()
            withdraw = [a for o in olds for a in _msg(o).actions if a.approval]
            project_id, conversation_id = conv.project_id, conv.id
        for a in withdraw:
            assert a.approval is not None
            st = await self.approvals.status_of(a.approval.kind, a.approval.id)
            if st == "PENDING_APPROVAL":
                await self.approvals.decide(a.approval.kind, a.approval.id, False)
        return await self._respond(project_id, conversation_id, user_id, model_key)

    async def _history(self, conversation_id: str, upto_user_id: str) -> list[ChatMessage]:
        conv = await self.get(conversation_id)
        msgs: list[ChatMessage] = []
        target_seq = next(m.sequence for m in conv.messages if m.id == upto_user_id)
        visible = [m for m in conv.messages if not m.superseded and m.sequence <= target_seq]
        for m in visible[-HISTORY_TURNS * 2 :]:
            if m.role == "assistant":
                done = "; ".join(f"{a.tool}: {a.summary}" for a in m.actions)
                content = m.content + (f"\n（実行: {done}）" if done else "")
                msgs.append(ChatMessage(role="assistant", content=content))
            else:
                msgs.append(ChatMessage(role="user", content=m.content))
        return msgs

    async def state_text(self, project_id: str) -> str:
        return json.dumps(await self.project_state(project_id), ensure_ascii=False)

    async def project_state(self, project_id: str) -> dict[str, Any]:
        """LLM・MCP に渡すプロジェクトの状態（ID・時間・素材・台本）。"""
        async with self.db.session_factory() as s:
            p = await s.get(Project, project_id)
            if p is None:
                raise ConversationNotFoundError(f"project {project_id}")
            fps = p.fps_num / p.fps_den
            tracks = (
                (
                    await s.execute(
                        select(Track).where(Track.project_id == project_id).order_by(Track.index)
                    )
                )
                .scalars()
                .all()
            )
            clips = (
                (
                    await s.execute(
                        select(Clip).where(Clip.project_id == project_id).order_by(Clip.start_frame)
                    )
                )
                .scalars()
                .all()
            )
            assets = (
                (await s.execute(select(Asset).where(Asset.project_id == project_id)))
                .scalars()
                .all()
            )
            scripts = (
                (await s.execute(select(Script).where(Script.project_id == project_id)))
                .scalars()
                .all()
            )
            counts = {
                sid: n
                for sid, n in (
                    await s.execute(
                        select(ScriptSegment.script_id, func.count())
                        .where(ScriptSegment.script_id.in_([x.id for x in scripts]))
                        .group_by(ScriptSegment.script_id)
                    )
                ).all()
            }
            names = {a.id: a.name for a in assets}
            return {
                "project": {
                    "id": p.id,
                    "name": p.name,
                    "width": p.width,
                    "height": p.height,
                    "fps": round(fps, 3),
                },
                "tracks": [
                    {"track_id": t.id, "kind": t.kind, "name": t.name, "muted": t.muted}
                    for t in tracks
                ],
                "clips": [
                    {
                        "clip_id": c.id,
                        "track_id": c.track_id,
                        "asset": names.get(c.asset_id, c.asset_id),
                        "start_frame": c.start_frame,
                        "end_frame": c.end_frame,
                        "time": f"{fmt_time(c.start_frame, fps)}-{fmt_time(c.end_frame, fps)}",
                        "volume": c.volume,
                        "animated": bool(c.animation),
                    }
                    for c in clips
                ][:200],
                "assets": [
                    {
                        "asset_id": a.id,
                        "name": a.name,
                        "kind": a.kind,
                        "duration_sec": a.duration_sec,
                    }
                    for a in assets
                ][:200],
                "scripts": [
                    {
                        "script_id": x.id,
                        "title": x.title,
                        "mode": x.mode,
                        "version": x.version,
                        "turns": counts.get(x.id, 0),
                    }
                    for x in scripts
                ],
            }

    async def _respond(
        self, project_id: str, conversation_id: str, user_id: str, model_key: str | None
    ) -> MessageRead:
        state = await self.state_text(project_id)
        msgs = [
            ChatMessage(role="system", content=P.SYSTEM + "\n\nプロジェクトの状態:\n" + state),
            *await self._history(conversation_id, user_id),
        ]
        res = await self.llm.complete_json(
            TaskType.EDIT_PLAN, msgs, P.PLAN_SCHEMA, model_key=model_key
        )
        plan = res.data
        results: list[ActionResult] = []
        for act in merge_timeline_actions(plan.get("actions", [])[:10]):
            results.append(await self._run_action(project_id, msgs, act, res.model_key, model_key))
        row = await self._add(
            conversation_id,
            role="assistant",
            content=str(plan.get("reply", ""))[:20000],
            model_key=res.model_key,
            reply_to=user_id,
            data={
                "prompt_version": P.PLAN_VERSION,
                "plan": plan,
                "actions": [r.model_dump(mode="json") for r in results],
                "questions": [str(q)[:500] for q in plan.get("questions") or []][:10],
                "duration_ms": res.duration_ms,
            },
        )
        log.info(
            "plan",
            conversation=conversation_id,
            model=res.model_key,
            actions=[f"{r.tool}:{'ok' if r.ok else 'ng'}" for r in results],
        )
        return _msg(row)

    async def _run_action(
        self,
        project_id: str,
        msgs: list[ChatMessage],
        act: dict[str, Any],
        model_key: str,
        chosen: str | None,
    ) -> ActionResult:
        tool, args = act.get("tool", ""), act.get("args") or {}
        try:
            parsed = await self.router.validate(project_id, tool, args)
        except ToolInputError as first:
            # P8-04: 誤りを伝えて 1 回だけ修正させる
            repair = [
                *msgs,
                ChatMessage(
                    role="assistant",
                    content=json.dumps({"tool": tool, "args": args}, ensure_ascii=False),
                ),
                ChatMessage(
                    role="user",
                    content=(
                        f"この操作は検証で無効でした: {first}\n"
                        "プロジェクトの状態にある ID・値の範囲を確認し、"
                        "修正した action を返してください。"
                        "修正できない場合は give_up を true にしてください。"
                    ),
                ),
            ]
            try:
                fixed = await self.llm.complete_json(
                    TaskType.STRUCTURED_COMMAND, repair, P.REPAIR_SCHEMA, model_key=model_key
                )
                if fixed.data.get("give_up"):
                    raise ToolInputError(str(first))
                act2 = fixed.data["action"]
                tool, args = act2.get("tool", tool), act2.get("args") or {}
                parsed = await self.router.validate(project_id, tool, args)
            except (ToolInputError, LLMError) as second:
                return ActionResult(
                    tool=tool,
                    ok=False,
                    summary="操作が無効のため実行しませんでした",
                    error=str(second)[:500],
                    data={"args": args},
                )
        try:
            return await self.router.execute(project_id, tool, parsed, chosen)
        except Exception as exc:  # 実行時の失敗は会話に残して続ける
            if not getattr(exc, "code", None):
                raise
            return ActionResult(
                tool=tool, ok=False, summary="実行に失敗しました", error=str(exc)[:500]
            )


def _drop_nulls(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: _drop_nulls(x) for k, x in v.items() if x is not None}
    if isinstance(v, list):
        return [_drop_nulls(x) for x in v]
    return v


def normalize_edit_args(args: dict[str, Any]) -> dict[str, Any]:
    """実機のモデルの書き方の揺れを吸収する（中身は通常どおりスキーマ検証する）。

    - 命令を args 直下に書く（qwen3-swallow 等）→ {command: ...} へ包み直す
    - 指定しない項目を null で埋める → 未指定（既定値）として扱う"""
    if not isinstance(args, dict):
        return args
    if "command" in args:
        if not isinstance(args["command"], dict):
            return args
        return {**args, "command": _drop_nulls(args["command"])}
    if "type" in args or "commands" in args:
        rest = {k: v for k, v in args.items() if k != "description"}
        if "type" not in rest:
            rest["type"] = "batch"
        return {"description": args.get("description", ""), "command": _drop_nulls(rest)}
    return args


def unknown_fields(raw: Any, parsed: BaseModel) -> list[str]:
    """命令の dict のうち、命令のモデルに無い項目（モデルが捏造した項目）を列挙する。"""
    if not isinstance(raw, dict):
        return []
    out: list[str] = []
    allowed = set(type(parsed).model_fields)
    for k, v in raw.items():
        if k not in allowed:
            out.append(f"{raw.get('type', '?')}.{k}")
            continue
        sub = getattr(parsed, k, None)
        if isinstance(sub, BaseModel):
            out += unknown_fields(v, sub)
        elif isinstance(sub, list) and isinstance(v, list):
            for rv, pv in zip(v, sub, strict=False):
                if isinstance(pv, BaseModel):
                    out += unknown_fields(rv, pv)
    return out


def merge_timeline_actions(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """1 回の計画に含まれるタイムライン変更を 1 つのバッチ命令にまとめる。

    別々に承認待ちにすると、それぞれは今の状態で正しくても組み合わせると矛盾する（同じ位置への
    二重配置など）。まとめて検証し、1 回の承認・1 回の Undo で扱う。"""
    edits = [a for a in actions if a.get("tool") == "edit_timeline"]
    if len(edits) < 2:
        return actions
    commands: list[Any] = []
    descriptions: list[str] = []
    for a in edits:
        raw = a.get("args")
        args: dict[str, Any] = raw if isinstance(raw, dict) else {}
        cmd = normalize_edit_args(args).get("command")
        if isinstance(cmd, dict) and cmd.get("type") == "batch":
            commands += list(cmd.get("commands") or [])
        elif cmd is not None:
            commands.append(cmd)
        if args.get("description"):
            descriptions.append(str(args["description"]))
    merged = {
        "tool": "edit_timeline",
        "args": {
            "description": " / ".join(descriptions)[:500],
            "command": {"type": "batch", "commands": commands},
        },
    }
    out: list[dict[str, Any]] = []
    for a in actions:
        if a.get("tool") != "edit_timeline":
            out.append(a)
        elif a is edits[0]:
            out.append(merged)
    return out


def _msg(m: ConversationMessage) -> MessageRead:
    data = m.data or {}
    return MessageRead(
        id=m.id,
        sequence=m.sequence,
        role=m.role,  # type: ignore[arg-type]
        content=m.content,
        model_key=m.model_key,
        reply_to=m.reply_to,
        superseded=m.superseded,
        actions=[ActionResult.model_validate(a) for a in data.get("actions", [])],
        questions=data.get("questions", []),
        created_at=m.created_at,
    )
