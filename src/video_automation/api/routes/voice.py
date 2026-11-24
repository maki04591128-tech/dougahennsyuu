"""音声API（仕様 §6: /voice/engines, /voice/speakers, /voice/generate, /voice/prosody,
/voice/dictionary）＋ プレビュー・アクセント編集・音声ファイル取得。"""

from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import FileResponse

from video_automation.api.dependencies import get_container
from video_automation.app.bootstrap import AppContainer
from video_automation.schemas.common import JobRead
from video_automation.schemas.voice import (
    AccentPhrase,
    AccentPhrasesRequest,
    AudioFileRead,
    DictionaryWordRead,
    DictionaryWordRequest,
    EngineStatus,
    MoraEditRequest,
    ProsodyRequest,
    ReadingCheckRequest,
    ReadingCheckResult,
    ReadingRuleIn,
    ReadingRuleRead,
    VoiceGenerateRequest,
    VoicePreviewRequest,
    VoiceSpeaker,
)
from video_automation.services.voice.characters import VoiceCharacterStyle

router = APIRouter(prefix="/voice", tags=["voice"])
scripts = APIRouter(prefix="/scripts", tags=["voice"])


# ---------- 声ごとの口調（声を選ぶとキャラクターの口調で台本を書く） ----------
@router.get("/characters", response_model=list[VoiceCharacterStyle])
async def list_character_styles(c: AppContainer = Depends(get_container)):
    return await c.voice_characters.list()


@router.get("/characters/by_style/{style_id}")
async def character_style_of(style_id: int, c: AppContainer = Depends(get_container)):
    """声（style_id）の話者名と口調。口調が未登録なら style は null。"""
    speaker, style = await c.voice_characters.for_style(style_id)
    return {
        "speaker": speaker,
        "style": style.model_dump() if style else None,
        "speaking_style": style.text() if style else "",
    }


@router.put("/characters/{speaker}", response_model=VoiceCharacterStyle)
async def put_character_style(
    speaker: str, body: VoiceCharacterStyle, c: AppContainer = Depends(get_container)
):
    return await c.voice_characters.put(body.model_copy(update={"speaker": speaker}))


@router.delete("/characters/{speaker}", status_code=204)
async def delete_character_style(speaker: str, c: AppContainer = Depends(get_container)):
    await c.voice_characters.delete(speaker)
    return Response(status_code=204)


@router.get("/engines", response_model=list[EngineStatus])
async def engines(c: AppContainer = Depends(get_container)):
    return await c.voice.engines()


@router.get("/speakers", response_model=list[VoiceSpeaker])
async def speakers(engine_id: str | None = None, c: AppContainer = Depends(get_container)):
    return await c.voice.speakers(engine_id)


@router.post("/accent_phrases", response_model=list[AccentPhrase])
async def accent_phrases(body: AccentPhrasesRequest, c: AppContainer = Depends(get_container)):
    return await c.voice.accent_phrases(body)


@router.post("/accent_phrases/recompute", response_model=list[AccentPhrase])
async def recompute(body: MoraEditRequest, c: AppContainer = Depends(get_container)):
    return await c.voice.recompute(body)


@router.post("/preview", response_class=Response)
async def preview(body: VoicePreviewRequest, c: AppContainer = Depends(get_container)):
    """音声試作（保存しない・承認不要）。"""
    return Response(content=await c.voice.preview(body), media_type="audio/wav")


@router.post("/generate", response_model=JobRead, status_code=202)
async def generate(body: VoiceGenerateRequest, c: AppContainer = Depends(get_container)):
    await c.voice.engines(refresh=False)

    async def run(progress: Any) -> list[dict[str, Any]]:
        rows = await c.voice.generate(body, progress)
        return [r.model_dump(mode="json") for r in rows]

    return c.jobs.submit("voice_generate", run)


@router.post("/prosody", response_model=AudioFileRead, status_code=201)
async def prosody(body: ProsodyRequest, c: AppContainer = Depends(get_container)):
    return await c.voice.regenerate_with_prosody(body)


@router.get("/audio", response_model=list[AudioFileRead])
async def list_audio(
    project_id: str | None = Query(None), c: AppContainer = Depends(get_container)
):
    return await c.voice.list_audio(project_id)


@router.get("/audio/{audio_id}/file")
async def audio_file(audio_id: str, c: AppContainer = Depends(get_container)):
    return FileResponse(await c.voice.audio_path(audio_id), media_type="audio/wav")


@router.get("/dictionary", response_model=list[DictionaryWordRead])
async def list_words(c: AppContainer = Depends(get_container)):
    return await c.voice.list_words()


@router.post("/dictionary", response_model=DictionaryWordRead, status_code=201)
async def add_word(body: DictionaryWordRequest, c: AppContainer = Depends(get_container)):
    return await c.voice.add_word(body)


@router.put("/dictionary/{word_id}", response_model=DictionaryWordRead)
async def update_word(
    word_id: str, body: DictionaryWordRequest, c: AppContainer = Depends(get_container)
):
    return await c.voice.update_word(word_id, body)


@router.delete("/dictionary/{word_id}", status_code=204)
async def delete_word(word_id: str, c: AppContainer = Depends(get_container)) -> None:
    await c.voice.delete_word(word_id)


@router.post("/dictionary/restore")
async def restore(engine_id: str | None = None, c: AppContainer = Depends(get_container)):
    return {"restored": await c.voice.restore_dictionary(engine_id)}


# ---------- 読みのルール（読み間違い対策） ----------
@router.get("/readings", response_model=list[ReadingRuleRead])
async def list_readings(project_id: str | None = None, c: AppContainer = Depends(get_container)):
    return await c.readings.list_rules(project_id)


@router.post("/readings", response_model=ReadingRuleRead, status_code=201)
async def add_reading(body: ReadingRuleIn, c: AppContainer = Depends(get_container)):
    return await c.readings.add(body)


@router.put("/readings/{rule_id}", response_model=ReadingRuleRead)
async def update_reading(
    rule_id: str, body: ReadingRuleIn, c: AppContainer = Depends(get_container)
):
    return await c.readings.update(rule_id, body)


@router.delete("/readings/{rule_id}", status_code=204)
async def delete_reading(rule_id: str, c: AppContainer = Depends(get_container)) -> Response:
    await c.readings.delete(rule_id)
    return Response(status_code=204)


@router.post("/readings/preview")
async def reading_preview(
    text: str, project_id: str | None = None, c: AppContainer = Depends(get_container)
) -> dict[str, str]:
    """字幕の文から、実際に読み上げに使う文を作って返す（確認用）。"""
    return {"text": text, "speech": await c.readings.speech_text(project_id, text)}


@scripts.post("/{script_id}/readings/check", response_model=ReadingCheckResult)
async def check_readings(
    script_id: str, body: ReadingCheckRequest, c: AppContainer = Depends(get_container)
):
    """台本の読みを AI で確認し、読み間違いを読みのルールとして登録する。"""
    return await c.readings.check_script(script_id, body)
