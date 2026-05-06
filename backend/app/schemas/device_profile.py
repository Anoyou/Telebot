"""设备伪装 (device_profile) 相关 schema。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DeviceProfileOut(BaseModel):
    id: int
    name: str
    device_model: str
    system_version: str
    app_version: str
    lang_code: str
    system_lang_code: str
    is_default: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DeviceProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    device_model: str = Field(min_length=1, max_length=128)
    system_version: str = Field(min_length=1, max_length=64)
    app_version: str = Field(min_length=1, max_length=64)
    lang_code: str = Field(default="zh", max_length=16)
    system_lang_code: str = Field(default="zh-Hans", max_length=16)
    # 创建时 = True 会自动把其他行的 is_default 置 False
    is_default: bool = False


class DeviceProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    device_model: str | None = Field(default=None, min_length=1, max_length=128)
    system_version: str | None = Field(default=None, min_length=1, max_length=64)
    app_version: str | None = Field(default=None, min_length=1, max_length=64)
    lang_code: str | None = Field(default=None, max_length=16)
    system_lang_code: str | None = Field(default=None, max_length=16)
    is_default: bool | None = None
