"""テーブル定義。Phase 1: projects / Phase 2: llm_providers, llm_models,
llm_task_assignments, system_settings。"""

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
