"""段階3 W1: 用途ごとの LLM への指示と例。"""

import pytest

from video_automation.schemas.design import Component, Purpose
from video_automation.services.design.profiles import PROFILES
from video_automation.services.direction import prompts
from video_automation.services.direction.plan import from_llm, normalize
from video_automation.services.direction.plan_examples import EXAMPLES
from video_automation.services.direction.service import fit_to_profile


@pytest.mark.parametrize("purpose", list(Purpose))
def test_four_examples_of_six_lines(purpose: Purpose) -> None:
    exs = EXAMPLES[purpose.value]
    assert len(exs) == 4
    assert all(len(e["lines"]) == 6 for e in exs)


@pytest.mark.parametrize("purpose", list(Purpose))
def test_examples_survive_normalize_and_profile(purpose: Purpose) -> None:
    """例の出力は、その用途の型で何も外れない（悪い例を見せない）。"""
    prof = PROFILES[purpose]
    for ex in EXAMPLES[purpose.value]:
        lines = [t for _, t in ex["lines"]]
        names = tuple({s for s, _ in ex["lines"]})
        raw = from_llm(prompts.example_output(ex))
        if not prompts.usable(prof):  # 題名だけの用途は演出が無いのが正しい
            assert not raw.shots
            continue
        assert not raw.notes
        plan = normalize(raw, lines, ex["title"], names)
        assert not plan.notes, (ex["title"], plan.notes)
        assert len(plan.shots) == len(ex["out"]["shots"]), ex["title"]
        fitted, dropped = fit_to_profile(plan, prof)
        assert not dropped, (ex["title"], dropped)
        assert len(fitted.shots) == len(plan.shots)
        # 図・カード・画像は 4 発話に 1 つ以上（使える用途だけ）
        need = prompts.need_main(prof, [], len(lines))
        have = sum(1 for s in plan.shots if s.kind.value in prompts.MAIN)
        assert have >= need, ex["title"]


def test_forbidden_comes_from_profile() -> None:
    sub = prompts.forbidden(PROFILES[Purpose.SUBTITLE])
    assert "card は使わない（この用途の型に無い）。" in sub
    assert "keyword は使わない（この用途の型に無い）。" in sub
    short = prompts.forbidden(PROFILES[Purpose.SHORT])
    assert "card は動画全体で最大 3 個。" in short
    assert "chart は動画全体で最大 3 個。" in short
    game = prompts.forbidden(PROFILES[Purpose.GAME])
    assert "card は動画全体で最大 2 個。" in game
    assert "chart は使わない（この用途の型に無い）。" in game
    # 利用者が足した部品（extras）は使える
    assert "chart は使わない（この用途の型に無い）。" not in prompts.forbidden(
        PROFILES[Purpose.GAME], [Component.CHART]
    )


def test_usable_kinds_in_instructions() -> None:
    txt = prompts.instructions(PROFILES[Purpose.VLOG])
    assert "- image:" in txt and "- keyword:" in txt
    assert "- card:" not in txt and "- chart:" not in txt
    assert "2〜4 発話に 1 つは image を置く" in txt
    none = prompts.instructions(PROFILES[Purpose.PODCAST])
    assert "shots は空の配列にする" in none
    assert "2〜4 発話に" not in none


def test_script_first_then_rules_then_examples() -> None:
    pp = prompts.build("台本:\n[1] A：こんにちは", PROFILES[Purpose.RECIPE], [], "決まりの節")
    u = pp.user
    assert u.startswith("台本:")
    assert u.index("決まりの節") < u.index("指示:") < u.index("例1") < u.index("例4")
    assert "料理・手順もの" in u and "基本の卵焼き" not in pp.system
    assert pp.system == prompts.ROLE


def test_need_main() -> None:
    assert prompts.need_main(PROFILES[Purpose.SUBTITLE], [], 12) == 0
    assert prompts.need_main(PROFILES[Purpose.EXPLAINER], [], 12) == 3
    assert prompts.need_main(PROFILES[Purpose.VLOG], [], 12) == 3
    assert prompts.need_main(PROFILES[Purpose.EXPLAINER], [], 2) == 1


def test_copied_example_item_is_dropped() -> None:
    """例の要点をそのまま写した項目は外す（例の台本は別の話題）。台本にある語は残す。"""
    prof = PROFILES[Purpose.EXPLAINER]
    pp = prompts.build("台本", prof)
    lines = ["夕焼けが赤いのは光が散らばるからです。", "青い光は途中で散らばります。"]
    raw = from_llm(
        {
            "title": "夕焼け",
            "shots": [
                {
                    "kind": "card",
                    "from_line": 1,
                    "heading": "夕焼け",
                    "body": ["屈折の差で色の帯に", "光が散らばる"],
                }
            ],
        }
    )
    plan = normalize(raw, lines, "夕焼け", (), (pp.rules, pp.examples))
    assert plan.shots[0].body == ["光が散らばる"]
    # 例を渡さなければ外さない（今までどおり）
    plan2 = normalize(raw, lines, "夕焼け")
    assert len(plan2.shots[0].body) == 2
