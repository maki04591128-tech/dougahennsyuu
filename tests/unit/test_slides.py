"""統合版 P3: スライドの割り当て・寄る先・箇条書き。"""

from PIL import Image

from video_automation.integrations.ocr.engine import OcrLine, OcrResult, OcrWord
from video_automation.services.direction import slides as sl


def _page(*lines: str) -> OcrResult:
    out = []
    for i, t in enumerate(lines):
        out.append(OcrLine([OcrWord(t, 100.0, 100.0 + 120 * i, 40.0 * len(t), 60.0)]))
    return OcrResult(1920, 1080, out)


P1 = _page("家計の基本", "・固定費を見直す", "・変動費を記録する")
P2 = _page("まとめ", "貯金の習慣")


def test_tokens_kanji_katakana_alnum() -> None:
    assert sl.tokens("まず固定費をチェックしてAPIのv2を見る") == {"固定費", "チェック", "api", "v2"}


def test_assign_keeps_order_and_best_line() -> None:
    texts = [
        "家計の基本を説明します",
        "固定費を見直そう",
        "変動費の記録",
        "最後のまとめと貯金の習慣",
    ]
    toks = [sl.tokens(t) for t in texts]
    assert sl.assign(toks, [P1, P2]) == [0, 0, 0, 1]
    # 戻らない: 後の文が 1 枚目の語を言っても 2 枚目のまま
    toks2 = toks + [sl.tokens("固定費")]
    assert sl.assign(toks2, [P1, P2])[-1] == 1
    assert sl.best_line(P1, toks[1]) == 1 and sl.best_line(P1, sl.tokens("こんにちは")) is None
    # 一致が無ければ文の数で等分
    assert sl.assign([set(), set(), set(), set()], [P1, P2]) == [0, 0, 1, 1]


def test_plan_shots_reveal_bullets_in_speaking_order() -> None:
    texts = ["家計の基本です", "変動費を記録します", "固定費も見直します", "まとめと貯金の習慣"]
    toks = [sl.tokens(t) for t in texts]
    frames = [(0, 60), (60, 120), (120, 180), (180, 240)]
    shots = sl.plan_shots([0, 0, 0, 1], frames, [P1, P2], toks, 240)
    assert [(s.page, s.start, s.end, s.hide) for s in shots] == [
        (0, 0, 60, [1, 2]),
        (0, 60, 120, [1]),  # 2 項目めを先に話した
        (0, 120, 180, []),
        (1, 180, 240, []),
    ]


def test_pan_centers_target() -> None:
    assert sl.pan_for(0.5, 0.5) == (0.0, 0.0)
    px, py = sl.pan_for(0.1, 0.9)
    assert px == -1.0 and py == 1.0
    # 中心 0.4: 見える幅 0.8、左端 0.0 → pan -1 ではなく範囲内の値
    x, _ = sl.pan_for(0.45, 0.5)
    assert -1 < x < 0


def test_cover_uses_background_and_fit_aspect(tmp_path) -> None:
    src = tmp_path / "p.png"
    img = Image.new("RGB", (400, 300), (250, 240, 220))
    img.paste((0, 0, 0), (50, 100, 300, 130))
    img.save(src)
    lines = [OcrLine([OcrWord("・項目", 50, 100, 250, 30)])]
    out = sl.cover(src, tmp_path / "c.png", lines, [0])
    px = Image.open(out).getpixel((150, 115))
    assert px == (250, 240, 220)
    sl.fit_aspect(src, 16 / 9)
    w, h = Image.open(src).size
    assert abs(w / h - 16 / 9) < 0.01 and Image.open(src).getpixel((2, 150)) == (250, 240, 220)


def test_tachie_is_eight_percent() -> None:
    tr = sl.tachie_transform(1920, 1080, 0.78 * 1080, 0.64 * 1080)
    k = tr["scale"]
    area = 0.55 * (0.64 * 1080 * k) ** 2
    assert abs(area / (1920 * 1080) - 0.08) < 0.002
    assert tr["x"] > 0 and tr["y"] > 0
