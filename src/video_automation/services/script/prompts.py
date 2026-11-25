"""台本生成のプロンプト（バージョン付き。GenerationRun に prompt_version を記録する）。"""

from typing import Any

from video_automation.services.voice.characters import EMOTION_LABELS, EMOTIONS

COMMON = (
    "あなたは日本語の動画台本作家です。指定されたキャラクターの性格・口調を一貫して守ります。"
    "一人称・相手の呼び方・語尾はキャラクターごとに決まっており、台本の最初から最後まで変えません"
    "（他のキャラクターの一人称や語尾が混ざらないよう、発話ごとに話者の設定を確かめます）。"
    "性格は口調だけでなく、反応の仕方（驚く・ツッコむ・まとめる等の役割）にも出します。"
    "口調の決まり（語尾・一人称）そのものを台本の中で話題にしません（「全部『のだ』だ」のような"
    "メタな発言をしない）。ほかのキャラクターの口調や乱暴な言い方（「〜ねぇ」等）を真似しません。"
    "事実が不明な点は推測で確定せず、notes に仮定や確認したいことを書きます。"
    "日本語の会話らしく、一人称や相手の呼び方を毎回のセリフに入れません（必要なときだけ。"
    "1 つのセリフで相手を呼ぶのは 1 回まで）。"
    "同じ説明や同じセリフを繰り返さず、聞き手の疑問には次の発話で新しい情報を返します。"
    "キャラクター同士は決まった呼び名で呼び合います（呼び名の規則があれば必ず守ります）。"
    "公序良俗に反する内容（性的・暴力的な表現）、政治・宗教の勧誘、特定の個人・団体への非難、"
    "誤った医療情報などの誤解を招く内容、キャラクターのイメージを大きく損なう内容は書きません。"
    "セリフは自然な話し言葉で、1つの発話は120文字以内、1 文は 40 文字程度までにします"
    "（音声で読み上げるので、長い文は句読点で区切ります）。"
)

EMOTION_TEXT = (
    "各発話の emotion は声の調子の切り替えに使います。ふつうの説明は neutral にし、"
    "気持ちがはっきり出る発話だけ次から選びます: "
    + "、".join(f"{e}（{EMOTION_LABELS[e]}）" for e in EMOTIONS if e != "neutral")
    + "。"
)

FULL_VERSION = "script.full.v7"
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
                                "emotion": {"type": "string", "enum": list(EMOTIONS)},
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

COUNTERPART_VERSION = "script.counterpart.v4"
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
    text = "口調の規則（必ず守る）:\n" + "\n".join(f"- {r}" for r in rows) if rows else ""
    ex = [
        f"- {p.name}: " + " ".join(f"「{e}」" for e in p.examples[:4])
        for p in personas.values()
        if getattr(p, "examples", None)
    ]
    if ex:
        text += (
            "\n\n話し方の手本（公式のセリフ。口調・言い回しの参考にし、文はそのまま使わない）:\n"
            + "\n".join(ex)
        )
    return text.strip()


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


# ---------- 推敲（会話として不自然なセリフを直す） ----------
POLISH_VERSION = "script.polish.v2"
POLISH_SYSTEM = (
    "あなたは掛け合い動画の台本の推敲者です。台本を通して読み、会話として不自然なセリフだけを"
    "挙げて書き直します。対象は、意味が通らない・前のセリフの流れに合っていない・同じことを"
    "繰り返している・言葉そのものを話題にする的外れなやり取り・日本語として不自然・"
    "前のセリフの内容と理屈が合わない反応（冷房 28 度の話に「冷たすぎる」と返すなど）、"
    "のどれかです。"
    "書き直すときは、そのキャラクターの口調の規則（一人称・相手の呼び方・語尾・使わない言い方）を"
    "守り、伝える内容と長さは元のセリフと同じくらいにします。問題の無いセリフは挙げません。"
    "直すものが無ければ fixes は空配列にします。"
)
POLISH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "fixes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "problem": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["id", "problem", "text"],
            },
            "maxItems": 8,
        }
    },
    "required": ["fixes"],
}


# ---------- 事実の確認（解説の台本の誤りを直す） ----------
FACT_VERSION = "script.fact.v3"
FACT_SYSTEM = (
    "あなたは解説動画の校閲者です。台本のセリフのうち、事実として誤っている・誤解を招く内容"
    "（科学・技術・数値・制度・手順の誤り）だけを挙げ、意味が正しくなるよう最小限に直したセリフを書きます。"
    "話し手の口調・一人称・語尾・長さはそのまま保ちます。冗談・感想・たとえ・質問は誤りとして扱いません。"
    "参考資料があれば、それを正しい情報として優先します。確信が持てないものは挙げません。"
    "数値（%・倍・半分・温度・金額・時間）を含むセリフは特に確かめ、誇張（「消費電力が半分になる」"
    "のような根拠の無い大きな数）は、確かな言い方（「電気代を抑えられる」など）に直します。"
    "説明が簡単すぎる・条件や例外を書いていない・別の言い方もある、といった点は誤りではありません"
    "（短い動画の台本なので、補足を足すための書き直しはしません）。"
    "誤りが無ければ fixes は空配列にします。"
)
FACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "fixes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "problem": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["id", "problem", "text"],
            },
            "maxItems": 10,
        }
    },
    "required": ["fixes"],
}
