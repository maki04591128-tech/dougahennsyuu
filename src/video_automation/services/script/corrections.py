"""AI が書いたセリフと、直した後のセリフの組を貯める（ローカル LLM の追加学習用）。

台本の生成では、AI の書いたセリフを規則・書き直し・推敲・校閲で直している
（「備えろなのだ」→「備えるのだ」、めたんの「始めましょうか」→「始めるわよ」など）。
この「直す前 → 直した後」の組は、将来 Swallow などを LoRA で追加学習して、口調や文法の誤り
そのものを減らすための教師データになる。

- 保存先は data/training/corrections.jsonl（data/ は Git に入れない。PC の中だけに置く）
- 1 行 1 件の JSON。キーの値（API キーなど）や利用者の個人情報は入れない（台本の文だけ）
- tools/export_training_data.py で SFT / DPO の形式に書き出す
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from video_automation.app.logging import get_logger

log = get_logger(__name__)


class CorrectionLog:
    """直す前後のセリフを JSONL に追記する。path が None なら何もしない。"""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._lock = threading.Lock()

    def record(
        self,
        *,
        stage: str,
        speaker: str,
        before: str,
        after: str,
        rules: str = "",
        context: list[str] | None = None,
        topic: str = "",
        model_key: str | None = None,
        reason: str = "",
    ) -> None:
        if self.path is None or not before.strip() or before.strip() == after.strip():
            return
        row: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "stage": stage,  # generate（生成直後→保存時）/ polish / fact
            "speaker": speaker,
            "before": before,
            "after": after,
            "reason": reason[:200],
            "rules": rules[:1500],
            "context": [c[:200] for c in (context or [])][-3:],
            "topic": topic[:200],
            "model": model_key or "",
        }
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError as exc:  # 記録できなくても台本づくりは止めない
            log.info("correction_log_failed", error=type(exc).__name__)
