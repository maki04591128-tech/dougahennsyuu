"""コンポーネント初期化。API と MCP サーバーで同じサービス構成を使う。"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from video_automation.app.logging import configure_logging
from video_automation.app.secrets import SecretProtector
from video_automation.app.settings import Settings
from video_automation.core.events.event_bus import EventBus
from video_automation.core.jobs.job_manager import JobManager
from video_automation.database.database import Database
from video_automation.integrations.blender.runner import Blender
from video_automation.integrations.ffmpeg.runner import FFmpeg
from video_automation.services.animation.preset_service import PresetService
from video_automation.services.animation.scene_service import SceneService
from video_automation.services.assets.asset_source_service import AssetSourceService
from video_automation.services.assets.downloader import Resolver
from video_automation.services.characters.service import CharacterService
from video_automation.services.design.edits import DesignEditLog
from video_automation.services.design.series import SeriesStore
from video_automation.services.design.service import DesignService
from video_automation.services.direction.service import DirectionService
from video_automation.services.llm_tasks.gateway import LLMGateway
from video_automation.services.llm_tasks.task_router import TaskRouter
from video_automation.services.model_discovery.inference_test import InferenceTester
from video_automation.services.model_discovery.provider_manager import ProviderManager
from video_automation.services.music.music_service import MusicService
from video_automation.services.orchestrator.approvals import ApprovalService
from video_automation.services.orchestrator.history import HistoryService
from video_automation.services.orchestrator.orchestrator import Orchestrator, ToolRouter
from video_automation.services.qa.qa_service import QAService
from video_automation.services.rendering.render_service import RenderService
from video_automation.services.rendering.title_card import TitleCardService
from video_automation.services.screencast.service import ScreencastService
from video_automation.services.script.corrections import CorrectionLog
from video_automation.services.script.studio import ScriptStudio
from video_automation.services.skills.library import SkillLibrary
from video_automation.services.subtitle.subtitle_service import SubtitleService
from video_automation.services.timeline.timeline_service import TimelineService
from video_automation.services.transcribe.service import TranscriptionService
from video_automation.services.voice.characters import VoiceCharacterService
from video_automation.services.voice.reading_service import ReadingService
from video_automation.services.voice.voice_service import VoiceService


@dataclass
class AppContainer:
    settings: Settings
    db: Database
    bus: EventBus
    jobs: JobManager
    providers: ProviderManager
    tester: InferenceTester
    router: TaskRouter
    voice: VoiceService
    voice_characters: VoiceCharacterService
    subtitles: SubtitleService
    ffmpeg: FFmpeg
    blender: Blender
    timeline: TimelineService
    render: RenderService
    titles: TitleCardService
    asset_sources: AssetSourceService
    music: MusicService
    presets: PresetService
    scenes: SceneService
    llm: LLMGateway
    studio: ScriptStudio
    approvals: ApprovalService
    history: HistoryService
    orchestrator: Orchestrator
    qa: QAService
    readings: ReadingService
    direction: DirectionService
    design: DesignService
    transcriber: TranscriptionService
    skills: SkillLibrary
    characters: CharacterService
    screencast: ScreencastService
    build: Any = None  # BuildService（コンテナ全体を使うので作った後で入れる）
    thumbnails: Any = None  # ThumbnailService（統合版 S4）

    async def aclose(self) -> None:
        await self.asset_sources.aclose()
        await self.music.aclose()
        await self.providers.aclose()
        await self.voice.aclose()
        await self.db.dispose()


def _find_whisper(settings: Settings) -> Path | None:
    """文字起こしのモデルが未設定なら、よく置く場所（アプリの隣・中の models）から探す。

    探すだけで、ダウンロードはしない。large-v3 があればそれを使う。"""
    app = Path(settings.data_dir).resolve().parent
    roots = [app.parent / "models", app / "models", Path(settings.data_dir) / "models"]
    hits: list[Path] = []
    for r in roots:
        if r.is_dir():
            hits += [d for d in sorted(r.glob("faster-whisper-*")) if (d / "model.bin").is_file()]
    if not hits:
        return None
    return next((d for d in hits if d.name.endswith("large-v3")), hits[0])


def _tools_ffmpeg(settings: Settings) -> str | None:
    """PATH に無ければ、Blender のアドオンが入れた FFmpeg（アプリの tools/ffmpeg）を使う。"""
    import os
    import shutil
    from pathlib import Path

    if shutil.which("ffmpeg"):
        return None
    root = Path(settings.data_dir).resolve().parent / "tools" / "ffmpeg"
    for name in ("ffmpeg.exe", "ffmpeg"):
        hits = sorted(root.glob(f"**/bin/{name}"))
        if hits:
            return str(hits[0])
    # PATH に入っていない（画面から起動したアプリには、後から足した PATH が届かない）ときに、
    # よくある入れ方（winget・Chocolatey・Scoop・C:\ffmpeg）の場所も見る
    local = Path(os.environ.get("LOCALAPPDATA", "") or "~").expanduser()
    fixed = [
        local / "Microsoft" / "WinGet" / "Links" / "ffmpeg.exe",
        Path("C:/ProgramData/chocolatey/bin/ffmpeg.exe"),
        Path.home() / "scoop" / "shims" / "ffmpeg.exe",
        Path("C:/ffmpeg/bin/ffmpeg.exe"),
    ]
    for f in fixed:
        if f.is_file():
            return str(f)
    pk = local / "Microsoft" / "WinGet" / "Packages"
    hits = sorted(pk.glob("*FFmpeg*/**/bin/ffmpeg.exe")) if pk.is_dir() else []
    return str(hits[0]) if hits else None


async def build_container(
    settings: Settings,
    *,
    http_transport: httpx.AsyncBaseTransport | None = None,
    protector: SecretProtector | None = None,
    configure_logs: bool = True,
    dns_resolver: Resolver | None = None,
) -> AppContainer:
    settings.ensure_dirs()
    if configure_logs:
        configure_logging(settings.log_level, settings.log_dir)
    db = Database.from_path(settings.database_path)
    await db.create_all()  # TODO: Alembic migration に置換
    bus = EventBus()
    providers = ProviderManager(settings, db, protector=protector, transport=http_transport)
    voice = VoiceService(settings, db, transport=http_transport)
    router = TaskRouter()
    llm = LLMGateway(settings, db, providers, router)
    skills = SkillLibrary(settings.skills_path)
    readings = ReadingService(db, voice, llm)
    # 利用者の直しの記録と、承認した既定値の変更（統合版 W2。ローカルに置き、Git には入れない）
    design = DesignService(
        db,
        DesignEditLog(
            Path(settings.data_dir) / "training" / "design_edits.jsonl",
            Path(settings.data_dir) / "design_defaults.json",
        ),
        SeriesStore(Path(settings.data_dir) / "series"),  # 統合版 Q4
    )
    subtitles = SubtitleService(settings, db, voice, readings, design)
    jobs = JobManager(bus)
    ffmpeg = FFmpeg(settings.ffmpeg_path or _tools_ffmpeg(settings))
    if settings.whisper_model_dir is None:
        settings.whisper_model_dir = _find_whisper(settings)
    blender = Blender(settings.blender_path)
    timeline = TimelineService(settings, db, ffmpeg)
    titles = TitleCardService(settings, db, blender, ffmpeg, timeline)
    render = RenderService(
        settings,
        db,
        ffmpeg,
        timeline,
        subtitles,
        jobs,
        design,
        blender=blender,
        font_for=lambda family: titles.font_path(family),
    )
    asset_sources = AssetSourceService(
        settings,
        db,
        timeline,
        jobs,
        providers.protector,
        transport=http_transport,
        resolver=dns_resolver,
    )
    voice_characters = VoiceCharacterService(
        db, voice, Path(settings.data_dir) / "voicevox_characters.json"
    )
    subtitles.characters = voice_characters  # 字幕の色・書体を手元のデータから（統合版 K1）
    # AI のセリフを直した前後の組をローカルに貯める（ローカル LLM の追加学習用。Git には入れない）
    studio = ScriptStudio(
        db,
        llm,
        voice_characters,
        CorrectionLog(Path(settings.data_dir) / "training" / "corrections.jsonl"),
    )

    characters = CharacterService(settings, db, timeline, ffmpeg)
    render.characters = characters

    async def _voice_credits(project_id: str) -> list[str]:
        """台本の音声のクレジット（VOICEVOX:キャラ名 / VOICEVOX Nemo）。

        「動画をつくる」は声をタイムラインの素材にせず台本から直接重ねるため、素材の
        クレジットだけでは声が抜ける。台本のセリフが使っている音声から集める。"""
        from sqlalchemy import select

        from video_automation.database.models import AudioFile, Script, ScriptSegment

        async with db.session_factory() as s:
            rows = await s.execute(
                select(AudioFile.credit)
                .join(ScriptSegment, ScriptSegment.audio_file_id == AudioFile.id)
                .join(Script, Script.id == ScriptSegment.script_id)
                .where(Script.project_id == project_id)
                .order_by(ScriptSegment.sequence)
            )
            out: list[str] = []
            for (credit,) in rows:
                if credit and credit not in out:
                    out.append(credit)
        return out

    async def _used_credits(project_id: str) -> str:
        text = (await asset_sources.credits(project_id, used_only=True)).text
        extra = await characters.project_credits(project_id)
        head = []
        if not text.startswith("音声: "):  # 声がタイムラインの素材に無いとき（台本から重ねる）
            voices = await _voice_credits(project_id)
            if voices:
                head.append("音声: " + " / ".join(voices))
        return "\n".join([x for x in [*head, text, *extra] if x])

    render.credits_for = _used_credits
    approvals = ApprovalService(settings, db, timeline, render, asset_sources, studio)
    await approvals.load_policy()
    orchestrator = Orchestrator(
        db,
        llm,
        ToolRouter(timeline, studio, render, asset_sources, approvals),
        approvals,
        skills=skills if settings.skills_enabled else None,
        skills_budget=settings.skills_budget_chars,
    )
    container = AppContainer(
        settings=settings,
        db=db,
        bus=bus,
        jobs=jobs,
        providers=providers,
        tester=InferenceTester(providers),
        router=router,
        voice=voice,
        voice_characters=voice_characters,
        subtitles=subtitles,
        ffmpeg=ffmpeg,
        blender=blender,
        timeline=timeline,
        render=render,
        titles=titles,
        asset_sources=asset_sources,
        music=MusicService(settings, db, timeline, providers, transport=http_transport),
        presets=PresetService(db),
        scenes=SceneService(settings, db, ffmpeg, timeline, titles),
        llm=llm,
        studio=studio,
        approvals=approvals,
        history=HistoryService(db, timeline, approvals),
        orchestrator=orchestrator,
        qa=QAService(settings, db, ffmpeg, timeline, subtitles, jobs, llm),
        readings=readings,
        direction=DirectionService(
            settings,
            db,
            llm,
            timeline,
            titles,
            ffmpeg,
            design,
            skills=skills if settings.skills_enabled else None,
        ),
        design=design,
        transcriber=TranscriptionService(settings, db, ffmpeg, design=design),
        skills=skills,
        characters=characters,
        screencast=ScreencastService(settings, db, timeline, ffmpeg),
    )
    from video_automation.services.build.service import BuildService

    container.build = BuildService(container)
    from video_automation.services.rendering.thumbnail_service import ThumbnailService

    container.thumbnails = ThumbnailService(settings, timeline, render, design, titles, llm)
    return container
