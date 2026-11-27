"""画面録画の仕上げ（W3）と OCR の部品。"""

import sys

import pytest

from video_automation.integrations.ocr.engine import (
    FakeOcr,
    OcrLine,
    OcrResult,
    OcrUnavailable,
    OcrWord,
    WindowsOcr,
    join_words,
    parse,
)
from video_automation.services.screencast import finish as fin


def _line(*words: str, y: float = 10.0) -> OcrLine:
    out, x = [], 0.0
    for w in words:
        out.append(OcrWord(w, x, y, 10.0 * len(w), 20.0))
        x += 10.0 * len(w) + 5
    return OcrLine(out)


def test_find_pii_email_key_phone_and_boxes() -> None:
    res = OcrResult(
        800,
        600,
        [
            _line("連", "絡", "先", ":", "taro.yamada@example.co.jp"),
            _line("key", "=", "sk-abcdefghij0123456789", y=50),
            _line("電", "話", "090-1234-5678", y=90),
            _line("AKIAABCDEFGHIJKLMNOP", y=130),
            _line("2024-01-02", "12:30", y=170),  # 日付・時刻は電話番号ではない
            _line("sk-short", y=210),
        ],
    )
    hits = fin.find_pii(res)
    kinds = sorted(h.kind for h in hits)
    assert kinds == ["email", "phone", "secret", "secret"]
    mail = next(h for h in hits if h.kind == "email")
    assert mail.text == "taro.yamada@example.co.jp"
    # 語の外接矩形（メールの語だけ。前の「連絡先:」は含まない）
    assert mail.box[0] > 30 and mail.box[1] == 10.0
    assert fin.mask(mail.text).startswith("ta") and "yamada" not in fin.mask(mail.text)


def test_track_interpolates_and_extends_one_step() -> None:
    h1 = fin.Hit("email", "a@b.cc", (0.0, 0.0, 10.0, 10.0))
    h2 = fin.Hit("email", "a@b.cc", (20.0, 0.0, 10.0, 10.0))
    tracks = fin.track([(1.0, [h1]), (1.5, [h2]), (2.0, []), (5.0, [h1])])
    assert len(tracks) == 2
    first = tracks[0]
    assert first.t0 == pytest.approx(0.5) and first.t1 == pytest.approx(2.0)
    ((x, _, w, _),) = fin.boxes_at(tracks, 1.25)
    assert x == pytest.approx(10.0 - fin.BLUR_PAD) and w == pytest.approx(10 + 2 * fin.BLUR_PAD)
    assert fin.boxes_at(tracks, 3.0) == []
    assert len(fin.boxes_at(tracks, 5.4)) == 1


def test_fast_ranges_need_still_and_silent() -> None:
    motion = [(t / 5, 0.1 if t in (1, 2, 30) else 0.0) for t in range(1, 40)]
    # 画面は 0.4〜5.8 秒と 6.0 秒〜最後が止まっている。声は 3 秒まで
    fast = fin.fast_ranges(motion, [(3.0, 8.0)], 8.0)
    assert fast == [(3.0, pytest.approx(5.8)), (6.0, 8.0)]
    assert fin.fast_ranges(motion, [(5.0, 6.0)], 8.0) == []  # 重なりが 2 秒未満
    segs = fin.speed_segments(fast[:1], 8.0)
    assert [s[2] for s in segs] == [1, 4, 1]
    af = fin.audio_filter(segs)
    assert af.count("atempo=2") == 2 and "concat=n=3:v=0:a=1[aout]" in af


def test_keys_hide_typing_bursts() -> None:
    keys = (
        [(1.0, "Ctrl+S")] + [(3.0 + i * 0.1, c) for i, c in enumerate("hello")] + [(6.0, "Enter")]
    )
    vis = fin.visible_keys(keys)
    assert [k for _, k in vis] == ["Ctrl+S", "Enter"]
    assert fin.key_at(vis, 2.7) == "Ctrl+S" and fin.key_at(vis, 2.9) is None


def test_cursor_interpolates() -> None:
    cur = [(1.0, 0.0, 0.0), (2.0, 100.0, 50.0)]
    assert fin.cursor_at(cur, 1.5) == (50.0, 25.0)
    assert fin.cursor_at(cur, 0.6) == (0.0, 0.0)
    assert fin.cursor_at(cur, 3.0) is None


def test_ocr_parse_join_and_fake() -> None:
    assert join_words(["画", "面", "Save", "As", "。"]) == "画面Save As。"
    res = parse(
        [{"w": 10, "h": 5, "lines": [{"words": [{"t": "A", "x": 1, "y": 2, "w": 3, "h": 4}]}]}]
    )
    assert res[0].lines[0].box == (1.0, 2.0, 3.0, 4.0) and res[0].text == "A"
    from pathlib import Path

    fake = FakeOcr({"f1": res[0]})
    out = fake.read([Path("f1.jpg"), Path("f2.jpg")])
    assert out[0].text == "A" and out[1].lines == []


@pytest.mark.skipif(sys.platform == "win32", reason="Windows では本物を使う")
def test_windows_ocr_unavailable_elsewhere(tmp_path) -> None:
    with pytest.raises(OcrUnavailable):
        WindowsOcr().read([tmp_path / "x.png"])
    assert WindowsOcr().read([]) == []
