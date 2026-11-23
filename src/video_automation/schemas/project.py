from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# 仕様の出力プロファイル: FHD/4K × 16:9/9:16
ALLOWED_RESOLUTIONS = {(1920, 1080), (1080, 1920), (3840, 2160), (2160, 3840)}


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    width: int = Field(1920, ge=16, le=7680)
    height: int = Field(1080, ge=16, le=7680)
    fps_num: int = Field(30, ge=1)
    fps_den: int = Field(1, ge=1)


class ProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    width: int
    height: int
    fps_num: int
    fps_den: int
    created_at: datetime
