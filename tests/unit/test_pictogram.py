"""要点カードの絵記号（素材をダウンロードせず図形から描く）。"""

from video_automation.schemas.animation import CardRequest
from video_automation.services.rendering.card import card_layout
from video_automation.services.rendering.pictogram import ICONS, pick_icon, render_icon


def test_pick_icon_by_words_heading_first():
    assert pick_icon("充電は80%で止める", ["過充電は寿命を縮める"]) == "battery"
    assert pick_icon("通信機能オフ", []) == "wifi"
    assert pick_icon("まとめ", ["設定を見直す"]) == "check"  # 見出しを優先
    assert pick_icon("", ["パスワードを使い回さない"]) == "lock"
    assert pick_icon("人気の商品", []) == "star"  # 「人」より「人気」
    assert pick_icon("ダイヤモンドの硬さ", ["114ギガパスカル"]) is None


def test_every_icon_draws_something_in_the_accent_color():
    for name in ICONS:
        im = render_icon(name, 96, "#F2B544")
        assert im.size == (96, 96)
        alpha = im.getchannel("A")
        filled = sum(alpha.histogram()[129:])
        assert 96 * 96 * 0.03 < filled < 96 * 96 * 0.7, name


def test_card_layout_makes_room_for_the_icon():
    base = CardRequest(project_id="p", heading="充電のコツ", body=["80%で止める"])
    plain = card_layout(base, 1920, 1080)
    withi = card_layout(base.model_copy(update={"icon": "battery"}), 1920, 1080)
    assert plain.icon is None and withi.icon is not None
    x, y, size = withi.icon
    assert all(ln.x >= x + size for ln in withi.lines)  # 文字は絵記号の右
    assert withi.height >= y + size
    # 知らない名前は付けない
    assert card_layout(base.model_copy(update={"icon": "nope"}), 1920, 1080).icon is None
