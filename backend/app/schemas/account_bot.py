"""账号绑定 Bot 联动系统 Schemas。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..db.models.account_bot import ACCOUNT_BOT_ROLES

AccountBotRole = Literal["viewer", "operator", "admin"]


class AccountBotRemotePluginPolicy(BaseModel):
    enabled: bool = False
    install: bool = False
    update: bool = False
    uninstall: bool = False
    enable_disable: bool = False


class AccountBotRemotePluginPolicyUpdate(BaseModel):
    enabled: bool | None = None
    install: bool | None = None
    update: bool | None = None
    uninstall: bool | None = None
    enable_disable: bool | None = None


class AccountBotConfigResponse(BaseModel):
    """账号 Bot 配置出参；永不返回明文 token。"""

    account_id: int
    enabled: bool
    status: str
    has_token: bool
    username: str | None = None
    remote_plugin_policy: AccountBotRemotePluginPolicy
    last_update_id: int | None = None
    last_error: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class AccountBotConfigUpdate(BaseModel):
    """账号 Bot 配置更新。"""

    bot_token: str | None = Field(default=None, min_length=10, max_length=256)
    clear_token: bool = False
    enabled: bool | None = None
    remote_plugin_policy: AccountBotRemotePluginPolicyUpdate | None = None

    @field_validator("bot_token")
    @classmethod
    def _trim_token(cls, v: str | None) -> str | None:
        if v is None:
            return None
        token = v.strip()
        if not token:
            return None
        if "\n" in token or "\r" in token:
            raise ValueError("Bot Token 不能包含换行")
        return token


class AccountBotTestRequest(BaseModel):
    """测试发送请求。"""

    text: str | None = Field(default=None, max_length=1000)


class AccountBotTestResponse(BaseModel):
    ok: bool
    sent: int = 0
    message: str | None = None


class AccountBotRuntimeResponse(BaseModel):
    ok: bool
    status: str | None = None
    message: str | None = None


class AccountBotUserCreate(BaseModel):
    """新增账号 Bot 授权用户。"""

    tg_user_id: int
    display_name: str | None = Field(default=None, max_length=128)
    role: AccountBotRole = "viewer"
    notify_enabled: bool = True
    enabled: bool = True

    @field_validator("role")
    @classmethod
    def _check_role(cls, v: str) -> str:
        if v not in ACCOUNT_BOT_ROLES:
            raise ValueError("role 只能是 viewer / operator / admin")
        return v


class AccountBotUserUpdate(BaseModel):
    """更新账号 Bot 授权用户。"""

    display_name: str | None = Field(default=None, max_length=128)
    role: AccountBotRole | None = None
    notify_enabled: bool | None = None
    enabled: bool | None = None

    @field_validator("role")
    @classmethod
    def _check_role(cls, v: str | None) -> str | None:
        if v is not None and v not in ACCOUNT_BOT_ROLES:
            raise ValueError("role 只能是 viewer / operator / admin")
        return v


class AccountBotUserResponse(BaseModel):
    """账号 Bot 授权用户出参。"""

    id: int
    account_id: int
    tg_user_id: int
    display_name: str | None = None
    role: AccountBotRole
    notify_enabled: bool
    last_chat_id: int | None = None
    enabled: bool
    created_at: datetime
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)
