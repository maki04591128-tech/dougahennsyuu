from enum import StrEnum
from typing import Any

from pydantic import BaseModel


class JobStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class HealthResponse(BaseModel):
    status: str
    application: str
    database: str
    version: str


class ErrorResponse(BaseModel):
    """統一エラー形式。機密情報を含めない。"""

    code: str
    message: str
    details: Any | None = None


class JobRead(BaseModel):
    id: str
    kind: str
    status: JobStatus
    progress: float = 0.0
    message: str = ""  # いまの段階（長い処理の途中経過を画面に出す）
    result: Any | None = None
    error: str | None = None
