"""认证相关 schema。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str
    password: str
    totp_code: str | None = Field(None, description="启用 TOTP 后必填")


class LoginResponse(BaseModel):
    ok: bool
    require_totp: bool = False


class RegisterRequest(BaseModel):
    """首次部署时创建超管账号的接口入参（系统已存在用户后该接口禁用）。"""
    username: str
    password: str


class TotpEnableResponse(BaseModel):
    secret: str
    otpauth_url: str


class TotpVerifyRequest(BaseModel):
    code: str


class CurrentUser(BaseModel):
    id: int
    username: str
    has_totp: bool


class ChangePasswordRequest(BaseModel):
    """修改当前用户密码：必须提供旧密码做二次校验。"""

    old_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=256, description="≥ 8 位")


class TotpDisableRequest(BaseModel):
    """禁用 TOTP：要求当前 TOTP 码做最后一次校验，避免 cookie 被偷后被静默关掉。"""

    code: str = Field(min_length=6, max_length=8)
