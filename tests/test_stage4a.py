"""段階4a: 図表（D3・S10）、カードの項目の出し方（D1）、量の揃え（D4）、強調テロップ（C3）、字幕の長さ（C4）。"""

import json
import os
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path

import pytest

from video_automation.schemas.animation import CardRequest
from video_automation.schemas.design import Purpose
from video_automation.schemas.direction import DirectionPlan, Shot, ShotKind
from video_automation.services.animation.entrance import element_animation
from video_automation.services.design.profiles import PROFILES
from video_automation.services.direction.charts import anim_frames, chart_kind
from video_automation.services.direction.plan import normalize
from video_automation.services.rendering.card import c3_runs, card_filter, card_layout, text_width


def _chart(values, labels, unit="") -> Shot:
    return Shot(kind=ShotKind.CHART, from_line=1, values=values, labels=labels, unit=unit)


def test_chart_kind_follows_the_shape_of_the_data() -> None:
    assert chart_kind(_chart([50, 30, 20], ["A", "B", "C"], "%")) == "donut"
    assert chart_kind(_chart([70, 30], ["A", "B"], "%")) == "donut"
    assert chart_kind(_chart([50, 30, 10], ["A", "B", "C"], "%")) == "bar"  # 合計が 100% でない
    months = ["1月", "2月", "3月", "4月", "5月"]
    assert chart_kind(_chart([1, 2, 3, 4, 5], months)) == "line"
    assert chart_kind(_chart([1, 2, 3, 4], months[:4])) == "bar"  # 時点が 4 つ
    assert chart_kind(_chart([2019, 2, 3, 4, 5], ["A", "B", "C", "D", "E"])) == "bar"
    assert chart_kind(_chart([37], ["100人中"], "人"), "100人中37人が使っています") == "dots"


def test_anim_frames_follow_d3() -> None:
    assert anim_frames("bar", 3) == 3 * 2 + 10
    assert anim_frames("donut", 4) == 3 * 3 + 10
    assert anim_frames("line", 6) == 3 * 5 + 10 + 4
    assert anim_frames("dots", 1) == 3 * 9 + 10 + 4


def test_single_value_per_hundred_chart_survives_normalize() -> None:
    lines = ["日本では100人中37人が使っています。", "意外と少ないですね。"]
    plan = DirectionPlan(
        shots=[Shot(kind=ShotKind.CHART, from_line=1, values=[37], labels=[], unit="人")]
    )
    out = normalize(plan, lines)
    assert out.shots and out.shots[0].labels == ["100人中"] and not out.notes
    bad = DirectionPlan(shots=[Shot(kind=ShotKind.CHART, from_line=2, values=[37], labels=["x"])])
    assert not normalize(bad, lines).shots  # 100 人中でない 1 つの値は外す


def test_card_item_entrance_follows_d1() -> None:
    spec = PROFILES[Purpose.EXPLAINER].motion
    anim = element_animation(60, spec, Fraction(30), entrance="item", item_dy=16 / 1080)
    k0, k1 = anim.keyframes[0], anim.keyframes[1]
    assert (k0.frame, k0.opacity, k0.y, k0.easing.value) == (0, 0.0, 16 / 1080, "cubic_out")
    assert (k1.frame, k1.opacity, k1.y) == (11, 1.0, 0.0)


def _card_drawn(req: CardRequest, tmp: Path) -> str:
    lay = card_layout(req, 1920, 1080)
    return card_filter(req, lay, Path("/f.ttf"), tmp)


def test_item_overlay_and_base_split_the_card(tmp_path: Path) -> None:
    base = dict(project_id="p", heading="見出し", body=["一", "二", "三"], style="point", reveal=2)
    full = _card_drawn(CardRequest(**base), tmp_path)
    hide = _card_drawn(CardRequest(**base, hide_item=1), tmp_path)
    only = _card_drawn(CardRequest(**base, only_item=1), tmp_path)
    assert full.count("drawtext") == 3  # 見出し・一・二
    assert hide.count("drawtext") == 2  # 二を描かない
    assert only.count("drawtext") == 1 and "0x000000@0" in only  # 二だけ、透明の下地
    assert only.count("drawbox") == 2 and "replace=1" in only.split(",")[-2]  # 下地と点


def test_amounts_are_right_aligned() -> None:
    req = CardRequest(project_id="p", heading="材料", body=["卵 3個", "高温"], style="point")
    lay = card_layout(req, 1920, 1080)
    lines = {ln.text: ln for ln in lay.lines}
    assert set(lines) == {"材料", "卵", "3個", "高温"}
    right = lay.width - round(min(1920, 1080) * 0.034)
    assert abs(lines["3個"].x + text_width("3個", lines["3個"].size) - right) <= 1
    assert lines["卵"].x == lines["高温"].x


def test_c3_runs_and_keyword_layout() -> None:
    assert c3_runs("熱中症に注意しよう") == [("熱中症に注意", 1.0), ("しよう", 0.68)]
    assert c3_runs("しかし実験では違う") == [("しかし", 0.68), ("実験では違う", 1.0)]
    assert c3_runs("40℃で75%") == [("40℃で75%", 1.0)]
    assert c3_runs("ねこ") == [("ねこ", 1.0)]  # 全部が小さくなるなら分けない
    lay = card_layout(
        CardRequest(project_id="p", heading="注意しよう", style="keyword"), 1920, 1080
    )
    big, small = lay.lines
    assert small.size == round(big.size * 0.68) and small.y > big.y
    assert small.x == round(big.x + text_width("注意", big.size))


def test_text_width_counts_ambiguous_symbols_as_full() -> None:
    assert text_width("℃", 10) == 10 and text_width("%", 10) == 6 and text_width("あ", 10) == 10


BPY = os.environ.get("VA_BPY_PYTHON")  # bpy を入れた Python（例: /tmp/bpyenv/bin/python）


@pytest.mark.skipif(not BPY, reason="VA_BPY_PYTHON is not set")
@pytest.mark.parametrize(
    ("kind", "values", "labels"),
    [
        ("bar", [30, 50, 20], ["A", "B", "C"]),
        ("line", [10, 25, 18, 40, 32], ["1月", "2月", "3月", "4月", "5月"]),
        ("donut", [50, 30, 20], ["A", "B", "C"]),
        ("dots", [37], ["100人中"]),
    ],
)
def test_infographic_kinds_render_in_blender(tmp_path: Path, kind, values, labels) -> None:
    font = (
        shutil.which("fc-match")
        and subprocess.run(
            ["fc-match", "-f", "%{file}", "Noto Sans CJK JP:bold"], capture_output=True, text=True
        ).stdout
    )
    args = {
        "kind": kind,
        "title": "t",
        "values": values,
        "labels": labels,
        "unit": "%",
        "decimals": 0,
        "width": 320,
        "height": 180,
        "fps": 30,
        "frames": 20,
        "samples": 1,
        "font_path": font or None,
        "text_color": [1, 1, 1, 1],
        "accent_colors": [[0.2, 0.6, 1, 1], [1, 0.6, 0.2, 1], [0.2, 0.8, 0.4, 1]],
        "output_dir": str(tmp_path / "out"),
        "result_path": str(tmp_path / "r.json"),
    }
    (tmp_path / "a.json").write_text(json.dumps(args), encoding="utf-8")
    script = Path(__file__).resolve().parents[1] / (
        "src/video_automation/integrations/blender/scripts/infographic.py"
    )
    subprocess.run(
        [BPY, str(script), "--", str(tmp_path / "a.json")],
        check=True,
        capture_output=True,
        timeout=300,
    )
    assert json.loads((tmp_path / "r.json").read_text())["frames"] == 20
