"""Apple push notification management schemas."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

ApnsEnvironment = Literal["production", "sandbox"]


class ApnsDeviceRegister(BaseModel):
    device_token: str = Field(min_length=64, max_length=128)
    bundle_id: str = Field(min_length=3, max_length=128)
    environment: ApnsEnvironment = "production"
    device_id: str = Field(min_length=8, max_length=64)
    server_origin_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    categories: list[Literal["assistant_approval", "assistant_result", "account_alert"]] = Field(default_factory=list, max_length=3)

    @field_validator("device_token")
    @classmethod
    def valid_token(cls, value: str) -> str:
        if not re.fullmatch(r"[0-9a-fA-F]+", value) or len(value) % 2:
            raise ValueError("device_token must be hexadecimal")
        return value.lower()

    @field_validator("bundle_id")
    @classmethod
    def valid_bundle(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", value):
            raise ValueError("invalid bundle_id")
        return value

    @field_validator("device_id")
    @classmethod
    def valid_device_id(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
            raise ValueError("invalid device_id")
        return value


class ApnsDeviceOut(BaseModel):
    device_id: str
    bundle_id: str
    environment: ApnsEnvironment
    enabled: bool
    categories: list[str]
    last_error: str | None = None
    registered_at: datetime | None = None
    updated_at: datetime | None = None
    expires_at: datetime | None = None


class ApnsConfigPatch(BaseModel):
    enabled: bool | None = None
    team_id: str | None = Field(default=None, min_length=10, max_length=10)
    key_id: str | None = Field(default=None, min_length=10, max_length=10)
    bundle_id: str | None = Field(default=None, min_length=3, max_length=128)
    environment: ApnsEnvironment | None = None
    auth_key_p8: str | None = Field(default=None, max_length=16_384)
    clear_auth_key: bool = False

    @field_validator("team_id", "key_id")
    @classmethod
    def valid_apple_identifier(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"[A-Z0-9]{10}", value):
            raise ValueError("Team ID / Key ID must have ten uppercase alphanumeric characters")
        return value

    @field_validator("bundle_id")
    @classmethod
    def valid_config_bundle(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", value):
            raise ValueError("invalid bundle_id")
        return value


class ApnsConfigOut(BaseModel):
    enabled: bool = False
    configured: bool = False
    team_id: str | None = None
    key_id: str | None = None
    bundle_id: str | None = None
    environment: ApnsEnvironment = "production"
    has_auth_key: bool = False
    updated_at: datetime | None = None


class ApnsTestRequest(BaseModel):
    device_id: str = Field(min_length=8, max_length=64)


class ApnsSendResult(BaseModel):
    ok: bool
    attempted: int = 0
    accepted: int = 0
    failed: int = 0
    unregistered: int = 0
    message: str
    event_id: str | None = None


class ApnsTestOut(ApnsSendResult):
    device_id: str
