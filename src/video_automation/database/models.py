"""テーブル定義。Phase 1: projects / Phase 2: llm_providers, llm_models,
llm_task_assignments, system_settings。以降のPhaseのテーブルは末尾に追加。"""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, LargeBinary, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False, default=1920)
    height: Mapped[int] = mapped_column(Integer, nullable=False, default=1080)
    fps_num: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    fps_den: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )


class LlmProvider(Base):
    __tablename__ = "llm_providers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    auto_discovered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # DPAPIで暗号化したAPIキー。平文は保存しない
    api_key_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    last_status: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )


class LlmModel(Base):
    __tablename__ = "llm_models"

    key: Mapped[str] = mapped_column(String(600), primary_key=True)  # provider_id::model_id
    provider_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("llm_providers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    model_id: Mapped[str] = mapped_column(String(500), nullable=False)
    info: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)  # ModelInfo スナップショット
    last_test: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    present: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )


class LlmTaskAssignment(Base):
    __tablename__ = "llm_task_assignments"

    task: Mapped[str] = mapped_column(String(64), primary_key=True)
    model_key: Mapped[str | None] = mapped_column(String(600), nullable=True)  # None=自動
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )


# ---------------- Phase 3: 音声 ----------------


class AudioFile(Base):
    """生成音声のメタデータ（実体はファイル）。台本セグメントと共通IDで関連付ける。"""

    __tablename__ = "audio_files"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    script_segment_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    engine_id: Mapped[str] = mapped_column(String(64), nullable=False)
    engine_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    style_id: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(String(2000), nullable=False)
    path: Mapped[str] = mapped_column(String(1000), nullable=False)  # data_dir からの相対パス
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    sample_rate: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_sec: Mapped[float] = mapped_column(Float, nullable=False)
    query: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)  # 合成に使ったAudioQuery
    prosody: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    timings: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    credit: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )


class PronunciationEntry(Base):
    """読み辞書。エンジンのユーザー辞書と同期し、エンジン再インストール時に復元できるよう保持する。"""

    __tablename__ = "pronunciation_dictionary"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    engine_id: Mapped[str] = mapped_column(String(64), nullable=False)
    engine_word_uuid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    surface: Mapped[str] = mapped_column(String(100), nullable=False)
    pronunciation: Mapped[str] = mapped_column(String(100), nullable=False)
    accent_type: Mapped[int] = mapped_column(Integer, nullable=False)
    word_type: Mapped[str] = mapped_column(String(20), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )


# ---------------- Phase 4: 台本・字幕 ----------------


class Script(Base):
    """台本（仕様 §17.3）。mode: FULL_AI / USER_ONE_SIDE / MANUAL。"""

    __tablename__ = "scripts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    mode: Mapped[str] = mapped_column(String(20), nullable=False, default="MANUAL")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="DRAFT")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    # Phase 8: テーマ・目的・尺・応答方針など（schemas.script_studio.ScriptSettings）
    settings: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class ScriptSegment(Base):
    """台本の発話単位。仕様 §17.3 の DialogueTurn を兼ねる（同じ発話を二重管理しない）。"""

    __tablename__ = "script_segments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    script_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scripts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scene_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    speaker: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    text: Mapped[str] = mapped_column(String(2000), nullable=False)
    generation_source: Mapped[str] = mapped_column(String(10), nullable=False, default="USER")
    reply_to_segment_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    voice_style_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    audio_file_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("audio_files.id", ondelete="SET NULL"), nullable=True
    )
    # 同期後のタイムライン上の位置（フレーム、半開区間）。Phase 5 の音声配置に使う
    start_frame: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_frame: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Phase 8: ユーザー入力の原文（固定セリフが変わっていないことの検査用）と生成記録
    original_text: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    generation_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class SubtitleStyleRow(Base):
    __tablename__ = "subtitle_styles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class Subtitle(Base):
    """字幕キュー。表示区間はフレーム整数の半開区間 [start_frame, end_frame)。"""

    __tablename__ = "subtitles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    script_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scripts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    script_segment_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("script_segments.id", ondelete="CASCADE"), nullable=False
    )
    index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(String(500), nullable=False)  # 改行は \n
    speaker: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    start_frame: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_frame: Mapped[int | None] = mapped_column(Integer, nullable=True)


# ---------------- Phase 5: タイムライン・編集 ----------------


class Asset(Base):
    """素材（実体はファイル。DB にはパス・ハッシュ・メタデータ）。"""

    __tablename__ = "assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    path: Mapped[str] = mapped_column(String(1000), nullable=False)  # data_dir からの相対
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    duration_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fps: Mapped[float | None] = mapped_column(Float, nullable=True)
    has_audio: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="import")
    audio_file_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )


class Track(Base):
    __tablename__ = "tracks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    index: Mapped[int] = mapped_column(Integer, nullable=False)
    muted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Clip(Base):
    """start/end はタイムライン位置、source_in/out は素材の範囲（いずれもフレーム、半開区間）。"""

    __tablename__ = "clips"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    track_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tracks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    asset_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("assets.id", ondelete="RESTRICT"), nullable=False
    )
    start_frame: Mapped[int] = mapped_column(Integer, nullable=False)
    end_frame: Mapped[int] = mapped_column(Integer, nullable=False)
    source_in_frame: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_out_frame: Mapped[int] = mapped_column(Integer, nullable=False)
    transform: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    volume: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    fade_in_frames: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fade_out_frames: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Phase 7: キーフレーム・入りのトランジション（schemas.animation.ClipAnimation）
    animation: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class EditCommand(Base):
    """編集命令と履歴。before/after は影響を受けた行のスナップショット（Undo/Redo 用）。"""

    __tablename__ = "edit_commands"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    origin: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    command: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )


class RenderJob(Base):
    __tablename__ = "render_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    profile: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    request: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    output_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    subtitle_paths: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    encoder: Mapped[str | None] = mapped_column(String(20), nullable=True)
    error: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ---------------- Phase 6: 素材・ライセンス・音楽生成 ----------------


class AssetLicenseRow(Base):
    """素材の取得元・取得日時・ライセンス条件のスナップショット（仕様 §3, §5）。"""

    __tablename__ = "asset_licenses"

    asset_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True
    )
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    source_id: Mapped[str] = mapped_column(String(200), nullable=False)
    landing_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    download_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    creator: Mapped[str | None] = mapped_column(String(300), nullable=True)
    license_code: Mapped[str] = mapped_column(String(40), nullable=False)
    license: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    verdict: Mapped[str] = mapped_column(String(20), nullable=False)
    acknowledged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    attribution: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)  # 取得時の結果全体
    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )


class AssetSourceKey(Base):
    """素材サービスのAPIキー（DPAPIで暗号化。平文保存しない）。"""

    __tablename__ = "asset_source_keys"

    source: Mapped[str] = mapped_column(String(20), primary_key=True)
    api_key_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, nullable=False
    )


class SearchCacheRow(Base):
    """検索結果キャッシュ（Pixabay は24時間キャッシュが利用条件）。"""

    __tablename__ = "asset_search_cache"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    results: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SearchResultRow(Base):
    """検索で見つかった個々の素材（サムネイル表示・承認画面の表示用）。"""

    __tablename__ = "asset_search_results"

    source: Mapped[str] = mapped_column(String(20), primary_key=True)
    media_type: Mapped[str] = mapped_column(String(10), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AssetRequestRow(Base):
    """LLM 由来の素材ダウンロード要求（ユーザー承認待ち）。"""

    __tablename__ = "asset_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    source_id: Mapped[str] = mapped_column(String(200), nullable=False)
    media_type: Mapped[str] = mapped_column(String(10), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    license_code: Mapped[str] = mapped_column(String(40), nullable=False)
    verdict: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    error: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )


class MusicGeneration(Base):
    """ACE-Step による BGM 生成の記録（プロンプト・条件・モデル・出力）。"""

    __tablename__ = "music_generations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    task_id: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt: Mapped[str] = mapped_column(String(1000), nullable=False)
    lyrics: Mapped[str] = mapped_column(String(4000), nullable=False, default="")
    params: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    dit_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    lm_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    seed: Mapped[str | None] = mapped_column(String(100), nullable=True)
    duration_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )


# ---------------- Phase 7: アニメーション ----------------


class MotionPresetRow(Base):
    """ユーザー定義のモーションプリセット（組み込みはコード側に持つ）。"""

    __tablename__ = "motion_presets"

    name: Mapped[str] = mapped_column(String(60), primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    definition: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )


# ---------------- Phase 8: 台本モード・対話型編集 ----------------


class ScriptCharacter(Base):
    """登場キャラクター（仕様 §17.3 Character）と音声設定（voice_settings）。"""

    __tablename__ = "script_characters"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    script_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scripts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    personality: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    speaking_style: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    is_user: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    voice_style_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    voice: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)  # 抑揚など


class ScriptScene(Base):
    __tablename__ = "script_scenes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    script_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scripts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    purpose: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    estimated_duration_sec: Mapped[float | None] = mapped_column(Float, nullable=True)


class GenerationRun(Base):
    """LLM による生成の記録（仕様 §17.3 GenerationRun）。"""

    __tablename__ = "generation_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    script_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    model_key: Mapped[str | None] = mapped_column(String(300), nullable=True)
    prompt_version: Mapped[str] = mapped_column(String(60), nullable=False)
    input_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    output_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    error: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )


class ScriptVersionRow(Base):
    """台本の変更履歴（変更ごとのスナップショット）。"""

    __tablename__ = "script_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    script_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scripts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(200), nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )


class ScriptProposal(Base):
    """LLM 由来の台本修正案（仕様 §10: 台本修正案は承認が必要）。承認で snapshot を適用する。"""

    __tablename__ = "script_proposals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    script_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scripts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    base_version: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(200), nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    notes: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING_APPROVAL")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )


class Conversation(Base):
    """対話型編集の会話（P8-01）。"""

    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # user / assistant
    content: Mapped[str] = mapped_column(String(20000), nullable=False, default="")
    model_key: Mapped[str | None] = mapped_column(String(300), nullable=True)
    reply_to: Mapped[str | None] = mapped_column(String(36), nullable=True)  # assistant→user
    # 計画・実行結果（schemas.orchestrator.AssistantTurn の plan / actions）
    data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    superseded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )


# ---------------- Phase 9: 品質検査 ----------------


class QAReportRow(Base):
    __tablename__ = "qa_reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    render_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    script_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    report: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    duration_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
