"""手描きの丸・虫めがね（統合版 N12・N13）と線のふるえ（F3）。"""

from PIL import Image, ImageDraw

from video_automation.integrations.ocr.engine import OcrLine, OcrResult, OcrWord
from video_automation.services.direction import slides as sl
from video_automation.services.rendering import n_marks as nm


def test_word_box_picks_the_spoken_word():
    res = OcrResult(1000, 500, [OcrLine([OcrWord("家計", 10, 10, 80, 40),
                                          OcrWord("の", 90, 10, 40, 40),
                                          OcrWord("固定", 130, 10, 80, 40),
                                          OcrWord("費", 210, 10, 40, 40)])])  # fmt: skip
    assert sl.word_box(res, sl.tokens("固定費を見直します")) == (130, 10, 120, 40)
    assert sl.word_box(res, sl.tokens("まったく別の話")) is None


def test_circle_draws_in_12_frames_and_jitters_every_3():
    fr = list(nm.circle_frames((400, 200), (150, 80, 100, 40), 30, "#FF0000", 1.0, 1))
    a = [im.getchannel("A").getbbox() for im in fr]
    assert a[0] is not None
    # 描き終わり（12f）までは線が伸びる
    assert (a[0][2] - a[0][0]) <= (a[11][2] - a[11][0])
    # 3 コマの間は同じ、次の 3 コマでずれる
    assert fr[12].tobytes() == fr[14].tobytes() and fr[14].tobytes() != fr[15].tobytes()


def test_magnifier_enlarges_the_word():
    slide = Image.new("RGB", (400, 200), (255, 255, 255))
    ImageDraw.Draw(slide).rectangle((195, 95, 205, 105), fill=(0, 0, 0))
    lens = nm.magnifier(slide, (190, 90, 20, 20), 80, 1.0)
    # 中心の黒い四角（10px）が 2.5 倍（25px）になる
    px = lens.load()
    cx = lens.size[0] // 2
    assert px[cx, cx][:3] == (0, 0, 0) and px[cx + 11, cx][:3] == (0, 0, 0)
    fr = list(nm.magnifier_frames((400, 200), lens, (190, 90, 20, 20), 20))
    assert fr[0].getchannel("A").getextrema()[1] < 255 and fr[-1].getbbox() is not None
