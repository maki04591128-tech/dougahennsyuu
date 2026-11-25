"""声（話者）ごとの口調: 手元の口調データの読み込みと、感情に合うスタイルの選び方。"""

import json

import pytest

from video_automation.schemas.voice import VoiceSpeaker, VoiceStyle
from video_automation.services.voice.characters import (
    BUILTIN,
    VoiceCharacterService,
    load_data_file,
)


class _Voice:
    def __init__(self, speakers):
        self.sp = speakers

    async def speaker_of(self, style_id):
        return next((s for s in self.sp if any(x.id == style_id for x in s.styles)), None)


class _NoDB:
    """組み込み・データだけを見る（ユーザー登録なし）。"""

    def session_factory(self):  # pragma: no cover - 使わない
        raise AssertionError


def _zundamon():
    styles = [
        VoiceStyle(id=3, name="ノーマル"),
        VoiceStyle(id=1, name="あまあま"),
        VoiceStyle(id=76, name="なみだめ"),
        VoiceStyle(id=88, name="ヘロヘロ"),
        VoiceStyle(id=99, name="ハミング", type="frame_decode"),
    ]
    return VoiceSpeaker(engine_id="e", name="ずんだもん", speaker_uuid="u", styles=styles)


def test_data_file_loads_and_skips_bad_rows(tmp_path):
    p = tmp_path / "voicevox_characters.json"
    rows = [
        {"speaker": "ずんだもん", "first_person": "僕", "call_names": {"四国めたん": "めたん"}},
        {"speaker": "", "first_person": "x"},  # 壊れた行は飛ばす
    ]
    p.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    data = load_data_file(p)
    assert list(data) == ["ずんだもん"] and data["ずんだもん"].builtin
    assert data["ずんだもん"].call_names == {"四国めたん": "めたん"}
    assert load_data_file(tmp_path / "none.json") == {}
    (tmp_path / "bad.json").write_text("{", encoding="utf-8")
    assert load_data_file(tmp_path / "bad.json") == {}


def test_metan_builtin_avoids_masculine_endings():
    text = BUILTIN["四国めたん"].text()
    assert "『〜しろよ』" in text and "『〜ですわ』" in text and "『〜よ』" in text
    assert "語尾は" not in text  # 目安は規則（語尾の点検）にしない


@pytest.mark.asyncio
async def test_emotion_picks_a_talk_style_only_from_the_default_voice():
    svc = VoiceCharacterService(_NoDB(), _Voice([_zundamon()]))
    svc.get = lambda name: _get(name)  # type: ignore[method-assign]
    assert await svc.emotion_style_id(3, "sad") == 76
    assert await svc.emotion_style_id(3, "sweet") == 1
    assert await svc.emotion_style_id(3, "neutral") is None
    assert await svc.emotion_style_id(3, "angry") is None  # 対応が無い
    # 人が「あまあま」を選んでいたら変えない
    assert await svc.emotion_style_id(1, "sad") is None


async def _get(name):
    return BUILTIN.get(name)


@pytest.mark.asyncio
async def test_studio_personas_get_official_call_names_between_the_cast():
    from video_automation.services.script.studio import ScriptStudio
    from video_automation.services.voice.characters import VoiceCharacterStyle

    styles = {
        1: VoiceCharacterStyle(speaker="春日部つむぎ", first_person="あーし",
                               call_names={"四国めたん": "めたん先輩", "雨晴はう": "はうちゃん"}),
        2: VoiceCharacterStyle(speaker="四国めたん", first_person="わたくし",
                               call_names={"春日部つむぎ": "つむぎさん"}),
    }  # fmt: skip

    class _VC:
        async def for_style(self, sid):
            st = styles.get(sid)
            return (st.speaker, st) if st else (None, None)

        async def known_as(self):
            return {"四国めたん": ["めたん", "めたんさん"], "春日部つむぎ": ["つむぎ"]}

    studio = ScriptStudio(None, None, _VC())  # type: ignore[arg-type]
    chars = [
        {"name": "つむぎ", "speaking_style": "", "voice": {"style_id": 1}},
        {"name": "めたん", "speaking_style": "", "voice": {"style_id": 2}},
        {"name": "ナレーター", "speaking_style": "", "voice": {}},
    ]
    ps = await studio._personas(chars)
    assert ps["つむぎ"].calls == {"めたん": "めたん先輩"}  # はうは出ないので入れない
    assert ps["めたん"].calls == {"つむぎ": "つむぎさん"}
    assert ps["めたん"].known_as == ["四国めたん", "めたん", "めたんさん"]
    assert ps["ナレーター"].calls == {}


@pytest.mark.asyncio
async def test_quiet_emotions_are_not_used_for_exclamations():
    from video_automation.services.script.studio import ScriptStudio

    class _VC:
        async def emotion_style_id(self, sid, emotion):
            return {"whisper": 36, "tsun": 6}.get(emotion)

    studio = ScriptStudio(None, None, _VC())  # type: ignore[arg-type]
    scenes = [
        {
            "turns": [
                {"speaker": "めたん", "text": "魔法を授けるわよ！", "emotion": "whisper"},
                {"speaker": "めたん", "text": "内緒よ。", "emotion": "whisper"},
                {"speaker": "めたん", "text": "ふん！", "emotion": "tsun"},
            ]
        }
    ]
    got = await studio._emotion_styles([{"name": "めたん", "voice": {"style_id": 2}}], scenes)
    assert got == {("めたん", "whisper"): 36, ("めたん", "tsun"): 6}
    assert scenes[0]["turns"][0]["emotion"] == "neutral"


def test_examples_go_into_the_prompt_as_reference():
    from video_automation.services.script import prompts as P
    from video_automation.services.script.persona import Persona

    p = Persona(
        "めたん", first=["わたくし"], examples=["おなかがすいたわ。なにか食べるものはないかしら"]
    )
    text = P.persona_rules_text({"めたん": p})
    assert "話し方の手本" in text and "そのまま使わない" in text and "おなかがすいたわ" in text


@pytest.mark.asyncio
async def test_polish_rewrites_only_editable_lines_that_keep_the_persona():
    from types import SimpleNamespace

    from video_automation.services.script.persona import parse_style
    from video_automation.services.script.studio import ScriptStudio
    from video_automation.services.voice.characters import BUILTIN

    class _LLM:
        async def complete_json(self, task, msgs, schema, **kw):
            assert "台本:" in msgs[-1].content
            return SimpleNamespace(
                data={
                    "fixes": [
                        {
                            "id": "L2",
                            "problem": "言葉遊び",
                            "text": "明るさを下げるのが一番効くわ。",
                        },
                        {"id": "L1", "problem": "固定", "text": "書き換え"},  # 固定セリフ
                        {"id": "L3", "problem": "x", "text": "ですわ。"},  # 使わない言い方
                    ]
                },
                model_key="m",
            )

    personas = {"めたん": parse_style("めたん", BUILTIN["四国めたん"].text())}
    turns = [
        {"speaker": "めたん", "text": "固定のセリフよ。"},
        {"speaker": "めたん", "text": "貴女は『下げる』と呼ぶのね。"},
        {"speaker": "めたん", "text": "そうね。"},
    ]
    studio = ScriptStudio(None, _LLM())  # type: ignore[arg-type]
    notes = await studio._polish(turns, {1, 2}, personas, None)
    assert [t["text"] for t in turns] == [
        "固定のセリフよ。",
        "明るさを下げるのが一番効くわ。",
        "そうね。",
    ]
    assert notes == ["会話として不自然なセリフを 1 件書き直しました"]
