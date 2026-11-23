"""読み上げ用テキストの正規化と抑揚づけ（実機 Nemo で見つかった読み誤りを基にした回帰テスト）。"""

from video_automation.schemas.voice import AccentPhrase, Mora
from video_automation.services.voice.expressive import find_spans, kana_of, shape
from video_automation.services.voice.reading import (
    ReadingRuleData,
    emphasis_terms,
    normalize_for_speech,
)


def test_known_misreadings_are_fixed():
    # 実機: 六方晶→ロッポオ/アキラ、立方晶→リッポオ/アキラ、化合物→カゴオモノ
    t = "六方晶のh-BNと立方晶のc-BNは化合物です。"
    assert (
        normalize_for_speech(t)
        == "ロッポウショウのエイチビーエヌとリッポウショウのシービーエヌはカゴウブツです。"
    )


def test_abbreviations_units_and_brackets():
    t = "w-BNの強度は114GPa、『準安定』な相。AIとGPUで3.5kmと18%。"
    assert normalize_for_speech(t) == (
        "ダブリュービーエヌの強度は114ギガパスカル、準安定な相。"
        "エーアイとジーピーユーで3.5キロメートルと18パーセント。"
    )
    # 英単語の一部や小文字の語は変えない（エンジンの英語読みに任せる）
    assert normalize_for_speech("X-rayとPythonとiPhone") == "X-rayとPythonとiPhone"


def test_user_rules_override_builtin_and_longest_first():
    rules = [
        ReadingRuleData("六方晶", "ロッポーショー"),
        ReadingRuleData("窒化ホウ素", "チッカホウソ"),
    ]
    assert normalize_for_speech("六方晶窒化ホウ素", rules) == "ロッポーショーチッカホウソ"
    assert normalize_for_speech("六方晶", builtin=False) == "六方晶"


def test_emphasis_terms():
    t = "2009年の計算で114ギガパスカル、ダイヤモンドの97GPaを約18%上回る『予測』が出た。"
    assert emphasis_terms(t) == ["予測", "114ギガパスカル", "97GPa", "約18%"]
    assert emphasis_terms("2019年に3回目。") == []


def _ap(text, pitch=5.5, pause=False):
    return AccentPhrase(
        moras=[Mora(text=ch, vowel="a", vowel_length=0.1, pitch=pitch) for ch in text],
        accent=1,
        pause_mora=Mora(text="、", vowel="pau", vowel_length=0.2, pitch=0) if pause else None,
    )


def test_shape_declines_emphasizes_and_keeps_unvoiced():
    aps = [_ap("ソレワ"), _ap("ヒャクジュウヨン", pause=True), _ap("デシタ")]
    aps[2].moras[2].pitch = 0.0  # 無声化
    spans = find_spans(aps, ["ヒャクジュウヨン"])
    assert spans == {(1, j) for j in range(8)}
    out = shape(aps, strength=1.0, emphasis=spans)
    first_unit = [m.pitch for ap in out[:2] for m in ap.moras]
    assert first_unit[0] > first_unit[2]  # 区切りの頭が高い
    assert out[1].moras[0].pitch > 5.5 + 0.1  # 強調
    assert out[1].moras[0].vowel_length > 0.1
    assert out[2].moras[2].pitch == 0.0
    assert out[2].moras[0].pitch > out[2].moras[1].pitch  # 次の区切りでやり直す
    assert kana_of(out) == "ソレワヒャクジュウヨンデシタ"
    # 元のデータは変えない・強さ 0 は何もしない
    assert aps[1].moras[0].pitch == 5.5
    assert shape(aps, strength=0) is aps
    ex = shape([_ap("スゴイ")], strength=1.0, exclaim=True)
    assert sum(m.pitch for m in ex[0].moras) > 5.5 * 3


def test_join_split_words() -> None:
    from video_automation.schemas.voice import AccentPhrase, Mora
    from video_automation.services.voice.expressive import join_split_words

    def ap(kana: str, accent: int = 1, pause: bool = False) -> AccentPhrase:
        return AccentPhrase(
            moras=[Mora(text=k, vowel="a", vowel_length=0.1, pitch=5.5) for k in kana],
            accent=accent,
            pause_mora=Mora(text="、", vowel="pau", vowel_length=0.2, pitch=0) if pause else None,
        )

    phrases = [ap("シタ", 2), ap("カゴ", 1), ap("ウブ", 2), ap("ツデス", 1)]
    out, changed = join_split_words(phrases, ["カゴウブツ"])
    assert changed
    assert ["".join(m.text for m in p.moras) for p in out] == ["シタ", "カゴウブツデス"]
    assert out[1].accent == 1
    # 息継ぎを挟む場合はつながない
    same, changed2 = join_split_words([ap("カゴ", 1, pause=True), ap("ウブツ")], ["カゴウブツ"])
    assert not changed2 and len(same) == 2
    # 元の句は変えない
    assert len(phrases) == 4
