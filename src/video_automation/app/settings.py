"""設定管理 (P1-03)。環境変数 VA_* と .env から読み込む。"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_NAME = "VideoAutomation"
APP_VERSION = "0.2.0"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VA_", env_file=".env", extra="ignore")

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

    # ---- Phase 3: 音声合成（VOICEVOX系。Nemo を優先）----
    voicevox_nemo_url: str | None = "http://127.0.0.1:50121"
    voicevox_url: str | None = "http://127.0.0.1:50021"

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
