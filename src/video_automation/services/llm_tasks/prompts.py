"""タスク別・モデル別プロンプト管理（仕様 §17.6 Phase 2 追加分）。

テンプレートはバージョン付きで管理し、生成記録（GenerationRun）に
prompt_version を残せるようにする。
モデルファミリー別の上書きを持てる。
"""

from dataclasses import dataclass, field
from string import Template
from typing import Any

from video_automation.schemas.provider import ChatMessage, TaskType


@dataclass(frozen=True)
class PromptTemplate:
    task: TaskType
    version: str
    system: str
    user: str
    json_schema: dict[str, Any] | None = None
    tags: tuple[str, ...] = field(default_factory=tuple)

    def render(self, **values: str) -> list[ChatMessage]:
        return [
            ChatMessage(role="system", content=Template(self.system).safe_substitute(values)),
            ChatMessage(role="user", content=Template(self.user).substitute(values)),
        ]


# 台本（仕様 §17.3）の構造化出力スキーマ。Phase 8 で Script/DialogueTurn に保存する。
SCRIPT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "scenes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "purpose": {"type": "string"},
                    "estimated_duration_sec": {"type": "integer", "minimum": 1, "maximum": 3600},
                    "turns": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "speaker": {"type": "string"},
                                "text": {"type": "string"},
                            },
                            "required": ["speaker", "text"],
                        },
                    },
                },
                "required": ["purpose", "turns"],
            },
        }
    },
    "required": ["scenes"],
}

_COMMON_JA = (
    "あなたは日本語の動画制作アシスタントです。"
    "事実が不明な点は推測で確定せず、仮定であると明示してください。"
)

DEFAULT_TEMPLATES: dict[TaskType, PromptTemplate] = {
    TaskType.SCRIPT_GENERATION: PromptTemplate(
        task=TaskType.SCRIPT_GENERATION,
        version="script.full.v1",
        system=_COMMON_JA
        + " 指定されたキャラクター設定・口調を守り、シーンと発話ターンに分けて台本を作ります。",
        user=(
            "テーマ: $topic\n目的・視聴者: $audience\n目標尺: 約$duration秒\n"
            "キャラクター:\n$characters\n禁止事項・必須キーワード: $constraints\n"
            "指定スキーマのJSONだけを出力してください。"
        ),
        json_schema=SCRIPT_SCHEMA,
    ),
    TaskType.PROOFREADING: PromptTemplate(
        task=TaskType.PROOFREADING,
        version="proofread.v1",
        system=_COMMON_JA + " 誤字脱字・不自然な表現のみを最小限修正します。",
        user="次の文章を校正し、修正後の本文だけを返してください。\n---\n$text",
    ),
    TaskType.SUBTITLE: PromptTemplate(
        task=TaskType.SUBTITLE,
        version="subtitle.split.v1",
        system=_COMMON_JA + " 字幕として読みやすい長さ（1行$max_chars文字以内）に区切ります。",
        user="次のセリフを字幕用に区切り、1行ずつ出力してください。\n---\n$text",
    ),
    TaskType.EDIT_PLAN: PromptTemplate(
        task=TaskType.EDIT_PLAN,
        version="edit.plan.v1",
        system=_COMMON_JA
        + " 動画編集の指示を、検証可能な手順に分解します。任意コード実行は提案しません。",
        user="プロジェクト状態:\n$state\n\n指示: $instruction",
    ),
    TaskType.CHAT: PromptTemplate(
        task=TaskType.CHAT, version="chat.v1", system=_COMMON_JA, user="$text"
    ),
}

# family（Ollama details.family 等）ごとの上書き。実機評価で必要に応じて追加する。
FAMILY_OVERRIDES: dict[tuple[TaskType, str], PromptTemplate] = {}


class PromptRegistry:
    def __init__(
        self,
        templates: dict[TaskType, PromptTemplate] | None = None,
        overrides: dict[tuple[TaskType, str], PromptTemplate] | None = None,
    ) -> None:
        self.templates = dict(templates or DEFAULT_TEMPLATES)
        self.overrides = dict(overrides or FAMILY_OVERRIDES)

    def get(self, task: TaskType, family: str | None = None) -> PromptTemplate:
        if family and (task, family.lower()) in self.overrides:
            return self.overrides[(task, family.lower())]
        if task in self.templates:
            return self.templates[task]
        return self.templates[TaskType.CHAT]
