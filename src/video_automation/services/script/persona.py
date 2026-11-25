"""キャラクターの口調（一人称・相手の呼び方・語尾）を規則で確かめ、直せるものは直す。

LLM は長い台本の途中で一人称を取り違えたり（めたんが「僕」）、語尾の決まったキャラクターの
語尾を落としたり（ずんだもんが「〜思ってんのか」）しやすい。口調の設定（speaking_style。
声を選ぶと組み込みの口調 VoiceCharacterStyle.text() が入る）から規則を読み取り、
- 一人称・二人称の取り違え: 決まった言い方に置き換える（意味は変わらない）
- 語尾の抜け: 指摘する（文の言い換えが要るので、規則では直さない）
を行う。かぎかっこの中（他の人の言葉の引用）は調べない。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# 調べる一人称・二人称（長いものから照合する）。「うち」「わし」は普通の言葉と紛れるので除く
FIRST_PERSONS = [
    "わたくし",
    "あたくし",
    "わたし",
    "ワタシ",
    "あたし",
    "あーし",
    "私",
    "僕",
    "ぼく",
    "ボク",
    "俺",
    "おれ",
    "オレ",
    "拙者",
    "吾輩",
    "我輩",
]
SECOND_PERSONS = [
    "あなたさま",
    "あなた",
    "貴方",
    "貴女",
    "アンタ",
    "あんた",
    "お前",
    "おまえ",
    "オマエ",
    "君",
    "きみ",
    "キミ",
    "貴様",
    "てめえ",
]
_PLURAL = ("たち", "達", "がた", "方", "ら", "ども", "等")
# 「私」は「わたし」「わたくし」「あたし」とも読める。それらを使うキャラクターでは正しい一人称にする
_WATASHI = {"私", "わたし", "ワタシ", "わたくし", "あたし", "あたくし"}
# 呼び名の照合に使わない語（一般の言葉と紛れる・短すぎる）
_CALL_STOP = {
    "うさぎ", "そら", "ずんだ", "あんこ", "おっさん", "親父", "おじいさん", "おっちゃん",
    "助手", "猫", "龍", "雪", "ゆき", "なな", "ユ", "T", "Ｌ", "きりたんぽ", "セブン",
}  # fmt: skip
# 「私立」「私鉄」「君主」など、代名詞ではない語の始まり
_NOT_PRONOUN_NEXT = set("立鉄服物有用的語信設営邸刑書費財道案主臨")
# 文の区切り（「…」は言いよどみで文の途中にも出るので区切りにしない）
_SENT = re.compile(r"[^。！？!?\n]+[。！？!?…]*")
_TRAIL = "。！？!?…〜ーっッ♪☆★w～、 　）)」』"


@dataclass
class Persona:
    name: str
    first: list[str] = field(default_factory=list)  # 使ってよい一人称（先頭が基本）
    second: list[str] = field(default_factory=list)  # 使ってよい相手の呼び方（先頭が基本）
    endings: list[str] = field(default_factory=list)  # 語尾（「〜」は除く）
    # 同じ台本に出るほかのキャラクター名 → このキャラクターからの公式の呼び名
    calls: dict[str, str] = field(default_factory=dict)
    # このキャラクターの呼ばれ方（名前・ほかのキャラクターからの呼び名）
    known_as: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.first or self.second or self.endings or self.calls)

    def rules_text(self) -> str:
        """台本を作るときに渡す、必ず守る規則の文。"""
        parts = []
        if self.first:
            parts.append(f"一人称は「{'」「'.join(self.first)}」だけ（他の一人称は使わない）")
        if self.second:
            parts.append(f"相手は「{'」「'.join(self.second)}」と呼ぶ")
        if self.endings:
            parts.append(f"文末の大半を「{'」「'.join(self.endings)}」にする（疑問文も含む）")
        if self.calls:
            parts.append("、".join(f"{n}を「{c}」" for n, c in self.calls.items()) + "と呼ぶ")
        return f"{self.name}: " + "。".join(parts) if parts else ""


@dataclass
class PersonaIssue:
    index: int  # 発話の番号（渡した lines の位置）
    kind: str  # first_person / second_person / ending
    found: str
    expected: str
    fixed: str | None = None  # 規則で直した文（直せないときは None）

    def message(self) -> str:
        if self.kind == "ending":
            return f"語尾「{self.expected}」がありません: {self.found}"
        if self.kind == "foreign_ending":
            return f"ほかのキャラクターの語尾「{self.found}」を使っています"
        if self.kind == "stray_ending":
            return f"語尾「{self.found}」だけを文の頭に置いています"
        if self.kind == "call_name":
            return f"呼び名が「{self.found}」になっています（公式は「{self.expected}」）"
        what = "一人称" if self.kind == "first_person" else "相手の呼び方"
        return f"{what}が「{self.found}」になっています（設定は「{self.expected}」）"


def _split(v: str) -> list[str]:
    return [x.strip() for x in re.split(r"[・/／、,]", v) if x.strip()]


def _base(word: str) -> str:
    """「お前ら」「アンタ達」→「お前」「アンタ」。"""
    for suf in _PLURAL:
        if len(word) > len(suf) and word.endswith(suf):
            return word[: -len(suf)]
    return word


def parse_style(name: str, text: str) -> Persona:
    """口調の文（「一人称は「僕」。相手は「オマエ・みんな」と呼ぶ。語尾は「〜のだ」…」）から規則を読む。"""
    p = Persona(name)
    m = re.search(r"一人称は「([^」]+)」", text)
    if m:
        p.first = _split(m.group(1))
    for m in re.finditer(r"一人称は(?:自分の名前)?「([^」]+)」も使う", text):
        p.first += [x for x in _split(m.group(1)) if x not in p.first]
    m = re.search(r"相手は「([^」]+)」と呼ぶ", text)
    if m:
        seen: list[str] = []
        for x in _split(m.group(1)):
            b = _base(x)
            if b not in seen:
                seen.append(b)
        p.second = seen
    m = re.search(r"語尾は((?:「[^」]+」・?)+)", text)
    if m:
        p.endings = [e.lstrip("〜~ー") for e in re.findall(r"「([^」]+)」", m.group(1))]
        p.endings = [e for e in p.endings if e]
    return p


def _outside_quotes(text: str) -> list[bool]:
    """各文字がかぎかっこの外か。"""
    out, depth = [], 0
    for ch in text:
        if ch in "「『":
            depth += 1
            out.append(False)
            continue
        if ch in "」』":
            depth = max(0, depth - 1)
            out.append(False)
            continue
        out.append(depth == 0)
    return out


def _find(text: str, vocab: list[str]) -> list[tuple[int, str]]:
    """text の中の代名詞（位置, 語）。長い語を優先し、重なりは数えない。"""
    ok = _outside_quotes(text)
    words = sorted(vocab, key=len, reverse=True)
    hits: list[tuple[int, str]] = []
    i = 0
    while i < len(text):
        for w in words:
            if text.startswith(w, i) and ok[i]:
                nxt = text[i + len(w)] if i + len(w) < len(text) else ""
                prev = text[i - 1] if i > 0 else ""
                if len(w) == 1 and nxt in _NOT_PRONOUN_NEXT:
                    break
                # 「田中君」「山田さん」の君（名前の後ろの敬称）は呼び方ではない
                if w == "君" and prev and not re.match(r"[\s、。！？!?「『（(]", prev):
                    break
                hits.append((i, w))
                i += len(w) - 1
                break
        i += 1
    return hits


def _replace(text: str, hits: list[tuple[int, str]], to: str) -> str:
    out, last = [], 0
    for i, w in hits:
        out.append(text[last:i])
        out.append(to)
        last = i + len(w)
    out.append(text[last:])
    return "".join(out)


def _ending_ok(sentence: str, endings: list[str]) -> bool:
    core = sentence.rstrip(_TRAIL)
    tail = core[-(max(len(e) for e in endings) + 3) :]
    return any(e in tail for e in endings)


_PARTICLES = set("よねなぞわさ")  # 語尾の後ろに付く終助詞


def _ends_with(core: str, e: str) -> bool:
    return core.endswith(e) or (core[-1:] in _PARTICLES and core[:-1].endswith(e))


def _strip_foreign(sentence: str, e: str) -> str | None:
    """文末のほかのキャラクターの語尾を、意味を変えずに外す（「〜のだ」→「〜の」）。"""
    core = sentence.rstrip(_TRAIL)
    tail = sentence[len(core) :]
    particle = ""
    if core[-1:] in _PARTICLES and core[:-1].endswith(e):  # 「〜のだよ」「〜のだね」
        core, particle = core[:-1], core[-1]
    if not core.endswith(e):
        return None
    if e.endswith("のだ"):  # 「進められているのだ」→「〜いるの」、「〜のだよ」→「〜のよ」
        return core[:-1] + particle + tail
    return None


_KATA = re.compile(r"[ァ-ヴーA-Za-zＡ-Ｚａ-ｚ]")


def _bounded(text: str, i: int, w: str) -> bool:
    """カタカナ・英字の名前が長い語の一部（「アルバム」の「アル」、「エンジニア」の「ニア」）でないか。"""
    prev = text[i - 1] if i > 0 else ""
    nxt = text[i + len(w)] if i + len(w) < len(text) else ""
    if _KATA.match(w[0]) and prev and _KATA.match(prev):
        return False
    if _KATA.match(w[-1]) and nxt and _KATA.match(nxt):
        return False
    return True


def _fix_calls(
    index: int, text: str, p: Persona, others: dict[str, Persona], issues: list[PersonaIssue]
) -> str:
    """同じ台本のキャラクターを公式と違う呼び名で呼んでいたら、公式の呼び名に置き換える。

    照合するのは、そのキャラクターの名前とほかのキャラクターからの呼び名（2 文字以上で、
    一般の言葉と紛れないもの）だけ。ほかのキャラクターの呼び名と重なる語は使わない。"""
    targets: dict[str, set[str]] = {}  # 語 → 呼ばれる側の名前
    for target in p.calls:
        o = others.get(target)
        for w in [target, *(o.known_as if o else [])]:
            if len(w) >= 2 and w not in _CALL_STOP:
                targets.setdefault(w, set()).add(target)
    # 二人以上の呼び名と重なる語は、どちらのことか決められないので使わない
    cand = {w: next(iter(t)) for w, t in targets.items() if len(t) == 1}
    for target, official in p.calls.items():
        cand[official] = target  # 公式の呼び名は必ず照合する（長い一致を優先させるため）
    ok = _outside_quotes(text)
    words = sorted(cand, key=len, reverse=True)
    out, i, last = [], 0, 0
    while i < len(text):
        hit = next(
            (w for w in words if text.startswith(w, i) and ok[i] and _bounded(text, i, w)), None
        )
        if hit is None:
            i += 1
            continue
        official = p.calls[cand[hit]]
        if hit != official:
            out.append(text[last:i] + official)
            last = i + len(hit)
            issues.append(PersonaIssue(index, "call_name", hit, official, None))
        i += len(hit)
    if not out:
        return text
    fixed = "".join(out) + text[last:]
    for x in issues:
        if x.kind == "call_name" and x.fixed is None:
            x.fixed = fixed
    return fixed


def check_line(
    index: int,
    text: str,
    p: Persona,
    foreign: tuple[str, ...] = (),
    others: dict[str, Persona] | None = None,
) -> tuple[str, list[PersonaIssue]]:
    """1 発話を調べる。戻り値は (規則で直した文, 問題の一覧)。

    foreign はほかのキャラクターに決まっている語尾（めたんが「〜のだ」を使わないように）。
    others は同じ台本のキャラクター（呼び名の照合に使う）。"""
    issues: list[PersonaIssue] = []
    fixed = text
    # 「のだ、長く使えるのだ」のように語尾だけを文頭に置く癖（口調の真似の誤り）を外す
    # 1 文字の語尾（「わ」「ね」）は感動詞と紛れるので対象にしない
    for e in sorted((x for x in {*p.endings, *foreign} if len(x) >= 2), key=len, reverse=True):
        m = re.match(rf"\s*{re.escape(e)}[、,！!。　 ]+", fixed)
        if m and len(fixed) > m.end():
            fixed = fixed[m.end() :]
            issues.append(PersonaIssue(index, "stray_ending", e, "", fixed))
            break
    # 「減るのか？のだ」のように、文を言い終えた後に語尾だけを付け足す癖も外す
    for e in sorted((x for x in {*p.endings, *foreign} if len(x) >= 2), key=len, reverse=True):
        m = re.search(rf"([？?！!。])\s*{re.escape(e)}[。！!]?\s*$", fixed)
        if m and m.start() > 0:
            fixed = fixed[: m.start() + 1]
            issues.append(PersonaIssue(index, "stray_ending", e, "", fixed))
            break
    if p.first:
        allowed = set(p.first)
        if allowed & _WATASHI:
            allowed |= {"私"}
        bad = [(i, w) for i, w in _find(fixed, FIRST_PERSONS) if w not in allowed]
        if bad:
            new = _replace(fixed, bad, p.first[0])
            for _, w in bad:
                issues.append(PersonaIssue(index, "first_person", w, p.first[0], new))
            fixed = new
    if p.second:
        allowed = set(p.second)
        bad = [(i, w) for i, w in _find(fixed, SECOND_PERSONS) if w not in allowed]
        # 決まった呼び方が代名詞でない（「みんな」だけ等）ときは、置き換えずに指摘だけする
        to = next((x for x in p.second if x in SECOND_PERSONS), None)
        if bad:
            swapped = _replace(fixed, bad, to) if to else None
            for _, w in bad:
                issues.append(PersonaIssue(index, "second_person", w, to or p.second[0], swapped))
            if swapped:
                fixed = swapped
    if p.calls and others:
        fixed = _fix_calls(index, fixed, p, others, issues)
    own = set(p.endings)
    bad_end = sorted((e for e in foreign if e not in own), key=len, reverse=True)
    if bad_end:
        out, last, changed = [], 0, False
        for m in _SENT.finditer(fixed):
            sent = m.group(0)
            core = sent.rstrip(_TRAIL)
            hit = next((e for e in bad_end if _ends_with(core, e)), None)
            if hit is not None:
                issues.append(PersonaIssue(index, "foreign_ending", hit, "", None))
                stripped = _strip_foreign(sent, hit)
                if stripped is not None:
                    out.append(fixed[last : m.start()] + stripped)
                    last = m.end()
                    changed = True
        if changed:
            fixed = "".join(out) + fixed[last:]
        # 文の途中で読点が続く形（「長持ちさせられるのだよ、貴女。」）も同じように直す
        for e in (x for x in bad_end if x.endswith("のだ")):
            pat = re.compile(rf"{re.escape(e)}([{''.join(sorted(_PARTICLES))}]?)(?=[、,])")
            if pat.search(fixed):
                issues.append(PersonaIssue(index, "foreign_ending", e, "", None))
                fixed = pat.sub(lambda m, e=e: e[:-1] + m.group(1), fixed)
                changed = True
        if changed:
            for x in issues:
                if x.kind == "foreign_ending":
                    x.fixed = fixed
    if p.endings:
        sents = [s.strip() for s in _SENT.findall(fixed) if s.strip()]
        # 相づち・感嘆だけの短い文（「えっ！」「うん」）は語尾の対象にしない
        eligible = [s for s in sents if len(s.rstrip(_TRAIL)) >= 5]
        missing = [s for s in eligible if not _ending_ok(s, p.endings)]
        if eligible and missing and len(missing) * 2 >= len(eligible):
            issues.append(PersonaIssue(index, "ending", missing[0][:40], "」「".join(p.endings)))
    return fixed, issues


def check_lines(
    lines: list[tuple[str, str]], personas: dict[str, Persona]
) -> tuple[list[str], list[PersonaIssue]]:
    """(話者, セリフ) の並びを調べる。戻り値は (規則で直したセリフの並び, 問題の一覧)。"""
    out: list[str] = []
    issues: list[PersonaIssue] = []
    for i, (speaker, text) in enumerate(lines):
        p = personas.get(speaker)
        if p is None or p.empty:
            out.append(text)
            continue
        fixed, iss = check_line(i, text, p, foreign_endings(personas, speaker), personas)
        out.append(fixed)
        issues += iss
    return out, issues


def foreign_endings(personas: dict[str, Persona], speaker: str) -> tuple[str, ...]:
    """speaker 以外のキャラクターに決まっている語尾。"""
    own = set(personas[speaker].endings) if speaker in personas else set()
    out = {e for n, p in personas.items() if n != speaker for e in p.endings if e not in own}
    return tuple(sorted(out))
