"""用途ごとの部品の決まり（統合版 段階4 の P4・P5・P7・P8）。台本の文字だけから決める。

台本に無い語・数値は作らない（材料・分量・順位・日付は台本の文字をそのまま使う）。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# ---------------- P4 料理の材料 ----------------

INGREDIENT_SEC = 2.0  # 材料が出てくる発話の頭に 2 秒
INGREDIENT_MAX = 2  # 同時に最大 2 つ
INGREDIENT_LIST_SEC = 5.0  # 最後の材料の一覧のカード

_NUMS = r"[0-9０-９]+(?:[./／][0-9０-９]+)?|[一二三四五六七八九十半]+"
_UNIT = (
    r"(?:g|ｇ|グラム|kg|キロ|ml|mL|ｍｌ|cc|ＣＣ|リットル|個|本|枚|片|かけ|株|束|袋|缶|"
    r"カップ|合|丁|尾|切れ|玉|房|粒|杯)"
)
_QTY = re.compile(
    rf"(?:大さじ|小さじ)\s*(?:{_NUMS})(?:と(?:{_NUMS}))?|(?:{_NUMS})\s*{_UNIT}|少々|適量|ひとつまみ"
)
_LEAD = re.compile(r"^(?:まず|まずは|次に|続いて|それから|そして|最後に|あと|さらに)")
_CUT = re.compile(r"[、。！？!?\s「」『』（）()・,，:：]")
_PARTICLE = re.compile(r"(?:を|は|が|も|に|と)$")


@dataclass(frozen=True)
class Ingredient:
    line: int  # 何番目の発話か（0 始まり）
    name: str
    amount: str

    @property
    def label(self) -> str:
        return f"{self.name} {self.amount}"


def ingredients(texts: list[str]) -> list[Ingredient]:
    """「材料名 分量」を台本から取り出す（例: 「玉ねぎを1個」→ 玉ねぎ 1個）。

    分量の前の語を材料名にする。名前が取れないときは数えない。1 つの発話から最大 2 つ。"""
    out: list[Ingredient] = []
    seen: set[str] = set()
    for i, t in enumerate(texts):
        k, prev = 0, 0
        for m in _QTY.finditer(t):
            head = _CUT.split(t[prev : m.start()])[-1]
            prev = m.end()
            head = re.split(r"(?<=[ぁ-んァ-ヶー一-龥])と(?=[ぁ-んァ-ヶー一-龥])", head)[-1]
            head = _PARTICLE.sub("", _LEAD.sub("", head.removeprefix("と")))
            if not 1 <= len(head) <= 10 or head in seen:
                continue
            seen.add(head)
            out.append(Ingredient(i, head, m.group(0).replace(" ", "")))
            k += 1
            if k >= INGREDIENT_MAX:
                break
    return out


_SERVES = re.compile(r"([0-9０-９]+|[一二三四五六七八九十]+)\s*人(?:分|前)")


def servings(texts: list[str]) -> str:
    """「〇人分」の見出し（台本に人数があるときだけ。無ければ「材料」）。"""
    for t in texts:
        m = _SERVES.search(t)
        if m:
            return f"材料（{m.group(1)}人分）"
    return "材料"


_STEP = re.compile(
    r"^(?:(?:STEP|ステップ|手順)\s*([0-9０-９]+)|まず|次に|続いて|それから|最後に|仕上げに)"
)


def steps(texts: list[str]) -> list[tuple[int, int]]:
    """手順の区切り [(発話の番号, STEP の番号)]。「まず・次に・最後に」か「手順 n」で始まる発話。

    番号が書いてあれば、その番号を使う。"""
    out: list[tuple[int, int]] = []
    n = 0
    for i, t in enumerate(texts):
        m = _STEP.match(t.strip())
        if not m:
            continue
        n = int(unicodedata.normalize("NFKC", m.group(1))) if m.group(1) else n + 1
        out.append((i, n))
    return out if len(out) >= 2 else []  # 区切りが 1 つだけなら手順ものではない


# ---------------- P5 ランキング ----------------

RANK_NUMBER_SEC = 2.0  # 大きな数字
RANK_BOARD_SEC = 3.0  # ここまでの一覧のボード
FIRST_PLACE_SILENCE_SEC = 0.8  # 1 位の前の無音
_RANK = re.compile(r"(?:第\s*)?([0-9０-９]+|[一二三四五六七八九十]+)\s*位")
_KANJI_NUM = {c: i for i, c in enumerate("〇一二三四五六七八九十")}


def _num(s: str) -> int:
    s = unicodedata.normalize("NFKC", s)
    if s.isdigit():
        return int(s)
    if s == "十":
        return 10
    if s.startswith("十"):
        return 10 + _KANJI_NUM.get(s[1:], 0)
    if "十" in s:
        a, b = s.split("十", 1)
        return _KANJI_NUM.get(a, 0) * 10 + (_KANJI_NUM.get(b, 0) if b else 0)
    return _KANJI_NUM.get(s, 0)


@dataclass(frozen=True)
class Rank:
    line: int
    number: int
    name: str  # 「第3位は京都」の「京都」（台本の語。無ければ空）


def ranks(texts: list[str]) -> list[Rank]:
    """順位の発表（発話の頭近くに「第 N 位」がある発話）。下位から 1 位へ並ぶときだけ。"""
    out: list[Rank] = []
    for i, t in enumerate(texts):
        m = _RANK.search(t)
        # 文の途中の「1位」（「売上1位の店」など）は発表ではない。前に置けるのは「いよいよ、」の
        # ような平仮名と記号だけで、後ろに「の」が続くものは数えない
        if not m or not re.fullmatch(r"[ぁ-んー、。！!？?\s]{0,8}", t[: m.start()]):
            continue
        if t[m.end() : m.end() + 1] == "の":
            continue
        n = _num(m.group(1))
        if n <= 0 or any(r.number == n for r in out):
            continue
        rest = re.sub(r"^[はがの、:：！!\s]+", "", t[m.end() :])
        name = re.split(r"[。！？!?、]|です|でした|だ$", rest, maxsplit=1)[0].strip()
        out.append(Rank(i, n, name[:12]))
    if len(out) < 2 or [r.number for r in out] != sorted((r.number for r in out), reverse=True):
        return []
    return out


def rank_pace(n: int) -> float:
    """1 項目の目安の秒数（統合版 P5）。"""
    return 3.0 if n >= 11 else 25.0 if n >= 4 else 37.0


RANKING_STRUCTURE = (
    "下位から 1 位へ順に発表する。各順位は「第 N 位は○○」で始める。"
    "1 項目の長さの目安は、11 位以下は 3 秒、4〜10 位は 25 秒、1〜3 位は 37 秒"
)

# ---------------- P8 予告編 ----------------

ACT_RATIOS = (0.30, 0.40, 0.30)
ACT2_SILENCE_SEC = 0.7
TRAILER_TITLE_SEC = 3.0
TRAILER_DATE_SEC = 2.0
TRAILER_CARDS = 3
TRAILER_STRUCTURE = (
    "3 幕で書く。第 1 幕（全体の 30%）は状況を落ち着いて、第 2 幕（40%）は事件と緊張、"
    "第 3 幕（30%）は短い言葉を畳みかける。題名は最後に出すので台本の冒頭で言わない"
)


def acts(durations: list[float]) -> list[int]:
    """各発話の幕（1〜3）。発話の長さの合計の 30%・70% で区切る。"""
    total = sum(durations) or 1.0
    out, cum = [], 0.0
    for d in durations:
        mid = (cum + d / 2) / total
        out.append(1 if mid < ACT_RATIOS[0] else 2 if mid < ACT_RATIOS[0] + ACT_RATIOS[1] else 3)
        cum += d
    return out


_DATE = re.compile(
    r"(?:[0-9０-９]{4}\s*年\s*)?[0-9０-９]{1,2}\s*月\s*[0-9０-９]{1,2}\s*日|"
    r"[0-9０-９]{4}\s*年\s*(?:春|夏|秋|冬|[0-9０-９]{1,2}\s*月)|"
    r"(?:近日|今冬|今夏|今春|今秋)公開|COMING SOON",
    re.IGNORECASE,
)


def date_text(texts: list[str]) -> str:
    """日付のカードの文字（台本に日付があるときだけ。最後に出てきたもの）。"""
    found = [m.group(0) for t in texts for m in _DATE.finditer(t)]
    return found[-1] if found else ""


# ---------------- 字幕の同期の無音（P5・P8） ----------------


def extra_gaps(purpose: str, texts: list[str], durations: list[float]) -> list[float]:
    """各発話の前に足す無音（秒）。ランキングは 1 位の前に 0.8 秒、予告編は第 2 幕の頭に 0.7 秒。"""
    out = [0.0] * len(texts)
    if purpose == "ranking":
        for r in ranks(texts):
            if r.number == 1 and r.line > 0:
                out[r.line] = FIRST_PLACE_SILENCE_SEC
    elif purpose == "trailer" and texts:
        a = acts(durations)
        if 2 in a and a.index(2) > 0:
            out[a.index(2)] = ACT2_SILENCE_SEC
    return out


# ---------------- P7 ふりがな ----------------

FURIGANA_GRADE = 3
RUBY_RATIO = 0.48  # 本文の 48%
RUBY_SPACE = 0.55  # 字幕の領域を上に（文字の大きさ × 0.55）広げる

# 学年別漢字配当表（小学校学習指導要領、平成 29 年告示）の 1〜3 年
GRADE_KANJI = {
    1: "一右雨円王音下火花貝学気九休玉金空月犬見五口校左三山子四糸字耳七車手十出女小上森人水正生"
    "青夕石赤千川先早草足村大男竹中虫町天田土二日入年白八百文木本名目立力林六",
    2: "引羽雲園遠何科夏家歌画回会海絵外角楽活間丸岩顔汽記帰弓牛魚京強教近兄形計元言原戸古午後語"
    "工公広交光考行高黄合谷国黒今才細作算止市矢姉思紙寺自時室社弱首秋週春書少場色食心新親図数西"
    "声星晴切雪船線前組走多太体台地池知茶昼長鳥朝直通弟店点電刀冬当東答頭同道読内南肉馬売買麦半"
    "番父風分聞米歩母方北毎妹万明鳴毛門夜野友用曜来里理話",
    3: "悪安暗医委意育員院飲運泳駅央横屋温化荷界開階寒感漢館岸起期客究急級宮球去橋業曲局銀区苦具"
    "君係軽血決研県庫湖向幸港号根祭皿仕死使始指歯詩次事持式実写者主守取酒受州拾終習集住重宿所暑"
    "助昭消商章勝乗植申身神真深進世整昔全相送想息速族他打対待代第題炭短談着注柱丁帳調追定庭笛鉄"
    "転都度投豆島湯登等動童農波配倍箱畑発反坂板皮悲美鼻筆氷表秒病品負部服福物平返勉放味命面問役"
    "薬由油有遊予羊洋葉陽様落流旅両緑礼列練路和",
}


def taught(grade: int = FURIGANA_GRADE) -> frozenset[str]:
    return frozenset("".join(v for g, v in GRADE_KANJI.items() if g <= grade))


def _is_kanji(c: str) -> bool:
    return "一" <= c <= "鿿" or c in "々〆ヶ"


def _kata(c: str) -> str:
    o = ord(c)
    return chr(o + 0x60) if 0x3041 <= o <= 0x3096 else c


_O_ROW, _E_ROW = set("おこごそぞとどのほぼぽもよろょ"), set("えけげせぜてでねへべぺめれ")


def _hira(s: str) -> str:
    """カタカナの読みを平仮名に。VOICEVOX は長音を母音で書くので（ボオケン）、お段の後の「お」は
    「う」、え段の後の「え」は「い」に戻す（ぼうけん・けいけん）。"""
    h = "".join(chr(ord(c) - 0x60) if 0x30A1 <= ord(c) <= 0x30F6 else c for c in s)
    out = []
    for i, c in enumerate(h):
        if i and c == "お" and h[i - 1] in _O_ROW:
            c = "う"
        elif i and c == "え" and h[i - 1] in _E_ROW:
            c = "い"
        out.append(c)
    return "".join(out)


# VOICEVOX のモーラの書き方の違い（助詞の「は」→ワ、長音 → 母音 など）
_ALT = {
    "ハ": "ハワ",
    "ヘ": "ヘエ",
    "ヲ": "ヲオ",
    "ウ": "ウオー",
    "イ": "イエー",
    "ヂ": "ヂジ",
    "ヅ": "ヅズ",
}


def _is_kana(c: str) -> bool:
    return "\u3041" <= c <= "\u3096" or "\u30a1" <= c <= "\u30fa" or c == "ー"


def _same(c: str, k: str) -> bool:
    """台本の仮名 c（カタカナにしたもの）とモーラの字 k が同じ音か。"""
    return c == "ー" or k == c or k in _ALT.get(c, "")


def _align(runs: list[tuple[str, str]], flat: str) -> list[str] | None:
    """runs（(種類, 字) の並び）を読み flat に当てる。漢字の続きの読み（1 字以上）を順に返す。

    正規表現の後戻りは長い文で時間がかかるので、届く位置の集合を前後から求めて決める
    （O(区切りの数 × 読みの長さ)）。"""
    n = len(flat)

    def step(kind: str, s: str, p: int) -> list[int]:
        if kind == "a":
            ok = p + len(s) <= n and all(_same(c, flat[p + i]) for i, c in enumerate(s))
            return [p + len(s)] if ok else []
        lo = p + (1 if kind == "k" else 0)
        return list(range(lo, n + 1))

    # 後ろから: その位置から最後まで当てられるか
    back: list[set[int]] = [set() for _ in range(len(runs) + 1)]
    back[-1] = {n}
    for i in range(len(runs) - 1, -1, -1):
        kind, s = runs[i]
        nxt = back[i + 1]
        if kind == "a":
            back[i] = {p for p in range(n + 1) if any(q in nxt for q in step(kind, s, p))}
        else:
            hi = max(nxt) if nxt else -1
            lo = 1 if kind == "k" else 0
            back[i] = set(range(0, hi - lo + 1)) if hi >= lo else set()
    if 0 not in back[0]:
        return None
    out, p = [], 0
    for i, (kind, s) in enumerate(runs):
        q = next(q for q in step(kind, s, p) if q in back[i + 1])  # いちばん短い読み
        if kind == "k":
            out.append(flat[p:q])
        p = q
    return out


def ruby(text: str, kana: str, grade: int = FURIGANA_GRADE) -> list[tuple[int, int, str]]:
    """ふりがなを付ける語 [(始まり, 終わり, 読み)]。text の文字の位置で、読みは平仮名。

    kana は VOICEVOX のモーラを並べた読み（カタカナ）。漢字の続きを 1 語とし、その中に
    対象の学年までに習わない漢字があるときだけ付ける。読みの位置が合わせられないときは付けない。"""
    runs: list[tuple[str, int, int]] = []  # (種類, 始まり, 終わり)
    for i, c in enumerate(text):
        kind = "k" if _is_kanji(c) else "a" if _is_kana(c) else "o"
        if runs and runs[-1][0] == kind:
            runs[-1] = (kind, runs[-1][1], i + 1)
        else:
            runs.append((kind, i, i + 1))
    if not any(k == "k" for k, _, _ in runs):
        return []
    flat = "".join(_kata(c) for c in re.sub(r"[、。/\s]", "", kana))
    if len(flat) > 400:
        return []
    readings = _align([(k, "".join(_kata(c) for c in text[a:b])) for k, a, b in runs], flat)
    if readings is None:
        return []
    known = taught(grade)
    out: list[tuple[int, int, str]] = []
    g = 0
    for kind, a, b in runs:
        if kind != "k":
            continue
        word = text[a:b]
        if any(c not in known and _is_kanji(c) for c in word):
            out.append((a, b, _hira(readings[g])))
        g += 1
    return out
