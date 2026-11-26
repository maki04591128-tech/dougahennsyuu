import importlib.util
import json
from pathlib import Path

import pytest

from video_automation.services.script.corrections import CorrectionLog


def test_records_only_real_changes(tmp_path: Path):
    log = CorrectionLog(tmp_path / "training" / "c.jsonl")
    log.record(stage="generate", speaker="ずんだもん", before="備えろなのだ", after="備えるのだ")
    log.record(stage="generate", speaker="ずんだもん", before="同じなのだ", after="同じなのだ")
    rows = [
        json.loads(x) for x in (tmp_path / "training" / "c.jsonl").read_text("utf-8").splitlines()
    ]
    assert len(rows) == 1 and rows[0]["after"] == "備えるのだ" and rows[0]["stage"] == "generate"


def test_disabled_log_writes_nothing(tmp_path: Path):
    CorrectionLog(None).record(stage="x", speaker="a", before="b", after="c")
    assert not any(tmp_path.iterdir())


def test_export_writes_sft_and_dpo(tmp_path: Path):
    src = tmp_path / "c.jsonl"
    log = CorrectionLog(src)
    for _ in range(2):  # 同じ組は 1 件にまとめる
        log.record(
            stage="polish",
            speaker="四国めたん",
            before="始めましょうか",
            after="始めるわよ",
            rules="四国めたん: 一人称は「わたくし」",
            context=["ずんだもん: 何から始めるのだ？"],
            topic="節電",
        )
    # 内容の誤りの修正（fact）は学習に使わない（セリフ回しだけを学ぶ）
    log.record(
        stage="fact", speaker="ずんだもん", before="電気が貯まるのだ", after="電気が減るのだ"
    )
    path = Path(__file__).resolve().parents[2] / "tools" / "export_training_data.py"
    spec = importlib.util.spec_from_file_location("export_training_data", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    res = mod.export(src, tmp_path / "out")
    assert res["total"] == 1 and "stage_fact" not in res
    dpo = json.loads((tmp_path / "out" / "dpo.jsonl").read_text("utf-8"))
    assert dpo["chosen"] == "始めるわよ" and dpo["rejected"] == "始めましょうか"
    assert "節電" in dpo["prompt"] and "わたくし" in dpo["prompt"]
    sft = json.loads((tmp_path / "out" / "sft.jsonl").read_text("utf-8"))
    assert sft["messages"][-1] == {"role": "assistant", "content": "始めるわよ"}


@pytest.mark.asyncio
async def test_generation_records_persona_fixes(tmp_path: Path):
    from types import SimpleNamespace

    from video_automation.services.script.persona import parse_style
    from video_automation.services.script.studio import ScriptStudio

    studio = ScriptStudio(None, None, None, CorrectionLog(tmp_path / "c.jsonl"))  # type: ignore[arg-type]
    t = {"speaker": "ずんだもん", "text": "備えるのだ"}
    personas = {"ずんだもん": parse_style("ずんだもん", "一人称は「ボク」。語尾は「〜のだ」")}
    studio._record_corrections(
        "generate",
        [{"turns": [t]}],
        {id(t): "備えろなのだ"},
        personas,
        SimpleNamespace(topic="防災"),
        "m",
    )
    row = json.loads((tmp_path / "c.jsonl").read_text("utf-8"))
    assert row["before"] == "備えろなのだ" and row["topic"] == "防災" and "ボク" in row["rules"]


def test_export_drops_broken_corrections(tmp_path: Path):
    src = tmp_path / "c.jsonl"
    log = CorrectionLog(src)
    log.record(
        stage="generate", speaker="ずんだもん", before="聞かせてくれ！", after="聞かせてくれのだ！"
    )
    log.record(
        stage="generate",
        speaker="ずんだもん",
        before="一口大にしなければのだ",
        after="一口大にしないのだ",
    )
    log.record(stage="generate", speaker="ずんだもん", before="完了だ！", after="完了なのだ！")
    path = Path(__file__).resolve().parents[2] / "tools" / "export_training_data.py"
    spec = importlib.util.spec_from_file_location("export_training_data", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    res = mod.export(src, tmp_path / "out")
    assert res["total"] == 1 and res["dropped"] == 2


def test_script_records_are_exported_only_when_clean(tmp_path: Path):
    log = CorrectionLog(tmp_path / "corrections.jsonl")
    msgs = [{"role": "system", "content": "台本作家"}, {"role": "user", "content": "テーマ: 節電"}]

    def out(*texts):
        return {
            "scenes": [
                {"purpose": "導入", "turns": [{"speaker": "ずんだもん", "text": t} for t in texts]}
            ],
            "notes": [],
        }

    good = out("節電の話なのだ。", "まず照明を消すのだ。", "次はエアコンなのだ。", "まとめなのだ。")
    log.record_script(messages=msgs, output=good, model_key="ollama::swallow", lines=4)
    log.record_script(messages=msgs, output=good, model_key="ollama::swallow", lines=4)  # 重複
    log.record_script(messages=msgs, output=good, model_key="x", issues=1, lines=4)  # 問題が残る
    log.record_script(
        messages=msgs,
        output=out("聞かせてくれのだ！", "a", "b", "c"),  # 壊れた形
        model_key="x",
        lines=4,
    )
    log.record_script(messages=msgs, output=good, model_key="ollama::swallow-tuned", lines=4)
    path = Path(__file__).resolve().parents[2] / "tools" / "export_training_data.py"
    spec = importlib.util.spec_from_file_location("export_training_data", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    res = mod.export_scripts(tmp_path / "scripts.jsonl", tmp_path / "out")
    assert res == {"scripts": 1, "scripts_dropped": 4}
    row = json.loads((tmp_path / "out" / "scripts_sft.jsonl").read_text("utf-8"))
    assert [m["role"] for m in row["messages"]] == ["system", "user", "assistant"]
    assert json.loads(row["messages"][-1]["content"]) == good
