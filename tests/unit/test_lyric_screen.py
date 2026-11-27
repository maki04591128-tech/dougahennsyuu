"""統合版 段階5e: 歌詞の画面の組み方（U2〜U5・U21・U22）。"""

from PIL import Image, ImageDraw

from video_automation.services.rendering import lyric_screen as ls


def test_element_table_has_103_values() -> None:
    assert len(ls.ELEMENTS) == 103
    assert ls.DRAWN <= set(ls.ELEMENTS)


def test_presets_follow_rules() -> None:
    assert set(ls.PRESETS) == {
        "vocaloid_cool", "vocaloid_cute", "vocaloid_calm", "anison", "ballad", "rap", "pop", "idol"
    }  # fmt: skip
    assert ls.presets_ok() == {k: [] for k in [*ls.PRESETS, *ls.EXTRA]}
    for verse, chorus in ls.PRESETS.values():
        for s in (verse, chorus):
            for b in s.blocks:
                for v in (b.uc, b.up, b.ul, b.us, b.uf, b.ug, b.ut):
                    assert v in ls.DRAWN, v
            assert s.uh in ls.DRAWN and s.uk in ls.DRAWN and s.ud in ls.DRAWN
    assert ls.PRESETS["anison"][1].flash  # U5: サビの頭に白の重ね


def test_rules_detect_violations() -> None:
    sub = ls.Block("UR1", us="US2")
    flat = ls.Screen((sub, ls.Block("UR2", us="US3", px=100)))  # 倍率 2.2
    assert any("決まり1" in x for x in ls.check(flat))
    bare = ls.Screen((ls.Block("UR2", up="UP4", us="US4", ug="UG0"),), uh="UH1")
    assert any("決まり2" in x for x in ls.check(bare))
    assert not ls.check(ls.Screen((ls.Block("UR2", up="UP4", us="US4"),), uh="UH4"))
    a = ls.Screen((sub,))
    assert any("決まり7" in x for x in ls.check(a, ls.Screen((sub,))))
    moved = ls.Screen((sub,), pic_x=0.2)
    assert any("決まり17" in x for x in ls.check(a, moved))
    assert ls.clip_big("テキストサンプル", ls.Block("UR2", us="US5")) == "テキスト"


def test_phrases_group_lines_and_pick_key() -> None:
    texts = ["あいう", "かきくけこさ", "たち", "なに", "はひふ"]
    spans = [(0, 30), (30, 60), (60, 90), (200, 230), (230, 260)]
    secs = ["verse", "verse", "verse", "chorus", "chorus"]
    ph = ls.phrases(texts, spans, secs, list(range(0, 300, 15)))
    assert [p.lines for p in ph] == [texts[:3], texts[3:]]
    assert ph[0].key == 1 and ph[1].key == 0  # A メロは最も長い行、サビは 1 行目
    assert ph[0].frames[0] == (0, 30) and ph[1].start == 200
    assert ph[0].end <= 200 and ph[1].beats[0] == 10


def test_frames_are_opaque_and_change_by_line() -> None:
    pic = Image.new("RGBA", (200, 300), (0, 0, 0, 0))
    ImageDraw.Draw(pic).rectangle((50, 20, 150, 290), fill=(60, 90, 200, 255))
    ph = ls.Phrase("verse", ["テキストサンプル", "サンプルテキ"], [(0, 20), (20, 40)], 0, 50, 0,
                   [0, 15, 30, 45])  # fmt: skip
    for name in ls.PRESETS:
        for sec in ("verse", "chorus"):
            fr = list(ls.frames(ls.screen_for(name, sec), ph, pic, 320, 180, lambda n: None, 30))
            assert len(fr) == 50
            assert fr[25].getchannel("A").getextrema() == (255, 255)  # 不透明
            assert fr[0].tobytes() != fr[25].tobytes(), (name, sec)
    # 変わらないフレームは同じ画を使う
    fr = list(ls.frames(ls.screen_for("vocaloid_calm", "verse"), ph, pic, 320, 180,
                        lambda n: None, 30))  # fmt: skip
    assert len({id(x) for x in fr}) < len(fr)


def test_section_screens_use_examples_and_keep_picture_position() -> None:
    cool_v, cool_c = ls.PRESETS["vocaloid_cool"]
    assert ls.screen_for("vocaloid_cool", "verse", 0) is cool_v
    assert ls.screen_for("vocaloid_cool", "verse", 1).uh == "UH5"  # U15 丸い窓
    assert ls.screen_for("vocaloid_cool", "bridge", 0).ud == "UD3"  # U13
    assert ls.screen_for("vocaloid_cool", "chorus_first", 0) is cool_c
    assert ls.screen_for("vocaloid_cool", "chorus", 1).uh == "UH3"  # U18 影絵
    assert ls.screen_for("pop", "verse", 0).blocks[1].ug == "UG3"  # U10 線だけの題字
    assert ls.screen_for("pop", "bridge", 2).uk == "UK2"  # U12
    for d, rows in ls.SECTION_SCREENS.items():
        for sec, n, _ex in rows:
            assert ls.screen_for(d, sec, n or 0).pic_x == ls.PRESETS[d][0].pic_x  # 決まり 17
    assert ls.presets_ok()["U13"] == []


def test_two_line_subtitle_shows_pairs() -> None:
    scr = ls.EXTRA["U12"]
    ph = ls.Phrase("bridge", ["あいう", "かきく", "さしす"], [(0, 10), (10, 20), (20, 30)], 0, 40)
    bd = ls.Builder(scr, ph, None, 320, 180, lambda n: None, 30, 0)
    bd.build()
    assert [it.t_in for it in bd.items] == [0, 0, 20]
    assert bd.items[0].t_out == 20 and bd.items[0].y < bd.items[1].y
