"""タスク別モデル割当 (P2-09)。

手動割当があればそれを使い、無ければ Capability 要件と仮割当ヒント（仕様 §2）で自動選択する。
仮割当は実機テストで確定するまでの初期値。
"""

from video_automation.schemas.provider import ModelInfo, ModelState, TaskAssignmentRead, TaskType

TASK_REQUIREMENTS: dict[TaskType, tuple[str, ...]] = {
    TaskType.EDIT_PLAN: ("structured_output",),
    TaskType.STRUCTURED_COMMAND: ("structured_output",),
    TaskType.TOOL_SELECTION: ("tool_calling",),
    TaskType.IMAGE_UNDERSTANDING: ("image_input",),
    TaskType.SCRIPT_GENERATION: ("structured_output",),
    TaskType.SUBTITLE: (),
    TaskType.PROOFREADING: (),
    TaskType.CHAT: (),
}

# 仕様 §2 の初期用途候補（表示名ではなく実モデルIDへの部分一致）
TASK_HINTS: dict[TaskType, tuple[str, ...]] = {
    TaskType.EDIT_PLAN: ("qwen3.5", "qwen35"),
    TaskType.STRUCTURED_COMMAND: ("qwen3.5", "qwen35"),
    TaskType.TOOL_SELECTION: ("qwen3.5", "qwen35"),
    # 台本の生成は日本語向けの Swallow を優先し、無ければ Qwen3.5。
    # 実機の比較（2026-11-25、3 つの題材×2 モデル）で、Qwen3.5 9B は内容の薄い・意味の通らない
    # セリフ（「ねこ型ロボット達！」「乾燥は嫌わ」）が多く、Swallow 8B は具体的で筋の通った
    # 解説になった。Swallow の口調の崩れ（「のだ、」を文頭に置く・「〜なのよ」）は口調の
    # 確認（persona.py）で直す。字幕・校正は Qwen3.5 のまま
    TaskType.SCRIPT_GENERATION: ("swallow", "qwen3.5", "qwen35"),
    TaskType.SUBTITLE: ("qwen3.5", "qwen35"),
    TaskType.PROOFREADING: ("qwen3.5", "qwen35"),
    TaskType.IMAGE_UNDERSTANDING: ("vision", "gemma"),
    TaskType.CHAT: (),
}


def _matches_hint(m: ModelInfo, hints: tuple[str, ...]) -> bool:
    mid = m.model_id.lower()
    return any(h in mid for h in hints)


def _hint_rank(m: ModelInfo, hints: tuple[str, ...]) -> int:
    """前に書いたヒントほど優先する（一致しなければ 0）。"""
    mid = m.model_id.lower()
    return next((len(hints) - i for i, h in enumerate(hints) if h in mid), 0)


def _meets(m: ModelInfo, reqs: tuple[str, ...]) -> bool | None:
    """True=要件を確認済み / None=未確認あり / False=非対応あり。"""
    result: bool | None = True
    for r in reqs:
        v = m.capabilities.supports(r)
        if v is False:
            return False
        if v is None:
            result = None
    return result


def _score(m: ModelInfo, task: TaskType, active_key: str | None) -> tuple[int, int, int, float]:
    return (
        _hint_rank(m, TASK_HINTS[task]),
        1 if m.state == ModelState.INFERENCE_READY else 0,
        1 if m.key == active_key else 0,
        (m.last_test or {}).get("tokens_per_sec") or 0.0,
    )


class TaskRouter:
    def resolve(
        self,
        task: TaskType,
        models: list[ModelInfo],
        assignments: dict[str, str | None],
        active_key: str | None,
    ) -> TaskAssignmentRead:
        by_key = {m.key: m for m in models}
        manual = assignments.get(task.value)
        if manual:
            m = by_key.get(manual)
            if m is not None:
                reason = None
                if _meets(m, TASK_REQUIREMENTS[task]) is False:
                    reason = f"要件 {TASK_REQUIREMENTS[task]} を満たさない可能性があります"
                return TaskAssignmentRead(
                    task=task,
                    mode="manual",
                    model_key=manual,
                    resolved_model_key=manual,
                    reason=reason,
                )
            missing = f"割当モデル {manual} が見つからないため自動選択"
        else:
            missing = None

        reqs = TASK_REQUIREMENTS[task]
        confirmed = [m for m in models if _meets(m, reqs) is True]
        unknown = [m for m in models if _meets(m, reqs) is None]
        pool, note = (confirmed, None) if confirmed else (unknown, "要件未検証のモデルから選択")
        if not pool:
            fallback = active_key if active_key in by_key else None
            return TaskAssignmentRead(
                task=task,
                mode="manual" if manual else "auto",
                model_key=manual,
                resolved_model_key=fallback,
                reason=missing
                or ("要件を満たすモデルなし" + ("（メインモデルを使用）" if fallback else "")),
            )
        best = max(pool, key=lambda m: _score(m, task, active_key))
        reason = "; ".join(x for x in (missing, note) if x) or None
        return TaskAssignmentRead(
            task=task,
            mode="manual" if manual else "auto",
            model_key=manual,
            resolved_model_key=best.key,
            reason=reason,
        )

    def resolve_all(
        self,
        models: list[ModelInfo],
        assignments: dict[str, str | None],
        active_key: str | None,
    ) -> list[TaskAssignmentRead]:
        return [self.resolve(t, models, assignments, active_key) for t in TaskType]
