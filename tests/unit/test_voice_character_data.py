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
    assert "『〜しろよ』" in text and "『〜よ』" in text
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
