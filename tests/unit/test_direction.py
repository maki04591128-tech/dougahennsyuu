"""演出プランの整形・カードの割り付け・素材の配置位置。"""

from video_automation.schemas.animation import CardRequest
from video_automation.schemas.direction import DirectionPlan, Shot, ShotKind
from video_automation.services.direction.plan import from_llm, normalize, rule_plan
from video_automation.services.direction.service import native_transform
from video_automation.services.rendering.card import card_layout, text_width

LINES = [
    "ダイヤモンドより硬い物質って本当にあるんですか？",
    "w-BNの押し込み強度は114ギガパスカル、ダイヤモンドは97ギガパスカルと予測されました。",
    "実験ではビッカース硬度は約46ギガパスカルでした。",
    "まとめると、予測と実験は違いました。",
]


def test_normalize_drops_invented_numbers_and_keywords():
    plan = DirectionPlan(
        shots=[
            Shot(kind="chart", from_line=2, labels=["w-BN", "ダイヤ"], values=[114, 97]),
            Shot(kind="chart", from_line=3, labels=["w-BN", "c-BN"], values=[46, 65]),  # 65 無し
            Shot(kind="chart", from_line=3, labels=["a"], values=[1, 2]),  # 数が合わない
            Shot(kind="keyword", from_line=2, text="114 GPa"),  # 単位の表記違いは可
            Shot(kind="keyword", from_line=2, text="97GPa"),  # 同じ発話の 2 つ目は外す
            Shot(kind="keyword", from_line=1, text="ロンズデーライト"),  # 発話に無い
            Shot(kind="card", from_line=9, heading="まとめ", body=["a", "b", "c", "d"]),
            Shot(kind="card", from_line=2, heading="同じ発話"),  # 図表と同じ発話 → 外す
            Shot(kind="card", from_line=1, heading="", body=[]),
        ]
    )
    out = normalize(plan, LINES, title="w-BN")
    kinds = [(s.kind.value, s.from_line) for s in out.shots]
    assert kinds == [("chart", 2), ("keyword", 2), ("card", 4)]
    assert out.title == "w-BN"
    card = out.shots[-1]
    assert card.to_line == 4 and card.body == ["a", "b", "c"]
    assert any("65" in n for n in out.notes)
    assert any("ロンズデーライト" in n for n in out.notes)


def test_rule_plan_and_from_llm():
    p = rule_plan(LINES, "タイトル")
    assert p.title == "タイトル"
    assert [(s.kind.value, s.from_line) for s in p.shots] == [("keyword", 2), ("keyword", 3)]
    raw = {
        "title": "t",
        "shots": [
            {"kind": "card", "from_line": 1, "heading": "あ" * 50, "body": ["x"] * 9},
            {"kind": "unknown", "from_line": 1},
            "broken",
            {"kind": "keyword", "from_line": 2, "text": "114GPa", "highlight": None},
        ],
    }
    p = from_llm(raw)
    assert [s.kind.value for s in p.shots] == ["card", "keyword"]
    assert len(p.shots[0].heading) == 40 and len(p.shots[0].body) == 6


def test_card_layout_and_placement():
    req = CardRequest(
        project_id="p", heading="まとめ", body=["短い", "とても" * 20], style="summary"
    )
    lay = card_layout(req, 1920, 1080)
    assert lay.lines[0].color == req.accent_color  # まとめの見出しは強調色
    assert len([ln for ln in lay.lines if ln.size < lay.lines[0].size]) >= 3  # 長い項目は折り返す
    assert all(ln.x + text_width(ln.text, ln.size) <= lay.width for ln in lay.lines)
    assert len(lay.bullets) == 2 and lay.divider is not None
    short = card_layout(CardRequest(project_id="p", heading="短い"), 1920, 1080)
    assert short.width == round(1920 * 0.3)  # 内容に合わせて詰める
    kw = card_layout(CardRequest(project_id="p", heading="114 GPa", style="keyword"), 1920, 1080)
    assert len(kw.lines) == 1 and kw.width < 600
    # そのままの大きさで置く。領域より大きいときは縮める
    t = native_transform(600, 300, 1920, 1080, cx=0.5, cy=0.42)
    assert t.fit.value == "fit" and t.scale == round(600 / 1920, 4) and abs(t.y + 0.08) < 1e-9
    t = native_transform(1000, 900, 1920, 1080, cx=0.5, cy=0.42, max_h=0.58)
    assert t.scale * 1080 <= 0.58 * 1080 + 1
    # 立ち絵があるときは横幅も中央の列（0.56）に収める
    t = native_transform(1800, 300, 1920, 1080, cx=0.5, cy=0.42, max_w=0.56)
    assert t.scale <= 0.56 + 1e-4


def test_infer_unit_and_whitespace():
    from video_automation.services.direction.plan import infer_unit

    assert infer_unit([114, 97], "w-BNは114ギガパスカル、ダイヤは97ギガパスカル") == "GPa"
    assert infer_unit([18], "約18%上回る") == "%"
    assert infer_unit([46], "約46GPaでした") == "GPa"
    assert infer_unit([3], "3人います") == ""
    plan = DirectionPlan(
        shots=[
            Shot(kind="chart", from_line=2, labels=["a", "b"], values=[114, 97]),
            Shot(kind="keyword", from_line=3, text="約46\nギガパスカル"),
        ]
    )
    out = normalize(plan, LINES)
    assert (
        out.shots[0].unit == "GPa" and out.shots[1].text == "約46ギガパスカル"
    )  # 改行は空白に。数字と和文の間の空白は取る


def test_from_llm_unwraps_nested_plan():
    p = from_llm(
        {"plan": {"title": "t", "shots": [{"kind": "keyword", "from_line": 1, "text": "x"}]}}
    )
    assert p.title == "t" and len(p.shots) == 1
    empty = from_llm({"foo": 1})
    assert empty.shots == [] and "演出がありませんでした" in empty.notes[0]


def test_from_llm_aliases():
    p = from_llm(
        {
            "shots": [
                {"kind": "card", "from_line": 1, "title": "見出し", "content": "a\nb"},
                {"kind": "keyword", "start_line": 2, "keyword": "114GPa", "heading": ""},
            ]
        }
    )
    assert p.shots[0].heading == "見出し" and p.shots[0].body == ["a", "b"]
    assert p.shots[1].from_line == 2 and p.shots[1].text == "114GPa"


def test_normalize_drops_field_names_in_card_body():
    # 小さなモデルが項目名や指示の言葉を本文に書いた例（実機の gemma4 で出た）
    body = [
        "title: 富士山の豆知識",
        "subtitle: 知るとかっこいい！",
        "card_body_style_summary",
        "標高は3776メートル",
        "CPU: 速い",
    ]
    plan = DirectionPlan(shots=[Shot(kind="card", from_line=1, heading="日本一の標高", body=body)])
    out = normalize(plan, LINES)
    assert out.shots[0].body == ["標高は3776メートル", "CPU: 速い"]  # 大文字の略語は残す
    assert any("要点でない文" in n for n in out.notes)


def test_pattern_background_is_not_flat(tmp_path):
    import numpy as np
    from PIL import Image

    from video_automation.services.direction.service import pattern_background

    out = tmp_path / "bg.png"
    pattern_background(out, 640, 360, "#0B1D3A", "#2A5A8A", "#3BB8DB")
    a = np.asarray(Image.open(out).convert("L"), dtype=float)
    assert a.shape == (360, 640)
    # グラデーション・光の円・点の格子で、場所ごとに明るさが違う（単色ではない）
    assert a.std() > 4 and abs(a[:, :80].mean() - a[:, -80:].mean()) > 3
    # 細かい点の格子（隣り合う画素の差）がある
    assert np.abs(np.diff(a, axis=1)).max() >= 2


def test_card_body_quotes_and_meta_items_are_removed():
    from video_automation.schemas.direction import DirectionPlan, Shot, ShotKind
    from video_automation.services.direction.plan import normalize

    raw = DirectionPlan(
        title="スマホバッテリー長持ちのコツ",
        shots=[
            Shot(
                kind=ShotKind.CARD,
                from_line=1,
                heading="「ケーブルの長さ」",
                body=[
                    "「短いケーブルを使う」",
                    "「解説動画」",
                    "スマホバッテリー長持ちのコツ",
                    "抵抗が減る",
                ],
            )
        ],
    )
    plan = normalize(raw, ["短いケーブルを使うと抵抗が減る"], "スマホバッテリー長持ちのコツ")
    card = plan.shots[0]
    assert card.heading == "ケーブルの長さ" and card.body == ["短いケーブルを使う", "抵抗が減る"]
    assert any("要点でない文" in n for n in plan.notes)


def test_card_reveal_keeps_size_and_draws_only_shown_items(tmp_path):
    from video_automation.services.rendering.card import card_filter

    full = CardRequest(project_id="p", heading="三つのコツ", body=["明るさ", "通信", "充電"])
    part = full.model_copy(update={"reveal": 1})
    lf, lp = card_layout(full, 1920, 1080), card_layout(part, 1920, 1080)
    assert (lf.width, lf.height) == (lp.width, lp.height)  # 順に出しても位置がずれない
    vf_full = card_filter(full, lf, tmp_path / "f.ttf", tmp_path)
    vf_part = card_filter(part, lp, tmp_path / "f.ttf", tmp_path)
    assert vf_full.count("drawtext") == 4 and vf_part.count("drawtext") == 2
    assert vf_part.count("@0.62") == 0  # 新しい項目は暗くしない
    two = card_filter(full.model_copy(update={"reveal": 2}), lf, tmp_path / "f.ttf", tmp_path)
    assert two.count("drawtext") == 3 and "@0.62" in two  # 前の項目は少し暗く


def test_reveal_steps_follow_lines():
    from types import SimpleNamespace as NS

    from video_automation.services.direction.service import _reveal_steps

    lines = [NS(start=i * 60, end=i * 60 + 55) for i in range(6)]
    shot = Shot(kind=ShotKind.CARD, from_line=1, to_line=6, heading="h", body=["a", "b", "c"])
    st = _reveal_steps(shot, lines, 0, 360, 30)
    assert [r for _, _, r in st] == [1, 2, 3]
    assert [t0 for t0, _, _ in st] == [0, 120, 240] and st[-1][1] == 360
    # 短すぎるときは一度に出す
    assert _reveal_steps(shot, lines, 0, 60, 30) == [(0, 60, None)]
    one = shot.model_copy(update={"body": ["a"]})
    assert _reveal_steps(one, lines, 0, 360, 30) == [(0, 360, None)]


def test_card_items_copied_from_lines_are_dropped():
    lines = ["まずは、画面の明るさは自動調整にして、最低限に抑えるの。", "なるほどなのだ。"]
    plan = DirectionPlan(
        title="t",
        shots=[
            Shot(
                kind=ShotKind.CARD,
                from_line=1,
                heading="明るさ",
                body=["画面の明るさは自動調整にして、最低限に抑えるの。", "明るさは自動に"],
            )
        ],
    )
    out = normalize(plan, lines)
    assert out.shots[0].body == ["明るさは自動に"]
    assert any("そのまま写した" in n for n in out.notes)


def test_card_items_packed_into_one_are_split():
    lines = ["節電のコツを話します。", "コンセントを抜きます。"]
    plan = DirectionPlan(
        title="t",
        shots=[
            Shot(
                kind=ShotKind.CARD,
                from_line=1,
                heading="節電のコツ",
                body=["「コンセントを抜く」\n「LED 照明」", "「温度上げ」「無駄チェック」"],
            )
        ],
    )
    out = normalize(plan, lines)
    assert out.shots[0].body == ["コンセントを抜く", "LED 照明", "温度上げ"]


def test_card_items_echoing_the_prompt_are_dropped():
    """実機: 解説のカードに、指示文の 1 文目を写した項目が出た。"""
    from video_automation.services.direction.plan import _echoes_prompt, _norm

    script = _norm("太陽系の惑星を比べるのだ。木星が最大で、土星はリングが特徴なのよ。")
    assert _echoes_prompt("解説動画の演出を担当します", script)
    assert _echoes_prompt("画面に出す演出を決定します", script)
    from video_automation.services.direction import prompts

    pp = prompts.build("", None)  # 例は用途ごとの例（W1）に移った
    assert _echoes_prompt("満充電が続く状態", script, pp.rules, pp.examples)  # 例の写し
    assert not _echoes_prompt("満充電が続く状態", script)  # 例を渡さなければ判定しない
    assert not _echoes_prompt("木星は太陽系の最大", script)
    assert not _echoes_prompt("土星はリングが特徴", script)
    assert not _echoes_prompt("高温", script)


def test_card_text_drops_spaces_between_japanese_and_numbers():
    from video_automation.services.direction.plan import _trim

    assert _trim("土星は直径約 11 万 6,000km", 40) == "土星は直径約11万6,000km"
    assert _trim("AI と人", 40) == "AI と人"


def test_gap_keywords_fill_long_still_stretches():
    """統合版 Q2: 縦型ショート（3 秒）で画面が 8 秒変わらなかった所に、発話の語のテロップを足す。"""
    from video_automation.schemas.design import Purpose
    from video_automation.schemas.timeline import AddClip
    from video_automation.services.design.profiles import PROFILES
    from video_automation.services.direction.service import DirectionService, _Line, gap_keyword

    assert gap_keyword("土星は直径約12万kmもある") == "約12万km"
    assert gap_keyword("色合わせが攻略の鍵なのだ") == "攻略"
    assert gap_keyword("ずんだもん、それはすごいのだ", ("ずんだもん",)) == ""
    fps = 30.0
    lines = [
        _Line("A", "最初の説明なのだ", 0, 60),
        _Line("B", "連続ヒントで同じエリアを補強するのよ", 60, 150),
        _Line("A", "色合わせが攻略の鍵なのだ", 150, 240),
        _Line("B", "端のブロックから崩すと速いのよ", 240, 330),
    ]
    cmds = [AddClip(track_id="t", asset_id="a", start_frame=0, duration_frames=330)]
    short = DirectionService._fill_gaps(
        cmds, [], lines, PROFILES[Purpose.SHORT], fps, 0, ("A", "B")
    )
    assert [s.from_line for s in short] == [2, 3, 4]  # 3 秒ごと
    assert short[0].text == "連続ヒント"
    calm = DirectionService._fill_gaps(cmds, [], lines, PROFILES[Purpose.INTERVIEW], fps, 0, ())
    assert calm == []  # 変化の間隔を検査しない用途には足さない


def test_keyword_card_does_not_wrap_by_rounding():
    """実機: 「2人で掛け合い解説」が、幅の丸めで「解／説」と折れた。"""
    from video_automation.schemas.animation import CardRequest
    from video_automation.services.rendering.card import card_layout

    r = CardRequest(project_id="x", heading="2人で掛け合い解説", style="keyword", scale=0.62)
    assert [ln.text for ln in card_layout(r, 1080, 1920).lines] == ["2人で掛け合い解説"]
