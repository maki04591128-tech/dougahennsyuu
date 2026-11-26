# ruff: noqa: E501  （指示の文は 1 項目 1 行で読めるように）
"""演出プランの LLM への指示（統合版 W1）。

- 前半（役目・出力の形）は全用途で共通、後半（使える演出・禁止事項）を用途ごとに差し替える
- 禁止事項は、用途の型の部品の許可（profiles.py。統合版 2節の値）から自動で作る
- 例は用途ごとに 4 個（1 例は台本 6 発話。plan_examples.py）
- 並びは「台本 → 決まり（スキル）→ 指示 → 例」（台本を先頭、指示と例を後ろに置く）
"""

import json
from dataclasses import dataclass
from typing import Any

from video_automation.schemas.design import ANY, Component, DesignProfile, Purpose
from video_automation.services.direction.plan_examples import EXAMPLES

ROLE = (
    "あなたは動画の演出担当です。発話番号つきの台本を読み、画面に出す演出を決めます。"
    "指定の JSON の形だけで答えます。"
)

COMMON = (
    "字幕は画面下に自動で出るので、字幕と同じ文を画面に出してはいけません。"
    "from_line はその演出を出し始める発話番号、to_line はその演出が説明している最後の発話番号。"
    "title は動画のタイトル(20文字以内)、subtitle は興味を引く一言(24文字以内)。"
    "使わない項目は空文字・空配列にする。"
    "台本に無い数値・語は絶対に作らない。"
)

KIND_TEXT = {
    "card": (
        "card: 見出し(heading, 16文字以内)と要点(body, 1〜3項目, 各28文字以内)のカード。"
        "話題が変わるところ・定義・比較・まとめに使う。body は必ず 1〜3 項目書き、"
        "セリフを写さず 15 文字ほどの体言止めに要約する。最後の発話には style=summary のまとめを置く。"
    ),
    "chart": (
        "chart: 棒グラフ。台本で 2 つ以上の数値を比べている所に使う。labels と values は同じ数"
        "(2〜5)、unit は単位。強調する項目は highlight(0始まり)。"
    ),
    "keyword": "keyword: 画面上部の短いテロップ(text, 12文字以内)。その発話に出てくる重要な語や数値。",
    "image": "image: 画像が分かりやすい場面。query に画像検索の語(日本語か英語)。",
}
_KIND_COMP = {
    "card": Component.CARD,
    "chart": Component.CHART,
    "keyword": Component.KEYWORD,
    "image": Component.IMAGE,
}
MAIN = ("card", "chart", "image")

# 用途ごとに何を画面に出すか（統合版 2節・5節の用途の型の要点）
FOCUS: dict[str, str] = {
    "subtitle": "映像と話者が主役。画面には題名だけを出す。",
    "interview": "話者と言葉が主役。画面には題名だけを出す。名前・肩書きは設定から出すので作らない。",
    "documentary": "事実と映像の重み。画面には題名だけを出し、装飾はしない。",
    "story": "声と情景が主役。場面ごとに情景の画像を置き、文字は題名だけにする。",
    "podcast": "声が主役。画面には題名だけを出す。",
    "explainer": "図と要点で整理する。定義・理由・比較の所に card、数値の比較に chart を置く。",
    "lecture": "資料が主役。用語の定義・手順・まとめを card にし、章の最後にまとめを置く。",
    "tutorial": "手順が主役。手順ごとに card（見出しは「手順1」の形）を置き、押す所・選ぶ所を keyword にする。",
    "recipe": "材料と手順が主役。最初に材料の card、続いて「STEP 1」の形の手順の card、分量・時間を keyword にする。",
    "news": "事実を正確に。いつ・どこで・何が・数値を card にし、あおる言葉を使わない。",
    "corporate": "会社の事実と数値。概要・実績・目標を card にし、誇張しない。",
    "short": "最初の発話で引きつける。短い keyword を多めにし、card・chart は要点だけにする。",
    "game": "実況の盛り上がり。驚き・成功の語を keyword にし、card は作戦や目標だけにする。",
    "vlog": "その日の情景が主役。場面ごとに image、地名・数を keyword にする。",
    "ranking": "順位の発表が主役。各順位を card（見出しは「第3位」の形）にし、最後に一覧のまとめを置く。",
    "product": "商品の事実。特長を card、数値の比較を chart にし、良い点と気になる点の両方を書く。",
    "promo": "伝えたい一言を短く。価格・日付・特典を keyword にし、card は特典と行動の呼びかけだけにする。",
    "event": "その日の情景が主役。場面ごとに image、人数・数を keyword にする。",
    "kids": "やさしい言葉で。むずかしい漢字を避け、1 つの card に 1 つのことだけを書く。",
    "mv": "歌詞の情景が主役。歌詞のまとまりごとに情景の image を置く。文字は画面に出さない。",
    "lyric": "歌詞の情景が主役。歌詞のまとまりごとに情景の image を置く。文字は画面に出さない。",
    "trailer": "緊張感のある情景。場面ごとに image を置き、文字は題名だけにする。",
}


def allowance(prof: DesignProfile | None, kind: str, extras: list[Component]) -> int:
    """その用途で使える回数（-1 は上限なし、0 は使わない）。型が無ければ全部使える。"""
    if prof is None:
        return ANY
    c = _KIND_COMP[kind]
    return ANY if c in extras else prof.allowance(c)


def usable(prof: DesignProfile | None, extras: list[Component] | None = None) -> list[str]:
    ex = extras or []
    return [k for k in KIND_TEXT if allowance(prof, k, ex) != 0]


def forbidden(prof: DesignProfile | None, extras: list[Component] | None = None) -> list[str]:
    """禁止事項（用途の型の部品の許可から作る）。"""
    ex = extras or []
    out: list[str] = []
    for k in KIND_TEXT:
        n = allowance(prof, k, ex)
        if n == 0:
            out.append(f"{k} は使わない（この用途の型に無い）。")
        elif n > 0:
            out.append(f"{k} は動画全体で最大 {n} 個。")
    if (
        prof is not None
        and not prof.allows(Component.TITLE_CARD)
        and (Component.TITLE_CARD not in ex)
    ):
        out.append("title と subtitle は空にする（この用途では題名を出さない）。")
    out.append("字幕と同じ文、台本に無い数値・語、話し手の名前を画面に出さない。")
    out.append("同じ発話から始まる card / chart / image は 1 つだけ、keyword も 1 つだけ。")
    return out


def instructions(prof: DesignProfile | None, extras: list[Component] | None = None) -> str:
    kinds = usable(prof, extras)
    label = prof.label if prof else "解説"
    purpose = prof.purpose.value if prof else Purpose.EXPLAINER.value
    lines = [f"この動画の用途: {label}。{FOCUS[purpose]}", "使える演出:"]
    lines += [f"- {KIND_TEXT[k]}" for k in kinds] or ["- なし（shots は空の配列にする）"]
    main = [k for k in MAIN if k in kinds]
    if main:
        lines.append(
            f"2〜4 発話に 1 つは {' / '.join(main)} を置く。話題が変わったら新しい"
            f" {' / '.join(main)} を置く。"
        )
    lines.append("禁止事項:")
    lines += [f"- {x}" for x in forbidden(prof, extras)]
    return "\n".join(lines)


def _shot_json(s: dict[str, Any]) -> dict[str, Any]:
    """例の出力を、指定の形（使わない項目も空で書く）にそろえる。"""
    out: dict[str, Any] = {
        "kind": s["kind"],
        "from_line": s["from_line"],
        "to_line": s.get("to_line", s["from_line"]),
        "heading": s.get("heading", ""),
        "body": s.get("body", []),
        "labels": s.get("labels", []),
        "values": s.get("values", []),
        "unit": s.get("unit", ""),
        "text": s.get("text", ""),
    }
    for key in ("style", "highlight", "query"):
        if s.get(key) is not None:
            out[key] = s[key]
    return out


def example_output(ex: dict[str, Any]) -> dict[str, Any]:
    o = ex["out"]
    return {
        "title": o["title"],
        "subtitle": o["subtitle"],
        "shots": [_shot_json(s) for s in o["shots"]],
    }


def examples_text(prof: DesignProfile | None) -> str:
    purpose = prof.purpose.value if prof else Purpose.EXPLAINER.value
    parts = []
    for i, ex in enumerate(EXAMPLES[purpose], 1):
        script = "\n".join(f"[{n}] {spk}：{t}" for n, (spk, t) in enumerate(ex["lines"], 1))
        out = json.dumps(example_output(ex), ensure_ascii=False)
        parts.append(f"例{i}（台本は例。内容を写さない）:\n{script}\n出力:\n{out}")
    return "\n\n".join(parts)


@dataclass(frozen=True)
class PlanPrompt:
    system: str
    user: str
    rules: str  # 指示の文（要点が指示の言い換えかの判定に使う）
    examples: str  # 例の文（例の要点をそのまま写したかの判定に使う）


def build(
    body: str,
    prof: DesignProfile | None,
    extras: list[Component] | None = None,
    skills: str = "",
) -> PlanPrompt:
    """台本（body）を先頭に、決まり・指示・例を後ろに置いた指示を作る。"""
    rules = COMMON + "\n" + instructions(prof, extras)
    exs = examples_text(prof)
    user = body
    if skills:
        user += "\n\n演出の決まり（参考）:\n" + skills
    user += "\n\n指示:\n" + rules + "\n\n" + exs
    user += "\n\n上の台本の演出を、例と同じ JSON の形で出してください。"
    return PlanPrompt(system=ROLE, user=user, rules=ROLE + COMMON + rules, examples=exs)


def retry_text(
    prof: DesignProfile | None, extras: list[Component] | None, have: int, n_lines: int, need: int
) -> str:
    kinds = "/".join(k for k in MAIN if k in usable(prof, extras))
    txt = (
        f"{kinds} が {have} 個しかありません。{n_lines} 発話なので、少なくとも {need} 個に"
        "してください（2〜4 発話に 1 つ、話題の変わり目ごと）。"
    )
    if "card" in usable(prof, extras):
        txt += "最後の発話には style=summary のまとめのカードを置いてください。"
    return txt + "同じ JSON の形で、演出全体を出し直してください。"


def need_main(prof: DesignProfile | None, extras: list[Component] | None, n_lines: int) -> int:
    """図・カード・画像の最低の数（4 発話に 1 つ。使える回数の合計を超えない）。"""
    caps = [allowance(prof, k, extras or []) for k in MAIN if k in usable(prof, extras)]
    if not caps:
        return 0
    need = max(2, -(-n_lines // 4)) if n_lines >= 4 else 1
    if all(c > 0 for c in caps):
        need = min(need, sum(caps))
    return need
