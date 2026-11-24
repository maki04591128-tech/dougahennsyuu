"""演出プランの作成（LLM）と整形（台本に無い数値・語を外す）。

LLM は発話番号つきの台本から、カード・図表・テロップ・画像の候補を出す。
出力はそのまま使わず normalize() で整える:
- 発話番号を範囲内に収める
- 図表は台本に出てくる数値だけ（作り話の数値は外す）
- テロップは、その発話に出てくる語・数値だけ
- 同じ発話から始まる図・カードは 1 つ、テロップも 1 つ
"""

import re
import unicodedata
from typing import Any

from video_automation.schemas.direction import MAIN_KINDS, DirectionPlan, Shot, ShotKind
from video_automation.services.voice.reading import UNITS, emphasis_terms

PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "subtitle": {"type": "string"},
        "shots": {
            "type": "array",
            "maxItems": 40,
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["card", "chart", "keyword", "image"]},
                    "from_line": {"type": "integer"},
                    "to_line": {"type": "integer"},
                    "heading": {"type": "string"},
                    "body": {"type": "array", "items": {"type": "string"}},
                    "style": {"type": "string", "enum": ["info", "point", "summary"]},
                    "labels": {"type": "array", "items": {"type": "string"}},
                    "values": {"type": "array", "items": {"type": "number"}},
                    "unit": {"type": "string"},
                    "highlight": {"type": ["integer", "null"]},
                    "text": {"type": "string"},
                    "query": {"type": "string"},
                },
                # 小さなモデルは任意の項目を省くので、使わない項目も空で書かせる
                "required": [
                    "kind",
                    "from_line",
                    "to_line",
                    "heading",
                    "body",
                    "labels",
                    "values",
                    "unit",
                    "text",
                ],
            },
        },
    },
    "required": ["title", "shots"],
}

SYSTEM = (
    "あなたは解説動画の演出担当です。発話番号つきの台本を読み、画面に出す演出を決めます。"
    "字幕は画面下に自動で出るので、字幕と同じ文を画面に出してはいけません。\n"
    "使える演出:\n"
    "- card: 見出し(heading, 16文字以内)と要点(body, 1〜3項目, 各28文字以内)のカード。"
    "話題が変わるところ・定義・比較・まとめに使う。最後の発話には style=summary のまとめを置く。\n"
    "- chart: 棒グラフ。台本に出てくる数値を比べるときだけ使う。labels と values は同じ数"
    "(2〜5)、unit は単位。台本に無い数値は絶対に作らない。強調する項目は highlight(0始まり)。\n"
    "- keyword: 画面上部の短いテロップ(text, 12文字以内)。その発話に出てくる重要な語や数値。\n"
    "- image: 画像が分かりやすい場面。query に画像検索の語(日本語か英語)。\n"
    "from_line はその演出を出し始める発話番号、to_line はその演出が説明している最後の発話番号。"
    "話題が変わったら（例: 計算の話から実験の話へ）、新しい card / chart を置く。"
    "2〜4 発話に 1 つは card / chart / image を置く。"
    "同じ発話から始まる card / chart / image は 1 つだけ。"
    "title は動画のタイトル(20文字以内)、subtitle は興味を引く一言(24文字以内)。"
    "card には必ず body を 1〜3 項目書く（見出しだけのカードは作らない）。"
    "台本で 2 つ以上の数値を比べている所には chart を置く。\n"
    "使わない項目は空文字・空配列にする。\n"
    "例: 電池の劣化の話なら、card（見出し「劣化の原因」、body「高温」「満充電のまま放置」）、"
    "chart（labels「25℃」「40℃」、values 90 と 75、unit「%」）、keyword「40℃で75%」のようにする。"
)


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFKC", text)
    return re.sub(r"[\s,、。・?!「」『』()（）]", "", t).lower()


def _num_str(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:g}"


_DIGITS = re.compile(r"\d+(?:\.\d+)?")


def _keyword_ok(text: str, source: str) -> bool:
    """テロップの語がその発話に出てくるか（数値はその数値が出てくれば単位の表記は問わない）。"""
    t, src = _norm(text), _norm(source)
    nums = _DIGITS.findall(t)
    if nums:
        return all(n in _DIGITS.findall(src) for n in nums)
    return bool(t) and t in src


# 「title: …」「card_body_style_summary」のような、項目名・指示の言葉そのもの
_META = re.compile(r"^(?:[a-z_][a-z0-9_]*\s*[:：=]|[a-z0-9]+(?:_[a-z0-9]+)+$)")


_QUOTES = ("「」", "『』", "【】", '""', "“”", "（）", "()")
_BULLET = re.compile(r"^\s*(?:[・•●○◆◇■□▪▫]|[-‐*＊](?=\s)|\d+[.)．）](?=\s|\D))\s*")


def _unquote(text: str) -> str:
    """全体を囲んだかぎかっこを外す（小さなモデルは要点を「」で囲むことがある）。"""
    t = _BULLET.sub("", text.strip())  # 行頭の「・」「1.」（カードが自分で印を付ける）
    for a, b in _QUOTES:
        if len(t) >= 2 and t[0] == a and t[-1] == b and t.count(a) == 1:
            return t[1:-1].strip()
    return t


def _trim(text: str, n: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()  # 改行や連続した空白は 1 つの空白に
    return text if len(text) <= n else text[: n - 1] + "…"


_KANA_UNITS = sorted(((v, k) for k, v in UNITS.items() if len(v) >= 3), key=lambda x: -len(x[0]))


def unit_symbol(unit: str) -> str:
    """図表の単位はカタカナを記号に戻す（ギガパスカル→GPa）。長い単位は図表の数値の横で読めない。"""
    u = unicodedata.normalize("NFKC", unit).strip()
    for kana, sym in _KANA_UNITS:
        if u == kana:
            return sym
    if u in ("パーセント", "％"):
        return "%"
    return u


def infer_unit(values: list[float], text: str) -> str:
    """台本で数値の直後に書かれている単位（カタカナは記号に戻す。例: ギガパスカル→GPa）。"""
    t = unicodedata.normalize("NFKC", text)
    for v in values:
        m = re.search(rf"(?<![\d.]){re.escape(_num_str(v))}\s*([^\d\s、。,]{{1,8}})", t)
        if not m:
            continue
        tail = m.group(1)
        for kana, sym in _KANA_UNITS:
            if tail.startswith(kana):
                return sym
        for sym in sorted(UNITS, key=len, reverse=True):
            if tail.startswith(sym):
                return sym
        if tail[0] in "%％":
            return "%"
    return ""


def normalize(plan: DirectionPlan, lines: list[str], title: str = "") -> DirectionPlan:
    n = len(lines)
    notes = list(plan.notes)
    all_text = _norm("".join(lines))
    shots: list[Shot] = []
    main_starts: set[int] = set()
    kw_starts: set[int] = set()
    if n == 0:
        return plan.model_copy(update={"shots": [], "notes": notes})
    for sh in sorted(plan.shots, key=lambda x: x.from_line):
        s = sh.model_copy(deep=True)
        s.from_line = min(max(1, s.from_line), n)
        s.to_line = min(max(s.from_line, s.to_line or s.from_line), n)
        if s.kind == ShotKind.CARD:
            s.heading = _trim(_unquote(s.heading), 20)
            s.body = [_unquote(b) for b in s.body]
            # 動画そのものの説明（「解説動画」）や、見出し・題名の繰り返しは要点にならない
            same = {_norm(s.heading), _norm(title)}
            junk = [
                b
                for b in s.body
                if _META.match(b.strip()) or _norm(b) in same or _norm(b).endswith("解説動画")
            ]
            if junk:  # 小さなモデルは項目名や指示の言葉を本文に入れることがある
                notes.append(
                    f"{s.from_line}: カードの本文から要点でない文（{len(junk)} 件）を外しました"
                )
            s.body = [_trim(b, 40) for b in s.body if b.strip() and b not in junk][:3]
            if not s.heading and not s.body:
                notes.append(f"{s.from_line}: 文の無いカードを外しました")
                continue
        elif s.kind == ShotKind.CHART:
            k = len(s.values)
            if k < 2 or len(s.labels) != k:
                notes.append(f"{s.from_line}: 項目と数値の数が合わない図表を外しました")
                continue
            missing = [_num_str(v) for v in s.values if _num_str(v) not in all_text]
            if missing:
                notes.append(
                    f"{s.from_line}: 台本に無い数値（{'、'.join(missing)}）の図表を外しました"
                )
                continue
            if s.highlight is not None and not 0 <= s.highlight < k:
                s.highlight = None
            s.labels = [_trim(x, 12) for x in s.labels]
            s.heading = _trim(s.heading, 24)
            if not s.unit.strip():
                s.unit = infer_unit(s.values, "".join(lines[s.from_line - 1 : s.to_line]))
                s.unit = s.unit or infer_unit(s.values, "".join(lines))
            s.unit = unit_symbol(s.unit)
        elif s.kind == ShotKind.KEYWORD:
            s.text = _trim(s.text or s.heading, 14)
            src = "".join(lines[s.from_line - 1 : s.to_line])
            if not s.text or not _keyword_ok(s.text, src):
                notes.append(f"{s.from_line}: 発話に無い語のテロップ「{s.text}」を外しました")
                continue
            if s.from_line in kw_starts:
                continue
            kw_starts.add(s.from_line)
            shots.append(s)
            continue
        # card / chart / image
        if s.from_line in main_starts:
            notes.append(f"{s.from_line}: 同じ発話から始まる図・カードは 1 つにしました")
            continue
        main_starts.add(s.from_line)
        shots.append(s)
    order = {k: i for i, k in enumerate([*MAIN_KINDS, ShotKind.KEYWORD])}
    shots.sort(key=lambda x: (x.from_line, order[x.kind]))
    return plan.model_copy(
        update={
            "title": _trim(plan.title or title, 30),
            "subtitle": _trim(plan.subtitle, 30),
            "shots": shots,
            "notes": notes,
        }
    )


def rule_plan(lines: list[str], title: str) -> DirectionPlan:
    """LLM を使わない場合の最低限の演出: タイトルと、数値のテロップ。"""
    shots = []
    for i, line in enumerate(lines, 1):
        nums = [t for t in emphasis_terms(line) if re.search(r"\d", t)]
        if nums:
            shots.append(Shot(kind=ShotKind.KEYWORD, from_line=i, text=nums[0][:14]))
    return DirectionPlan(title=title, shots=shots)


def prompt_lines(speakers: list[str], lines: list[str]) -> str:
    return "\n".join(
        f"[{i}] {spk + '：' if spk else ''}{text}"
        for i, (spk, text) in enumerate(zip(speakers, lines, strict=True), 1)
    )


_ALIASES = {
    "title": "heading",
    "header": "heading",
    "points": "body",
    "items": "body",
    "content": "body",
    "bullets": "body",
    "keyword": "text",
    "start_line": "from_line",
    "end_line": "to_line",
}


def from_llm(data: dict[str, Any]) -> DirectionPlan:
    """LLM の出力を DirectionPlan に（型の合わない項目は捨てる）。"""
    shots: list[Shot] = []
    dropped = 0
    # 小さなモデルは {"plan": {...}} のように一段包むことがある
    if "shots" not in data:
        inner = next((v for v in data.values() if isinstance(v, dict) and "shots" in v), None)
        data = inner or data
    items = data.get("shots", []) or []
    for raw in items:
        if not isinstance(raw, dict):
            dropped += 1
            continue
        item = {k: v for k, v in raw.items() if v is not None}
        for alias, key in _ALIASES.items():  # よくある別名を正しい項目名に
            if alias in item and not item.get(key):
                item[key] = item.pop(alias)
        if not isinstance(item.get("to_line"), int) or item["to_line"] < 1:
            item.pop("to_line", None)  # 0 や空は「from_line と同じ」
        if isinstance(item.get("from_line"), int) and item["from_line"] < 1:
            item["from_line"] = 1
        if isinstance(item.get("body"), str):
            item["body"] = [x for x in re.split(r"[\n／/]", item["body"]) if x.strip()]
        try:
            shots.append(Shot.model_validate(item))
        except ValueError:
            # 長すぎる文などは切り詰めて再挑戦
            for key, lim in (("heading", 40), ("text", 24), ("query", 100), ("unit", 10)):
                if isinstance(item.get(key), str):
                    item[key] = item[key][:lim]
            for key in ("body", "labels", "values"):
                if isinstance(item.get(key), list):
                    item[key] = item[key][:6]
            try:
                shots.append(Shot.model_validate(item))
            except ValueError:
                dropped += 1
    notes = [f"AI の出力のうち {dropped} 件は形式が合わず外しました"] if dropped else []
    if not items:
        notes.append("AI の出力に演出がありませんでした")
    return DirectionPlan(
        title=str(data.get("title") or "")[:60],
        subtitle=str(data.get("subtitle") or "")[:60],
        shots=shots[:60],
        notes=notes,
    )


KIND_LABEL = {"card": "カード", "chart": "図表", "keyword": "テロップ", "image": "画像"}


def describe_plan(plan: dict[str, Any]) -> str:
    """演出プランを読みやすい文に（確認用）。"""
    out = [f"タイトル: {plan.get('title') or '（なし）'}"]
    if plan.get("subtitle"):
        out.append(f"ひとこと: {plan['subtitle']}")
    for s in plan.get("shots", []):
        rng = f"{s['from_line']}" + (
            f"〜{s['to_line']}" if s.get("to_line") and s["to_line"] != s["from_line"] else ""
        )
        kind = KIND_LABEL.get(s["kind"], s["kind"])
        if s["kind"] == "card":
            body = " / ".join(s.get("body") or [])
            detail = f"{s.get('heading', '')}：{body}" if body else s.get("heading", "")
        elif s["kind"] == "chart":
            pairs = ", ".join(
                f"{lab} {v:g}{s.get('unit', '')}"
                for lab, v in zip(s.get("labels", []), s.get("values", []), strict=False)
            )
            detail = f"{s.get('heading', '')}（{pairs}）"
        elif s["kind"] == "keyword":
            detail = s.get("text", "")
        else:
            detail = f"検索語: {s.get('query', '')}" + ("（素材あり）" if s.get("asset_id") else "")
        out.append(f"[{rng}] {kind}: {detail}")
    if plan.get("notes"):
        out.append("\n[整えた点]")
        out += plan["notes"]
    return "\n".join(out)
