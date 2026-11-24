"""文章の指示だけで動画を作る（つくる → 構成の確認 → 制作中 → できあがり）。

動画編集に関わったことがない人が、Blender のアドオンから文章を書くだけで動画を作れるようにする。
1. plan: 用途を判定し、プロジェクト・台本の設定・話し手（声と立ち絵）を決めて「構成」を返す。
   ここではタイムラインを変えず、LLM も使わない（すぐ返る）
2. run: 構成の確認で人が 1 回承認したら、台本 → 音声 → 立ち絵 → 字幕 → 演出 → 検査 → 書き出し
   を続けて行う。承認したのは人なので、各段は人の操作（origin=user）として行う
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from video_automation.app.logging import get_logger
from video_automation.database.models import Project
from video_automation.schemas.build import (
    BuildChange,
    BuildCharacter,
    BuildPlan,
    BuildRequest,
    BuildRunRequest,
    PackChoice,
    ReadinessItem,
)
from video_automation.schemas.design import ClassifyRequest, ProjectDesign, Purpose
from video_automation.schemas.direction import DirectionApplyRequest, DirectionPlanRequest
from video_automation.schemas.script_studio import (
    CharacterIn,
    ScriptGenerateRequest,
    ScriptMode,
    ScriptSettings,
    ScriptStudioCreate,
    ScriptUpdate,
    VoiceSettings,
)
from video_automation.schemas.subtitle import SubtitleGenerateRequest, SubtitleSyncRequest
from video_automation.schemas.timeline import RenderRequest, RenderStatus
from video_automation.services.design.profiles import PROFILES

if TYPE_CHECKING:
    from video_automation.app.bootstrap import AppContainer

log = get_logger("build")

# 用途ごとの話し手（VOICEVOX の話者名, 役割）。掛け合いが合う用途は 2 人
_DUO = [
    ("四国めたん", "解説役（要点を分かりやすく説明する）"),
    ("ずんだもん", "聞き手（素朴な疑問を言い、驚く）"),
]
CAST: dict[Purpose, list[tuple[str, str]]] = {
    Purpose.EXPLAINER: _DUO,
    Purpose.SHORT: _DUO,
    Purpose.RANKING: _DUO,
    Purpose.GAME: _DUO,
    Purpose.KIDS: [("ずんだもん", "案内役（やさしく話す）"), ("春日部つむぎ", "聞き手")],
    Purpose.PODCAST: [("春日部つむぎ", "進行役"), ("玄野武宏", "ゲスト")],
    Purpose.INTERVIEW: [("春日部つむぎ", "聞き手"), ("玄野武宏", "話し手")],
    Purpose.STORY: [("冥鳴ひまり", "語り手")],
    Purpose.DOCUMENTARY: [("青山龍星", "ナレーター")],
    Purpose.NEWS: [("青山龍星", "キャスター")],
}
_NARRATOR = [("四国めたん", "ナレーター（落ち着いて説明する）")]
# 曲（音源）が要る用途。文章だけでは作れない
NEEDS_MUSIC = {Purpose.MV, Purpose.LYRIC}
SHORT_PURPOSES = {Purpose.SHORT, Purpose.PROMO, Purpose.TRAILER, Purpose.PRODUCT}

STEPS = [
    ("script", "台本を作る"),
    ("voice", "音声を作る"),
    ("tachie", "立ち絵を置く"),
    ("subtitles", "字幕を作る"),
    ("direction", "画面の演出を付ける"),
    ("check", "デザインを検査する"),
    ("render", "書き出す"),
]


class BuildError(Exception):
    """利用者に見せる失敗（ジョブの error に本文を付ける）。"""

    public_message = True


def _title(instruction: str) -> str:
    t = instruction.strip().splitlines()[0]
    for suf in ("を作って", "を作ってください", "を作りたい", "を作る", "の動画", "動画"):
        if t.endswith(suf):
            t = t[: -len(suf)]
    return (t.strip(" 　。") or "新しい動画")[:40]


class BuildService:
    def __init__(self, c: AppContainer) -> None:
        self.c = c

    # ---------- 準備の確認 ----------
    async def readiness(self) -> list[ReadinessItem]:
        """作るのに要るもの（声・AI・FFmpeg・Blender）があるか。"""
        c = self.c
        out: list[ReadinessItem] = []
        n = 0
        for st in await c.voice.engines(refresh=True):
            if st.available:
                try:
                    n += len(await c.voice.speakers(st.engine_id))
                except Exception:  # noqa: BLE001 - 話者が取れないエンジンは数えない
                    continue
        out.append(
            ReadinessItem(
                key="voice",
                label="音声（VOICEVOX）",
                ok=n > 0,
                required=True,
                detail=f"話者 {n} 人" if n else "動いていません",
            )
        )
        models = [m for m in await c.providers.list_models()]
        out.append(
            ReadinessItem(
                key="llm",
                label="文章を作る AI（ローカル LLM）",
                ok=bool(models),
                required=True,
                detail=f"モデル {len(models)} 個" if models else "モデルが見つかりません",
            )
        )
        out.append(
            ReadinessItem(
                key="ffmpeg", label="FFmpeg（動画の変換）", ok=c.ffmpeg.available, required=True
            )
        )
        out.append(
            ReadinessItem(
                key="blender",
                label="Blender（画面演出）",
                ok=c.blender.available,
                required=False,
                detail=""
                if c.blender.available
                else "無くても作れます（凝った画面演出は付きません）",
            )
        )
        return out

    async def _style_ids(self) -> dict[str, int]:
        """話者名 → ノーマル（無ければ最初の話し声）のスタイル。"""
        sps = []
        for st in await self.c.voice.engines(refresh=False):
            if st.available:
                try:
                    sps += await self.c.voice.speakers(st.engine_id)
                except Exception:  # noqa: BLE001
                    continue
        out: dict[str, int] = {}
        for sp in sps:
            talk = [s for s in sp.styles if s.is_talk]
            if talk and sp.name not in out:
                normal = next((s for s in talk if s.name == "ノーマル"), talk[0])
                out[sp.name] = normal.id
        return out

    def _pack_for(self, name: str) -> str | None:
        for p in self.c.characters.packs():
            if name and (name in str(p.get("name", "")) or name in p["id"]):
                return str(p["id"])
        return None

    # ---------- 1. 構成 ----------
    async def plan(self, req: BuildRequest) -> BuildPlan:
        c = self.c
        vertical = {"auto": None, "landscape": False, "vertical": True}[req.orientation]
        cls = c.design.classify(
            ClassifyRequest(text=req.instruction, has_script=False, vertical=vertical)
        )
        purpose = cls.purpose
        prof = PROFILES[purpose]
        portrait = vertical if vertical is not None else prof.orientation == "vertical"
        w, h = (1080, 1920) if portrait else (1920, 1080)
        notes: list[str] = []
        if cls.note:
            notes.append(cls.note)
        dur = req.duration_sec or (45 if purpose in SHORT_PURPOSES else 120)
        styles = await self._style_ids()
        cast = [(n, "話し手") for n in req.characters] or CAST.get(purpose, _NARRATOR)
        chars: list[BuildCharacter] = []
        for name, role in cast:
            sid = styles.get(name)
            if styles and sid is None:
                notes.append(f"「{name}」の声が見つからないため、使える声で代わりに作ります")
                name, sid = next(iter(styles.items()))
            chars.append(
                BuildCharacter(
                    name=name, role=role, voice_style_id=sid, tachie_pack=self._pack_for(name)
                )
            )
        # 同じ話者が 2 回にならないように
        uniq: dict[str, BuildCharacter] = {}
        for x in chars:
            uniq.setdefault(x.name, x)
        chars = list(uniq.values())
        title = _title(req.instruction)
        async with c.db.session_factory() as s:
            p = Project(name=title, width=w, height=h, fps_num=30, fps_den=1)
            s.add(p)
            await s.flush()
            pid = p.id
            await s.commit()
        await c.design.set(pid, ProjectDesign(purpose=purpose))
        detail = await c.studio.create(
            ScriptStudioCreate(
                project_id=pid,
                title=title,
                mode=ScriptMode.FULL_AI,
                settings=ScriptSettings(
                    topic=req.instruction[:500],
                    purpose=prof.label,
                    target_duration_sec=dur,
                    reference=req.reference,
                ),
                characters=[
                    CharacterIn(
                        name=x.name,
                        role=x.role,
                        voice=VoiceSettings(style_id=x.voice_style_id),
                        tachie_pack=x.tachie_pack,
                    )
                    for x in chars
                ],
            )
        )
        return await self._view(pid, detail.id, notes=notes, uncertain=cls.needs_confirmation)

    async def _view(
        self, project_id: str, script_id: str, notes: list[str], uncertain: bool = False
    ) -> BuildPlan:
        """プロジェクトと台本の今の設定から「構成」を作る（構成の確認で見せる内容）。"""
        c = self.c
        async with c.db.session_factory() as s:
            p = await s.get(Project, project_id)
            if p is None:
                raise BuildError("プロジェクトが見つかりません")
            w, h, title = p.width, p.height, p.name
        design = await c.design.get(project_id)
        purpose = Purpose(design.purpose) if design.purpose else Purpose.EXPLAINER
        prof = PROFILES[purpose]
        detail = await c.studio.detail(script_id)
        if detail.project_id != project_id:
            raise BuildError("台本がこのプロジェクトのものではありません")
        chars = [
            BuildCharacter(
                name=ch.name,
                role=ch.role,
                voice_style_id=ch.voice.style_id,
                tachie_pack=ch.tachie_pack,
            )
            for ch in detail.characters
        ]
        notes = list(notes)
        ready = await self.readiness()
        can_run = all(r.ok for r in ready if r.required)
        if purpose in NEEDS_MUSIC:
            notes.append(
                f"「{prof.label}」は曲のファイルが要ります。文章だけでは作れないので、"
                "くわしい設定から曲を読み込んでください"
            )
            can_run = False
        steps = [
            label for key, label in STEPS if key != "tachie" or any(x.tachie_pack for x in chars)
        ]
        packs = [
            PackChoice(id=str(x["id"]), name=str(x.get("name") or x["id"]))
            for x in c.characters.packs()
        ]
        return BuildPlan(
            project_id=project_id,
            script_id=script_id,
            title=title,
            purpose=purpose.value,
            purpose_label=prof.label,
            purpose_uncertain=uncertain,
            width=w,
            height=h,
            duration_sec=detail.settings.target_duration_sec or 0,
            characters=chars,
            steps=steps,
            notes=notes,
            readiness=ready,
            can_run=can_run,
            packs=packs,
        )

    async def change(self, project_id: str, req: BuildChange) -> BuildPlan:
        """構成の確認で、用途・長さ・立ち絵を変える（まだ何も作らない）。"""
        c = self.c
        detail = await c.studio.detail(req.script_id)
        if detail.project_id != project_id:
            raise BuildError("台本がこのプロジェクトのものではありません")
        notes: list[str] = []
        settings = detail.settings.model_copy()
        if req.purpose is not None:
            try:
                purpose = Purpose(req.purpose)
            except ValueError as exc:
                raise BuildError(f"知らない用途です: {req.purpose}") from exc
            prof = PROFILES[purpose]
            await c.design.set(project_id, ProjectDesign(purpose=purpose))
            settings.purpose = prof.label
            # 縦長が決まりの用途に変えたら縦長に、縦長から横長の用途に変えたら横長にする
            async with c.db.session_factory() as s:
                p = await s.get(Project, project_id)
                assert p is not None
                portrait = p.height > p.width
                if prof.orientation == "vertical" and not portrait:
                    p.width, p.height = 1080, 1920
                elif prof.orientation == "landscape" and portrait:
                    p.width, p.height = 1920, 1080
                await s.commit()
            notes.append(f"用途を「{prof.label}」にしました")
        if req.duration_sec is not None:
            settings.target_duration_sec = req.duration_sec
        chars = None
        if req.tachie is not None:
            known = {str(x["id"]) for x in c.characters.packs()}
            chars = []
            for ch in detail.characters:
                ci = CharacterIn.model_validate(ch.model_dump())
                if ch.name in req.tachie:
                    pack = req.tachie[ch.name] or None
                    if pack is not None and pack not in known:
                        raise BuildError(f"立ち絵「{pack}」が見つかりません")
                    ci.tachie_pack = pack
                chars.append(ci)
        await c.studio.update(req.script_id, ScriptUpdate(settings=settings, characters=chars))
        return await self._view(project_id, req.script_id, notes=notes)

    async def _script_to_tachie(
        self, sid: str, has_tachie: bool, redo: str, at: Any, notes: list[str]
    ) -> None:
        """台本 → 音声 → 字幕 → 立ち絵（redo は直したいこと。台本の生成に渡す）。"""
        c = self.c
        await at("script")
        if redo:
            # 作り直すときは、前の台本を見せて直したいことに沿って書き直させる
            old = await c.studio.detail(sid)
            if old.segments:
                tail = f"\n\nこの台本の直したいこと: {redo[:600]}"
                prev = "\n".join(f"{x.speaker}: {x.text}" for x in old.segments)
                prev = prev[: max(0, 2000 - len(tail) - 12)]
                redo = f"前に作った台本:\n{prev}{tail}"
        try:
            d = await c.studio.generate(sid, ScriptGenerateRequest(instruction=redo[:2000]))
        except Exception as exc:  # noqa: BLE001
            raise BuildError(f"台本を作れませんでした: {type(exc).__name__}") from exc
        notes += list(getattr(d, "notes", []) or [])[:5]

        await at("voice")

        async def vp(v: float) -> None:
            await at("voice", v)

        try:
            await c.subtitles.generate_voices(sid, False, vp)
        except Exception as exc:  # noqa: BLE001
            raise BuildError(
                f"音声を作れませんでした（VOICEVOX を確かめてください）: {type(exc).__name__}"
            ) from exc

        await at("subtitles")
        await c.subtitles.generate(SubtitleGenerateRequest(script_id=sid))
        await c.subtitles.synchronize(SubtitleSyncRequest(script_id=sid))

        if has_tachie:
            await at("tachie")
            try:
                await c.characters.apply(sid)
            except Exception as exc:  # noqa: BLE001 - 立ち絵なしで続ける
                notes.append(
                    f"立ち絵を置けませんでした（{type(exc).__name__}）。立ち絵なしで続けます"
                )

    # ---------- 2. 続けて作る ----------
    async def run(self, project_id: str, req: BuildRunRequest, progress: Any) -> dict[str, Any]:
        c = self.c
        sid = req.script_id
        notes: list[str] = []
        detail = await c.studio.detail(sid)
        if detail.project_id != project_id:
            raise BuildError("台本がこのプロジェクトのものではありません")
        has_tachie = any(ch.tachie_pack for ch in detail.characters)
        keys = [k for k, _ in STEPS if k != "tachie" or has_tachie]
        redo = req.instruction.strip()
        if req.keep_script:
            if not detail.segments:
                raise BuildError("台本がまだありません。先に作ってください")
            # 見た目だけ作り直す: 台本・音声・字幕・立ち絵はそのまま、演出から
            keys = [k for k in keys if k in ("direction", "check", "render")]
        labels = dict(STEPS)

        async def at(key: str, frac: float = 0.0) -> None:
            i = keys.index(key)
            await progress((i + frac) / len(keys), f"{labels[key]}（{i + 1}/{len(keys)}）")

        if not req.keep_script:
            await self._script_to_tachie(sid, has_tachie, redo, at, notes)

        await at("direction")
        plan = (
            await c.direction.plan(
                DirectionPlanRequest(
                    script_id=sid,
                    use_llm=req.use_llm_direction,
                    instruction=redo if req.keep_script else "",
                )
            )
        ).plan
        res = await c.direction.apply(DirectionApplyRequest(script_id=sid, plan=plan))
        skipped = list(getattr(res, "skipped", []) or [])
        if skipped:
            notes.append(f"用途の型に合わない演出 {len(skipped)} 個を外しました")

        await at("check")
        rreq = RenderRequest(
            project_id=project_id, script_id=sid, rich_direction=req.rich_direction
        )
        chk = await c.render.design_check(rreq)
        if chk is not None:
            notes += [f"検査: {v.message}" for v in chk.violations if v.severity == "warning"][:5]
            if not chk.ok:
                errs = "、".join(v.message for v in chk.violations if v.severity == "error")[:300]
                raise BuildError(f"デザインの検査で止まりました: {errs}")

        await at("render")
        r = await c.render.request(rreq)
        while True:
            r = await c.render.get(r.id)
            if r.status in (RenderStatus.SUCCEEDED, RenderStatus.FAILED, RenderStatus.CANCELLED):
                break
            job = c.jobs.get(r.job_id) if r.job_id else None
            await at("render", job.progress if job else 0.0)
            await asyncio.sleep(1.0)
        if r.status != RenderStatus.SUCCEEDED or not r.output_path:
            raise BuildError(f"書き出しに失敗しました: {r.error or r.status}")
        video = str((c.settings.data_dir / r.output_path).resolve())
        await progress(1.0, "できあがりました")
        return {"render_id": r.id, "video": video, "notes": notes, "script_id": sid}
