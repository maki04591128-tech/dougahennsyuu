"""読み上げ用テキストの正規化（読み間違い対策）。

字幕に出す文はそのままにし、音声合成に渡す文だけを読みやすい形に変える。

1. 読みのルール（利用者・AI が登録した「表記 → カタカナ」）を長い表記から順に置き換える
2. 既知の読み誤り（例: 「六方晶」を「ロッポオ・アキラ」と読む）を内蔵ルールで直す
3. 「w-BN」「GPa」のような英字の略語・単位をカタカナにする
   （エンジンはハイフンを区切りとして読み、略語の途中に間が空くため）
4. 『』「」などの括弧を外す（括弧の前後で不自然な間が入るため）
5. 飾りの記号（♪☆★♡†、絵文字）を外し、「……」「――」は読点にして間を空け、
   「！！」「？！」のような続けた記号は 1 文字にする

実機 VOICEVOX Nemo 0.26 で、解説動画の台本 17 発話の読みを確認して見つかった誤りを基にしている。
"""

import re
from dataclasses import dataclass

LETTERS = {
    "A": "エー", "B": "ビー", "C": "シー", "D": "ディー", "E": "イー", "F": "エフ",
    "G": "ジー", "H": "エイチ", "I": "アイ", "J": "ジェー", "K": "ケー", "L": "エル",
    "M": "エム", "N": "エヌ", "O": "オー", "P": "ピー", "Q": "キュー", "R": "アール",
    "S": "エス", "T": "ティー", "U": "ユー", "V": "ブイ", "W": "ダブリュー", "X": "エックス",
    "Y": "ワイ", "Z": "ゼット",
}  # fmt: skip

# 数字の後ろの単位（大文字小文字を区別する）
UNITS = {
    "GPa": "ギガパスカル", "MPa": "メガパスカル", "kPa": "キロパスカル", "hPa": "ヘクトパスカル",
    "Pa": "パスカル", "GHz": "ギガヘルツ", "MHz": "メガヘルツ", "kHz": "キロヘルツ", "Hz": "ヘルツ",
    "km": "キロメートル", "cm": "センチメートル", "mm": "ミリメートル", "μm": "マイクロメートル",
    "nm": "ナノメートル", "m": "メートル", "kg": "キログラム", "mg": "ミリグラム", "g": "グラム",
    "kW": "キロワット", "W": "ワット", "kV": "キロボルト", "V": "ボルト", "mA": "ミリアンペア",
    "A": "アンペア", "GB": "ギガバイト", "MB": "メガバイト", "TB": "テラバイト", "KB": "キロバイト",
    "fps": "エフピーエス", "K": "ケルビン", "°C": "ド", "℃": "ド", "%": "パーセント",
}  # fmt: skip

# 実機で確認した読み誤り（エンジンの辞書に無い複合語）。利用者のルールが優先する
BUILTIN_RULES: dict[str, str] = {
    "六方晶": "ロッポウショウ",
    "立方晶": "リッポウショウ",
    "正方晶": "セイホウショウ",
    "斜方晶": "シャホウショウ",
    "単斜晶": "タンシャショウ",
    "三方晶": "サンポウショウ",
    "三斜晶": "サンシャショウ",
    "化合物": "カゴウブツ",
    "結晶系": "ケッショウケイ",
    # VOICEVOX のキャラクター名（公式サイトのルビ）。一般語と同じ字の名前（雌雄など）は
    # 名字と合わせた形だけにする
    "剣崎雌雄": "ケンザキメスオ",
    "黒沢冴白": "クロサワコハク",
    "冴白": "コハク",
    "雀松朱司": "ワカマツアカシ",
    "雀松": "ワカマツ",
    "朱司": "アカシ",
    "麒ヶ島宗麟": "キガシマソウリン",
    "麒ヶ島": "キガシマ",
    "宗麟": "ソウリン",
    "†聖騎士 紅桜†": "ホーリーナイトベニザクラ",
    "聖騎士 紅桜": "ホーリーナイトベニザクラ",
    "離途": "リト",
    "冥鳴ひまり": "メイメイヒマリ",
    "冥鳴": "メイメイ",
    "暁記ミタマ": "アカツキミタマ",
    "暁記": "アカツキ",
    "夜語トバリ": "ヨガタリトバリ",
    "夜語": "ヨガタリ",
    "琴詠ニア": "コトヨミニア",
    "琴詠": "コトヨミ",
    "後鬼": "ゴキ",
    "No.7": "ナンバーセブン",
    "WhiteCUL": "ホワイトカル",
    "Voidoll": "ボイドール",
    "ナースロボ＿タイプＴ": "ナースロボタイプティー",
}

BRACKETS = "『』「」【】〈〉《》〔〕"
_KATAKANA = re.compile(r"^[ァ-ヴー・]+$")
# 英字 1〜3 文字 + ハイフン + 英大文字（w-BN, h-BN, c-BN）。X-ray のような小文字の語は対象外
_HYPHEN_ABBR = re.compile(r"(?<![A-Za-z])([A-Za-z]{1,3})-([A-Z]{1,6})(?![A-Za-z])")
# 2〜6 文字の大文字略語（BN, AI, GPU）。後ろに英小文字が続く単語の一部は対象外
_ACRONYM = re.compile(r"(?<![A-Za-z])([A-Z]{2,6})(?![A-Za-z])")
_NUM = r"\d+(?:[.,]\d+)?"
_UNIT_RE = re.compile(
    rf"({_NUM})\s*("
    + "|".join(sorted((re.escape(u) for u in UNITS), key=len, reverse=True))
    + r")(?![A-Za-z])"
)


@dataclass(frozen=True)
class ReadingRuleData:
    surface: str
    reading: str


def is_katakana(text: str) -> bool:
    return bool(_KATAKANA.match(text))


def spell(letters: str) -> str:
    return "".join(LETTERS.get(ch.upper(), ch) for ch in letters)


def normalize_for_speech(
    text: str, rules: list[ReadingRuleData] | None = None, builtin: bool = True
) -> str:
    """音声合成に渡す文を作る（字幕には元の文を使う）。"""
    table: dict[str, str] = dict(BUILTIN_RULES) if builtin else {}
    for r in rules or []:
        table[r.surface] = r.reading
    out = text
    for surface in sorted(table, key=len, reverse=True):
        if surface and surface in out:
            out = out.replace(surface, table[surface])
    out = _UNIT_RE.sub(lambda m: f"{m.group(1)}{UNITS[m.group(2)]}", out)
    out = _HYPHEN_ABBR.sub(lambda m: spell(m.group(1)) + spell(m.group(2)), out)
    out = _ACRONYM.sub(lambda m: spell(m.group(1)), out)
    for b in BRACKETS:
        out = out.replace(b, "")
    return clean_symbols(out)


_DECOR = re.compile(r"[♪♫♬☆★♡♥❤†‡※＿\u2600-\u27BF\U0001F000-\U0001FAFF\uFE0F]")
_PAUSE = re.compile(r"(?:…|‥|\.\.\.){1,}|[―—─]{2,}")
_MARKS = re.compile(r"[！!？?]{2,}")


def clean_symbols(text: str) -> str:
    """読み上げで余計な間や読み誤りになる記号を整える（字幕の文には使わない）。"""
    out = _DECOR.sub("", text)
    out = _PAUSE.sub("、", out)
    out = _MARKS.sub(lambda m: "？" if any(c in "？?" for c in m.group(0)) else "！", out)
    out = re.sub(r"、{2,}", "、", out)
    out = re.sub(r"^、|、(?=[。！？!?])", "", out)
    return out


_EMPH_BRACKET = re.compile(r"[『「【]([^』」】]{1,20})[』」】]")
_EMPH_NUMBER = re.compile(rf"(?:約)?{_NUM}(?:[%％]|[ァ-ヴー]{{1,8}}|[A-Za-zμ°℃]{{1,4}})?")


def emphasis_terms(text: str) -> list[str]:
    """強調して読む語（括弧で囲んだ語・数値と単位）。字幕の元の文から取り出す。"""
    terms = [m.group(1) for m in _EMPH_BRACKET.finditer(text)]
    for m in _EMPH_NUMBER.finditer(text):
        t = m.group(0)
        # 「2009年」のような年号・日付は強調しない（説明の数値を立てる）
        if re.fullmatch(rf"{_NUM}(?:年|月|日|時|分|秒|回|人|つ|個|本)?", t):
            continue
        terms.append(t)
    seen: list[str] = []
    for t in terms:
        if t not in seen:
            seen.append(t)
    return seen[:6]
