"""演出の自動化（演出プラン → 素材の生成 → タイムラインへの配置）。

画面の割り付け（16:9 / 9:16 共通の比率）:
- 上の帯（高さの 2.5%〜）: キーワードのテロップ（左寄せ）
- 中央の領域（13%〜71%）: カード・図表・画像（中央に 1 つ）
- 下: 字幕（字幕の帯と重ならない）

画面が空にならないよう、図・カードは次の図・カードが出るまで表示し続ける。
適用は 1 つの編集（Batch）としてまとめ、元に戻せる。LLM 経由（MCP）の適用は承認が必要。
"""

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from video_automation.app.logging import get_logger
from video_automation.app.settings import Settings
from video_automation.database.database import Database
from video_automation.database.models import Project, Script, ScriptSegment
from video_automation.integrations.ffmpeg.runner import FFmpeg, FFmpegError
from video_automation.schemas.animation import CardRequest, InfographicRequest
from video_automation.schemas.design import Component, DesignProfile
from video_automation.schemas.direction import (
    MAIN_KINDS,
    DirectionApplyRequest,
    DirectionApplyResult,
    DirectionPlan,
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
from video_automation.services.design.guard import Placement, is_dark
from video_automation.services.design.service import DesignService, manifest_record
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
CARD_SCALE = 1.25  # 図・カード領域のカードの文字の大きさ（字幕より少し小さい程度）
HOLD_LINES = 2  # 図・カードを説明の範囲の後に出し続ける発話数の上限


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


def native_scale(aw: int, ah: int, cw: int, ch: int, max_h: float = 1.0) -> float:
    """native_transform で置いたときの、素材の画素に対する表示の倍率。"""
    return min(1.0, max_h * ch / ah, 0.92 * cw / aw)


def fit_to_profile(
    plan: DirectionPlan, prof: DesignProfile | None, extras: list[Component] | None = None
) -> tuple[DirectionPlan, list[str]]:
    """用途の型で使わない部品（題名・カード・図表・画像・テロップ）を外し、回数の上限で切る。"""
    if prof is None:
        return plan, []
    extras = extras or []
    kind_comp = {
        ShotKind.CARD: Component.CARD,
        ShotKind.CHART: Component.CHART,
        ShotKind.IMAGE: Component.IMAGE,
        ShotKind.KEYWORD: Component.KEYWORD,
    }
    notes: list[str] = []
    count: dict[Component, int] = {}
    shots = []
    for s in plan.shots:
        c = kind_comp[s.kind]
        limit = -1 if c in extras else prof.allowance(c)
        n = count.get(c, 0)
        if limit == 0 or (limit > 0 and n >= limit):
            why = "使いません" if limit == 0 else f"{limit} 回までです"
            notes.append(
                f"{s.from_line}: 「{prof.label}」では {s.kind.value} を{why}（外しました）"
            )
            continue
        count[c] = n + 1
        shots.append(s)
    update: dict[str, object] = {"shots": shots}
    if plan.title and not (prof.allows(Component.TITLE_CARD) or Component.TITLE_CARD in extras):
        update.update(title="", subtitle="")
        notes.append(f"「{prof.label}」では題名カードを使いません（外しました）")
    return plan.model_copy(update=update), notes


class DirectionService:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        llm: LLMGateway,
        timeline: TimelineService,
        titles: TitleCardService,
        ffmpeg: FFmpeg,
        design: DesignService | None = None,
    ) -> None:
        self.design = design
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
            res = await self.llm.complete_json(  # 形式が合わないときは 2 回まで直させる
                TaskType.EDIT_PLAN,
                [
                    ChatMessage(role="system", content=SYSTEM),
                    ChatMessage(role="user", content=body),
                ],
                PLAN_SCHEMA,
                model_key=req.model_key,
                temperature=0.3,
                retries=2,
            )
            model_key = res.model_key
            raw = from_llm(res.data)
            if not raw.shots:  # AI が何も出さなかったときは規則の演出（数値のテロップ）で補う
                raw.shots = rule_plan(texts, title).shots
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
    async def _background(
        self, project_id: str, style: str, w: int, h: int, prof: DesignProfile | None = None
    ) -> AssetRead:
        c0, c1 = BACKGROUNDS.get(style, BACKGROUNDS["navy"])
        if prof is not None:  # 用途の型の背景（暗い側→明るい側のグラデーション）
            c0 = "0x" + prof.palette.base.lstrip("#")
            c1 = "0x" + prof.palette.base2.lstrip("#")
            style = f"{prof.purpose.value}"
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
        prof = await self.design.profile_for(p.id) if self.design else None
        extras = (await self.design.get(p.id)).extras if self.design and prof else []
        plan, dropped = fit_to_profile(plan, prof, extras)
        w, h = p.width, p.height
        # 色・余白は用途の型から（型が無いときは従来の値）
        accent = prof.palette.main if prof else plan.accent_color
        emphasis = prof.palette.accent if prof else plan.accent_color
        panel = prof.palette.surface if prof else "#0F1B2D"
        ink = prof.palette.text if prof else "#FFFFFF"
        chart_colors = prof.palette.chart if prof else [accent, "#FFB74D", "#81C784", "#F06292"]
        muted = prof.palette.text_muted if prof else "#6F829E"
        heading_font = prof.typography.heading_font if prof else None
        # 題名の縁取り: 暗い文字なら背景の明るい色で縁取る
        title_outline = prof.palette.base if prof and is_dark(prof.palette.text) else "#000000"
        strip_top = max(STRIP_TOP, prof.safe_area.top) if prof else STRIP_TOP
        side = max(SIDE, prof.safe_area.left) if prof else SIDE
        used: dict[Component, int] = {}
        boxes: list[Placement] = []

        def use(c: Component, n: int = 1) -> None:
            used[c] = used.get(c, 0) + n

        fps = p.fps_num / p.fps_den
        tl = await self.timeline.get_timeline(p.id)
        total = max(tl.duration_frames, lines[-1].end)
        assets = {a.id: a for a in tl.assets}
        skipped: list[str] = list(plan.notes) + dropped

        def frames(sec: float) -> int:
            return round(sec * fps)

        def media_frames(a: AssetRead, fallback: int) -> int:
            """素材の長さ（フレーム）。タイムラインの判定と同じく切り捨て。"""
            if not a.duration_sec:
                return fallback
            return max(1, math.floor(a.duration_sec * fps + 1e-6))

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
        bg = await self._background(p.id, plan.background, w, h, prof)
        cid = add(bg_t, bg.id, 0, total)
        if prof is None or prof.allows(Component.BACKGROUND_MOTION):
            cmds.append(SetClipAnimation(clip_id=cid, preset="ken_burns_in", intensity=0.4))
            use(Component.BACKGROUND_MOTION)

        # タイトル（最初の発話の間。短すぎるときは 3 秒）
        title_end = 0
        if plan.title:
            use(Component.TITLE_CARD)
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
                    text_color=ink,
                    outline_color=title_outline,
                    font=heading_font,
                )
            )
            add(txt_t, t_asset.id, 0, min(title_end, media_frames(t_asset, title_end)))
            if plan.subtitle:
                sub = await self.titles.card(
                    CardRequest(
                        project_id=p.id,
                        heading=plan.subtitle,
                        style="keyword",
                        accent_color=emphasis,
                        panel_color=panel,
                        text_color=ink,
                        font=heading_font,
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
                        sub.width or 1, sub.height or 1, w, h, cx=0.5, cy=0.6
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
            if i + 1 < len(mains):
                # 次の図・カードまで表示する。ただし説明している範囲（to_line）の
                # 2 発話先までに留める（話題が変わった後に古い図を出し続けない）
                hold = lines[min(len(lines), (s.to_line or s.from_line) + HOLD_LINES) - 1].end
                nxt = lines[mains[i + 1].from_line - 1].start
                if prof is not None and prof.max_empty_sec is not None:
                    # 型に「背景だけの時間の上限」があれば、次までの空きがそれ以下になるよう延ばす
                    hold = max(hold, nxt - frames(prof.max_empty_sec))
                end = min(nxt, hold)
            else:
                end = total
            end = max(end, title_end)
            if end - start < frames(0.5):
                skipped.append(f"{s.from_line}: 表示時間が短すぎる {s.kind.value} を外しました")
                continue
            windows.append((s, start, end))
        shown: list[tuple[int, int]] = []  # 実際に図・カード・画像を出した区間
        for s, start, end in windows:
            length = end - start
            fade = min(8, length // 4)
            if s.kind == ShotKind.CARD:
                use(Component.CARD)
                a = await self.titles.card(
                    CardRequest(
                        project_id=p.id,
                        heading=s.heading,
                        body=s.body,
                        style=s.style,
                        accent_color=accent,
                        panel_color=panel,
                        text_color=ink,
                        font=heading_font,
                        scale=CARD_SCALE,
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
                shown.append((start, end))
            elif s.kind == ShotKind.CHART:
                use(Component.CHART)
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
                        colors=chart_colors,
                        muted_color=muted,
                        font=heading_font,
                        text_color=ink,
                        box_color=panel,
                    )
                )
                media = media_frames(a, length)
                shown_len = min(length, media)
                add(
                    fig_t,
                    a.id,
                    start,
                    shown_len,
                    transform=ClipTransform(fit=FitMode.FIT, scale=zone_h, y=zone_cy - 0.5),
                    fade_in_frames=fade,
                    # 図の素材が表示区間より短いとき（長さの上限）は、急に消さずに消す
                    fade_out_frames=min(8, shown_len // 4) if shown_len < length else 0,
                )
                shown.append((start, start + shown_len))
            else:  # IMAGE
                img = assets.get(s.asset_id or "")
                if img is None or img.kind != AssetKind.IMAGE:
                    q = f"（検索語: {s.query}）" if s.query else ""
                    skipped.append(f"{s.from_line}: 画像の素材が未選択のため外しました{q}")
                    continue
                use(Component.IMAGE)
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
                shown.append((start, end))

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
            use(Component.KEYWORD)
            a = await self.titles.card(
                CardRequest(
                    project_id=p.id,
                    heading=s.text,
                    style="keyword",
                    accent_color=emphasis,
                    panel_color=panel,
                    text_color=ink,
                    font=heading_font,
                    scale=0.62,
                )
            )
            aw, ah = a.width or 1, a.height or 1
            kx, ky = side + aw / 2 / w, strip_top + ah / 2 / h
            k = native_scale(aw, ah, w, h)
            boxes.append(
                Placement(
                    Component.KEYWORD,
                    kx - aw * k / 2 / w,
                    ky - ah * k / 2 / h,
                    kx + aw * k / 2 / w,
                    ky + ah * k / 2 / h,
                )
            )
            cid = add(
                txt_t,
                a.id,
                start,
                end - start,
                transform=native_transform(aw, ah, w, h, cx=kx, cy=ky),
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
        # 背景だけ（題名・図・カード・画像が無い）区間
        covered = sorted([(0, title_end)] + shown)
        empty: list[tuple[float, float]] = []
        cur = 0
        for w0, w1 in covered:
            if w0 > cur:
                empty.append((cur / fps, w0 / fps))
            cur = max(cur, w1)
        if cur < total:
            empty.append((cur / fps, total / fps))
        if self.design is not None:  # 書き出し前の検査のために、作った要素を記録する
            colors = [accent, emphasis, panel, ink]
            if used.get(Component.CHART):
                colors += chart_colors
            fonts = [Path(f).stem for f in [self.titles.font_path(heading_font)] if f]
            # 書き出し時の画面演出（rich_direction）が使う、題名と章の区切り
            outline = {
                "title": plan.title,
                "catch": plan.subtitle,
                "chapters": [
                    [start, (s.heading or "").strip()]
                    for s, start, _end in windows
                    if s.kind in (ShotKind.CARD, ShotKind.CHART) and (s.heading or "").strip()
                ],
            }
            await self.design.record(
                p.id,
                "direction",
                manifest_record(used, colors, fonts, boxes, empty, outline=outline),
            )
        log.info("direction_applied", project_id=p.id, clips=clips, skipped=len(skipped))
        return DirectionApplyResult(
            command_id=res.id, status=res.status, clips=clips, skipped=skipped
        )
