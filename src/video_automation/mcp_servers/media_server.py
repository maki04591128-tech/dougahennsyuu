"""mcp_media サーバー（Phase 3: 音声）。内部の VoiceService を呼ぶアダプター。

素材・字幕・音楽生成のツールは Phase 4 / 6 で追加する。
"""

from typing import Any

from mcp.server.fastmcp import FastMCP

from video_automation.mcp_servers._shared import get_container
from video_automation.schemas.voice import (
    DictionaryWordRequest,
    Prosody,
    ProsodyRequest,
    VoiceGenerateItem,
    VoiceGenerateRequest,
)

mcp = FastMCP("mcp_media")


async def list_speakers(engine_id: str | None = None) -> list[dict[str, Any]]:
    """音声エンジンの話者とスタイルIDの一覧。"""
    c = await get_container()
    return [s.model_dump() for s in await c.voice.speakers(engine_id)]


async def generate_voice(
    text: str,
    style_id: int,
    project_id: str | None = None,
    script_segment_id: str | None = None,
    speed_scale: float | None = None,
    pitch_scale: float | None = None,
    intonation_scale: float | None = None,
) -> dict[str, Any]:
    """テキストから音声を生成して保存し、ファイル情報と区間情報を返す。"""
    c = await get_container()
    req = VoiceGenerateRequest(
        project_id=project_id,
        items=[
            VoiceGenerateItem(
                text=text,
                style_id=style_id,
                script_segment_id=script_segment_id,
                prosody=Prosody(
                    speed_scale=speed_scale,
                    pitch_scale=pitch_scale,
                    intonation_scale=intonation_scale,
                ),
            )
        ],
    )
    return (await c.voice.generate(req))[0].model_dump(mode="json")


async def adjust_prosody(
    audio_id: str,
    speed_scale: float | None = None,
    pitch_scale: float | None = None,
    intonation_scale: float | None = None,
    volume_scale: float | None = None,
) -> dict[str, Any]:
    """既存音声の抑揚・話速等を変更して作り直す（元音声は履歴として残る）。"""
    c = await get_container()
    req = ProsodyRequest(
        audio_id=audio_id,
        prosody=Prosody(
            speed_scale=speed_scale,
            pitch_scale=pitch_scale,
            intonation_scale=intonation_scale,
            volume_scale=volume_scale,
        ),
    )
    return (await c.voice.regenerate_with_prosody(req)).model_dump(mode="json")


async def register_pronunciation(
    surface: str, pronunciation: str, accent_type: int, priority: int = 5
) -> dict[str, Any]:
    """読み辞書に単語を登録する（pronunciation はカタカナ）。"""
    c = await get_container()
    row = await c.voice.add_word(
        DictionaryWordRequest(
            surface=surface, pronunciation=pronunciation, accent_type=accent_type, priority=priority
        )
    )
    return {"id": row.id, "surface": row.surface, "pronunciation": row.pronunciation}


mcp.tool(name="list_speakers", description="音声合成の話者・スタイル一覧")(list_speakers)
mcp.tool(name="generate_voice", description="テキストから音声を生成して保存する")(generate_voice)
mcp.tool(name="adjust_prosody", description="既存音声の抑揚・話速を調整して再生成する")(
    adjust_prosody
)
mcp.tool(name="register_pronunciation", description="読み辞書に単語を登録する")(
    register_pronunciation
)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
