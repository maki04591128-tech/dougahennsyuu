"""地図（統合版 J1〜J3）の地名の突き合わせと画。"""

from video_automation.integrations.maps import natural_earth as ne
from video_automation.services.rendering import maps as mp


def _world():
    def sq(x0, y0, x1, y1):
        return {"type": "Polygon", "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1]]]}

    c = {"features": [
        {"properties": {"NAME_JA": "日本", "ADM0_A3": "JPN"}, "geometry": sq(130, 30, 145, 45)},
        {"properties": {"NAME_JA": "アメリカ合衆国", "ADM0_A3": "USA"},
         "geometry": {"type": "MultiPolygon", "coordinates": [[[[-120, 30], [-70, 30], [-70, 48],
                                                                [-120, 48]]]]}},
    ]}  # fmt: skip
    p = {"features": [
        {"properties": {"NAME_JA": "東京都", "ADM0_A3": "JPN"},
         "geometry": {"type": "Point", "coordinates": [139.7, 35.7]}},
    ]}  # fmt: skip
    return ne.parse(c, p)


def test_names_aliases_and_suffixes():
    w = _world()
    assert w.country("米国").code == "USA" and w.country("アメリカ").code == "USA"
    assert w.place("東京").name == "東京都" and w.place("大阪") is None
    assert mp.places_in(w, "東京からアメリカへ", "東京") == ["東京", "アメリカ"]
    # 同じ国の別の呼び方は 2 地点に数えない
    assert mp.places_in(w, "米国、つまりアメリカ合衆国", "米国") == ["米国"]


def test_frames_for_each_kind():
    w = _world()
    pal = mp.Palette("#000000", "#333333", "#777777", "#FF0000")
    name, fr, still = mp.frames_for(w, "日本", "日本の話", 160, 90, pal)
    assert name == "国の塗り（J1）" and len(fr) == mp.J1_FILL + mp.J1_ZOOM
    # 寄った後は国が画面の高さの 40%（赤の範囲の高さ）
    import numpy as np

    a = np.asarray(still)
    rows = np.where((a[..., 0] > 200) & (a[..., 1] < 60))[0]
    assert abs((rows.max() - rows.min() + 1) / 90 - 0.40) < 0.06
    name, fr, _ = mp.frames_for(w, "東京", "東京の話", 160, 90, pal)
    assert name == "地点へ寄る（J2）" and len(fr) == mp.J2_PIN + mp.J2_ZOOM
    name, fr, _ = mp.frames_for(w, "東京", "東京からアメリカへ", 160, 90, pal)
    assert name == "経路の線（J3）" and len(fr) == mp.J3_DRAW
    assert mp.frames_for(w, "アトランティス", "", 160, 90, pal) is None


def test_ensure_downloads_once(tmp_path):
    import json

    calls = []

    def fetch(url):
        calls.append(url)
        return json.dumps({"features": []}).encode()

    assert ne.ensure(tmp_path, fetch) is not None and len(calls) == 2
    assert ne.ensure(tmp_path, fetch) is not None and len(calls) == 2

    def broken(url):
        raise OSError("offline")

    assert ne.ensure(tmp_path / "x", broken) is None
