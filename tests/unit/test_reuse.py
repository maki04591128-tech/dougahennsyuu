"""統合版 U23: 歌詞の画面の要素を N11（引用）に流用する。"""

import numpy as np

from video_automation.services.rendering import lyric_screen as ls
from video_automation.services.rendering import reuse as ru

TEXT = "テキストサンプルテキストサンプルテキストサンプル"  # 24 字 → 3 列


def _alpha(im):
    return np.asarray(im.getchannel("A"))


def test_reuse_table_uses_known_elements():
    for r in ru.REUSE:
        for code in r.codes:
            assert code in ls.ELEMENTS, (r.target, code)
    assert {r.target for r in ru.REUSE if r.drawn} == {"N11"}


def test_quote_vertical_stacks_columns_and_keeps_them():
    w, h = 960, 540
    fr = list(ru.quote_vertical(w, h, TEXT, "A", lambda n: None, "#FFFFFF", "#E0193A", 60, True))
    assert len(fr) == 60
    ink = [int((_alpha(f) > 0).sum()) for f in fr]
    # UT5: 列が 12f ずつ増え、出た列は残る
    assert ink[0] > 0 and ink[0] < ink[13] < ink[25]
    assert all(a <= b for a, b in zip(ink, ink[1:], strict=False))
    # UH12: 左の 3 分の 1 には何も描かない
    assert (_alpha(fr[-1])[:, : w // 3] == 0).all()


def test_quote_vertical_key_word_on_right_for_story():
    w, h = 960, 540
    text = "テキストサンプルの光"
    fr = list(ru.quote_vertical(w, h, text, "", lambda n: None, "#FFFFFF", "#E0193A", 20, False))
    rgb = np.asarray(fr[5].convert("RGB"))
    a = _alpha(fr[5]) > 200
    # 最初のフレームには鍵の語（差し色）だけが右端に出ている
    xs = np.nonzero(a.any(axis=0))[0]
    assert xs.min() > w * 0.6
    red = (rgb[..., 0] > 180) & (rgb[..., 1] < 90) & a
    assert red.sum() > 0
    assert ru.fits_vertical(text, w, h, False)
    assert not ru.fits_vertical("テキストサンプル" * 20, w, h, True)


def test_quote_vertical_key_word_is_the_end_of_the_quote():
    """PC で見つけた不具合: 鍵の語に長い語の頭 4 字が出ていた。終わりの 4 字を使う。"""
    import inspect

    src = inspect.getsource(ru.quote_vertical)
    assert "last_word(text)[-4:]" in src
    assert ls.last_word("テキストサンプルテキ光")[-4:] == "ルテキ光"
