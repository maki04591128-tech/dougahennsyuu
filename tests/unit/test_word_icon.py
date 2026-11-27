"""統合版 T28: 語が図に変わる。"""

from PIL import Image, ImageDraw

from video_automation.services.rendering import word_icon as wi


def test_dictionary_and_resample() -> None:
    assert wi.icon_for("電池の持ち") == "battery" and wi.icon_for("今日の天気") is None
    pts = wi.resample([(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)], 8)
    assert len(pts) == 8 and pts[0] == (0.0, 0.0) and pts[2] == (10.0, 0.0)


def test_morph_hold_then_icon() -> None:
    fr = list(wi.morph_frames("電池", "battery", 30, 40, None, "#FFFFFF", "#2A6FDB"))
    assert len(fr) == 30 and len({im.size for im in fr}) == 1
    assert fr[0].tobytes() == fr[wi.HOLD - 1].tobytes()  # 12f 静止
    assert fr[wi.HOLD + 4].tobytes() != fr[0].tobytes()  # 変形中
    last = fr[-1]
    colors = {last.getpixel((x, y))[:3] for x in range(last.width) for y in range(last.height)
              if last.getpixel((x, y))[3] > 250}  # fmt: skip
    assert (0x2A, 0x6F, 0xDB) in colors and (255, 255, 255) not in colors  # 図は main・文字は無い


def test_card_morph_keeps_card_and_centers_icon() -> None:
    card = Image.new("RGBA", (300, 80), (0, 0, 0, 0))
    d = ImageDraw.Draw(card)
    d.rectangle((0, 0, 299, 79), fill=(15, 27, 45, 224))
    d.rectangle((60, 20, 240, 60), fill=(255, 255, 255, 255))  # 文字の代わり
    fr = list(wi.card_morph_frames(card, (60, 20, 240, 60), "電池", "battery", 50, 10, 40, None,
                                   "#2A6FDB"))  # fmt: skip
    assert len(fr) == 50
    assert fr[0].size == fr[-1].size and fr[0].height >= 80
    pad = (fr[0].height - 80) // 2
    assert fr[0].getpixel((150, 40 + pad)) == (255, 255, 255, 255)  # 最初は元のカード
    assert fr[-1].getpixel((70, 22 + pad))[:3] == (15, 27, 45)  # 文字は面の色で消える
