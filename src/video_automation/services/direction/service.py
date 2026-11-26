"""演出の自動化（演出プラン → 素材の生成 → タイムラインへの配置）。

画面の割り付け（16:9 / 9:16 共通の比率）:
- 上の帯（高さの 2.5%〜）: キーワードのテロップ（左寄せ）
- 中央の領域（13%〜71%）: カード・図表・画像（中央に 1 つ）
- 下: 字幕（字幕の帯と重ならない）

画面が空にならないよう、図・カードは次の図・カードが出るまで表示し続ける。
適用は 1 つの編集（Batch）としてまとめ、元に戻せる。LLM 経由（MCP）の適用は承認が必要。
"""

import json
import math
import re
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from video_automation.app.logging import get_logger
from video_automation.app.settings import Settings
from video_automation.database.database import Database
from video_automation.database.models import Project, Script, ScriptCharacter, ScriptSegment
from video_automation.integrations.ffmpeg.runner import FFmpeg
from video_automation.schemas.animation import CardRequest, InfographicRequest
from video_automation.schemas.design import Component, DesignProfile, Purpose
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
    MoveTrack,
    SetClipAnimation,
    TitleCardRequest,
    TrackKind,
)
from video_automation.services.animation.entrance import element_animation, slide_preset
from video_automation.services.design.guard import Placement, TextUse, is_dark
from video_automation.services.design.profiles import PROFILES
from video_automation.services.design.service import DesignService, manifest_record
from video_automation.services.direction.plan import (
    PLAN_SCHEMA,
    SYSTEM,
    from_llm,
    normalize,
    prompt_lines,
    rule_plan,
)
from video_automation.services.llm_tasks.gateway import LLMError, LLMGateway
from video_automation.services.rendering.card import card_layout
from video_automation.services.rendering.pictogram import pick_icon
from video_automation.services.rendering.title_card import TitleCardService
from video_automation.services.skills.library import SkillLibrary
from video_automation.services.timeline.timeline_service import (
    CommandError,
    TimelineNotFoundError,
    TimelineService,
)
from video_automation.services.voice.reading import emphasis_terms

log = get_logger("direction")

ZONE_TOP, ZONE_BOTTOM = 0.13, 0.71  # 図・カードの領域（高さ比）
# ケン・バーンズ（統合版 B3）: 拡大量 = 秒数 × 1.75%（8〜20%）。向きは順に繰り返す
KB_PER_SEC, KB_MIN, KB_MAX = 0.0175, 0.08, 0.20
KB_ORDER = ("ken_burns_in", "ken_burns_out", "pan_left", "pan_right")
STRIP_TOP = 0.025  # テロップの上端
SIDE = 0.035  # テロップの左右の余白（幅比）
# 立ち絵があるとき、図・カードを置ける横の範囲（幅比）。左右の端は立ち絵の列（layout-no-overlap）
TACHIE_MAIN_W = 0.56
_GAP_WORD = re.compile(r"[一-龥々ァ-ヴーA-Za-z0-9０-９]{2,10}")
_GAP_NUMBER = re.compile(
    r"約?[0-9０-９][0-9０-９.,/]*(?:[万億兆千]|[A-Za-z%％℃]+|倍|円|人|個|回|秒|分|時間|日|年)+"
)
_PARTICLES = set("がをにはのでともへやか")
_GAP_STOP = {"それ", "これ", "あれ", "ここ", "そこ", "本当", "今日", "大丈夫", "一緒", "次回"}


def gap_keyword(text: str, names: tuple[str, ...] = ()) -> str:
    """空いた時間に出すテロップの語。その発話の中の語だけを使う（統合版 Q2 の補い）。

    数値と単位（「約12万km」）、かぎかっこの語、無ければ漢字・カタカナの最も長い語
    （送り仮名が続く語「色合（わせ）」は語の途中なので使わない）。"""
    m = _GAP_NUMBER.search(text)
    if m:
        return m.group(0)[:12]
    terms = [t for t in emphasis_terms(text) if not re.search(r"\d", t)]
    if terms:
        return terms[0][:12]
    words = []
    for m in _GAP_WORD.finditer(text):
        w, nxt = m.group(0), text[m.end() : m.end() + 1]
        if w in _GAP_STOP or any(w in n or n in w for n in names):
            continue
        if nxt and "ぁ" <= nxt <= "ゟ" and nxt not in _PARTICLES:
            continue
        words.append(w)
    return max(words, key=len) if words else ""


def names_of(lines: list[Any]) -> tuple[str, ...]:
    return tuple({x.speaker for x in lines if getattr(x, "speaker", "")})


TRACKS = ("演出・背景", "演出・図", "演出・文字")
TACHIE_TRACK = "立ち絵"  # characters.service.TRACK
RECORDING_TRACK = "画面録画"  # 画面録画を中央の枠に置くトラック（build.service）
CHAPTER_CARD_SEC = 2.4  # 画面録画があるときのカードの長さ（章カード: 出す・止める・消す）
BACKGROUNDS = {
    "navy": ("0x0B1D3A", "0x2A5A8A"),
    "charcoal": ("0x15171C", "0x3A3F4A"),
    "teal": ("0x062A2E", "0x1D6B6B"),
    "plum": ("0x1F0F2E", "0x5A2D6E"),
}
# 演出プランを作るときに参考にするスキル
SKILLS_FOR_PLAN = [
    "video-design-by-purpose",
    "advanced-motion-techniques",
    "layout-no-overlap",
    "blender-video-motion-design",
    "blender-rich-screen-direction",
    "screen-tutorial-direction",
]
# 画面録画を見せる用途（screen-tutorial-direction の既定の演出を優先して読ませる）
_SCREEN_PURPOSES = {"tutorial", "explainer"}
# 動く背景を「浮かぶ形」にする用途（それ以外は、箱の床が波打つアイソメトリック）
FLOATING_BG_PURPOSES = {"mv", "lyric", "trailer", "promo", "event", "product"}
CHART_MAX_SEC = 30.0
CARD_SCALE = 1.25  # 図・カード領域のカードの文字の大きさ（字幕より少し小さい程度）
HOLD_LINES = 2  # 図・カードを説明の範囲の後に出し続ける発話数の上限
REVEAL_MIN_SEC = 1.2  # カードの項目を順に出すとき、1 項目を出しておく最短の時間


@dataclass
class _Line:
    speaker: str
    text: str
    start: int
    end: int


KINETIC_TITLE_MAX = 16  # 1 文字ずつ動かす題名の文字数の上限（長いと読み終わる前に消える）
_NUM = re.compile(r"[0-9０-９]")


def _has_number(text: str) -> bool:
    return bool(_NUM.search(text or ""))


def _emphasis_budget(prof: DesignProfile | None) -> int:
    """数値テロップを強調の動きで出してよい回数（用途の型の許可。動きの弱い型は 2 回まで）。"""
    if prof is None:
        return 2
    n = prof.allowance(Component.EMPHASIS_TEXT)
    if n == 0:
        return 0
    cap = 2 if prof.motion_level <= 1 else 99
    return cap if n < 0 else min(n, cap)


def _reveal_steps(shot: Shot, lines: list, start: int, end: int, min_len: int) -> list:
    """カードの本文を 1 項目ずつ出す区間 [(開始, 終了, 出す項目数)] を返す。

    項目が 2 つ以上で、見出しか要点・まとめの型のときだけ順に出す。各項目は説明している
    発話（from_line〜to_line）に合わせて出し、発話が足りなければ等分する。
    どの区間も min_len 未満になるなら順に出さず、全部を一度に出す（None）。
    """
    n = len(shot.body)
    if n < 2 or shot.style == "keyword" or end - start < min_len * n:
        return [(start, end, None)]
    lo, hi = shot.from_line, max(shot.from_line, shot.to_line or shot.from_line)
    starts = [ln.start for ln in lines[lo - 1 : hi]]
    if len(starts) >= n:
        # 発話を項目数で区切り、各区切りの最初の発話で次の項目を出す
        times = [starts[(k * len(starts)) // n] for k in range(n)]
    else:
        times = [start + (end - start) * k // n for k in range(n)]
    times[0] = start
    times = [max(start, min(t, end)) for t in times]
    bounds = [*times, end]
    if any(bounds[k + 1] - bounds[k] < min_len for k in range(n)):
        times = [start + (end - start) * k // n for k in range(n)]
        bounds = [*times, end]
    return [(bounds[k], bounds[k + 1], k + 1) for k in range(n)]


def _hex_rgb(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def pattern_background(out: Path, w: int, h: int, c0: str, c1: str, main: str) -> None:
    """模様つきの静止画の背景: 斜めのグラデーション＋柔らかい光の円＋細かい点の格子。

    平らな単色・グラデーションだけだと画面が止まって見えるので、目立たない奥行きを付ける。"""
    from PIL import Image, ImageDraw, ImageFilter

    a, b, m = _hex_rgb(c0), _hex_rgb(c1), _hex_rgb(main)
    small = Image.new("RGB", (64, 36) if w >= h else (36, 64))
    sw, sh = small.size
    px = small.load()
    assert px is not None
    for y in range(sh):
        for x in range(sw):
            u = (x / (sw - 1) + y / (sh - 1)) / 2
            px[x, y] = tuple(round(a[i] + (b[i] - a[i]) * u) for i in range(3))
    im = small.resize((w, h), Image.Resampling.BICUBIC).convert("RGBA")
    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(glow)
    r = round(max(w, h) * 0.32)
    for cx, cy, al in ((0.82, 0.18, 46), (0.12, 0.9, 34)):
        x, y = round(w * cx), round(h * cy)
        d.ellipse((x - r, y - r, x + r, y + r), fill=(*m, al))
    glow = glow.filter(ImageFilter.GaussianBlur(r * 0.45))
    im.alpha_composite(glow)
    dots = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(dots)
    step = max(24, round(min(w, h) / 27))
    rad = max(1, step // 14)
    for y in range(step // 2, h, step):
        for x in range(step // 2, w, step):
            d.ellipse((x - rad, y - rad, x + rad, y + rad), fill=(255, 255, 255, 14))
    im.alpha_composite(dots)
    im.convert("RGB").save(out)


def native_transform(
    aw: int,
    ah: int,
    cw: int,
    ch: int,
    *,
    cx: float,
    cy: float,
    max_h: float = 1.0,
    max_w: float = 0.92,
) -> ClipTransform:
    """素材をそのままの大きさ（はみ出すときは縮小）で、中心 (cx, cy)（画面比）に置く。"""
    s = max(aw / cw, ah / ch)
    k = native_scale(aw, ah, cw, ch, max_h, max_w)
    return ClipTransform(fit=FitMode.FIT, scale=round(s * k, 4), x=cx - 0.5, y=cy - 0.5)


def text_use(
    kind: str,
    req: CardRequest,
    a: AssetRead,
    cw: int,
    ch: int,
    start: float,
    end: float,
    max_h: float = 1.0,
    max_w: float = 0.92,
) -> TextUse:
    """カード・テロップの文字の、画面での大きさ（短辺比）と表示の秒（統合版 T39・T40・T52）。"""
    layout = card_layout(req, cw, ch)
    k = native_scale(a.width or layout.width, a.height or layout.height, cw, ch, max_h, max_w)
    unit = min(cw, ch)
    sizes = sorted({round(ln.size * k / unit, 4) for ln in layout.lines})
    chars = sum(len(ln.text) for ln in layout.lines)
    return TextUse(kind, sizes, chars, start, end)


def native_scale(
    aw: int, ah: int, cw: int, ch: int, max_h: float = 1.0, max_w: float = 0.92
) -> float:
    """native_transform で置いたときの、素材の画素に対する表示の倍率。"""
    return min(1.0, max_h * ch / ah, max_w * cw / aw)


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
        skills: SkillLibrary | None = None,
    ) -> None:
        self.skills = skills
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

    async def _has_tachie(self, script_id: str) -> bool:
        """台本の登場キャラクターに立ち絵が付いているか（付いていれば左右の端は立ち絵の列）。"""
        async with self.db.session_factory() as s:
            row = (
                await s.execute(
                    select(ScriptCharacter.id)
                    .where(
                        ScriptCharacter.script_id == script_id,
                        ScriptCharacter.tachie_pack.is_not(None),
                    )
                    .limit(1)
                )
            ).first()
        return row is not None

    # ---------- プラン ----------
    async def _skill_context(self, project_id: str) -> tuple[str, list[str]]:
        """演出の決まり（スキル）のうち、この用途に関係する節。"""
        if self.skills is None:
            return "", []
        prof = await self.design.profile_for(project_id) if self.design else None
        purpose = f"{prof.label} {prof.summary} " if prof else ""
        query = (
            f"{purpose}演出 テロップ カード 図表 題名 字幕 配置 動き "
            "別用途のデザインを混ぜない 背景だけの時間"
        )
        if prof and prof.purpose in _SCREEN_PURPOSES:
            # 画面録画の解説は、強化版の演出（ズーム・強調・章・音・立ち絵）を既定にする
            query += " 既定で入れる演出 画面録画 ズーム 章の切り替え 立ち絵 BGM 効果音"
        # 依頼文ではなく決まった問い合わせなので、しきい値は使わず上位の節を入れる
        ctx, picked = self.skills.context(
            query, names=SKILLS_FOR_PLAN, budget=self.settings.skills_budget_chars, min_score=0.0
        )
        return ctx, [f"{p.skill} / {p.path}" for p in picked]

    async def plan(self, req: DirectionPlanRequest) -> DirectionPlanResult:
        p, sc, lines = await self._script(req.script_id)
        texts = [x.text for x in lines]
        names = tuple({x.speaker for x in lines})
        if not texts:
            raise CommandError("script has no lines")
        title = sc.title or ""
        model_key = None
        used_skills: list[str] = []
        if req.use_llm:
            ctx, used_skills = await self._skill_context(p.id)
            body = f"タイトル案: {title or '（なし）'}\n台本:\n" + prompt_lines(
                [x.speaker for x in lines], texts
            )
            if req.instruction.strip():
                body += (
                    "\n\n演出への希望（作った人から。上の決まりに反しない範囲で従う）: "
                    + req.instruction.strip()
                )
            res = await self.llm.complete_json(  # 形式が合わないときは 2 回まで直させる
                TaskType.EDIT_PLAN,
                [
                    ChatMessage(role="system", content=SYSTEM + ("\n\n" + ctx if ctx else "")),
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
            plan = normalize(raw, texts, title, names)
            # 図・カードが少なすぎる（同じカードが出続けて画面が止まる）ときは 1 回だけ直させる。
            # 目安は 4 発話に 1 つ（SYSTEM の「2〜4 発話に 1 つ」の下限）
            need = max(2, -(-len(texts) // 4)) if len(texts) >= 4 else 1
            have = sum(1 for x in plan.shots if x.kind in MAIN_KINDS)
            if have < need:
                more = (
                    f"card / chart / image が {have} 個しかありません。{len(texts)} 発話なので、"
                    f"少なくとも {need} 個にしてください（2〜4 発話に 1 つ、話題の変わり目ごと）。"
                    "最後の発話には style=summary のまとめのカードを置いてください。"
                    "同じ JSON の形で、演出全体を出し直してください。"
                )
                try:
                    res2 = await self.llm.complete_json(
                        TaskType.EDIT_PLAN,
                        [
                            ChatMessage(
                                role="system", content=SYSTEM + ("\n\n" + ctx if ctx else "")
                            ),
                            ChatMessage(role="user", content=body),
                            ChatMessage(
                                role="assistant", content=json.dumps(res.data, ensure_ascii=False)
                            ),
                            ChatMessage(role="user", content=more),
                        ],
                        PLAN_SCHEMA,
                        model_key=req.model_key,
                        temperature=0.3,
                        retries=1,
                    )
                    plan2 = normalize(from_llm(res2.data), texts, title, names)
                    if sum(1 for x in plan2.shots if x.kind in MAIN_KINDS) > have:
                        plan = plan2
                except LLMError:
                    pass  # 直せなければ最初の案のまま
        else:
            plan = normalize(rule_plan(texts, title), texts, title, names)
        log.info(
            "direction_plan",
            script_id=req.script_id,
            shots=len(plan.shots),
            notes=len(plan.notes),
            model=model_key,
        )
        return DirectionPlanResult(
            plan=plan, model_key=model_key, lines=len(texts), skills=used_skills
        )

    # ---------- 適用 ----------
    @staticmethod
    def _fill_gaps(
        cmds: list[Any],
        kws: list[Shot],
        lines: list[_Line],
        prof: DesignProfile | None,
        fps: float,
        title_end: int,
        names: tuple[str, ...],
    ) -> list[Shot]:
        """画面が変わらない時間が用途の「変化の間隔の最大」（統合版 Q2）を超える所に、
        その発話の語のテロップを足す（実機: 縦型ショートで 8 秒間、画面が変わらなかった）。

        語はその発話から取る（数値と単位・かぎかっこの語、無ければ漢字・カタカナの最も長い語）。
        台本に無い語は作らない。"""
        if prof is None or prof.motion.change_interval_sec is None:
            return []
        if not prof.allows(Component.KEYWORD):
            return []
        limit = round(prof.motion.change_interval_sec * fps)
        times = {c.start_frame for c in cmds if isinstance(c, AddClip)} | {
            lines[k.from_line - 1].start for k in kws
        }
        times.add(title_end)
        out: list[Shot] = []
        last = title_end
        taken = {k.from_line for k in kws}
        for i, ln in enumerate(lines, 1):
            nearby = [t for t in times if last < t <= ln.start]
            if nearby:
                last = max(nearby)
            if ln.start - last < limit * 0.6 or i in taken:
                continue
            word = gap_keyword(ln.text, names)
            if word:
                out.append(Shot(kind=ShotKind.KEYWORD, from_line=i, text=word))
                last = ln.start
        return out

    async def _background(
        self, project_id: str, style: str, w: int, h: int, prof: DesignProfile | None = None
    ) -> AssetRead:
        """背景。単色・平らなグラデーションだけにはしない（advanced-motion-techniques）。

        動きが許される用途は Blender の 3D の動く背景（箱の床が波打つ／形が浮かぶ）。
        Blender が無い・失敗したとき、動きが許されない用途は、模様つきの静止画にする。"""
        c0, c1 = BACKGROUNDS.get(style, BACKGROUNDS["navy"])
        main, accent = "#3BB8DB", "#FFC752"
        if prof is not None:  # 用途の型の背景（暗い側→明るい側のグラデーション）
            c0 = "0x" + prof.palette.base.lstrip("#")
            c1 = "0x" + prof.palette.base2.lstrip("#")
            main, accent = prof.palette.main, prof.palette.accent
            style = f"{prof.purpose.value}"
        moving = prof is None or prof.allows(Component.BACKGROUND_MOTION)
        if moving and self.titles.blender.available:
            loud = prof is not None and prof.purpose.value in FLOATING_BG_PURPOSES
            try:
                return await self.titles.motion_background(
                    project_id,
                    style="floating" if loud else "iso_blocks",
                    base="#" + c0[2:],
                    base2="#" + c1[2:],
                    main=main,
                    accent=accent,
                    name=f"動く背景（{style}）",
                )
            except Exception as exc:  # noqa: BLE001 - 静止画の背景で続ける
                log.warning("motion_background_failed", error=str(exc)[:300])
        out_dir = self.settings.data_dir / "projects" / project_id / "assets"
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"bg_{style}_{w}x{h}_p.png"
        if not out.is_file():
            pattern_background(out, w, h, "#" + c0[2:], "#" + c1[2:], main)
        a = await self.timeline.register_generated(
            project_id, out, AssetKind.IMAGE, "pillow", f"背景（{style}）"
        )
        return AssetRead.model_validate(a)

    async def apply(
        self, req: DirectionApplyRequest, origin: CommandOrigin = CommandOrigin.USER
    ) -> DirectionApplyResult:
        p, _, lines = await self._script(req.script_id)
        if not lines or any(x.end <= x.start for x in lines):
            raise CommandError("generate voices and synchronize subtitles first")
        plan = normalize(req.plan, [x.text for x in lines], "", tuple({x.speaker for x in lines}))
        ts = req.text_scale
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
        side = max(SIDE, prof.safe_area.right) if prof else SIDE
        # 横長で立ち絵があるときは、図・カードを中央の列に収める（左右の端は立ち絵の列）
        tachie = w >= h and await self._has_tachie(req.script_id)
        main_w = TACHIE_MAIN_W if tachie else 0.92
        used: dict[Component, int] = {}
        boxes: list[Placement] = []
        texts: list[TextUse] = []

        def use(c: Component, n: int = 1) -> None:
            used[c] = used.get(c, 0) + n

        fps = p.fps_num / p.fps_den
        ffps = Fraction(p.fps_num, p.fps_den)
        # 用途ごとの動きの値（統合版 2.1）。型が無いときは解説の値
        mspec = prof.motion if prof else PROFILES[Purpose.EXPLAINER].motion

        def animate(clip_id: str, length: int, entrance: str | None = "fade", **kw: Any) -> None:
            """入り（フェード・pop・スライド）と出（不透明度 1→0）を付ける（統合版 A2・B1・B2）。"""
            anim = element_animation(length, mspec, ffps, entrance=entrance, **kw)
            if anim is not None:
                cmds.append(SetClipAnimation(clip_id=clip_id, animation=anim))

        kb_index = 0  # 連続する画像のケン・バーンズの向き（B3）

        def ken_burns(clip_id: str, a: AssetRead, length: int, box: float) -> None:
            """画像のケン・バーンズ（統合版 B3）。拡大量 = 秒数 × 1.75%（最小 8%・最大 20%）。
            元画像の幅を超えて拡大しない。向きは in → out → 左へパン → 右へパン の順に繰り返す。"""
            nonlocal kb_index
            amount = min(max(sec(length) * KB_PER_SEC, KB_MIN), KB_MAX)
            if a.width and a.height:
                disp_w = min(box * w, box * h * a.width / a.height)
                amount = min(amount, a.width / max(disp_w, 1.0) - 1.0)
            if amount <= 0.005:
                return
            name = KB_ORDER[kb_index % len(KB_ORDER)]
            kb_index += 1
            full = 0.2 if name.startswith("pan_") else 0.15  # プリセットの intensity 1 の拡大量
            cmds.append(
                SetClipAnimation(clip_id=clip_id, preset=name, intensity=round(amount / full, 4))
            )

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
        # 重なり順: 背景 → 図・カード → 立ち絵 → 文字（立ち絵を先に置いていても背景で隠さない）
        tachie = next(
            (t for t in tl.tracks if t.name == TACHIE_TRACK and t.kind == TrackKind.VIDEO), None
        )
        rec_t = next(
            (t for t in tl.tracks if t.name == RECORDING_TRACK and t.kind == TrackKind.VIDEO), None
        )
        # 画面録画が主役のときは、図・カードで録画を長く隠さない（章カードとして短く出す）
        recording = rec_t is not None and any(c.track_id == rec_t.id for c in tl.clips)
        if tachie is not None or rec_t is not None:
            # 背景 → 画面録画 → 図・カード → 立ち絵 → 文字（後から足した背景で隠さない）
            order = [rec_t.id if rec_t else None, fig_t, tachie.id if tachie else None, txt_t]
            for tid in order:
                if tid is not None:
                    cmds.append(MoveTrack(track_id=tid, index=1000))
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
        if bg.kind == AssetKind.VIDEO:
            # 動く背景は継ぎ目なく繰り返せる長さなので、並べて全体を覆う
            loop = max(1, media_frames(bg, total))
            pos = 0
            while pos < total:
                add(bg_t, bg.id, pos, min(loop, total - pos))
                pos += loop
            use(Component.BACKGROUND_MOTION)
        else:
            cid = add(bg_t, bg.id, 0, total)
            if prof is None or prof.allows(Component.BACKGROUND_MOTION):
                cmds.append(SetClipAnimation(clip_id=cid, preset="ken_burns_in", intensity=0.4))
                use(Component.BACKGROUND_MOTION)

        # タイトル（最初の発話の間。短すぎるときは 3 秒）
        title_end = 0
        if plan.title:
            use(Component.TITLE_CARD)
            title_end = min(total, max(lines[0].end, frames(3.0)))
            if prof is not None and prof.allows(Component.LYRIC_TEXT):
                # 歌詞が出る型（MV・リリック）は、最初の歌詞が出る前に題名を消す
                # （前奏が長い曲で題名と歌詞が重ならないように。短くても 2 秒は出す）
                title_end = min(total, max(frames(2.0), min(title_end, lines[0].start)))
            dur = min(sec(title_end), 30.0)
            # 冒頭の題名だけは 1 文字ずつ跳ねて出す（情報系では 1 本に 1 回。短い 1 行の題名のみ）
            kinetic = (
                prof is not None
                and prof.allows(Component.KINETIC_TEXT)
                and "\n" not in plan.title
                and len(plan.title) <= KINETIC_TITLE_MAX
            )
            if kinetic:
                use(Component.KINETIC_TEXT)
            t_asset = await self.titles.create(
                TitleCardRequest(
                    project_id=p.id,
                    text=plan.title[:100],
                    duration_sec=dur,
                    animation="bounce" if kinetic else "pop",
                    # 出は direction 側で付ける（不透明度 1→0 と 0.96 倍への縮小。統合版 A2）
                    fade_out_sec=0.0,
                    # 立ち絵があるときは、題名を中央の列（立ち絵の間）に収める。
                    # 題名の幅は画面幅の 80%×size（title_card.py）。題名は立ち絵の頭と同じ高さに
                    # 出るので、縁取りの分も含めて列の 85% に収める（実機: 18 文字の題名が
                    # ずんだもんの頭に重なった）
                    size=min(0.95, main_w * (0.85 if tachie else 0.95) / 0.8 * ts),
                    position="center",
                    text_color=ink,
                    outline_color=title_outline,
                    font=heading_font,
                )
            )
            t_len = min(title_end, media_frames(t_asset, title_end))
            t_cid = add(txt_t, t_asset.id, 0, t_len)
            animate(t_cid, t_len, None, title_exit=True)
            if plan.subtitle:
                sreq = CardRequest(
                    project_id=p.id,
                    heading=plan.subtitle,
                    style="keyword",
                    accent_color=emphasis,
                    panel_color=panel,
                    text_color=ink,
                    font=heading_font,
                    scale=round(0.62 * ts, 3),
                )
                sub = await self.titles.card(sreq)
                st = min(frames(0.6), max(0, title_end - 15))
                texts.append(
                    text_use("subtitle_card", sreq, sub, w, h, sec(st), sec(title_end), 1.0, main_w)
                )
                cid = add(
                    fig_t,
                    sub.id,
                    st,
                    max(1, title_end - st),
                    transform=native_transform(
                        sub.width or 1, sub.height or 1, w, h, cx=0.5, cy=0.6, max_w=main_w
                    ),
                )
                animate(cid, max(1, title_end - st), slide_preset("up", mspec))

        # 図・カード・画像（次が出るまで表示し続ける）
        # 置けない画像（素材が未選択）は、表示時間を決める前に外す。後で外すと、その画像の
        # 時間（最後の図なら動画の終わりまで）が背景だけの空白になる
        mains = []
        for s in plan.shots:
            if s.kind not in MAIN_KINDS:
                continue
            if recording and s.kind != ShotKind.CARD:
                skipped.append(f"{s.from_line}: 画面録画を隠さないよう {s.kind.value} を外しました")
                continue
            if s.kind == ShotKind.IMAGE:
                img = assets.get(s.asset_id or "")
                if img is None or img.kind != AssetKind.IMAGE:
                    q = f"（検索語: {s.query}）" if s.query else ""
                    skipped.append(f"{s.from_line}: 画像の素材が未選択のため外しました{q}")
                    continue
            mains.append(s)
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
            if recording:  # 章カード: 短く出して録画に戻す
                end = min(end, start + frames(CHAPTER_CARD_SEC))
            if end - start < frames(0.5):
                skipped.append(f"{s.from_line}: 表示時間が短すぎる {s.kind.value} を外しました")
                continue
            windows.append((s, start, end))
        shown: list[tuple[int, int]] = []  # 実際に図・カード・画像を出した区間
        for s, start, end in windows:
            length = end - start
            if s.kind == ShotKind.CARD:
                use(Component.CARD)
                steps = _reveal_steps(s, lines, start, end, frames(REVEAL_MIN_SEC))
                # 見出しの語に合う絵記号を添える（合うものが無ければ付けない）
                icon = pick_icon(s.heading, s.body)
                last = len(steps) - 1
                for k, (t0, t1, reveal) in enumerate(steps):
                    creq = CardRequest(
                        project_id=p.id,
                        heading=s.heading,
                        body=s.body,
                        style=s.style,
                        accent_color=accent,
                        panel_color=panel,
                        text_color=ink,
                        font=heading_font,
                        scale=round(min(2.0, CARD_SCALE * ts), 3),
                        reveal=reveal,
                        # 録画の上に出す章カードは、後ろの細かい画面が透けないよう濃く
                        panel_opacity=0.96 if recording else 0.88,
                        icon=icon,
                        # 縦長では横幅いっぱいに使う（実機: 既定の 62% では「パズルゲー／
                        # ムのコツ」「先に置／く」と語の途中で折り返した）
                        width_ratio=main_w if h > w else 0.62,
                    )
                    a = await self.titles.card(creq)
                    if k == last:  # 文字の大きさと表示の長さ（統合版 T39・T40・T52）
                        texts.append(
                            text_use(
                                "card", creq, a, w, h, sec(steps[0][0]), sec(t1), zone_h, main_w
                            )
                        )
                    # 順に出すときは同じ大きさの絵を切り替えるだけ。出入りの動きは最初と最後のみ
                    cid = add(
                        fig_t,
                        a.id,
                        t0,
                        t1 - t0,
                        transform=native_transform(
                            a.width or 1,
                            a.height or 1,
                            w,
                            h,
                            cx=0.5,
                            cy=zone_cy,
                            max_h=zone_h,
                            max_w=main_w,
                        ),
                    )
                    # 入りは用途の pop（B2）、出は最後の項目だけ
                    if k == 0 or k == last:
                        animate(cid, t1 - t0, mspec.pop if k == 0 else None, exit_=k == last)
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
                        # 表示区間が長さの上限を超えるときは、止まった後半を繰り返して埋める
                        # ので、素材自体は消えない（最後のクリップで消す）
                        fade_out_sec=0.0 if sec(length) > CHART_MAX_SEC else 0.3,
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
                tf = ClipTransform(fit=FitMode.FIT, scale=min(zone_h, main_w), y=zone_cy - 0.5)
                # 動き終わった後（止まっている部分）。区間が素材より長ければ、ここを繰り返す
                still_from = min(media - 1, frames(1.2) + 2)
                seg = media - still_from
                fill = length > shown_len and seg >= frames(1.0)
                cid = add(fig_t, a.id, start, shown_len, transform=tf)
                # 繰り返せないほど短いときだけ、急に消さずに消す
                animate(cid, shown_len, exit_=shown_len < length and not fill)
                shown_end = start + shown_len
                if fill:
                    pos, stop = start + shown_len, start + length
                    while pos < stop:
                        n = min(seg, stop - pos)
                        cid = add(fig_t, a.id, pos, n, source_in_frame=still_from, transform=tf)
                        if pos + n >= stop:
                            animate(cid, n, None)
                        pos += n
                    shown_end = stop
                shown.append((start, shown_end))
            else:  # IMAGE
                img = assets[s.asset_id or ""]  # 置けるものだけ mains に残している
                use(Component.IMAGE)
                box = min(zone_h, main_w)
                cid = add(
                    fig_t,
                    img.id,
                    start,
                    length,
                    transform=ClipTransform(fit=FitMode.FIT, scale=box, y=zone_cy - 0.5),
                )
                animate(cid, length)
                ken_burns(cid, img, length, box)
                shown.append((start, end))

        # テロップ（その発話の間。重なる分は前を短くする）
        kws = [s for s in plan.shots if s.kind == ShotKind.KEYWORD]
        kws += self._fill_gaps(cmds, kws, lines, prof, fps, title_end, names_of(lines))
        kws.sort(key=lambda x: x.from_line)
        emphasis_left = _emphasis_budget(prof)
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
            kreq = CardRequest(
                project_id=p.id,
                heading=s.text,
                style="keyword",
                accent_color=emphasis,
                panel_color=panel,
                text_color=ink,
                font=heading_font,
                scale=round(0.62 * ts, 3),
            )
            a = await self.titles.card(kreq)
            texts.append(text_use("keyword", kreq, a, w, h, sec(start), sec(end)))
            aw, ah = a.width or 1, a.height or 1
            # 右上に置く（左上は章の見出しの場所。layout-no-overlap の「補助」の枠）
            k = native_scale(aw, ah, w, h)
            kx, ky = 1 - side - aw * k / 2 / w, strip_top + ah * k / 2 / h
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
            )
            # 数値の要点は「ポンと飛び出す」動きで強調する（喋りテロップの動き。情報系は 1 本で
            # 1〜2 回まで）。それ以外は横からの登場（B1）
            if _has_number(s.text) and emphasis_left > 0:
                emphasis_left -= 1
                use(Component.EMPHASIS_TEXT)
                animate(cid, end - start, mspec.pop)
            else:
                animate(cid, end - start, slide_preset("right", mspec))

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
        if recording:  # 画面録画が出ている間は背景だけにならない
            empty = []
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
                manifest_record(used, colors, fonts, boxes, empty, outline=outline, texts=texts),
            )
        log.info("direction_applied", project_id=p.id, clips=clips, skipped=len(skipped))
        return DirectionApplyResult(
            command_id=res.id, status=res.status, clips=clips, skipped=skipped
        )
