"""Phase 8 の純粋関数: LLM 出力の JSON 抽出・台本の版差分・タイムライン差分・推定読み上げ時間。"""

import pytest

from video_automation.services.llm_tasks.gateway import LLMOutputError, extract_json
from video_automation.services.orchestrator.diff import describe_snapshots
from video_automation.services.script.studio import diff_snapshots, estimated_sec


def test_extract_json_strips_think_and_fences():
    assert extract_json('<think>{"x": 1}</think>\n```json\n{"a": [1, 2]}\n```') == {"a": [1, 2]}
    assert extract_json('前置き {"a": 1} と {"b": {"c": 2}}') == {"b": {"c": 2}}
    with pytest.raises(LLMOutputError):
        extract_json("<think>考え中</think>JSONなし")


def test_estimated_sec_ignores_punctuation():
    assert estimated_sec("こんにちは。", 5.0) == pytest.approx(1.3)
    assert estimated_sec("「あ」、（い）！", 2.0) == pytest.approx(1.3)


def _t(i, text, speaker="A"):
    return {"id": f"t{i}", "speaker": speaker, "text": text}


def test_script_diff_kinds():
    a = {"turns": [_t(1, "一"), _t(2, "二"), _t(3, "三")], "settings": {}, "characters": []}
    b = {
        "turns": [_t(3, "三"), _t(1, "壱"), _t(4, "四", "B")],
        "settings": {"topic": "x"},
        "characters": [],
    }
    d = diff_snapshots(a, b, 1, 2)
    kinds = {(c.kind, c.turn_id) for c in d.changes}
    assert kinds == {("changed", "t1"), ("added", "t4"), ("removed", "t2"), ("moved", "t3")}
    assert d.settings_changed and not d.characters_changed


def _clip(**kw):
    base = {
        "id": "c",
        "track_id": "v",
        "asset_id": "a",
        "start_frame": 0,
        "end_frame": 30,
        "source_in_frame": 0,
        "source_out_frame": 30,
        "transform": {},
        "volume": 1.0,
        "fade_in_frames": 0,
        "fade_out_frames": 0,
        "animation": None,
    }
    return {**base, **kw}


def test_timeline_diff_describes_changes():
    tracks = {"v": {"id": "v", "name": "映像", "kind": "video", "muted": False}}
    before = {"tracks": tracks, "clips": {"c": _clip(), "d": _clip(id="d"), "e": None}}
    after = {
        "tracks": tracks,
        "clips": {
            "c": _clip(start_frame=30, end_frame=60, source_in_frame=0, source_out_frame=30),
            "d": None,
            "e": _clip(id="e", volume=0.5, animation={"keyframes": [{}, {}]}),
        },
    }
    out = describe_snapshots(before, after, 30.0, {"a": "clip.mp4"})
    assert "クリップ 「clip.mp4」: 移動 00:00.00 → 00:01.00" in out
    assert any(x.startswith("クリップ削除: 「clip.mp4」") for x in out)
    assert any("音量 0.50" in x and "アニメーションあり" in x for x in out)
    out = describe_snapshots(
        {"tracks": {}, "clips": {"c": _clip()}},
        {"tracks": {}, "clips": {"c": _clip(end_frame=45, source_out_frame=45, fade_out_frames=9)}},
        30.0,
    )
    assert out == [
        "クリップ 「a」: 区間 00:00.00〜00:01.00 → 00:00.00〜00:01.50、フェードアウト 0f → 9f"
    ]


def test_merge_timeline_actions_into_one_batch():
    from video_automation.services.orchestrator.orchestrator import merge_timeline_actions

    acts = [
        {"tool": "search_assets", "args": {"query": "x"}},
        {"tool": "edit_timeline", "args": {"description": "配置", "command": {"type": "add_clip"}}},
        {
            "tool": "edit_timeline",
            "args": {
                "description": "フェード",
                "command": {"type": "batch", "commands": [{"type": "set_clip_props"}]},
            },
        },
        {"tool": "render_project", "args": {}},
    ]
    out = merge_timeline_actions(acts)
    assert [a["tool"] for a in out] == ["search_assets", "edit_timeline", "render_project"]
    assert out[1]["args"] == {
        "description": "配置 / フェード",
        "command": {
            "type": "batch",
            "commands": [{"type": "add_clip"}, {"type": "set_clip_props"}],
        },
    }
    single = acts[:2]
    assert merge_timeline_actions(single) == single


def test_normalize_edit_args_wraps_bare_commands():
    from video_automation.services.orchestrator.orchestrator import normalize_edit_args

    assert normalize_edit_args({"type": "delete_clip", "clip_id": "c", "description": "x"}) == {
        "description": "x",
        "command": {"type": "delete_clip", "clip_id": "c"},
    }
    assert normalize_edit_args({"commands": [{"type": "delete_clip", "clip_id": "c"}]}) == {
        "description": "",
        "command": {"type": "batch", "commands": [{"type": "delete_clip", "clip_id": "c"}]},
    }
    ok = {"description": "", "command": {"type": "delete_clip", "clip_id": "c"}}
    assert normalize_edit_args(ok) == ok
    nulls = {"command": {"type": "add_clip", "volume": None, "transform": {"x": None, "y": 1}}}
    assert normalize_edit_args(nulls) == {"command": {"type": "add_clip", "transform": {"y": 1}}}
    assert normalize_edit_args({"foo": 1}) == {"foo": 1}


def test_unknown_fields_detects_invented_keys():
    from video_automation.services.orchestrator.orchestrator import (
        EditTimelineArgs,
        unknown_fields,
    )

    raw = {
        "type": "batch",
        "commands": [
            {
                "type": "add_clip",
                "track_id": "t",
                "asset_id": "a",
                "start_frame": 0,
                "fade_in_frames": 15,
                "fade_in": 15,
                "transform": {"x": 0, "blur": 3},
            },
            {"type": "delete_clip", "clip_id": "c"},
        ],
    }
    a = EditTimelineArgs.model_validate({"command": raw})
    assert unknown_fields(raw, a.command) == ["add_clip.fade_in", "?.blur"]
