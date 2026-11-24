"""演出プランの整形・カードの割り付け・素材の配置位置。"""

from video_automation.schemas.animation import CardRequest
from video_automation.schemas.direction import DirectionPlan, Shot
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
    assert out.shots[0].unit == "GPa" and out.shots[1].text == "約46 ギガパスカル"


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
    assert any("項目名" in n for n in out.notes)
