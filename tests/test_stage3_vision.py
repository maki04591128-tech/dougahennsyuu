"""段階3 Q1: 画像を読める LLM で確かめる。"""

import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from video_automation.integrations.ffmpeg.runner import FFmpeg
from video_automation.schemas.qa import DEFAULT_SECTIONS, QALevel, QASectionName
from video_automation.services.llm_tasks.gateway import NoModelError
from video_automation.services.qa import vision
from video_automation.services.qa.qa_service import QAService


def _clip(cid: str, a: int, b: int) -> Any:
    return SimpleNamespace(id=cid, start_frame=a, end_frame=b)


def test_scenes_group_reveal_steps_and_skip_other_clips() -> None:
    clips = [
        _clip("t", 0, 60),
        _clip("c1", 60, 90),
        _clip("c2", 90, 150),  # 順に出すカードの 2 段目（続けて置いた）
        _clip("ch", 150, 240),
        _clip("kw", 100, 130),  # テロップは場面に数えない
        _clip("i", 300, 360),  # 間が空いた画像は別の場面
    ]
    roles = {
        "t": "title_card",
        "c1": "card",
        "c2": "card",
        "ch": "chart",
        "kw": "keyword",
        "i": "image",
    }
    got = vision.scenes(clips, roles)
    assert [(s.role, s.start, s.end) for s in got] == [
        ("title_card", 0, 60),
        ("card", 60, 150),
        ("chart", 150, 240),
        ("image", 300, 360),
    ]
    assert got[1].at == 90 + 42  # 出そろった最後の段の 70% の所
    many = [_clip(f"x{i}", i * 100, i * 100 + 50) for i in range(40)]
    assert len(vision.scenes(many, {c.id: "image" for c in many}, max_n=24)) == 24


class FakeLLM:
    def __init__(self, answers: list[dict[str, Any]], model: bool = True) -> None:
        self.answers = list(answers)
        self.calls: list[Any] = []
        self.model = model

    async def resolve(self, task: Any, model_key: Any = None) -> str:
        if not self.model:
            raise NoModelError("no vision model")
        return "vision"

    async def complete_json(self, task: Any, messages: Any, schema: Any, **kw: Any) -> Any:
        self.calls.append((task, messages, schema))
        return SimpleNamespace(data=self.answers.pop(0))


def _ans(**yes: str) -> dict[str, Any]:
    return {
        k: {"answer": "はい" if k in yes else "いいえ", "where": yes.get(k, "")}
        for k in vision.QUESTIONS
    }


async def test_ask_returns_yes_with_place() -> None:
    llm = FakeLLM([_ans(cut_off="右上のテロップ")])
    got = await vision.ask(llm, b"png", "台本の文")
    assert got["cut_off"] == (True, "右上のテロップ") and got["face_overlap"] == (False, "")
    task, msgs, schema = llm.calls[0]
    assert task.value == "image_understanding" and msgs[0].images
    assert "台本の文" in msgs[0].content and "点" not in schema["properties"]  # 点数は聞かない


async def test_compare_needs_the_same_choice_in_both_orders() -> None:
    assert await vision.compare(FakeLLM([{"choice": "1"}, {"choice": "2"}]), b"a", b"b", "q") == "a"
    assert await vision.compare(FakeLLM([{"choice": "2"}, {"choice": "1"}]), b"a", b"b", "q") == "b"
    # いつも 1 枚目を選ぶ（位置の偏り）なら採らない
    assert (
        await vision.compare(FakeLLM([{"choice": "1"}, {"choice": "1"}]), b"a", b"b", "q") is None
    )


def test_vision_is_not_in_the_default_sections() -> None:
    assert QASectionName.VISION not in DEFAULT_SECTIONS


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg")
async def test_vision_section_asks_once_per_scene(tmp_path: Path, media_dir: Path) -> None:
    data = tmp_path / "data"
    (data / "renders").mkdir(parents=True)
    shutil.copy(media_dir / "clip.mp4", data / "renders" / "out.mp4")
    qa = QAService.__new__(QAService)
    qa.settings = SimpleNamespace(data_dir=data)
    qa.ffmpeg = FFmpeg()
    clips = [_clip("t", 0, 30), _clip("ch", 30, 80)]
    tl = SimpleNamespace(fps_num=30, fps_den=1, clips=clips)

    async def get_timeline(_pid: str) -> Any:
        return tl

    async def context(_pid: str, _sid: str | None) -> Any:
        return {"t": "title_card", "ch": "chart"}, [(30, 80, "AとBは114と97です")]

    qa.timeline = SimpleNamespace(get_timeline=get_timeline)
    qa._vision_context = context  # type: ignore[method-assign]
    render = SimpleNamespace(output_path="renders/out.mp4")
    # 題名の場面の図表の答えは使わない。図表の場面で数値の食い違い・顔の重なり
    qa.llm = FakeLLM([_ans(chart_mismatch="?"), _ans(chart_mismatch="棒の数値", face_overlap="左")])
    sec = await qa._vision("p", render, "s", None)  # type: ignore[arg-type]
    assert sec.metrics == {"scenes": 2, "asked": 2}
    assert [(c.code, c.level, c.message) for c in sec.checks] == [
        ("face_overlap", QALevel.WARNING, "文字が顔・キャラクターに重なっています（左）"),
        ("chart_mismatch", QALevel.WARNING, "図表の数値が台本と合いません（棒の数値）"),
    ]
    assert "114と97" in qa.llm.calls[1][1][0].content
    png = qa.llm.calls[0][1][0].images[0]
    assert png  # 1 場面 1 枚
    # 画像を読めるモデルが無ければ飛ばす
    qa.llm = FakeLLM([], model=False)
    sec = await qa._vision("p", render, "s", None)  # type: ignore[arg-type]
    assert sec.status == "skipped" and sec.checks[0].code == "no_model"
    sec = await qa._vision("p", None, "s", None)
    assert sec.status == "skipped"
