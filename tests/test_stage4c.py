"""段階4c: 名前帯（P1）・料理の材料（P4）・ランキング（P5）・ふりがな（P7）・予告編（P8）・シリーズ（Q4）。"""

from fractions import Fraction

import pytest
from PIL import Image

from video_automation.schemas.design import Component, Purpose, SeriesSettings
from video_automation.schemas.subtitle import SubtitleStyle
from video_automation.services.design.profiles import PROFILES
from video_automation.services.design.series import (
    SeriesNotFoundError,
    SeriesStore,
    apply_series,
    series_violations,
)
from video_automation.services.direction import purpose_parts as pp
from video_automation.services.rendering import lower_third as lt
from video_automation.services.rendering.purpose_cards import (
    draw_labels,
    draw_list_card,
    draw_rank_number,
)
from video_automation.services.subtitle.formats import build_ass

# ---------------- P1 ----------------


def test_lower_third_show_sec():
    assert lt.show_sec("佐藤", "研究員") == 5.0
    assert lt.show_sec("アレクサンダー・グラハム", "") == 7.0  # 8 字以上
    assert lt.show_sec("佐藤", "研究所\n主任") == 7.0  # 肩書きに改行


def test_lower_third_schedule_once_and_after_five_minutes():
    fps = 30.0
    m = int(60 * fps)
    lines = [("A", 0, 30), ("B", 40, 80), ("A", 90, 120), ("A", 6 * m, 6 * m + 30)]
    assert lt.schedule(lines, fps) == [("A", 15), ("B", 55), ("A", 6 * m + 15)]
    assert lt.schedule([("", 0, 10)], fps) == []


def test_lower_third_png(tmp_path):
    a = lt.draw_lower_third(
        tmp_path / "a.png", 1080, "佐藤", "", None, "#FFFFFF", "#FF0000", "#000000"
    )
    b = lt.draw_lower_third(
        tmp_path / "b.png", 1080, "佐藤", "研究員", None, "#FFFFFF", "#FF0000", "#000000"
    )
    ia, ib = Image.open(a), Image.open(b)
    assert ib.height > ia.height and ia.mode == "RGBA"
    assert ia.getpixel((2, ia.height // 2))[:3] == (255, 0, 0)  # 左端の線は話者の色


# ---------------- P4 ----------------


def test_ingredients_and_steps():
    texts = [
        "今日は2人分のカレーです。",
        "まず玉ねぎを1個と、にんじん1本を用意します。",
        "次に砂糖大さじ2と塩少々を入れます。",
        "最後に10分煮込みます。",
    ]
    ings = pp.ingredients(texts)
    assert [(g.line, g.label) for g in ings] == [
        (1, "玉ねぎ 1個"),
        (1, "にんじん 1本"),
        (2, "砂糖 大さじ2"),
        (2, "塩 少々"),
    ]
    assert pp.servings(texts) == "材料（2人分）"
    assert pp.servings(["材料です"]) == "材料"
    assert pp.steps(texts) == [(1, 1), (2, 2), (3, 3)]
    assert pp.steps(["まず", "おわり"]) == []  # 区切りが 1 つだけ
    assert pp.steps(["手順3 切る", "手順4 焼く"]) == [(0, 3), (1, 4)]


def test_ingredients_max_two_per_line_and_no_duplicates():
    ings = pp.ingredients(["卵2個、牛乳200ml、砂糖30gを混ぜます", "卵2個を足します"])
    assert [g.label for g in ings] == ["卵 2個", "牛乳 200ml"]


# ---------------- P5 ----------------


def test_ranks():
    texts = [
        "今日の発表です",
        "第3位は京都です",
        "第2位は、大阪！",
        "売上1位の店も",
        "第1位は東京でした",
    ]
    rs = pp.ranks(texts)
    assert [(r.line, r.number, r.name) for r in rs] == [
        (1, 3, "京都"),
        (2, 2, "大阪"),
        (4, 1, "東京"),
    ]
    assert pp.ranks(["第1位は東京", "第2位は大阪"]) == []  # 上位から下位へは並べない
    assert pp.ranks(["第十位は札幌", "第九位は仙台"])[0].number == 10
    assert (pp.rank_pace(11), pp.rank_pace(4), pp.rank_pace(1)) == (3.0, 25.0, 37.0)


def test_extra_gaps():
    texts = ["はじめ", "第2位は大阪", "第1位は東京"]
    assert pp.extra_gaps("ranking", texts, [1, 1, 1]) == [0.0, 0.0, 0.8]
    g = pp.extra_gaps("trailer", ["x"] * 10, [1.0] * 10)
    assert g[3] == 0.7 and sum(g) == 0.7
    assert pp.extra_gaps("explainer", texts, [1, 1, 1]) == [0.0] * 3


# ---------------- P8 ----------------


def test_trailer_acts_and_date():
    assert pp.acts([1.0] * 10) == [1, 1, 1, 2, 2, 2, 2, 3, 3, 3]
    assert pp.date_text(["2027年3月14日、公開。"]) == "2027年3月14日"
    assert pp.date_text(["あの夏が来る"]) == ""  # 日付は作らない
    assert PROFILES[Purpose.TRAILER].allowance(Component.CARD) == pp.TRAILER_CARDS


# ---------------- P7 ----------------


def test_grade_kanji_counts():
    assert [len(set(pp.GRADE_KANJI[g])) for g in (1, 2, 3)] == [80, 160, 200]
    assert len(pp.taught()) == 440


def test_ruby_only_for_untaught_kanji():
    assert pp.ruby("今日は冒険の話です", "キョオワボオケンノハナシデス") == [(3, 5, "ぼうけん")]
    assert pp.ruby("雷が鳴った", "カミナリガナッタ") == [(0, 1, "かみなり")]
    assert pp.ruby("経験", "ケエケン") == [(0, 2, "けいけん")]
    assert pp.ruby("山へ行く", "ヤマエイク") == []  # 全部 1〜2 年の漢字
    assert pp.ruby("冒険です", "ゼンゼンチガウ") == []  # 読みの位置が合わなければ付けない
    assert pp.ruby("ひらがな", "ヒラガナ") == []


def test_ruby_in_ass():
    st = SubtitleStyle()
    text = "今日は冒険\nの話です"
    ass = build_ass(
        [(0, 30, "", text)], Fraction(30), 1920, 1080, st, ruby={0: [(0, 3, 5, "ぼうけん")]}
    )
    rub = [x for x in ass.split("\r\n") if x.startswith("Dialogue: 3,")]
    assert len(rub) == 1 and "\\pos(" in rub[0] and rub[0].endswith("ぼうけん")
    body = [x for x in ass.split("\r\n") if x.startswith("Dialogue: 1,")][0]
    assert "\\h" in body  # 行の間に空き
    plain = build_ass([(0, 30, "", text)], Fraction(30), 1920, 1080, st)
    assert "Dialogue: 3," not in plain and "\\h" not in plain


# ---------------- 絵 ----------------


def test_purpose_cards(tmp_path):
    a = Image.open(
        draw_labels(
            tmp_path / "l.png",
            1080,
            ["玉ねぎ 1個", "塩 少々"],
            None,
            "#FFFFFF",
            "#FF0000",
            "#000000",
        )
    )
    b = Image.open(
        draw_labels(tmp_path / "s.png", 1080, ["STEP 1"], None, "#FFFFFF", "#FF0000", "#000000")
    )
    assert a.height > b.height
    card = Image.open(
        draw_list_card(
            tmp_path / "c.png",
            1920,
            1080,
            "材料",
            [f"品{i}" for i in range(14)],
            None,
            "#FFFFFF",
            "#FFD000",
            "#222222",
        )
    )
    assert card.width == round(1920 * 0.46)
    num = Image.open(
        draw_rank_number(tmp_path / "n.png", 1080, 1, None, "#FFFFFF", "#FFD000", "#000000")
    )
    assert num.height >= 1080 * 0.22


# ---------------- Q4 ----------------


def test_series_store(tmp_path):
    st = SeriesStore(tmp_path / "series")
    assert st.list() == []
    s = st.save(
        SeriesSettings(name="週刊", colors={"main": "#112233"}, heading_font="Zen Kaku Gothic New")
    )
    assert s.id and st.get(s.id).colors == {"main": "#112233"}
    st.save(s.model_copy(update={"name": "週刊2"}))
    assert [x.name for x in st.list()] == ["週刊2"]
    st.delete(s.id)
    with pytest.raises(SeriesNotFoundError):
        st.get(s.id)
    with pytest.raises(SeriesNotFoundError):
        st.get("../etc")
    with pytest.raises(ValueError):
        SeriesSettings(name="x", colors={"nope": "#000000"})


def test_apply_series_and_violations():
    base = PROFILES[Purpose.EXPLAINER]
    s = SeriesSettings(name="週刊", colors={"main": "#112233"}, heading_font="Zen Kaku Gothic New")
    p = apply_series(base, s)
    assert p.palette.main == "#112233" and p.typography.heading_font == "Zen Kaku Gothic New"
    assert base.palette.main != "#112233"  # 元の型は変えない
    assert series_violations(p, s, ["#112233", "#FFFFFF"], {"Zen Kaku Gothic New"}) == []
    v = series_violations(p, s, ["#FF00FF"], {"Comic Sans"})
    assert {x.code for x in v} == {"series_color", "series_font"}
    assert all(x.severity == "warning" for x in v)


def test_load_long_and_unmatched_text_is_fast():
    """負荷: 読みが合わない長い文・長い台本でも、すぐに終わる（正規表現の後戻りを使わない）。"""
    import time

    t0 = time.perf_counter()
    assert pp.ruby("冒1険2" * 25 + "です", "ア" * 300 + "ズ") == []
    assert len(pp.ruby("冒険の話" * 40, "ボオケンノハナシ" * 40)) == 40
    assert pp.ruby("冒険" * 300, "ボオケン" * 300) == []  # 長すぎる読みは付けない
    pp.ingredients(["卵2個" * 500] * 50)
    pp.ranks(["第1位は" * 500] * 50)
    assert time.perf_counter() - t0 < 2.0


def test_ranks_skip_preface_and_strip_endings():
    """実機: 「第11位から第3位まで発表するわ」を 11 位と数え、名前に口調の語尾が残った。"""
    rs = pp.ranks(
        [
            "第11位から第3位まで、順に発表するわ",
            "第11位は長野県・松本城なのだ",
            "第10位は北海道・小樽運河なの",
            "第1位は東京・東京スカイツリーなのよ！",
        ]
    )
    assert [(r.line, r.number, r.name) for r in rs] == [
        (1, 11, "長野県・松本城"),
        (2, 10, "北海道・小樽運河"),
        (3, 1, "東京・東京スカイツリー"),
    ]


def test_shorten_animation_moves_exit_keys():
    """実機: 順位の数字の後へ寄せたカードの出のキーがクリップの外に残り、命令が通らなかった。"""
    from video_automation.schemas.animation import ClipAnimation, Keyframe
    from video_automation.schemas.timeline import SetClipAnimation
    from video_automation.services.direction.service import shorten_animation

    keys = [Keyframe(frame=f, opacity=o) for f, o in ((0, 0.0), (10, 1.0), (264, 1.0), (269, 0.0))]
    cmds = [SetClipAnimation(clip_id="c", animation=ClipAnimation(keyframes=keys))]
    shorten_animation(cmds, "c", 270, 60)
    got = [(k.frame, k.opacity) for k in cmds[0].animation.keyframes]
    assert got == [(0, 0.0), (10, 1.0), (204, 1.0), (209, 0.0)]
