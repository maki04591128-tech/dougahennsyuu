"""読みのルールの管理と、AI による読み間違いの検出。

台本の各セリフを（正規化してから）エンジンに読ませ、得られたカナを LLM に見せて
読み間違いの語と正しい読みを挙げさせる。挙がった語は「表記 → カタカナ」のルールとして登録し、
次の音声生成から使う（字幕の文は変えない）。ルールは UI・API から確認・無効化・削除できる。
"""

import re
from typing import Any

from sqlalchemy import or_, select

from video_automation.app.logging import get_logger
from video_automation.database.database import Database
from video_automation.database.models import ReadingRule, Script, ScriptSegment
from video_automation.schemas.provider import ChatMessage, TaskType
from video_automation.schemas.voice import (
    AccentPhrasesRequest,
    ReadingCheckRequest,
    ReadingCheckResult,
    ReadingIssue,
    ReadingRuleIn,
    ReadingRuleRead,
)
from video_automation.services.llm_tasks.gateway import LLMGateway
from video_automation.services.voice.reading import (
    ReadingRuleData,
    is_katakana,
    normalize_for_speech,
)
from video_automation.services.voice.voice_service import NotFoundError, VoiceService

log = get_logger("reading")
CHECK_VERSION = "reading.check.v1"
CHECK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "fixes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "line": {"type": "integer"},
                    "surface": {"type": "string"},
                    "reading": {"type": "string"},
                },
                "required": ["line", "surface", "reading"],
            },
            "maxItems": 30,
        }
    },
    "required": ["fixes"],
}
SYSTEM = (
    "あなたは日本語の読み上げ音声の校正者です。文と、音声エンジンが付けた読み（カタカナ）を比べ、"
    "読み間違いの語だけを挙げます。エンジンの読みでは長音を「オ」「ウ」「エ」などで書き"
    "（例: ホントウ→ホントオ、ケイサン→ケエサン）、「/」はアクセントの区切り、「、」は間です。"
    "これらは誤りではありません。数字の読みが正しい場合も挙げません。"
    "surface には文の中にそのまま現れる表記（2〜20文字程度の語）を、"
    "reading にはその語の正しい読みをカタカナで書きます。誤りが無ければ fixes は空配列にします。"
)
_HIRA = re.compile(r"[ぁ-ゖ]")


def _to_katakana(s: str) -> str:
    return _HIRA.sub(lambda m: chr(ord(m.group(0)) + 0x60), s.strip())


def _skeleton(kana: str) -> str:
    """長音（ー・伸ばす母音）と促音を除いた読みの骨組み（音を伸ばしただけの違いを見分ける）。"""
    kana = kana.translate(str.maketrans("ヲヅヂ", "オズジ"))  # 同じ音の書き分け
    return kana[:1] + re.sub(r"[ーッアイウエオ]", "", kana[1:])


class ReadingService:
    def __init__(self, db: Database, voice: VoiceService, llm: LLMGateway) -> None:
        self.db = db
        self.voice = voice
        self.llm = llm

    # ---------- ルール ----------
    async def list_rules(self, project_id: str | None = None) -> list[ReadingRuleRead]:
        async with self.db.session_factory() as s:
            q = select(ReadingRule)
            if project_id is not None:
                q = q.where(
                    or_(ReadingRule.project_id == project_id, ReadingRule.project_id.is_(None))
                )
            rows = (await s.execute(q.order_by(ReadingRule.created_at))).scalars()
            return [ReadingRuleRead.model_validate(r) for r in rows]

    async def rules_for(self, project_id: str | None) -> list[ReadingRuleData]:
        """有効なルール（プロジェクト固有のものが共通より優先）。"""
        rules = [r for r in await self.list_rules(project_id) if r.enabled]
        rules.sort(key=lambda r: r.project_id is not None)  # 共通 → 固有の順（後勝ち）
        return [ReadingRuleData(r.surface, r.reading) for r in rules]

    async def add(self, req: ReadingRuleIn, source: str = "user") -> ReadingRuleRead:
        async with self.db.session_factory() as s:
            q = select(ReadingRule).where(
                ReadingRule.surface == req.surface,
                ReadingRule.project_id.is_(None)
                if req.project_id is None
                else ReadingRule.project_id == req.project_id,
            )
            row = (await s.execute(q)).scalars().first()
            if row is None:
                row = ReadingRule(**req.model_dump(), source=source)
                s.add(row)
            else:  # 同じ表記は上書き
                row.reading, row.enabled, row.note = req.reading, req.enabled, req.note
                row.source = source
            await s.commit()
            return ReadingRuleRead.model_validate(row)

    async def update(self, rule_id: str, req: ReadingRuleIn) -> ReadingRuleRead:
        async with self.db.session_factory() as s:
            row = await s.get(ReadingRule, rule_id)
            if row is None:
                raise NotFoundError(f"reading rule {rule_id}")
            for k, v in req.model_dump().items():
                setattr(row, k, v)
            await s.commit()
            return ReadingRuleRead.model_validate(row)

    async def delete(self, rule_id: str) -> None:
        async with self.db.session_factory() as s:
            row = await s.get(ReadingRule, rule_id)
            if row is None:
                raise NotFoundError(f"reading rule {rule_id}")
            await s.delete(row)
            await s.commit()

    async def speech_text(self, project_id: str | None, text: str) -> str:
        return normalize_for_speech(text, await self.rules_for(project_id))

    # ---------- AI による読みの確認 ----------
    async def _kana(self, text: str, style_id: int) -> str:
        aps = await self.voice.accent_phrases(AccentPhrasesRequest(text=text, style_id=style_id))
        return "".join(
            "".join(m.text for m in ap.moras) + ("、" if ap.pause_mora else "/") for ap in aps
        )

    async def _already_read(self, reading: str, kana: str, style_id: int) -> bool:
        flat = kana.replace("/", "").replace("、", "")
        try:
            want = (await self._kana(reading, style_id)).replace("/", "").replace("、", "")
        except Exception:  # noqa: BLE001 - 判定できないときは候補として残す
            return False
        return bool(want) and want in flat

    async def _not_better(self, surface: str, reading: str, style_id: int) -> bool:
        """提案の読みが、エンジンがその語だけを読んだ読みと同じか、音を落としただけか。

        実機で「照明は」→「ショメエワ」（エンジンは「ショオメエワ」と正しく読んでいた）の
        ように、長音を落とした誤った提案が採用されていた。"""
        try:
            have = (await self._kana(surface, style_id)).replace("/", "").replace("、", "")
            want = (await self._kana(reading, style_id)).replace("/", "").replace("、", "")
        except Exception:  # noqa: BLE001 - 判定できないときは候補として残す
            return False
        if not have or not want:
            return False
        if want == have:
            return True
        # 長音・促音の有無だけが違う提案（実機:「食料」→「ショクリョオオ」）は直しにならない
        if _skeleton(want) == _skeleton(have):
            return True
        it = iter(have)
        return len(want) < len(have) and all(ch in it for ch in want)

    async def check_script(self, script_id: str, req: ReadingCheckRequest) -> ReadingCheckResult:
        async with self.db.session_factory() as s:
            sc = await s.get(Script, script_id)
            if sc is None:
                raise NotFoundError(f"script {script_id}")
            project_id = sc.project_id
            segs = list(
                (
                    await s.execute(
                        select(ScriptSegment)
                        .where(ScriptSegment.script_id == script_id)
                        .order_by(ScriptSegment.sequence)
                    )
                ).scalars()
            )
        rules = await self.rules_for(project_id)
        lines: list[tuple[ScriptSegment, str]] = []
        for seg in segs:
            speech = normalize_for_speech(seg.text, rules)
            lines.append((seg, await self._kana(speech, seg.voice_style_id or 0)))
        issues: list[ReadingIssue] = []
        model_key = None
        for start in range(0, len(lines), 8):  # 小さなモデルでも扱えるよう 8 発話ずつ
            chunk = lines[start : start + 8]
            body = "\n".join(
                f"[{i + 1}] 文: {seg.text}\n    読み: {kana}" for i, (seg, kana) in enumerate(chunk)
            )
            res = await self.llm.complete_json(
                TaskType.PROOFREADING,
                [
                    ChatMessage(role="system", content=SYSTEM),
                    ChatMessage(role="user", content=body),
                ],
                CHECK_SCHEMA,
                model_key=req.model_key,
                temperature=0.1,
            )
            model_key = res.model_key
            for fx in res.data.get("fixes", []):
                idx = int(fx.get("line", 0)) - 1
                if not 0 <= idx < len(chunk):
                    continue
                seg, kana = chunk[idx]
                surface = str(fx.get("surface", "")).strip()
                reading = _to_katakana(str(fx.get("reading", "")))
                # 文に無い表記・カタカナでない読み・すでに同じ読みのものは採用しない
                if not surface or surface not in seg.text or not is_katakana(reading):
                    continue
                # 語単位の指摘だけを採る（文や句をまるごと置き換える提案は不採用）
                if surface == reading or len(surface) > 12 or re.search(r"[、。，．\s]", surface):
                    continue
                if any(x.surface == surface for x in issues):
                    continue
                # すでにその読みで読めているもの（モデルの勘違い）は採用しない
                if await self._already_read(reading, kana, seg.voice_style_id or 0):
                    continue
                if await self._not_better(surface, reading, seg.voice_style_id or 0):
                    continue
                # 送り仮名の読みが合わない提案（「防げる」→「フセグ」）は採らない
                tail = re.search(r"[ぁ-ゖ]+$", surface)
                if tail and not reading.endswith(_to_katakana(tail.group(0))):
                    continue
                issue = ReadingIssue(
                    segment_id=seg.id, text=seg.text, surface=surface, wrong=kana, reading=reading
                )
                if req.apply:
                    rule = await self.add(
                        ReadingRuleIn(
                            surface=surface,
                            reading=reading,
                            project_id=project_id,
                            note=f"AI の読み確認（{res.model_key}）",
                        ),
                        source="llm",
                    )
                    issue.rule_id = rule.id
                issues.append(issue)
        log.info("reading_check", script_id=script_id, lines=len(lines), issues=len(issues))
        return ReadingCheckResult(checked=len(lines), issues=issues, model_key=model_key)
