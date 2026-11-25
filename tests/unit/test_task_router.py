from video_automation.schemas.provider import (
    Capabilities,
    ModelInfo,
    ModelState,
    ProviderType,
    TaskType,
)
from video_automation.services.llm_tasks.prompts import PromptRegistry
from video_automation.services.llm_tasks.task_router import TaskRouter


def _m(mid, **caps):
    return ModelInfo(
        key=f"ollama-local::{mid}",
        provider_id="ollama-local",
        provider_type=ProviderType.OLLAMA,
        model_id=mid,
        display_name=mid,
        state=ModelState.MODEL_DISCOVERED,
        capabilities=Capabilities(**caps),
    )


QWEN = _m("qwen3.5-9b-local:latest", structured_output=True, tool_calling=True, image_input=False)
SWALLOW = _m(
    "qwen3-swallow-8b-local:latest",
    structured_output=True,
    tool_calling=False,
    tool_calling_fallback=True,
    image_input=False,
)
GEMMA = _m("gemma4-e4b-local:latest", structured_output=True, tool_calling=True, image_input=False)
MODELS = [GEMMA, QWEN, SWALLOW]


def test_auto_uses_spec_hints_and_requirements():
    r = TaskRouter()
    assert r.resolve(TaskType.TOOL_SELECTION, MODELS, {}, None).resolved_model_key == QWEN.key
    # 台本の生成は Swallow を優先し、無ければ Qwen3.5
    assert r.resolve(TaskType.SCRIPT_GENERATION, MODELS, {}, None).resolved_model_key == SWALLOW.key
    no_swallow = [GEMMA, QWEN]
    assert (
        r.resolve(TaskType.SCRIPT_GENERATION, no_swallow, {}, None).resolved_model_key == QWEN.key
    )
    assert r.resolve(TaskType.PROOFREADING, MODELS, {}, None).resolved_model_key == QWEN.key


def test_image_task_prefers_a_vision_model():
    vision = _m("qwen3.5-9b-vision:latest", structured_output=True, image_input=True)
    res = TaskRouter().resolve(TaskType.IMAGE_UNDERSTANDING, [*MODELS, vision], {}, QWEN.key)
    assert res.resolved_model_key == vision.key


def test_image_task_without_vision_falls_back_to_active():
    res = TaskRouter().resolve(TaskType.IMAGE_UNDERSTANDING, MODELS, {}, QWEN.key)
    assert res.resolved_model_key == QWEN.key and "要件を満たすモデルなし" in res.reason


def test_unknown_capability_used_only_when_nothing_confirmed():
    unknown = _m("llama:latest")  # 全て None
    res = TaskRouter().resolve(TaskType.TOOL_SELECTION, [unknown], {}, None)
    assert res.resolved_model_key == unknown.key and "未検証" in res.reason


def test_manual_assignment_and_missing_model():
    r = TaskRouter()
    manual = r.resolve(TaskType.CHAT, MODELS, {"chat": GEMMA.key}, None)
    assert manual.mode == "manual" and manual.resolved_model_key == GEMMA.key
    gone = r.resolve(TaskType.CHAT, MODELS, {"chat": "x::gone"}, None)
    assert gone.resolved_model_key is not None and "見つからない" in gone.reason
    warn = r.resolve(TaskType.IMAGE_UNDERSTANDING, MODELS, {"image_understanding": QWEN.key}, None)
    assert warn.resolved_model_key == QWEN.key and "満たさない" in warn.reason
    assert len(r.resolve_all(MODELS, {}, None)) == len(TaskType)


def test_prompt_registry_renders_versioned_template():
    t = PromptRegistry().get(TaskType.SCRIPT_GENERATION, "qwen3")
    msgs = t.render(
        topic="AI", audience="初心者", duration="60", characters="A, B", constraints="なし"
    )
    assert t.version == "script.full.v1" and t.json_schema is not None
    assert "AI" in msgs[1].content and msgs[0].role == "system"
    assert PromptRegistry().get(TaskType.TOOL_SELECTION).task == TaskType.CHAT
