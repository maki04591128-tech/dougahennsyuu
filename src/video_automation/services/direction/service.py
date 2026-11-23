"""演出の自動化（演出プラン → 素材の生成 → タイムラインへの配置）。

画面の割り付け（16:9 / 9:16 共通の比率）:
- 上の帯（高さの 2.5%〜）: キーワードのテロップ（左寄せ）
- 中央の領域（13%〜71%）: カード・図表・画像（中央に 1 つ）
- 下: 字幕（字幕の帯と重ならない）

画面が空にならないよう、図・カードは次の図・カードが出るまで表示し続ける。
適用は 1 つの編集（Batch）としてまとめ、元に戻せる。LLM 経由（MCP）の適用は承認が必要。
"""

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from video_automation.app.logging import get_logger
from video_automation.app.settings import Settings
from video_automation.database.database import Database
from video_automation.database.models import Project, Script, ScriptSegment
from video_automation.integrations.ffmpeg.runner import FFmpeg, FFmpegError
from video_automation.schemas.animation import CardRequest, InfographicRequest
from video_automation.schemas.direction import (
    MAIN_KINDS,
    DirectionApplyRequest,
    DirectionApplyResult,
    DirectionPlanRequest,
    DirectionPlanResult,
    Shot,
    ShotKind,
)
from video_automation.schemas.provider import ChatMessage, TaskType
from video_automation.schemas.timeline import (
    AddClip,
    AddTrack,
    AssetKind,
    AssetRead,
    Batch,
    ClipTransform,
    CommandOrigin,
    CommandRequest,
    DeleteClip,
    FitMode,
    SetClipAnimation,
    TitleCardRequest,
    TrackKind,
)
from video_automation.services.direction.plan import (
    PLAN_SCHEMA,
    SYSTEM,
    from_llm,
    normalize,
    prompt_lines,
    rule_plan,
)
from video_automation.services.llm_tasks.gateway import LLMGateway
from video_automation.services.rendering.title_card import TitleCardService
from video_automation.services.timeline.timeline_service import (
    CommandError,
    TimelineNotFoundError,
    TimelineService,
)

log = get_logger("direction")

ZONE_TOP, ZONE_BOTTOM = 0.13, 0.71  # 図・カードの領域（高さ比）
STRIP_TOP = 0.025  # テロップの上端
SIDE = 0.035  # テロップの左余白（幅比）
TRACKS = ("演出・背景", "演出・図", "演出・文字")
BACKGROUNDS = {
    "navy": ("0x0B1D3A", "0x2A5A8A"),
    "charcoal": ("0x15171C", "0x3A3F4A"),
    "teal": ("0x062A2E", "0x1D6B6B"),
    "plum": ("0x1F0F2E", "0x5A2D6E"),
}
CHART_MAX_SEC = 30.0


@dataclass
class _Line:
    speaker: str
    text: str
    start: int
    end: int


def native_transform(
    aw: int, ah: int, cw: int, ch: int, *, cx: float, cy: float, max_h: float = 1.0
) -> ClipTransform:
    """素材をそのままの大きさ（はみ出すときは縮小）で、中心 (cx, cy)（画面比）に置く。"""
    s = max(aw / cw, ah / ch)
    k = min(1.0, max_h * ch / ah, 0.92 * cw / aw)
    return ClipTransform(fit=FitMode.FIT, scale=round(s * k, 4), x=cx - 0.5, y=cy - 0.5)


class DirectionService:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        llm: LLMGateway,
        timeline: TimelineService,
        titles: TitleCardService,
        ffmpeg: FFmpeg,
    ) -> None:
        self.settings = settings
        self.db = db
        self.llm = llm
        self.timeline = timeline
        self.titles = titles
        self.ffmpeg = ffmpeg

    async def _script(self, script_id: str) -> tuple[Project, Script, list[_Line]]:
        async with self.db.session_factory() as s:
            sc = await s.get(Script, script_id)
            if sc is None:
                raise TimelineNotFoundError(f"script {script_id}")
            p = await s.get(Project, sc.project_id)
            assert p is not None
            segs = (
                (
                    await s.execute(
                        select(ScriptSegment)
                        .where(ScriptSegment.script_id == script_id)
                        .order_by(ScriptSegment.sequence)
                    )
                )
                .scalars()
                .all()
            )
        lines = [_Line(x.speaker or "", x.text, x.start_frame or 0, x.end_frame or 0) for x in segs]
        return p, sc, lines

    # ---------- プラン ----------
    async def plan(self, req: DirectionPlanRequest) -> DirectionPlanResult:
        _, sc, lines = await self._script(req.script_id)
        texts = [x.text for x in lines]
        if not texts:
            raise CommandError("script has no lines")
        title = sc.title or ""
        model_key = None
        if req.use_llm:
            body = f"タイトル案: {title or '（なし）'}\n台本:\n" + prompt_lines(
                [x.speaker for x in lines], texts
            )
            res = await self.llm.complete_json(
                TaskType.EDIT_PLAN,
                [
                    ChatMessage(role="system", content=SYSTEM),
                    ChatMessage(role="user", content=body),
                ],
                PLAN_SCHEMA,
                model_key=req.model_key,
                temperature=0.3,
            )
            model_key = res.model_key
            raw = from_llm(res.data)
        else:
            raw = rule_plan(texts, title)
        plan = normalize(raw, texts, title)
        log.info(
            "direction_plan",
            script_id=req.script_id,
            shots=len(plan.shots),
            notes=len(plan.notes),
            model=model_key,
        )
        return DirectionPlanResult(plan=plan, model_key=model_key, lines=len(texts))

    # ---------- 適用 ----------
    async def _background(self, project_id: str, style: str, w: int, h: int) -> AssetRead:
        c0, c1 = BACKGROUNDS.get(style, BACKGROUNDS["navy"])
        out_dir = self.settings.data_dir / "projects" / project_id / "assets"
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"bg_{style}_{w}x{h}.png"
        if not out.is_file():
            src = f"gradients=s={w}x{h}:c0={c0}:c1={c1}:x0=0:y0=0:x1={w}:y1={h}:d=1"
            try:
                await self.ffmpeg.run(
                    ["-f", "lavfi", "-i", src, "-frames:v", "1", "-update", "1", str(out)],
                    timeout=60,
                )
            except FFmpegError:  # 古い FFmpeg には gradients が無い
                await self.ffmpeg.run(
                    [
                        "-f",
                        "lavfi",
                        "-i",
                        f"color=c={c1}:s={w}x{h}:d=1",
                        "-frames:v",
                        "1",
                        "-update",
                        "1",
                        str(out),
                    ],
                    timeout=60,
                )
        a = await self.timeline.register_generated(
            project_id, out, AssetKind.IMAGE, "ffmpeg", f"背景（{style}）"
        )
        return AssetRead.model_validate(a)

    async def apply(
        self, req: DirectionApplyRequest, origin: CommandOrigin = CommandOrigin.USER
    ) -> DirectionApplyResult:
        p, _, lines = await self._script(req.script_id)
        if not lines or any(x.end <= x.start for x in lines):
            raise CommandError("generate voices and synchronize subtitles first")
        plan = normalize(req.plan, [x.text for x in lines])
        w, h = p.width, p.height
        fps = p.fps_num / p.fps_den
        tl = await self.timeline.get_timeline(p.id)
        total = max(tl.duration_frames, lines[-1].end)
        assets = {a.id: a for a in tl.assets}
        skipped: list[str] = list(plan.notes)

        def frames(sec: float) -> int:
            return round(sec * fps)

        def sec(n: int) -> float:
            return n / fps

        cmds: list[Any] = []
        track: dict[str, str] = {}
        for t in tl.tracks:
            if t.name in TRACKS and t.kind == TrackKind.VIDEO and t.name not in track:
                track[t.name] = t.id
        # 以前の演出は外して作り直す（同じトラックを使う）
        for c in tl.clips:
            if c.track_id in track.values():
                cmds.append(DeleteClip(clip_id=c.id))
        for name in TRACKS:
            if name not in track:
                track[name] = str(uuid4())
                cmds.append(AddTrack(kind=TrackKind.VIDEO, name=name, track_id=track[name]))
        bg_t, fig_t, txt_t = (track[n] for n in TRACKS)
        clips = 0

        def add(track_id: str, asset_id: str, start: int, length: int, **kw: Any) -> str:
            nonlocal clips
            clip_id = str(uuid4())
            cmds.append(
                AddClip(
                    track_id=track_id,
                    asset_id=asset_id,
                    start_frame=start,
                    duration_frames=length,
                    clip_id=clip_id,
                    **kw,
                )
            )
            clips += 1
            return clip_id

        # 背景（ゆっくり寄る）
        bg = await self._background(p.id, plan.background, w, h)
        cid = add(bg_t, bg.id, 0, total)
        cmds.append(SetClipAnimation(clip_id=cid, preset="ken_burns_in", intensity=0.4))

        # タイトル（最初の発話の間。短すぎるときは 3 秒）
        title_end = 0
        if plan.title:
            title_end = min(total, max(lines[0].end, frames(3.0)))
            dur = min(sec(title_end), 30.0)
            t_asset = await self.titles.create(
                TitleCardRequest(
                    project_id=p.id,
                    text=plan.title[:100],
                    duration_sec=dur,
                    animation="pop",
                    size=0.9,
                    position="center",
                )
            )
            add(txt_t, t_asset.id, 0, min(title_end, frames(t_asset.duration_sec or dur)))
            if plan.subtitle:
                sub = await self.titles.card(
                    CardRequest(
                        project_id=p.id,
                        heading=plan.subtitle,
                        style="keyword",
                        accent_color=plan.accent_color,
                        scale=0.62,
                    )
                )
                st = min(frames(0.6), max(0, title_end - 15))
                cid = add(
                    fig_t,
                    sub.id,
                    st,
                    max(1, title_end - st),
                    transform=native_transform(
                        sub.width or 1, sub.height or 1, w, h, cx=0.5, cy=0.68
                    ),
                    fade_in_frames=8,
                    fade_out_frames=8,
                )
                cmds.append(SetClipAnimation(clip_id=cid, preset="slide_in_up"))

        # 図・カード・画像（次が出るまで表示し続ける）
        mains = [s for s in plan.shots if s.kind in MAIN_KINDS]
        zone_cy = (ZONE_TOP + ZONE_BOTTOM) / 2
        zone_h = ZONE_BOTTOM - ZONE_TOP
        windows: list[tuple[Shot, int, int]] = []
        for i, s in enumerate(mains):
            start = max(lines[s.from_line - 1].start, title_end)
            end = lines[mains[i + 1].from_line - 1].start if i + 1 < len(mains) else total
            end = max(end, title_end)
            if end - start < frames(0.5):
                skipped.append(f"{s.from_line}: 表示時間が短すぎる {s.kind.value} を外しました")
                continue
            windows.append((s, start, end))
        for s, start, end in windows:
            length = end - start
            fade = min(8, length // 4)
            if s.kind == ShotKind.CARD:
                a = await self.titles.card(
                    CardRequest(
                        project_id=p.id,
                        heading=s.heading,
                        body=s.body,
                        style=s.style,
                        accent_color=plan.accent_color,
                    )
                )
                cid = add(
                    fig_t,
                    a.id,
                    start,
                    length,
                    transform=native_transform(
                        a.width or 1, a.height or 1, w, h, cx=0.5, cy=zone_cy, max_h=zone_h
                    ),
                    fade_in_frames=fade,
                    fade_out_frames=fade,
                )
                cmds.append(SetClipAnimation(clip_id=cid, preset="pop_in"))
            elif s.kind == ShotKind.CHART:
                a = await self.titles.infographic(
                    InfographicRequest(
                        project_id=p.id,
                        kind="bar",
                        title=s.heading,
                        labels=s.labels,
                        values=s.values,
                        unit=s.unit,
                        decimals=0 if all(float(v).is_integer() for v in s.values) else 1,
                        duration_sec=min(CHART_MAX_SEC, sec(length)),
                        animation_sec=1.2,
                        highlight=s.highlight,
                        colors=[plan.accent_color, "#FFB74D", "#81C784", "#F06292"],
                    )
                )
                media = frames(a.duration_sec or 0) or length
                add(
                    fig_t,
                    a.id,
                    start,
                    min(length, media),
                    transform=ClipTransform(fit=FitMode.FIT, scale=zone_h, y=zone_cy - 0.5),
                    fade_in_frames=fade,
                )
            else:  # IMAGE
                img = assets.get(s.asset_id or "")
                if img is None or img.kind != AssetKind.IMAGE:
                    q = f"（検索語: {s.query}）" if s.query else ""
                    skipped.append(f"{s.from_line}: 画像の素材が未選択のため外しました{q}")
                    continue
                cid = add(
                    fig_t,
                    img.id,
                    start,
                    length,
                    transform=ClipTransform(fit=FitMode.FIT, scale=zone_h, y=zone_cy - 0.5),
                    fade_in_frames=fade,
                    fade_out_frames=fade,
                )
                cmds.append(SetClipAnimation(clip_id=cid, preset="ken_burns_in", intensity=0.5))

        # テロップ（その発話の間。重なる分は前を短くする）
        kws = [s for s in plan.shots if s.kind == ShotKind.KEYWORD]
        spans: list[tuple[Shot, int, int]] = []
        for s in kws:
            start = max(lines[s.from_line - 1].start + frames(0.15), title_end)
            end = lines[(s.to_line or s.from_line) - 1].end
            if spans and spans[-1][2] > start:
                prev = spans[-1]
                spans[-1] = (prev[0], prev[1], start)
            spans.append((s, start, end))
        for s, start, end in spans:
            if end - start < frames(0.6):
                continue
            a = await self.titles.card(
                CardRequest(
                    project_id=p.id,
                    heading=s.text,
                    style="keyword",
                    accent_color=plan.accent_color,
                    scale=0.62,
                )
            )
            aw, ah = a.width or 1, a.height or 1
            cid = add(
                txt_t,
                a.id,
                start,
                end - start,
                transform=native_transform(
                    aw, ah, w, h, cx=SIDE + aw / 2 / w, cy=STRIP_TOP + ah / 2 / h
                ),
                fade_in_frames=min(6, (end - start) // 4),
                fade_out_frames=min(6, (end - start) // 4),
            )
            cmds.append(SetClipAnimation(clip_id=cid, preset="slide_in_left"))

        res = await self.timeline.submit(
            p.id,
            CommandRequest(
                command=Batch(commands=cmds),
                origin=origin,
                description="演出プランを適用",
            ),
        )
        log.info("direction_applied", project_id=p.id, clips=clips, skipped=len(skipped))
        return DirectionApplyResult(
            command_id=res.id, status=res.status, clips=clips, skipped=skipped
        )
