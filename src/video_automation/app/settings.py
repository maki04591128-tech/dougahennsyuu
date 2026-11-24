"""設定管理 (P1-03)。環境変数 VA_* と .env から読み込む。"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_ROOT = Path(__file__).resolve().parents[3]  # src/video_automation/app/settings.py → リポジトリ

APP_NAME = "VideoAutomation"
APP_VERSION = "0.9.0"


class Settings(BaseSettings):
    # .env はアプリのフォルダ（リポジトリ直下）と、起動したフォルダの両方から読む（後者が優先）
    model_config = SettingsConfigDict(
        env_prefix="VA_", env_file=(str(_ROOT / ".env"), ".env"), extra="ignore"
    )

    # セキュリティ要件: ローカルAPIは原則localhostにバインド
    host: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1, le=65535)
    data_dir: Path = Path("data")
    log_level: str = "INFO"

    # ---- Phase 2: LLM / モデル検出 ----
    # 自動検出は既定ポートのみ（無制限なポートスキャンはしない）
    ollama_url: str = "http://127.0.0.1:11434"
    lmstudio_url: str = "http://127.0.0.1:1234"
    autodetect_ollama: bool = True
    autodetect_lmstudio: bool = True
    discovery_timeout_s: float = Field(default=8.0, gt=0)
    # VRAM 8GB 環境のため既定は同時ロード1モデル（切替時に他モデルをアンロード）
    single_loaded_model: bool = True
    # クラウドAPIキーは環境変数でも指定可能（DBには平文保存しない）
    openai_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    # 音楽生成（ACE-Step 1.5、別プロセス）
    acestep_url: str = "http://127.0.0.1:8001"
    acestep_path: Path | None = None

    # 文字起こし（faster-whisper）。モデルはこのフォルダから読むだけで、自動でダウンロードしない
    whisper_model_dir: Path | None = None
    whisper_device: Literal["auto", "cpu", "cuda"] = "auto"

    # ---- Phase 3: 音声合成（VOICEVOX系。Nemo を優先）----
    voicevox_nemo_url: str | None = "http://127.0.0.1:50121"
    voicevox_url: str | None = "http://127.0.0.1:50021"
    # 台本の読み上げ: 抑揚の強さ（0=エンジンのまま、1=標準、2=強め）と、未指定時の抑揚スケール
    voice_expressiveness: float = Field(default=1.0, ge=0, le=2)
    voice_default_intonation: float = Field(default=1.15, ge=0, le=2)

    # ---- Phase 5: 編集・レンダリング ----
    ffmpeg_path: str | None = None  # None=PATH から検出
    blender_path: str | None = None  # None=PATH・Steam・既定インストール先から検出
    # 素材の取り込みを許可するフォルダ（セキュリティ：ファイル操作は許可ディレクトリに限定）
    import_dirs: list[Path] = Field(
        default_factory=lambda: [
            Path.home() / d for d in ("Videos", "Pictures", "Music", "Downloads", "Desktop")
        ]
    )
    title_font_path: str | None = None  # Blender タイトル用フォント（None=OS既定の日本語フォント）

    # ---- Phase 6: 素材・音楽生成 ----
    # 素材サービスのAPIキー（環境変数でも指定可。UIから登録した場合はDPAPIで暗号化してDB保存）
    pexels_api_key: SecretStr | None = None
    pixabay_api_key: SecretStr | None = None
    freesound_api_key: SecretStr | None = None
    # Wikimedia の利用規約で連絡先入りの User-Agent が求められる
    http_user_agent: str = (
        "VideoAutomation/0.9 (+https://github.com/maki04591128-tech/dougahennsyuu)"
    )
    asset_search_cache_hours: float = Field(default=24.0, ge=0)  # Pixabay は24時間キャッシュ必須
    asset_max_download_mb: int = Field(default=1024, ge=1)
    # LLM（MCP）からの素材ダウンロードはユーザー承認を必要とする（仕様 §10「設定可能」）
    llm_asset_download_requires_approval: bool = True
    # ライセンス不明・要確認の素材: ack=明示承認で使用可 / forbidden=使用禁止（仕様 §10）
    unknown_license_policy: Literal["ack", "forbidden"] = "ack"
    acestep_api_key: SecretStr | None = None  # acestep-api を --api-key 付きで起動した場合
    music_timeout_s: float = Field(default=1800.0, gt=0)
    music_poll_interval_s: float = Field(default=2.0, gt=0)
    # VRAM 8GB 環境のため、BGM生成前にロード中のLLMをアンロードする
    music_unload_llm: bool = True

    def voice_engine_urls(self) -> dict[str, str]:
        urls = {"voicevox-nemo": self.voicevox_nemo_url, "voicevox": self.voicevox_url}
        return {k: v for k, v in urls.items() if v}

    @property
    def database_path(self) -> Path:
        return self.data_dir / "database" / "app.db"

    @property
    def database_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.database_path.as_posix()}"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def api_base_url(self) -> str:
        return f"http://{self.host}:{self.port}/api/v1"

    def ensure_dirs(self) -> None:
        for sub in ("database", "projects", "assets", "cache", "dictionaries", "logs"):
            (self.data_dir / sub).mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
