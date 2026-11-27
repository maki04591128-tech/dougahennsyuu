"""文字の追加（統合版 段階5d: T44・T45・T47・T48・T49・T50）の画。"""

from PIL import Image, ImageDraw

from video_automation.services.rendering import t_extras as tx

W, H = 640, 360


def test_rotating_words_split():
    assert tx.rotating_words("速い／軽い／安い") == ("", ["速い", "軽い", "安い"])
    assert tx.rotating_words("新しい 速い／軽い") == ("新しい", ["速い", "軽い"])
    assert tx.rotating_words("ふつうの題名") is None
    assert tx.rotating_words("速い／") is None
    assert tx.rotating_words("速い\n／軽い") is None


def test_rotating_frames_hold_and_stop_on_last_word():
    fr = list(tx.rotating_frames(W, H, "", ["速い", "軽い"], 70, None, "#FFFFFF", "#FF0000"))
    assert len(fr) == 70 and fr[0].size == (W, H)
    # 24f の表示 → 8f の入れ替え → 最後の語で止まる（最後の 2 枚は同じ）
    assert fr[-1].tobytes() == fr[-2].tobytes()
    assert fr[0].tobytes() != fr[28].tobytes()
    # 最後の語は accent の色
    import numpy as np

    arr = np.asarray(fr[-1])
    px = arr[arr[..., 3] > 200]
    assert len(px) and (px[:, 0] > 200).all() and (px[:, 1] < 60).all()


def test_band_ring_echo_and_crawl_have_content():
    band = tx.title_band(W, H, "発表", None, "#FF0000", "#FFFFFF", 8)
    assert band.size[0] > W and band.getchannel("A").getbbox() is not None
    ring = tx.ring_text(round(H * 0.38), "夏祭り", None, "#AAAAAA", H)
    assert ring.size[0] == ring.size[1] and ring.getchannel("A").getbbox() is not None
    pair = tx.echo_pair(W, H, "題名", None, "#FFFFFF", 0.8, 1)
    a = pair.getchannel("A")
    top, bottom = a.crop((0, 0, W, H // 2)).getbbox(), a.crop((0, H // 2, W, H)).getbbox()
    assert top is not None and bottom is not None and a.getextrema()[1] <= 90  # 不透明度 35%
    crawl = list(tx.crawl_frames(W, H, "遠い昔の物語が始まる。" * 3, None, "#FFFF00", 60, 30))
    assert len(crawl) == 60
    # 下から奥へ: 文字のある範囲の上端が時間とともに上がる
    y1 = crawl[20].getchannel("A").getbbox()
    y2 = crawl[59].getchannel("A").getbbox()
    assert y1 is not None and y2 is not None and y2[1] < y1[1]


def test_outline_then_fill():
    card = Image.new("RGBA", (200, 80), (0, 0, 0, 0))
    mask = Image.new("L", (200, 80), 0)
    ImageDraw.Draw(card).rectangle((40, 20, 160, 60), fill=(255, 255, 255, 255))
    ImageDraw.Draw(mask).rectangle((40, 20, 160, 60), fill=255)
    fr = list(tx.outline_to_fill_frames(card, mask, "#00FF00", 40, 360))
    # 塗る前は縁だけ（中は透明）、塗り終わると中が白
    assert fr[15].getpixel((100, 40))[3] == 0 and fr[15].getchannel("A").getbbox() is not None
    assert fr[39].getpixel((100, 40)) == (255, 255, 255, 255)
    # 塗りの途中は左だけ
    mid = fr[22]
    assert mid.getpixel((50, 40))[3] == 255 and mid.getpixel((150, 40))[3] == 0


def test_not_phrases_and_strike_retype():
    assert tx.not_phrases("塩ではなく砂糖を使います。") == ("塩", "砂糖")
    assert tx.not_phrases("右クリックじゃなく、左クリックです。") == ("右クリック", "左クリック")
    assert tx.not_phrases("これは問題ではない。") is None
    (cw, ch), gen = tx.strike_frames("塩", "砂糖", None, 40, "#FFFFFF", "#000000", 40)
    fr = list(gen)
    assert len(fr) == 40 and fr[0].size == (cw, ch)
    # 線を引いた後（9f 目）は赤がある、消えた後は無い
    import numpy as np

    def red(im):
        a = np.asarray(im)
        return int(((a[..., 0] > 200) & (a[..., 1] < 90) & (a[..., 3] > 200)).sum())

    assert red(fr[9]) > 0 and red(fr[-1]) == 0
    (cw, ch), gen = tx.retype_frames("右", "左クリック", None, 40, "#FFFFFF", "#000000", 40)
    assert len(list(gen)) == 40
