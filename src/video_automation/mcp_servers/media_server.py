"""mcp_media サーバー。内部サービスを呼ぶアダプター。

Phase 3: 音声 / Phase 4: generate_subtitles・export_subtitles /
Phase 6: search_assets・inspect_license・download_asset・generate_music・add_bgm。
LLM 由来の素材取得・タイムライン変更はユーザー承認待ちになる（ライセンスの明示承認もできない）。
"""

from typing import Any

from mcp.server.fastmcp import FastMCP

from video_automation.mcp_servers._shared import get_container
from video_automation.schemas.asset_source import (
    AddBgmRequest,
    AssetDownloadRequest,
    AssetRef,
    AssetSearchRequest,
    LicenseInspectRequest,
    MediaType,
    MusicGenerateRequest,
    Orientation,
    SourceName,
)
from video_automation.schemas.subtitle import (
    SubtitleExportRequest,
    SubtitleFormat,
    SubtitleGenerateRequest,
    SubtitleSyncRequest,
)
from video_automation.schemas.timeline import CommandOrigin
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


# ---------------- Phase 4: 字幕 ----------------
async def generate_subtitles(
    script_id: str, max_chars_per_line: int | None = None, max_lines: int = 2
) -> list[dict[str, Any]]:
    """台本から字幕を作り、音声の実時間で同期する（音声生成済みであること）。"""
    c = await get_container()
    await c.subtitles.generate(
        SubtitleGenerateRequest(
            script_id=script_id, max_chars_per_line=max_chars_per_line, max_lines=max_lines
        )
    )
    cues = await c.subtitles.synchronize(SubtitleSyncRequest(script_id=script_id))
    return [q.model_dump() for q in cues]


async def export_subtitles(script_id: str, format: str = "ass") -> dict[str, Any]:
    """同期済み字幕を ASS または SRT で書き出す。"""
    c = await get_container()
    r = await c.subtitles.export(
        SubtitleExportRequest(script_id=script_id, format=SubtitleFormat(format))
    )
    return {"path": r.path, "format": r.format.value, "cue_count": r.cue_count}


mcp.tool(name="generate_subtitles", description="台本から字幕を生成し音声に同期する")(
    generate_subtitles
)
mcp.tool(name="export_subtitles", description="字幕をASS/SRTで書き出す")(export_subtitles)


# ---------------- Phase 6: 素材・音楽生成 ----------------


async def search_assets(
    query: str,
    media_type: str = "image",
    sources: list[str] | None = None,
    orientation: str = "any",
    max_duration_sec: float | None = None,
    allowed_only: bool = True,
    limit: int = 10,
) -> dict[str, Any]:
    """フリー素材を検索する（承認不要）。既定ではそのまま使えるライセンスのものだけ返す。"""
    c = await get_container()
    resp = await c.asset_sources.search(
        AssetSearchRequest(
            query=query,
            media_type=MediaType(media_type),
            sources=[SourceName(x) for x in sources] if sources else None,
            orientation=Orientation(orientation),
            max_duration_sec=max_duration_sec,
            allowed_only=allowed_only,
        )
    )
    keep = (
        "source",
        "source_id",
        "media_type",
        "title",
        "creator",
        "width",
        "height",
        "duration_sec",
        "verdict",
        "attribution",
    )
    return {
        "results": [
            {**{k: getattr(r, k) for k in keep}, "license": r.license.code}
            for r in resp.results[: max(1, min(limit, 50))]
        ],
        "errors": resp.errors,
        "skipped": resp.skipped,
    }


async def inspect_license(
    source: str | None = None,
    source_id: str | None = None,
    media_type: str | None = None,
    asset_id: str | None = None,
) -> dict[str, Any]:
    """素材のライセンス条件と利用可否（allowed / needs_ack）を確認する。"""
    c = await get_container()
    ref = (
        AssetRef(source=SourceName(source), source_id=source_id, media_type=MediaType(media_type))
        if source and source_id and media_type
        else None
    )
    r = await c.asset_sources.inspect(LicenseInspectRequest(ref=ref, asset_id=asset_id))
    return r.model_dump(mode="json")


async def download_asset(
    project_id: str, source: str, source_id: str, media_type: str
) -> dict[str, Any]:
    """素材をプロジェクトに取り込むよう要求する（ユーザーの承認後に取得）。"""
    c = await get_container()
    r = await c.asset_sources.download(
        AssetDownloadRequest(
            project_id=project_id,
            source=SourceName(source),
            source_id=source_id,
            media_type=MediaType(media_type),
            origin="llm",
        )
    )
    out = r.model_dump(mode="json")
    if r.status == "PENDING_APPROVAL":
        out["note"] = "ユーザーの承認後に取得されます"
    return out


async def generate_music(
    project_id: str,
    prompt: str,
    duration_sec: float = 60.0,
    instrumental: bool = True,
    lyrics: str = "",
    bpm: int | None = None,
    key_scale: str = "",
    seed: int | None = None,
) -> dict[str, Any]:
    """ACE-Step 1.5 で BGM を生成し素材として登録する。生成には数十秒〜数分かかる（Job）。"""
    c = await get_container()
    req = MusicGenerateRequest(
        project_id=project_id,
        prompt=prompt,
        duration_sec=duration_sec,
        instrumental=instrumental,
        lyrics=lyrics,
        bpm=bpm,
        key_scale=key_scale,
        seed=seed,
    )

    async def run(progress: Any) -> dict[str, Any]:
        out = await c.music.generate(req, progress)
        return {"assets": [a.model_dump(mode="json") for a in out]}

    job = c.jobs.submit("music_generate", run)
    return {"job_id": job.id, "status": job.status.value}


async def add_bgm(
    project_id: str, asset_id: str, volume: float = 0.25, loop: bool = True
) -> dict[str, Any]:
    """BGM トラックの追加を提案する（承認待ち）。曲が短ければ繰り返して全体を埋める。"""
    c = await get_container()
    row = await c.timeline.add_bgm(
        project_id, AddBgmRequest(asset_id=asset_id, volume=volume, loop=loop), CommandOrigin.LLM
    )
    return {
        "command_id": row.id,
        "status": row.status.value,
        "note": "ユーザーの承認後に適用されます",
    }


for _fn, _desc in (
    (search_assets, "フリー素材（画像・動画・効果音）を検索する"),
    (inspect_license, "素材のライセンス条件と利用可否を確認する"),
    (download_asset, "素材の取得を要求する（ユーザー承認後に取得）"),
    (generate_music, "ACE-Step 1.5 で BGM を生成する（Job）"),
    (add_bgm, "BGM トラックの追加を提案する（承認待ち）"),
):
    mcp.tool(name=_fn.__name__, description=_desc)(_fn)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
