"""声（話者）ごとの口調。声を選ぶとキャラクターの口調で台本を書けるようにする。

組み込みは公式・出典のある設定だけにする（推測で口調を作らない）。
一人称・二人称・紹介は VOICEVOX 公式サイト「ボイボ寮」の各キャラクターの紹介と「呼び方」
（https://voicevox.hiroshiba.jp/dormitory/<名前>/ と .../calls/、2026-11-24 確認）から。
それ以外の声（VOICEVOX Nemo の「女声1」など、または組み込みに無いキャラクター）は
ユーザーが登録する。登録は組み込みより優先する。

公式データ（VOICEVOX/voicevox_blog の callNameInfo.ts など）から作った全キャラクター分の
口調・呼び名は「VOICEVOX の開発のための利用のみ許可」のため、このリポジトリには含めない。
手元の data/voicevox_characters.json に置くと読み込み、組み込みより優先する
（ユーザーの登録はさらに優先）。形式は VoiceCharacterStyle の JSON の配列。

感情 → スタイル名の対応（EMOTION_STYLES）は、エンジンのスタイル名の意味から決めた初期案。
話者ごとに人が変えられる（emotion_styles）。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field, ValidationError

from video_automation.database.database import Database
from video_automation.database.models import SystemSetting

if TYPE_CHECKING:
    from video_automation.services.voice.voice_service import VoiceService

logger = logging.getLogger(__name__)

SETTING_KEY = "voice_character_styles"
DATA_FILE = "voicevox_characters.json"

# 台本の発話ごとに選ぶ感情（LLM が出す）。neutral は既定のスタイルのまま
EMOTIONS = (
    "neutral", "happy", "sad", "angry", "surprise", "fear", "tired",
    "excited", "calm", "sweet", "tsun", "whisper", "serious", "annoyed",
)  # fmt: skip
EMOTION_LABELS = {
    "neutral": "ふつう", "happy": "うれしい", "sad": "悲しい", "angry": "怒り",
    "surprise": "驚き", "fear": "こわい", "tired": "疲れ", "excited": "熱くなる",
    "calm": "落ち着き", "sweet": "甘える", "tsun": "ツンとする", "whisper": "ささやく",
    "serious": "真剣", "annoyed": "呆れ",
}  # fmt: skip

# 感情 → スタイル名（話者ごと）。姿や別人格が変わるスタイル（鬼ver.・あん子・ツクモちゃん等）や
# セクシー系は自動では選ばない（ここに入れない）。ハミング・歌も台詞には使わない
EMOTION_STYLES: dict[str, dict[str, str]] = {
    "四国めたん": {"sweet": "あまあま", "tsun": "ツンツン", "whisper": "ささやき"},
    "ずんだもん": {
        "sweet": "あまあま", "tsun": "ツンツン", "whisper": "ささやき",
        "tired": "ヘロヘロ", "sad": "なみだめ",
    },
    "玄野武宏": {"happy": "喜び", "angry": "ツンギレ", "sad": "悲しみ"},
    "白上虎太郎": {"happy": "わーい", "angry": "おこ", "fear": "びくびく", "sad": "びえーん"},
    "青山龍星": {
        "excited": "熱血", "angry": "不機嫌", "happy": "喜び", "calm": "しっとり",
        "sad": "かなしみ", "whisper": "囁き",
    },
    "九州そら": {"sweet": "あまあま", "tsun": "ツンツン", "whisper": "ささやき"},
    "もち子さん": {"sad": "泣き", "angry": "怒り", "happy": "喜び", "calm": "のんびり"},
    "WhiteCUL": {"happy": "たのしい", "sad": "かなしい"},
    "後鬼": {"angry": "人間（怒り）ver."},
    "No.7": {"calm": "読み聞かせ"},
    "ナースロボ＿タイプＴ": {"calm": "楽々", "fear": "恐怖", "whisper": "内緒話"},
    "猫使アル": {
        "calm": "おちつき", "happy": "うきうき", "excited": "つよつよ", "tired": "へろへろ",
    },
    "猫使ビィ": {"calm": "おちつき", "fear": "人見知り", "excited": "つよつよ"},
    "中国うさぎ": {"surprise": "おどろき", "fear": "こわがり", "tired": "へろへろ"},
    "満別花丸": {"happy": "元気", "whisper": "ささやき", "sweet": "ぶりっ子"},
    "ぞん子": {"tired": "低血圧"},
    "中部つるぎ": {
        "angry": "怒り", "whisper": "ヒソヒソ", "fear": "おどおど", "sad": "絶望と敗北",
    },
    "離途": {"serious": "シリアス"},
    "ユーレイちゃん": {"sweet": "甘々", "sad": "哀しみ", "whisper": "ささやき"},
    "あんこもん": {"excited": "つよつよ", "tired": "よわよわ", "whisper": "ささやき"},
    "夜語トバリ": {"happy": "明るい", "sad": "哀しみ", "annoyed": "呆れ"},
    "暁記ミタマ": {"angry": "怒り", "sad": "哀しみ", "whisper": "ささやき"},
}  # fmt: skip
# 既定のスタイルが「ノーマル」でない話者
DEFAULT_STYLES = {"白上虎太郎": "ふつう", "後鬼": "人間ver.", "里石ユカ": "つぼみ"}


class VoiceCharacterStyle(BaseModel):
    """声（話者名）ごとの口調。台本の生成でキャラクターの「口調」に使う。"""

    speaker: str = Field(min_length=1, max_length=100)  # エンジンの話者名（例: ずんだもん）
    first_person: str = Field(default="", max_length=20)  # 一人称
    second_person: str = Field(default="", max_length=40)  # 相手の呼び方（二人称）
    endings: list[str] = Field(default_factory=list, max_length=8)  # 語尾
    tone: str = Field(default="", max_length=300)  # 話し方の特徴
    # 語尾の手がかり（守らせる規則ではなく、話し方の目安としてだけ渡す）
    ending_hints: list[str] = Field(default_factory=list, max_length=8)
    avoid: list[str] = Field(default_factory=list, max_length=8)  # 使わない言い方
    # ほかのキャラクター（話者名）→ 公式の呼び名
    call_names: dict[str, str] = Field(default_factory=dict)
    # 感情 → スタイル名（空なら EMOTION_STYLES）
    emotion_styles: dict[str, str] = Field(default_factory=dict)
    source: str = Field(default="", max_length=300)  # 出典（組み込みのとき）
    builtin: bool = False

    def text(self) -> str:
        """台本生成に渡す「口調」の文。"""
        parts = []
        if self.first_person:
            parts.append(f"一人称は「{self.first_person}」")
        if self.second_person:
            parts.append(f"相手は「{self.second_person}」と呼ぶ")
        if self.endings:
            parts.append("語尾は" + "・".join(f"「{e}」" for e in self.endings))
        if self.ending_hints:
            parts.append("話し方の目安は" + "・".join(f"『{e}』" for e in self.ending_hints))
        if self.avoid:
            parts.append("『" + "』『".join(self.avoid) + "』のような言い方はしない")
        if self.tone:
            parts.append(self.tone)
        return "。".join(parts)

    def style_for(self, emotion: str) -> str | None:
        """感情に合うスタイル名（無ければ None = 既定のまま）。"""
        table = self.emotion_styles or EMOTION_STYLES.get(self.speaker, {})
        return table.get(emotion)


BUILTIN: dict[str, VoiceCharacterStyle] = {
    s.speaker: s
    for s in [
        VoiceCharacterStyle(
            speaker="ずんだもん",
            first_person="僕",
            second_person="オマエ・みんな",
            endings=["〜のだ", "〜なのだ"],
            tone="ずんだ餅の精。やや不幸属性が備わっており、ないがしろにされることもしばしば。一人称は自分の名前「ずんだもん」も使う",
            source="ボイボ寮 zundamon（紹介・呼び方）／語尾: Game Watch 2024-12-09",
            builtin=True,
        ),
        VoiceCharacterStyle(
            speaker="四国めたん",
            first_person="わたくし",
            second_person="アンタ・貴女",
            ending_hints=["〜よ", "〜わ", "〜かしら"],
            avoid=["〜だぜ", "〜しろよ", "〜みないか"],
            tone="お嬢様風のタメ口。誰にでも遠慮しない。若干ツンデレ気味。趣味は中二病妄想、常に金欠",
            source="ボイボ寮 shikoku_metan（紹介・呼び方）",
            builtin=True,
        ),
        VoiceCharacterStyle(
            speaker="春日部つむぎ",
            first_person="あーし",
            second_person="きみ・きみたち",
            tone="埼玉県内の高校に通うギャル。やんちゃに見えて実は真面目な一面もある",
            source="ボイボ寮 kasukabe_tsumugi（紹介・呼び方）",
            builtin=True,
        ),
        VoiceCharacterStyle(
            speaker="雨晴はう",
            first_person="僕",
            second_person="あなた・あなた達",
            tone="現役看護師。看護師のあれこれを伝える",
            source="ボイボ寮 amehare_hau（紹介・呼び方）",
            builtin=True,
        ),
        VoiceCharacterStyle(
            speaker="波音リツ",
            first_person="あたし",
            second_person="アンタ・アンタら",
            tone="2ch・VIP 発のキャラクター",
            source="ボイボ寮 namine_ritsu（紹介・呼び方）",
            builtin=True,
        ),
        VoiceCharacterStyle(
            speaker="玄野武宏",
            first_person="俺",
            second_person="お前・お前ら",
            tone="サッパリした青年。やや短気だが面倒見は良い",
            source="ボイボ寮 kurono_takehiro（紹介・呼び方）",
            builtin=True,
        ),
        VoiceCharacterStyle(
            speaker="白上虎太郎",
            first_person="おれ",
            second_person="きみ・きみ達",
            tone="まっすぐで人懐っこい青年。愛嬌はあるものの少しおばか",
            source="ボイボ寮 shirakami_kotarou（紹介・呼び方）",
            builtin=True,
        ),
        VoiceCharacterStyle(
            speaker="青山龍星",
            first_person="オレ",
            second_person="アンタ・アンタ達・お前達",
            tone="大柄で無骨な青年。寡黙で冷静なストッパー役",
            source="ボイボ寮 aoyama_ryusei（紹介・呼び方）",
            builtin=True,
        ),
        VoiceCharacterStyle(
            speaker="冥鳴ひまり",
            first_person="私",
            second_person="君たち",
            tone="冥界から来た死神。可愛いものに目がない。優しくて清楚（自称）",
            source="ボイボ寮 meimei_himari（紹介・呼び方）",
            builtin=True,
        ),
        VoiceCharacterStyle(
            speaker="九州そら",
            first_person="まーくつー",
            second_person="あなたさま・みなさま",
            tone="宇宙開拓用に開発されたアンドロイド（正式名称は九州そらmk=II）。相手には「さま」を付ける",
            source="ボイボ寮 kyushu_sora（紹介・呼び方）",
            builtin=True,
        ),
        VoiceCharacterStyle(
            speaker="もち子さん",
            first_person="私",
            second_person="あなた・あなた達",
            tone="福島県生まれのプラモ好きの犬系ヲタ娘。小さい背丈で頑張る人を応援する。一人称は「もち子」も使う",
            source="ボイボ寮 mochikosan（紹介・呼び方）",
            builtin=True,
        ),
        VoiceCharacterStyle(
            speaker="東北ずん子",
            first_person="私",
            second_person="あなた・みんな",
            tone="前向きで明るい、母性の塊のような性格。ずんだの話をし始めると饒舌になる",
            source="ボイボ寮 tohoku_zunko（紹介・呼び方）",
            builtin=True,
        ),
        VoiceCharacterStyle(
            speaker="東北きりたん",
            first_person="わたし",
            second_person="あなた・あなたがた",
            tone="ひきこもり気質で夜型。他人に対してはクールで毒舌",
            source="ボイボ寮 tohoku_kiritan（紹介・呼び方）",
            builtin=True,
        ),
        VoiceCharacterStyle(
            speaker="東北イタコ",
            first_person="あたし",
            second_person="あなた・みなさん",
            endings=["〜ですわ"],
            tone="ですわ口調。イタコ専門学校を首席で卒業したが、本来のイタコ能力はイマイチ。東北三姉妹の長女",
            source="ボイボ寮 tohoku_itako（紹介・呼び方）",
            builtin=True,
        ),
        VoiceCharacterStyle(
            speaker="あんこもん",
            first_person="あんこもん",
            second_person="おまえ",
            endings=["〜だもん"],
            tone="ずんだもんをライバル視していて、ツンツンしている",
            source="ボイボ寮 ankomon（紹介・呼び方）",
            builtin=True,
        ),
    ]
}


def load_data_file(path: Path | None) -> dict[str, VoiceCharacterStyle]:
    """手元の口調データ（VoiceCharacterStyle の JSON の配列）。無い・壊れているときは空。"""
    if path is None or not path.is_file():
        return {}
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("口調データを読めませんでした: %s (%s)", path.name, exc)
        return {}
    out: dict[str, VoiceCharacterStyle] = {}
    for row in rows if isinstance(rows, list) else []:
        try:
            st = VoiceCharacterStyle.model_validate({**row, "builtin": True})
        except ValidationError as exc:
            logger.warning("口調データの 1 件を飛ばしました: %s", exc.errors()[0].get("msg"))
            continue
        out[st.speaker] = st
    return out


class VoiceCharacterService:
    def __init__(
        self, db: Database, voice: VoiceService | None = None, data_file: Path | None = None
    ) -> None:
        self.db = db
        self.voice = voice
        self.data_file = data_file
        self._data: dict[str, VoiceCharacterStyle] | None = None

    def _builtin(self) -> dict[str, VoiceCharacterStyle]:
        """組み込み + 手元の口調データ（データが優先）。"""
        if self._data is None:
            self._data = load_data_file(self.data_file)
        return {**BUILTIN, **self._data}

    async def known_as(self) -> dict[str, list[str]]:
        """話者名 → ほかのキャラクターからの呼ばれ方（呼び名の取り違えを見つけるのに使う）。"""
        out: dict[str, list[str]] = {}
        for st in await self.list():
            for target, call in st.call_names.items():
                names = out.setdefault(target, [])
                if call not in names:
                    names.append(call)
        return out

    async def emotion_style_id(self, style_id: int, emotion: str) -> int | None:
        """声（style_id）の話者で、感情に合うスタイルの ID。

        既定のスタイル（ノーマル等）を選んでいる声だけ切り替える（人が「あまあま」などを
        選んでいたら、その選択を変えない）。合うスタイルが無ければ None。"""
        if self.voice is None or not emotion or emotion == "neutral":
            return None
        sp = await self.voice.speaker_of(style_id)
        if sp is None:
            return None
        current = next((x for x in sp.styles if x.id == style_id), None)
        default = DEFAULT_STYLES.get(sp.name, "ノーマル")
        if current is None or current.name != default:
            return None
        style = await self.get(sp.name)
        name = style.style_for(emotion) if style else EMOTION_STYLES.get(sp.name, {}).get(emotion)
        if not name:
            return None
        hit = next((x for x in sp.styles if x.name == name and x.is_talk), None)
        return hit.id if hit else None

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
        out = self._builtin()
        for name, v in (await self._user()).items():
            out[name] = VoiceCharacterStyle.model_validate({**v, "speaker": name})
        return sorted(out.values(), key=lambda x: (not x.builtin, x.speaker))

    async def get(self, speaker: str) -> VoiceCharacterStyle | None:
        user = await self._user()
        if speaker in user:
            return VoiceCharacterStyle.model_validate({**user[speaker], "speaker": speaker})
        return self._builtin().get(speaker)

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
