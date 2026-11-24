"""声（話者）ごとの口調。声を選ぶとキャラクターの口調で台本を書けるようにする。

組み込みは公式・出典のある設定だけにする（推測で口調を作らない）。
それ以外の声（VOICEVOX Nemo の「女声1」など、または組み込みに無いキャラクター）は
ユーザーが登録する。登録は組み込みより優先する。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from video_automation.database.database import Database
from video_automation.database.models import SystemSetting

if TYPE_CHECKING:
    from video_automation.services.voice.voice_service import VoiceService

SETTING_KEY = "voice_character_styles"


class VoiceCharacterStyle(BaseModel):
    """声（話者名）ごとの口調。台本の生成でキャラクターの「口調」に使う。"""

    speaker: str = Field(min_length=1, max_length=100)  # エンジンの話者名（例: ずんだもん）
    first_person: str = Field(default="", max_length=20)  # 一人称
    endings: list[str] = Field(default_factory=list, max_length=8)  # 語尾
    tone: str = Field(default="", max_length=300)  # 話し方の特徴
    source: str = Field(default="", max_length=300)  # 出典（組み込みのとき）
    builtin: bool = False

    def text(self) -> str:
        """台本生成に渡す「口調」の文。"""
        parts = []
        if self.first_person:
            parts.append(f"一人称は「{self.first_person}」")
        if self.endings:
            parts.append("語尾は" + "・".join(f"「{e}」" for e in self.endings))
        if self.tone:
            parts.append(self.tone)
        return "。".join(parts)


BUILTIN: dict[str, VoiceCharacterStyle] = {
    s.speaker: s
    for s in [
        VoiceCharacterStyle(
            speaker="ずんだもん",
            first_person="ボク",
            endings=["〜のだ", "〜なのだ"],
            tone="ずんだ餅の精。やや不幸属性で、ないがしろにされることもしばしば",
            source="語尾: Game Watch 2024-12-09 の紹介／一人称は一般の設定（要確認）",
            builtin=True,
        ),
        VoiceCharacterStyle(
            speaker="あんこもん",
            endings=["〜だもん"],
            tone="ずんだもんをライバル視していて、ツンツンしている",
            source="VOICEVOX 公式 X（2026-01-05 ボイボ寮キャラクター紹介）",
            builtin=True,
        ),
        VoiceCharacterStyle(
            speaker="四国めたん",
            tone="上品なお嬢様の話し方。中二病の妄想が趣味で「漆黒のめたん」を自称する",
            source="ピクシブ百科事典「VOICEVOX」のキャラクター紹介（要確認）",
            builtin=True,
        ),
    ]
}


class VoiceCharacterService:
    def __init__(self, db: Database, voice: VoiceService | None = None) -> None:
        self.db = db
        self.voice = voice

    async def for_style(self, style_id: int) -> tuple[str | None, VoiceCharacterStyle | None]:
        """声（style_id）の話者名と、その口調（登録が無ければ None）。"""
        if self.voice is None:
            return None, None
        sp = await self.voice.speaker_of(style_id)
        if sp is None:
            return None, None
        return sp.name, await self.get(sp.name)

    async def _user(self) -> dict[str, Any]:
        async with self.db.session_factory() as s:
            row = await s.get(SystemSetting, SETTING_KEY)
            return dict(row.value or {}) if row else {}

    async def list(self) -> list[VoiceCharacterStyle]:
        out = dict(BUILTIN)
        for name, v in (await self._user()).items():
            out[name] = VoiceCharacterStyle.model_validate({**v, "speaker": name})
        return sorted(out.values(), key=lambda x: (not x.builtin, x.speaker))

    async def get(self, speaker: str) -> VoiceCharacterStyle | None:
        user = await self._user()
        if speaker in user:
            return VoiceCharacterStyle.model_validate({**user[speaker], "speaker": speaker})
        return BUILTIN.get(speaker)

    async def put(self, style: VoiceCharacterStyle) -> VoiceCharacterStyle:
        """ユーザーの登録（組み込みと同じ名前なら上書き）。"""
        style = style.model_copy(update={"builtin": False, "source": style.source})
        async with self.db.session_factory() as s:
            row = await s.get(SystemSetting, SETTING_KEY)
            data = dict(row.value or {}) if row else {}
            data[style.speaker] = style.model_dump(exclude={"speaker", "builtin"})
            if row is None:
                s.add(SystemSetting(key=SETTING_KEY, value=data))
            else:
                row.value = data
            await s.commit()
        return style

    async def delete(self, speaker: str) -> bool:
        async with self.db.session_factory() as s:
            row = await s.get(SystemSetting, SETTING_KEY)
            data = dict(row.value or {}) if row else {}
            if row is None or speaker not in data:
                return False
            data.pop(speaker)
            row.value = data
            await s.commit()
        return True
