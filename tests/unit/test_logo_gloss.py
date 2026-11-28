"""統合版 L7 のロゴ: 帯はロゴの透明でない所だけを、左から右へ 1 回通る。"""

import numpy as np
from PIL import Image

from video_automation.services.rendering import logo_gloss as lg


def test_logo_gloss_band_moves_left_to_right_inside_the_logo(tmp_path):
    im = Image.new("RGBA", (200, 100), (0, 0, 0, 0))
    im.paste((255, 0, 0, 255), (0, 0, 120, 100))  # 左の 120px だけがロゴ
    im.save(tmp_path / "logo.png")
    fr = list(lg.frames(tmp_path / "logo.png"))
    assert len(fr) == lg.FRAMES and fr[0].size == (200, 100)
    a = [np.asarray(f.getchannel("A")) for f in fr]
    assert all(x[:, 121:].max() == 0 for x in a)  # 透明な所には描かない
    assert max(x.max() for x in a) <= round(255 * lg.OPACITY) + 1
    cx = [np.average(np.arange(200), weights=x.sum(axis=0) + 1e-9) for x in a if x.max() > 0]
    assert cx == sorted(cx)  # 帯の中心は右へ進む
