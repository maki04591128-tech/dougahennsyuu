"""プロジェクトの用途（デザインの型）の設定・演出の記録・書き出し前の検査。"""

from fractions import Fraction
from typing import Any

from video_automation.database.database import Database
from video_automation.database.models import AudioFile, Clip, Project, ScriptSegment, Subtitle
from video_automation.schemas.design import (
    ClassifyRequest,
    ClassifyResult,
    Component,
    DesignCheck,
    DesignProfile,
    ProjectDesign,
    Purpose,
    Violation,
)
from video_automation.schemas.subtitle import SubtitleStyle
from video_automation.services.design.classify import classify
from video_automation.services.design.guard import (
    DesignManifest,
    Placement,
    SubtitleUse,
    check,
)
from video_automation.services.design.profiles import PROFILES


class DesignError(Exception):
    code = "design_error"


class DesignNotFoundError(DesignError):
    code = "not_found"


class DesignViolationError(DesignError):
    """書き出す前の検査で、用途の型に反する部品などが見つかった。"""

    code = "design_violation"

    def __init__(self, result: DesignCheck) -> None:
        self.result = result
        lines = [v.message for v in result.violations if v.severity == "error"]
        super().__init__("デザインの検査で止めました: " + " / ".join(lines))


def manifest_record(
    used: dict[Component, int],
    colors: list[str],
    fonts: list[str],
    placements: list[Placement] | None = None,
    empty_spans: list[tuple[float, float]] | None = None,
    outline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """演出の処理が保存する記録（JSON）。outline は題名・章の区切り（検査には使わない）。"""
    rec: dict[str, Any] = {
        "used": {c.value: n for c, n in used.items() if n},
        "colors": sorted(set(c.upper() for c in colors)),
        "fonts": sorted(set(fonts)),
        "placements": [
            [p.component.value, round(p.x0, 4), round(p.y0, 4), round(p.x1, 4), round(p.y1, 4)]
            for p in placements or []
        ],
        "empty_spans": [[round(a, 2), round(b, 2)] for a, b in empty_spans or []],
    }
    if outline is not None:
        rec["outline"] = outline
    return rec


class DesignService:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ---------- 用途 ----------
    @staticmethod
    def classify(req: ClassifyRequest) -> ClassifyResult:
        return classify(req)

    async def _project(self, s: Any, project_id: str) -> Project:
        p = await s.get(Project, project_id)
        if p is None:
            raise DesignNotFoundError(f"project {project_id}")
        return p

    async def get(self, project_id: str) -> ProjectDesign:
        async with self.db.session_factory() as s:
            p = await self._project(s, project_id)
            return ProjectDesign(
                purpose=Purpose(p.purpose) if p.purpose else None,
                extras=[Component(x) for x in p.design_extras or []],
            )

    async def set(self, project_id: str, design: ProjectDesign) -> ProjectDesign:
        async with self.db.session_factory() as s:
            p = await self._project(s, project_id)
            p.purpose = design.purpose.value if design.purpose else None
            p.design_extras = [c.value for c in dict.fromkeys(design.extras)]
            await s.commit()
        return design

    async def outline(self, project_id: str) -> dict[str, Any]:
        """演出プランを適用したときに記録した題名・章の区切り（無ければ空）。"""
        async with self.db.session_factory() as s:
            p = await self._project(s, project_id)
            rec = (p.design_manifest or {}).get("direction") or {}
        return dict(rec.get("outline") or {})

    async def profile_for(self, project_id: str) -> DesignProfile | None:
        d = await self.get(project_id)
        return PROFILES[d.purpose] if d.purpose else None

    # ---------- 記録 ----------
    async def record(self, project_id: str, source: str, data: dict[str, Any] | None) -> None:
        """演出の処理（source）ごとに、作った要素の記録を置き換える。None は消す。"""
        async with self.db.session_factory() as s:
            p = await self._project(s, project_id)
            cur = dict(p.design_manifest or {})
            if data is None:
                cur.pop(source, None)
            else:
                cur[source] = data
            p.design_manifest = cur
            await s.commit()

    # ---------- 検査 ----------
    async def manifest(
        self,
        project_id: str,
        script_id: str | None = None,
        subtitle_style: SubtitleStyle | None = None,
        speaker_colors: list[str] | None = None,
        burn_subtitles: bool = True,
    ) -> DesignManifest:
        from sqlalchemy import select

        async with self.db.session_factory() as s:
            p = await self._project(s, project_id)
            records = dict(p.design_manifest or {})
            w, h = p.width, p.height
            fps = Fraction(p.fps_num, p.fps_den)
            rows = []
            if script_id and burn_subtitles:
                rows = list(
                    (
                        await s.execute(
                            select(Subtitle)
                            .where(Subtitle.script_id == script_id)
                            .order_by(Subtitle.index)
                        )
                    ).scalars()
                )
            # 音声の長さ（字幕の区切りの元になった台本の行ごと）
            seg_ids = {r.script_segment_id for r in rows}
            audio_sec: dict[str, float] = {}
            if seg_ids:
                q = (
                    select(ScriptSegment.id, AudioFile.duration_sec)
                    .join(AudioFile, AudioFile.id == ScriptSegment.audio_file_id)
                    .where(ScriptSegment.id.in_(seg_ids))
                )
                audio_sec = {sid: float(d) for sid, d in (await s.execute(q)).all() if d}
            # 切り替え（入りのトランジション）の時刻と種類、動画の長さ（統合版 E1）
            clip_rows = (
                await s.execute(
                    select(Clip.start_frame, Clip.end_frame, Clip.animation).where(
                        Clip.project_id == project_id
                    )
                )
            ).all()
        m = DesignManifest(vertical=h > w)
        for st, en, anim in clip_rows:
            m.duration_sec = max(m.duration_sec, float(Fraction(en) / fps))
            tran = (anim or {}).get("transition_in")
            if tran:
                m.transitions.append(
                    (float(Fraction(st) / fps), str(tran.get("type") or "dissolve"))
                )
        m.transitions.sort()
        for rec in records.values():
            for c, n in (rec.get("used") or {}).items():
                m.add(Component(c), int(n))
            m.colors.extend(rec.get("colors") or [])
            m.fonts.update(rec.get("fonts") or [])
            for comp, x0, y0, x1, y1 in rec.get("placements") or []:
                m.placements.append(Placement(Component(comp), x0, y0, x1, y1))
            m.empty_spans.extend((a, b) for a, b in rec.get("empty_spans") or [])
        if rows and subtitle_style is not None and subtitle_style.mode == "lyric":
            # 歌詞として出す（字幕の大きさ・行の長さ・速さの決まりは当てない）
            m.add(Component.LYRIC_TEXT, len(rows))
            m.fonts.add(subtitle_style.font_name)
            m.colors.append(subtitle_style.glow_color)
        elif rows and subtitle_style is not None:
            st = subtitle_style
            unit = min(w, h)
            ratio = (st.font_size / unit) if st.font_size else st.font_size_ratio
            lines = [
                (
                    r.text,
                    float(Fraction((r.end_frame or 0) - (r.start_frame or 0)) / fps),
                )
                for r in rows
            ]
            # 音声の速さ = その行の字幕の字数 ÷ 音声の長さ（字幕が音声に合わせてある前提）
            chars: dict[str, int] = {}
            for r in rows:
                chars[r.script_segment_id] = chars.get(r.script_segment_id, 0) + len(
                    r.text.replace("\n", "")
                )
            rates: list[float | None] = [
                chars[r.script_segment_id] / audio_sec[r.script_segment_id]
                if r.script_segment_id in audio_sec and r.start_frame is not None
                else None
                for r in rows
            ]
            band = st.band_mode if st.band else "none"
            m.subtitle = SubtitleUse(
                size_ratio=ratio, band=band, font=st.font_name, lines=lines, voice_rates=rates
            )
            m.add(Component.SUBTITLE, len(rows))
            speakers = {r.speaker for r in rows if r.speaker}
            if st.speaker_label and len(speakers) >= 2:
                m.add(Component.SPEAKER_LABEL, len(rows))
            m.fonts.add(st.font_name)
            m.colors.extend(c for c in speaker_colors or [])
        return m

    async def check(
        self,
        project_id: str,
        script_id: str | None = None,
        subtitle_style: SubtitleStyle | None = None,
        speaker_colors: list[str] | None = None,
        burn_subtitles: bool = True,
    ) -> DesignCheck:
        d = await self.get(project_id)
        m = await self.manifest(
            project_id, script_id, subtitle_style, speaker_colors, burn_subtitles
        )
        if d.purpose is None:
            return DesignCheck(
                purpose=None,
                ok=True,
                violations=[
                    Violation(
                        code="purpose_unset",
                        severity="warning",
                        message="用途が未設定のため、デザインの型による検査をしていません",
                    )
                ],
                used={c: n for c, n in m.used.items() if n > 0},
            )
        return check(PROFILES[d.purpose], m, d.extras)
