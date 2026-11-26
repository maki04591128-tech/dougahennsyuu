"""品質検査（Phase 9: P9-02〜05）のスキーマ。"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class QALevel(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class QASectionName(StrEnum):
    PROJECT = "project"  # タイムラインの整合
    AUDIO = "audio"  # P9-02 音声品質
    SUBTITLE = "subtitle"  # P9-03 字幕同期
    VIDEO = "video"  # P9-04 映像品質（書き出し結果）
    LICENSE = "license"  # P9-05 ライセンス
    VISION = "vision"  # 統合版 Q1 画像を読める LLM での確認（頼んだときだけ。時間がかかる）


SECTION_LABEL = {
    QASectionName.PROJECT: "タイムライン",
    QASectionName.AUDIO: "音声品質",
    QASectionName.SUBTITLE: "字幕同期",
    QASectionName.VIDEO: "映像品質",
    QASectionName.LICENSE: "ライセンス",
    QASectionName.VISION: "画像での確認",
}
# sections を省いたときに行う検査（画像での確認は LLM を場面の数だけ呼ぶので、頼んだときだけ）
DEFAULT_SECTIONS = [x for x in QASectionName if x != QASectionName.VISION]


class QACheck(BaseModel):
    level: QALevel
    code: str
    message: str
    target: str | None = None  # クリップ・セリフ・素材・書き出しの ID
    at_sec: float | None = None  # 動画内の位置（秒）


class QASection(BaseModel):
    name: QASectionName
    label: str
    status: str  # passed / warnings / failed / skipped
    checks: list[QACheck] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)


class QARequest(BaseModel):
    render_id: str | None = None  # 省略時は最新の成功した書き出し
    script_id: str | None = None  # 省略時は書き出しに使った台本、無ければ最新の台本
    sections: list[QASectionName] | None = None  # 省略時は画像での確認以外のすべて
    vision_model_key: str | None = None  # 画像での確認に使うモデル（省略時は割り当て）


class QAReportRead(BaseModel):
    id: str
    project_id: str
    render_id: str | None
    script_id: str | None
    status: str  # passed / warnings / failed
    sections: list[QASection]
    duration_ms: float
    created_at: datetime


class QAReportSummary(BaseModel):
    id: str
    status: str
    render_id: str | None
    counts: dict[str, int]
    created_at: datetime
