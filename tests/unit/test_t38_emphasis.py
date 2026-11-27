"""統合版 T38: 1 文に 1 つの強調（最も強い句を含む文節だけ accent・×1.15）。"""

from video_automation.schemas.voice import AudioQuery
from video_automation.services.direction.service import T38_PURPOSES, t38_hit
from video_automation.services.voice.timing import strongest_phrase_span


def _q(*phrases: list[float]) -> dict:
    return {
        "accent_phrases": [
            {
                "moras": [
                    {"text": "ア", "vowel": "a", "vowel_length": 0.1, "pitch": p} for p in ps
                ],
                "accent": 1,
            }
            for ps in phrases
        ]
    }


def test_span_is_share_of_moras() -> None:
    q = AudioQuery.model_validate(_q([5.5, 5.6], [5.4, 6.1, 5.8, 5.7], [5.2, 5.1]))
    assert strongest_phrase_span(q) == (0.25, 0.75)
    assert strongest_phrase_span(AudioQuery.model_validate({"accent_phrases": []})) is None


def test_hit_only_the_phrase_with_strongest_accent() -> None:
    q = _q([5.5, 5.6], [5.4, 6.1, 5.8, 5.7], [5.2, 5.1])  # 真ん中の半分が最も強い
    text = "今日は新しい機能を紹介します"  # 14 字: 3.5〜10.5 字目
    assert t38_hit(text, "新しい機能", q)
    assert not t38_hit(text, "今日", q)
    assert not t38_hit(text, "します", q)
    assert not t38_hit(text, "無い語", q)  # 文に無い文節
    assert not t38_hit(text, "新しい機能", None)  # 音声が無い


def test_not_sentence_skips_only_where_t36_is_used() -> None:
    q = _q([6.5, 6.6], [5.4, 5.1], [5.2, 5.1])  # 先頭が最も強い
    text = "紙ではなく画面で見ます"
    assert not t38_hit(text, "紙", q, strike=True)
    assert t38_hit(text, "紙", q, strike=False)


def test_purposes() -> None:
    assert len(T38_PURPOSES) == 13
