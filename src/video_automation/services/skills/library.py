"""ローカル LLM のスキル（Claude のスキルと同じ SKILL.md の形）。

- スキルは「名前・説明（いつ使うか）・本文（手順・決まり・例）」の Markdown。
  同梱（video_automation/skills/<名前>/SKILL.md）と、ユーザーのフォルダ（設定 skills_dir）から読む。
  同じ名前ならユーザーのものが優先（Claude で直したスキルを置けば、そのまま使われる）。
- ローカル LLM は扱える文の長さが小さいので、スキル全体ではなく、依頼に関係する節だけを
  上限の文字数までプロンプトに入れる（見出しごとに分け、依頼文との文字の重なりで選ぶ）。
- コードの例（``` で囲んだ部分）は、選んだ節でも既定では省く（LLM が作るのは計画・台本で、
  Blender のコードではないため）。読むときは read で全文を取れる。
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from video_automation.app.logging import get_logger

log = get_logger("skills")
BUNDLED = Path(__file__).resolve().parents[2] / "skills"
_FRONT = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)
_HEAD = re.compile(r"^(#{1,4})\s+(.+?)\s*$", re.M)
_CODE = re.compile(r"```.*?```", re.S)
_SKIP = re.compile(r"(情報源|出典|参考文献|参考資料|Sources?|References?)\s*$", re.I)
_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


@dataclass
class Section:
    skill: str
    path: str  # 見出しの並び（「2. 用途ごとの型 > 2.2 用途ごとの数値」）
    text: str  # 見出しを含む本文
    order: int


@dataclass
class Skill:
    name: str
    description: str
    body: str
    source: str  # bundled / user
    path: Path
    sections: list[Section] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.body)


@dataclass
class Picked:
    skill: str
    path: str
    text: str
    score: float


def _front(text: str) -> tuple[dict[str, str], str]:
    m = _FRONT.match(text)
    if not m:
        return {}, text
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        k, sep, v = line.partition(":")
        if sep:
            meta[k.strip()] = v.strip().strip('"').strip("'")
    return meta, text[m.end() :]


def split_sections(name: str, body: str) -> list[Section]:
    """見出し（#〜####）ごとに分ける。各節は上位の見出しの並びを持つ。"""
    heads = list(_HEAD.finditer(body))
    # コードの中の「#」は見出しではない
    code = [(m.start(), m.end()) for m in _CODE.finditer(body)]
    heads = [h for h in heads if not any(a <= h.start() < b for a, b in code)]
    out: list[Section] = []
    stack: list[tuple[int, str]] = []
    if heads and heads[0].start() > 0 and body[: heads[0].start()].strip():
        out.append(Section(name, "（前書き）", body[: heads[0].start()].strip(), 0))
    for i, h in enumerate(heads):
        level, title = len(h.group(1)), h.group(2).strip()
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        end = heads[i + 1].start() if i + 1 < len(heads) else len(body)
        text = body[h.start() : end].strip()
        if len(text) > len(h.group(0)) + 2:  # 見出しだけの節は外す
            # 一番上の見出し（スキルの題名）は道筋に入れない（どの節も同じで長くなるだけ）
            titles = [t for lv, t in stack if lv > 1] or [title]
            out.append(Section(name, " > ".join(titles), text, len(out)))
    return out


_KANA = re.compile(r"[\u3040-\u309f]")  # ひらがな
_WORDCH = re.compile(r"[\u4e00-\u9fff\u30a0-\u30ffA-Za-z0-9]")  # 漢字・カタカナ・英数字


def terms(text: str) -> list[str]:
    """検索の単位。日本語は単語に分けず、漢字・カタカナ・英数字の 1 文字と、
    ひらがなだけではない 2 文字の並びを使う（「して」「ので」などの機能語を外す）。"""
    t = re.sub(r"\s+", "", text.lower())
    out = [c for c in t if _WORDCH.match(c)]
    for i in range(len(t) - 1):
        bg = t[i : i + 2]
        if not all(_KANA.match(c) for c in bg) and any(_WORDCH.match(c) for c in bg):
            out.append(bg)
    return out


class SkillLibrary:
    def __init__(self, user_dir: Path | None = None, bundled: Path = BUNDLED) -> None:
        self.user_dir = user_dir
        self.bundled = bundled
        self._skills: dict[str, Skill] = {}
        self.reload()

    def reload(self) -> None:
        skills: dict[str, Skill] = {}
        for source, root in (("bundled", self.bundled), ("user", self.user_dir)):
            if root is None or not root.is_dir():
                continue
            for f in sorted(root.glob("*/SKILL.md")):
                try:
                    meta, body = _front(f.read_text(encoding="utf-8-sig"))
                except OSError as exc:
                    log.warning("skill_unreadable", path=str(f), error=str(exc))
                    continue
                name = meta.get("name") or f.parent.name
                if not _NAME.match(name):
                    log.warning("skill_bad_name", path=str(f), name=name)
                    continue
                sk = Skill(name, meta.get("description", ""), body.strip(), source, f)
                sk.sections = split_sections(name, sk.body)
                skills[name] = sk
        self._skills = skills
        log.info(
            "skills_loaded",
            count=len(skills),
            user=sum(s.source == "user" for s in skills.values()),
        )

    def all(self) -> list[Skill]:
        return sorted(self._skills.values(), key=lambda s: s.name)

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def catalog(self, names: list[str] | None = None) -> str:
        """プロンプトに入れるスキルの一覧（名前と、いつ使うか）。"""
        items = [s for s in self.all() if names is None or s.name in names]
        return "\n".join(f"- {s.name}: {s.description}" for s in items)

    def search(
        self,
        query: str,
        *,
        names: list[str] | None = None,
        budget: int = 4000,
        limit: int = 6,
        keep_code: bool = False,
        min_score: float = 0.2,
    ) -> list[Picked]:
        """依頼文に関係する節を、合計 budget 文字まで選ぶ（関係の強い順、同じスキルは本文の順）。"""
        q = set(terms(query))
        if not q:
            return []
        secs: list[tuple[Section, str, Counter[str], int]] = []
        for sk in self.all():
            if names is not None and sk.name not in names:
                continue
            for sec in sk.sections:
                if _SKIP.search(sec.path):  # 出典の一覧などは指示にならない
                    continue
                text = sec.text if keep_code else _CODE.sub("（コード例は省略）", sec.text)
                # 見出しは 3 回分として数える（何についての節かを強く表す）
                tf = Counter(terms(text) + terms(sec.path) * 3)
                secs.append((sec, text, tf, sum(tf.values())))
        if not secs:
            return []
        n = len(secs)
        df = Counter(t for _, _, tf, _ in secs for t in tf.keys() & q)
        avg = sum(ln for *_, ln in secs) / n
        k1, b_ = 1.2, 0.75
        idf = {t: math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5)) for t in q if df[t]}
        # どのスキルにも無い語は、ある語の中で一番重いものと同じ重さとして数える
        # （スキルの数が少ないと、無い語の重さが大きくなりすぎるのを防ぐ）
        missing = len(q) - len(idf)
        full = (k1 + 1) * (sum(idf.values()) + missing * max(idf.values(), default=1.0))
        cands: list[tuple[float, Section, str]] = []
        for sec, text, tf, ln in secs:
            score = 0.0
            for t in q & tf.keys():
                f = tf[t]
                score += idf[t] * f * (k1 + 1) / (f + k1 * (1 - b_ + b_ * ln / avg))
            score /= full  # 0〜1（依頼文のどれだけを覆うか）。スキルの数に左右されにくい
            if score >= min_score:  # 関係の薄い節は入れない（小さいモデルが迷う）
                cands.append((score, sec, text))
        cands.sort(key=lambda c: -c[0])
        if cands:  # 一番近い節より大きく劣る節は入れない
            top = cands[0][0]
            cands = [c for c in cands if c[0] >= 0.6 * top]
        picked: list[Picked] = []
        used = 0
        for score, sec, text in cands:
            if len(picked) >= limit:
                break
            if used + len(text) > budget:
                room = budget - used
                if room < 400:
                    continue
                text = text[:room].rsplit("\n", 1)[0] + "\n…（以下略）"
            picked.append(Picked(sec.skill, sec.path, text, round(score, 3)))
            used += len(text)
        order = {(s.skill, s.path): s.order for sk in self.all() for s in sk.sections}
        picked.sort(key=lambda p: (p.skill, order.get((p.skill, p.path), 0)))
        return picked

    def context(self, query: str, **kw: object) -> tuple[str, list[Picked]]:
        """プロンプトに付ける文（「参考: スキル」）と、選んだ節。"""
        picked = self.search(query, **kw)  # type: ignore[arg-type]
        if not picked:
            return "", []
        parts = [
            "参考（スキル）: 次の決まりと手順に従ってください。"
            "ここに無いことは推測で補わないでください。"
        ]
        for p in picked:
            parts.append(f"【{p.skill} / {p.path}】\n{p.text}")
        return "\n\n".join(parts), picked
