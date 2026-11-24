"""台本生成のプロンプト（バージョン付き。GenerationRun に prompt_version を記録する）。"""

from typing import Any

COMMON = (
    "あなたは日本語の動画台本作家です。指定されたキャラクターの性格・口調を一貫して守ります。"
    "一人称・相手の呼び方・語尾はキャラクターごとに決まっており、台本の最初から最後まで変えません"
    "（他のキャラクターの一人称や語尾が混ざらないよう、発話ごとに話者の設定を確かめます）。"
    "性格は口調だけでなく、反応の仕方（驚く・ツッコむ・まとめる等の役割）にも出します。"
    "事実が不明な点は推測で確定せず、notes に仮定や確認したいことを書きます。"
    "セリフは自然な話し言葉で、1つの発話は120文字以内、1 文は 40 文字程度までにします"
    "（音声で読み上げるので、長い文は句読点で区切ります）。"
)

FULL_VERSION = "script.full.v3"
FULL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "scenes": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "purpose": {"type": "string"},
                    "estimated_duration_sec": {"type": "number", "minimum": 0},
                    "turns": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "properties": {
                                "speaker": {"type": "string"},
                                "text": {"type": "string", "minLength": 1},
                            },
                            "required": ["speaker", "text"],
                        },
                    },
                },
                "required": ["purpose", "turns"],
            },
        },
        "notes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["scenes"],
}

COUNTERPART_VERSION = "script.counterpart.v3"
COUNTERPART_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "replies": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "after": {"type": "string"},  # 応答する入力セリフの ID（L1…）。L0 は冒頭
                    "turns": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "properties": {"text": {"type": "string", "minLength": 1}},
                            "required": ["text"],
                        },
                    },
                },
                "required": ["after", "turns"],
            },
        },
        "notes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["replies"],
}

REGEN_VERSION = "script.regen.v2"


def regen_schema(n: int) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "candidates": {
                "type": "array",
                "minItems": n,
                "items": {"type": "string", "minLength": 1},
            }
        },
        "required": ["candidates"],
    }


CHECK_VERSION = "script.check.v1"
CHECK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "turn": {"type": "string"},
                    "level": {"type": "string", "enum": ["error", "warning", "info"]},
                    "message": {"type": "string"},
                },
                "required": ["level", "message"],
            },
        }
    },
    "required": ["issues"],
}


def characters_text(chars: list[dict[str, Any]]) -> str:
    lines = []
    for c in chars:
        parts = [c["name"]]
        for k, label in (("role", "役割"), ("personality", "性格"), ("speaking_style", "口調")):
            if c.get(k):
                parts.append(f"{label}: {c[k]}")
        lines.append("- " + " / ".join(parts))
    return "\n".join(lines)


PERSONA_FIX_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "fixes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "text": {"type": "string"}},
                "required": ["id", "text"],
            },
        }
    },
    "required": ["fixes"],
}


def persona_rules_text(personas: dict[str, Any]) -> str:
    """キャラクターごとの必ず守る口調の規則（一人称・呼び方・語尾）。"""
    rows = [p.rules_text() for p in personas.values() if not p.empty]
    rows = [r for r in rows if r]
    if not rows:
        return ""
    return "口調の規則（必ず守る）:\n" + "\n".join(f"- {r}" for r in rows)


def settings_text(st: dict[str, Any]) -> str:
    rows = [
        ("テーマ", st.get("topic")),
        ("目的", st.get("purpose")),
        ("想定視聴者", st.get("audience")),
        ("目標尺", f"約{st['target_duration_sec']}秒" if st.get("target_duration_sec") else ""),
        ("構成の希望", st.get("structure")),
        ("テンポ", st.get("tempo")),
        ("必ず入れる言葉", "、".join(st.get("must_keywords") or [])),
        ("禁止事項", "、".join(st.get("banned") or [])),
    ]
    text = "\n".join(f"{k}: {v}" for k, v in rows if v)
    if st.get("reference"):
        text += f"\n参考資料（この内容を根拠にする）:\n{st['reference'][:8000]}"
    return text
