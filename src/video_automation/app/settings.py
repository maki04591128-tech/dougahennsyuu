"""設定管理 (P1-03)。環境変数 VA_* と .env から読み込む。"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_NAME = "VideoAutomation"
APP_VERSION = "0.1.0"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VA_", env_file=".env", extra="ignore")

    # セキュリティ要件: ローカルAPIは原則localhostにバインド
    host: str = "127.0.0.1"
    port: int = Field(8765, ge=1, le=65535)
    data_dir: Path = Path("data")
    log_level: str = "INFO"

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
