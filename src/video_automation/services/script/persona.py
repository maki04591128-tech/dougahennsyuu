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
from difflib import SequenceMatcher

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
    avoid: list[str] = field(default_factory=list)  # 文末に使わない言い方（「〜」は除く）
    examples: list[str] = field(default_factory=list)  # 手本のセリフ（規則ではなく参考）
    # 手本の決めゼリフの語（「白地図」「ずんだ餅」）。テーマと関係なく使ったら書き直させる
    signature: set[str] = field(default_factory=set)

    @property
    def empty(self) -> bool:
        return not (self.first or self.second or self.endings or self.calls or self.avoid)

    def rules_text(self) -> str:
        """台本を作るときに渡す、必ず守る規則の文。"""
        parts = []
        if self.first:
            parts.append(f"一人称は「{'」「'.join(self.first)}」だけ（他の一人称は使わない）")
        if self.second:
            parts.append(f"相手は「{'」「'.join(self.second)}」と呼ぶ")
        if self.endings:
            parts.append(f"文末の大半を「{'」「'.join(self.endings)}」にする（疑問文も含む）")
        if self.avoid:
            parts.append(f"文末に「{'」「'.join(self.avoid)}」は使わない")
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
        if self.kind == "example_copy":
            return f"手本のセリフの写し（{self.found}）を外しました"
        if self.kind == "grammar":
            return f"「{self.found}」を「{self.expected}」に直しました"
        if self.kind == "quote":
            return "セリフ全体を囲んでいた「」を外しました"
        if self.kind == "noda":
            return "「のだ」の付け方（「なるほどのだ」「やるののだ」）を直しました"
        if self.kind == "avoid":
            return f"このキャラクターが使わない言い方「{self.expected}」があります: {self.found}"
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
    m = re.search(r"((?:『[^』]+』)+)のような言い方はしない", text)
    if m:
        p.avoid = [e.lstrip("〜~ー") for e in re.findall(r"『([^』]+)』", m.group(1))]
        p.avoid = [e for e in p.avoid if e]
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


# 日本語の台本に出てはいけない文字（実機で「一 градус 上げる」が出た）
FOREIGN_SCRIPT = re.compile(
    r"[\u0370-\u03FF\u0400-\u04FF\u0590-\u05FF\u0600-\u06FF\u0E00-\u0E7F\uAC00-\uD7AF]"
)
# 中国語の簡体字（日本語に無い字）→ 日本語の字。実機で「细菌」「贵女」が出ていた
_SIMPLIFIED = dict(
    zip(
        "细贵间时说这对为门问题话语认识让进还过动发实现电车东长开关习书买卖钱银样种应该给经结网线"
        "红绿蓝级纸记讲计设证质页头脑气处务养饭饮鱼鸟马闻隐际陆难爱觉见观规视亲",
        "細貴間時説這対為門問題話語認識譲進還過動発実現電車東長開関習書買売銭銀様種応該給経結網線"
        "紅緑藍級紙記講計設証質頁頭脳気処務養飯飲魚鳥馬聞隠際陸難愛覚見観規視親",
        strict=True,
    )
)
_SIMPLIFIED_ONLY = re.compile("[们吗呢么]")


def fix_simplified(text: str) -> str:
    """中国語の簡体字を日本語の字に置き換える（小さなモデルが混ぜることがある）。"""
    return "".join(_SIMPLIFIED.get(ch, ch) for ch in text)


# どのキャラクターでも誤りになる語尾のつなぎ方（「しますのだ」「しなさいわ」）
BAD_JOINS = ("ますのだ", "ですのだ", "ましたのだ", "でしたのだ", "なさいわ", "なさいのだ")

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


_VERB_END = set("うくすつぬふむゆるぐぶぷたい")


def add_noda(sentence: str) -> str:
    """「のだ」の付いていない文に「のだ」を付ける（書き直しで直らなかったときの最後の手当て）。

    「28 度だ！」→「28 度なのだ！」、「消す！」→「消すのだ！」"""
    core = sentence.rstrip(_TRAIL)
    tail = sentence[len(core) :]
    if not core or core.endswith("のだ"):
        return sentence
    particle = ""
    if len(core) > 1 and core[-1] in "よね":
        core, particle = core[:-1], core[-1]
    if core.endswith(("ます", "です", "ました", "でした", "わ", "の")) or (
        core[-1] in "をにがはへとで" and not core.endswith("まで")
    ):
        # 丁寧語・ほかの語尾・助詞で終わる文（「習慣を！」）は、つなげると壊れるので触らない
        return sentence
    if core.endswith("だ"):
        core = core[:-1] + "なのだ"
    elif core[-1] in _VERB_END:
        core += "のだ"
    else:
        core += "なのだ"
    return core + particle + tail


def fill_endings(text: str, p: Persona) -> str:
    """語尾が「のだ」のキャラクターで、語尾の抜けた文に「のだ」を付ける。"""
    if not any(e.endswith("のだ") for e in p.endings):
        return text
    out, last = [], 0
    for m in _SENT.finditer(text):
        sent = m.group(0)
        if len(sent.strip().rstrip(_TRAIL)) >= 5 and not _ending_ok(sent.strip(), p.endings):
            out.append(text[last : m.start()] + add_noda(sent))
            last = m.end()
    return "".join(out) + text[last:]


# 3 文字以上の漢字・カタカナの語（「白地図」「カレーパン」）。2 文字の語は一般的な語と紛れる
_SIG_WORD = re.compile(r"[一-龥]{3,}|[ァ-ヺー]{3,}")
_SIG_STOP = {"センパイ", "オマエ", "アンタ"}  # 呼び方は口調の規則で扱う


def set_signature(p: Persona, topic: str) -> None:
    """手本のセリフに出てくる特徴的な語のうち、テーマに無いものを決めゼリフの語にする。"""
    words: set[str] = set()
    for ex in p.examples:
        for w in _SIG_WORD.findall(ex):
            if w not in _SIG_STOP and w not in p.name and w not in topic:
                words.add(w)
    p.signature = words


def _example_sentences(p: Persona) -> list[str]:
    out = []
    for ex in p.examples:
        for m in _SENT.finditer(ex):
            core = re.sub(r"[\s、。！？!?…]", "", m.group(0))
            if len(core) >= 6:
                out.append(core)
    return out


def _drop_copied_examples(index: int, text: str, p: Persona, issues: list[PersonaIssue]) -> str:
    exs = _example_sentences(p)
    if not exs:
        return text
    kept, dropped = [], []
    for m in _SENT.finditer(text):
        sent = m.group(0)
        core = re.sub(r"[\s、。！？!?…]", "", sent)
        copied = len(core) >= 6 and any(
            e in core or SequenceMatcher(None, e, core).ratio() >= 0.75 for e in exs
        )
        (dropped if copied else kept).append(sent)
    if not dropped:
        return text
    rest = "".join(kept).strip()
    if len(re.sub(r"[\s、。！？!?…]", "", rest)) >= 4:
        issues.append(PersonaIssue(index, "example_copy", dropped[0][:40], "", rest))
        return rest
    issues.append(PersonaIssue(index, "avoid", text[:40], "手本のセリフの写し"))
    return text


_GRAMMAR_FIXES = (
    (re.compile(r"てみまっす(?!ぐ)"), "てみるっす"),
    (re.compile(r"しまっす(?!ぐ)"), "するっす"),
    (re.compile(r"んだっすか"), "んすか"),
    (re.compile(r"んだっす"), "んす"),
)  # fmt: skip（「まっすぐ」は直さない）


def one_line(text: str) -> str:
    """セリフの中の改行を文の区切りにする（「…のだ\nそれなら…」→「…のだ。それなら…」）。"""
    # 行末に紛れ込んだ半角の引用符（「声が出ないのだ！'」）も外す
    # 太字などの Markdown の記号（「**変更する**」）も外す（字幕にそのまま出てしまう）
    text = re.sub(r"\*\*|__|`", "", text)
    parts = [x.strip().strip("'\"`") for x in re.split(r"[\r\n]+", text) if x.strip()]
    out = ""
    for x in parts:
        if out and out[-1] not in "。！？!?…、」』）)♪":
            out += "。"
        out += x
    return out


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
    fixed = one_line(text)
    # 行の頭に付いた見出し（「L3 ずんだもん: 」「[L5] 」）を外す
    names = [p.name, *(others or {})]
    m = re.match(r"\s*\[?[LT]\d+\]?\s*[:：]?\s*", fixed)
    if m and m.end() > 0 and re.match(r"\s*\[?[LT]\d", fixed):
        rest = fixed[m.end() :]
        labeled = bool(re.search(r"[\]:：]", m.group(0)))
        for n in sorted(names, key=len, reverse=True):
            mm = re.match(rf"{re.escape(n)}\s*[:：]\s*", rest)
            if mm:
                rest, labeled = rest[mm.end() :], True
                break
        if labeled and rest.strip():
            issues.append(PersonaIssue(index, "grammar", "行の見出し", "", rest))
            fixed = rest
    # 簡体字は日本語の字に置き換える（置き換えられない字は下の「日本語以外の文字」で知らせる）
    new = fix_simplified(fixed)
    if new != fixed:
        issues.append(PersonaIssue(index, "grammar", "簡体字", "日本語の字", new))
        fixed = new
    # 形容詞の語幹に「かしら」を付ける誤り（「触り心地良かしら」→「良いかしら」）
    # （「最高かしら」のような名詞もあるので「良」だけ）
    new = fixed.replace("良かしら", "良いかしら").replace("にくくなりやすい", "にくくなる")
    # 「上がりるわ」→「上がるわ」（連用形に「る」を重ねる誤り）
    new = re.sub(r"([ぁ-ゖ])りる(?=[わよのね])", r"\1る", new)
    # 「防ぐのわ」→「防ぐのよ」
    new = re.sub(r"のわ(?=[。！？!?]|$)", "のよ", new)
    # 「〜っす」の付け方の誤り（「してみまっす」「勉強するんだっすか」）
    for pat, to in _GRAMMAR_FIXES:
        new = pat.sub(to, new)
    if new != fixed:
        issues.append(PersonaIssue(index, "grammar", "言い回し", "正しい形", new))
        fixed = new
    # 手本のセリフ（公式の決めゼリフなど）をそのまま写した文は外す（実機で解説の途中に
    # 「すべての白地図を四国に染める女よ」「埼玉、いいところだよ」が出ていた）
    if p.examples:
        fixed = _drop_copied_examples(index, fixed, p, issues)
    # セリフ全体を「」で囲む癖（「「ブルーライトが…」」）は外す
    m = re.fullmatch(r"\s*[「『]([^「」『』]+)[」』]\s*", fixed)
    if m:
        fixed = m.group(1)
        issues.append(PersonaIssue(index, "quote", "「」", "", fixed))
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
    # 「退治するのよ、わ」のように、言い終えた後に 1 文字の語尾を読点で足す癖も外す
    for e in ("わ", "よ", "ね"):
        m = re.search(rf"([よねわのだ])[、,]\s*{e}[。！!]?\s*$", fixed)
        if m:
            fixed = fixed[: m.start() + 1]
            issues.append(PersonaIssue(index, "stray_ending", e, "", fixed))
            break
    # 「のだ」の付け方の誤り（「やるののだ」「なるほどのだ」）を直す
    if any(e.endswith("のだ") for e in p.endings):
        new = re.sub(r"のの(だ)", r"の\1", fixed)
        new = re.sub(
            r"(なるほど|そう|本当|ほんと|大丈夫|簡単|便利|大事|大切|おはよう|ありがとう)のだ",
            r"\1なのだ",
            new,
        )
        # 形容詞・「〜たい」の後は「のだ」（「食べたいなのだ」→「食べたいのだ」）
        new = re.sub(r"(たい|ない|しい|いい|よい|かった)なのだ", r"\1のだ", new)
        # 命令形＋「なのだ」（「備えろなのだ」「入れておけなのだ」）を直す
        new = re.sub(r"([えけげせぜてでねべめれ])ろなのだ", r"\1るのだ", new)
        new = re.sub(r"ておけなのだ", "ておくのだ", new)
        new = re.sub(r"(聞|書|置|行|開|働|抜|引|続|向)けのだ", r"\1くのだ", new)
        # 「知ってるのだか？」→「知ってるのだ？」（「のだから」は直さない）
        new = re.sub(r"のだか(?=[？?。！!]|$)", "のだ", new)
        # 「試してみななのだ」→「試してみるのだ」
        new = re.sub(r"てみななのだ", "てみるのだ", new)
        # 「始めようのだ」→「始めるのだ」、「しようのだ」→「するのだ」
        new = re.sub(r"しようのだ", "するのだ", new)
        new = re.sub(r"(てみ|見)ようのだ", r"\1るのだ", new)
        new = new.replace("くんなのだ", "くのだ").replace("ごらんのだ", "ほしいのだ")
        new = re.sub(r"([えけげせぜてでねべめれ])ようのだ", r"\1るのだ", new)
        # 終助詞の後に「のだ」を重ねる誤り（「気をつけてねのだ」「深呼吸だぜのだ」「守ろうねのだ」）
        new = re.sub(r"([てで])[よね]のだ", r"\1ほしいのだ", new)
        new = re.sub(r"だ[ぜよね]?のだ", "なのだ", new)
        new = re.sub(r"てみなのだ", "てみるのだ", new)
        new = re.sub(r"てくれないのだ(?=[！!])", "てほしいのだ", new)
        new = re.sub(r"(みんな|オマエたち|オマエら)なのだ(?=[！!、])", r"\1", new)
        new = re.sub(r"^(朝|昼|夜|今日|明日)のだ、", r"\1は、", new)
        # 名詞にそのまま「のだ」（「大敵のだ」）→「なのだ」、「食べするのだ」→「食べるのだ」
        new = re.sub(r"([一-龥ァ-ヺ])のだ", r"\1なのだ", new)
        new = re.sub(r"([べめれけせてね])するのだ", r"\1るのだ", new)
        new = re.sub(
            r"([こそとのほもろごぞどぼぽ])うね?のだ",
            lambda m: (
                "くすつぬふむるぐずづぶぷ"["こそとのほもろごぞどぼぽ".index(m.group(1))] + "のだ"
            ),
            new,
        )
        new = re.sub(r"([うくすつぬむるぐぶ])[ぜわ]のだ", r"\1のだ", new)
        # 「聞いてなのだ」→「聞いてほしいのだ」
        new = re.sub(r"([てで])なのだ", r"\1ほしいのだ", new)
        # 「疲れたのかいなのだ？」「痛くないのかのだ」→「疲れたのだ？」「痛くないのだ？」
        new = re.sub(r"のかい?な?のだ([？?]?)", lambda m: "のだ" + (m.group(1) or "？"), new)
        if new != fixed:
            issues.append(PersonaIssue(index, "noda", "のだ", "なのだ", new))
            fixed = new
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
    if FOREIGN_SCRIPT.search(fixed) or _SIMPLIFIED_ONLY.search(fixed):
        issues.append(PersonaIssue(index, "avoid", fixed[:40], "日本語以外の文字"))
    bad_join = next((b for b in BAD_JOINS if b in fixed), None)
    if bad_join is not None:
        issues.append(PersonaIssue(index, "avoid", fixed[:40], bad_join))
    if "しろ" in p.avoid:
        # 命令口調を使わないキャラクター（めたん）の「水分を取れよ」「早く寝ろ」
        for sent in (x.strip() for x in _SENT.findall(fixed) if x.strip()):
            core = sent.rstrip(_TRAIL)
            if re.search(r"(?:[一-龥][けげせべめれ]|ろ)よ?$", core) and not re.search(
                r"(?:ところ|こころ|ころ|いろ|ひろ|しろ)よ?$", core
            ):
                issues.append(PersonaIssue(index, "avoid", sent[:40], "命令口調"))
                break
    hit_sig = next((w for w in sorted(p.signature) if w in fixed), None)
    if hit_sig is not None:
        issues.append(PersonaIssue(index, "avoid", fixed[:40], f"決めゼリフの語「{hit_sig}」"))
    if p.avoid:
        # 文末と、読点で区切った句の終わり（「ご存じですわ、アンタ。」）を見る
        parts = [x for x in re.split(r"(?<=[、,])", fixed) if x.strip()]
        avoid = list(p.avoid)
        if "ます" in avoid:  # です・ます調を使わないキャラクターは、丁寧語の言い切りも使わない
            avoid += ["ましょう", "ません", "ました", "でした", "のです"]
        for part in (y.strip() for x in parts for y in _SENT.findall(x) if y.strip()):
            core = part.rstrip(_TRAIL)
            hit = next((e for e in avoid if _ends_with(core, e) or core.endswith(e + "か")), None)
            if hit is not None:
                issues.append(PersonaIssue(index, "avoid", part[:40], hit))
                break
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
