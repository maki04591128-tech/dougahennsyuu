"""統合版 段階5f: モーショングラフィックスの要素（Z1・Z2・Z3）。"""

from video_automation.services.design import motion_elements as me


def test_table_and_parts() -> None:
    assert len(me.ELEMENTS) == 101
    axes = {v[0] for v in me.ELEMENTS.values()}
    assert len(axes) == 10
    for codes in me.PARTS.values():
        assert all(c in me.ELEMENTS for c in codes)


def test_parts_in_asset_names() -> None:
    names = ["手描きの丸（N12）", "背景（R5 点の格子の波紋）", "数字が回る（T35）「114」",
             "MV・ショット", "背景（R5 点の格子の波紋）", "形のワイプ（E6）"]  # fmt: skip
    assert me.parts_in(names) == ["R5", "T35", "E6"]  # N12 は対応の表に無い


def test_rules() -> None:
    assert me.check_parts(["N1", "E6"]) == []
    many = me.check_parts(["N1", "R5", "T35", "D5"])  # 角丸・円・進み具合の棒・横棒
    assert any("決まり1" in x for x in many)
    assert any("決まり8" in x for x in me.check_parts(["E6", "E8"]))
    assert me.check_parts(["E6"], ["ZT4"]) == []  # カットは数えない
    assert me.check_parts(["R14"]) == []  # 背景の模様にはラベルを求めない
