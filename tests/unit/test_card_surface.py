"""統合版 Y1・Y2・Y3・Y8・Y9: 見た目の型ごとのカードの面。"""

from pathlib import Path

from PIL import Image, ImageDraw

from video_automation.services.rendering.title_card import Y_SURFACES, card_surface


def _card(path: Path) -> None:
    im = Image.new("RGBA", (200, 80), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, 199, 79), fill=(15, 27, 45, 224))
    d.rectangle((60, 30, 140, 50), fill=(255, 255, 255, 255))
    im.save(path)


def test_surfaces(tmp_path: Path) -> None:
    assert Y_SURFACES == {
        "Y1": "chalk", "Y2": "blueprint", "Y3": "paper", "Y8": "glass", "Y9": "comic"
    }  # fmt: skip
    out = {}
    for k in ("chalk", "blueprint", "paper", "glass", "comic"):
        p = tmp_path / f"{k}.png"
        _card(p)
        card_surface(p, k, 1080)
        out[k] = Image.open(p).convert("RGBA")
    assert out["blueprint"].getpixel((30, 40))[3] == 0  # 面は塗らない
    assert out["blueprint"].getpixel((0, 40)) == (255, 255, 255, 255)  # 白の線
    assert out["glass"].getpixel((30, 40)) == (255, 255, 255, 36)  # 白 14%
    assert out["paper"].size[0] > 200 and out["paper"].getpixel((203, 80))[3] > 0  # 右下の影
    assert out["comic"].size == (212, 92) and out["comic"].getpixel((2, 40))[:3] == (17, 17, 17)
    assert out["chalk"].getpixel((100, 40)) == (255, 255, 255, 255)  # 中は欠けない
    assert out["chalk"].tobytes() != Image.open(tmp_path / "blueprint.png").tobytes()
    for k in ("blueprint", "glass"):
        assert out[k].getpixel((100, 40)) == (255, 255, 255, 255)  # 文字は残す
