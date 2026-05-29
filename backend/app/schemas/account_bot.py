"""账号绑定 Bot 联动系统 Schemas。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..account_bot_defaults import (
    DEFAULT_INTERACTION_DISABLED_MESSAGE,
    DEFAULT_INTERACTION_RESPONSE_TEMPLATE,
    DEFAULT_TRANSFER_NOTICE_TEMPLATE,
)
from ..db.models.account_bot import ACCOUNT_BOT_ROLES

AccountBotRole = Literal["viewer", "operator", "admin"]
InteractionTriggerMode = Literal["payment", "keyword", "both"]
InteractionAmountMatchMode = Literal["eq", "gte"]
InteractionConcurrency = Literal["chat", "user", "none"]


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
    chat_id: int | None = None
    bot_token_override: str | None = Field(default=None, min_length=10, max_length=256)

    @field_validator("bot_token_override")
    @classmethod
    def _trim_override_token(cls, v: str | None) -> str | None:
        if v is None:
            return None
        token = v.strip()
        if not token:
            return None
        if "\n" in token or "\r" in token:
            raise ValueError("Bot Token 不能包含换行")
        return token


class AccountBotTestResponse(BaseModel):
    ok: bool
    sent: int = 0
    message: str | None = None


class AccountBotInteractionRule(BaseModel):
    """交互 Bot 规则；用于后续把不同模块拆成互不干扰的多条规则。"""

    id: str = Field(default="default", max_length=64)
    name: str = Field(default="默认规则", max_length=64)
    enabled: bool = True
    chat_ids: list[int] = Field(default_factory=list, max_length=20)
    trigger_mode: InteractionTriggerMode = "payment"
    trigger_texts: list[str] = Field(default_factory=lambda: ["转账成功"], max_length=20)
    module_start_keywords: list[str] = Field(default_factory=list, max_length=20)
    receiver_user_id: int | None = Field(default=None, ge=1)
    receiver_text: str | None = Field(default=None, max_length=128)
    amount: int | None = Field(default=None, ge=1)
    amount_match_mode: InteractionAmountMatchMode = "eq"
    action: Literal["notice", "math10", "module"] = "notice"
    math_prize: int = Field(default=123, ge=1)
    module_key: str | None = Field(default=None, max_length=64)
    module_action: str | None = Field(default=None, max_length=64)
    module_prize: int | None = Field(default=None, ge=1)
    module_config: dict[str, Any] = Field(default_factory=dict)
    module_start_text: str | None = Field(default=None, max_length=500)
    user_cooldown_seconds: str | None = Field(default=None, max_length=16)
    daily_limit_per_user: int | None = Field(default=None, ge=1, le=1000)
    open_commands: list[str] = Field(default_factory=list, max_length=20)
    close_commands: list[str] = Field(default_factory=list, max_length=20)
    status_commands: list[str] = Field(default_factory=list, max_length=20)
    disabled_message: str | None = Field(default=DEFAULT_INTERACTION_DISABLED_MESSAGE, max_length=500)
    valid_seconds: int = Field(default=600, ge=30, le=86400)
    concurrency: InteractionConcurrency = "chat"
    response_template: str = Field(
        default=DEFAULT_INTERACTION_RESPONSE_TEMPLATE,
        max_length=1000,
    )

    @field_validator("id", "name", "response_template")
    @classmethod
    def _trim_required_text(cls, v: str) -> str:
        value = str(v or "").strip()
        if not value:
            raise ValueError("不能为空")
        return value

    @field_validator("receiver_text", "module_key", "module_action", "module_start_text", "user_cooldown_seconds", "disabled_message")
    @classmethod
    def _trim_optional_text(cls, v: str | None) -> str | None:
        if v is None:
            return None
        value = v.strip()
        if value and "\n" in value:
            raise ValueError("不能包含换行")
        return value or None

    @field_validator("chat_ids")
    @classmethod
    def _normalize_chat_ids(cls, v: list[int]) -> list[int]:
        return _normalize_chat_id_list(v)

    @field_validator("trigger_texts")
    @classmethod
    def _normalize_trigger_texts(cls, v: list[str]) -> list[str]:
        return _normalize_string_list(v, default=["转账成功"])

    @field_validator("module_start_keywords", "open_commands", "close_commands", "status_commands")
    @classmethod
    def _normalize_optional_string_list(cls, v: list[str]) -> list[str]:
        return _normalize_string_list(v)


def _normalize_string_list(v: list[str], *, default: list[str] | None = None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in v or []:
        value = str(raw or "").strip()
        if not value or "\n" in value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out or list(default or [])


def _normalize_chat_id_list(v: list[int]) -> list[int]:
    out: list[int] = []
    seen: set[int] = set()
    for raw in v or []:
        value = int(raw)
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


class AccountBotInteractionConfig(BaseModel):
    """交互 Bot / 转账联动测试配置。

    用于测试阶段：
    - 交互 Bot 监听群内回复 ``+数字``，用 Abot token 发出模拟转账通知
    - 转账通知命中后，交互 Bot 可继续发出简单算数题
    """

    enabled: bool = False
    chat_id: int | None = None
    chat_ids: list[int] = Field(default_factory=list, max_length=20)
    interaction_bot_token: str | None = Field(default=None, min_length=10, max_length=256)
    clear_interaction_bot_token: bool = False
    has_interaction_bot_token: bool = False
    interaction_bot_username: str | None = None
    interaction_bot_id: int | None = None
    interaction_running: bool = False
    interaction_runtime_status: Literal["running", "stopped"] = "stopped"
    interaction_last_update_id: int | None = None
    interaction_last_error: str | None = None
    trusted_bot_id: int | None = None
    transfer_bot_id: int | None = None
    transfer_bot_token: str | None = Field(default=None, min_length=10, max_length=256)
    clear_transfer_bot_token: bool = False
    has_transfer_bot_token: bool = False
    trigger_mode: InteractionTriggerMode = "payment"
    trigger_text: str = Field(default="转账成功", max_length=64)
    trigger_texts: list[str] = Field(default_factory=lambda: ["转账成功"], max_length=20)
    module_start_keywords: list[str] = Field(default_factory=list, max_length=20)
    receiver_user_id: int | None = Field(default=None, ge=1)
    receiver_text: str | None = Field(default=None, max_length=128)
    amount: int | None = Field(default=None, ge=1)
    amount_match_mode: InteractionAmountMatchMode = "eq"
    action: Literal["notice", "math10", "module"] = "notice"
    math_prize: int = Field(default=123, ge=1)
    module_key: str | None = Field(default=None, max_length=64)
    module_action: str | None = Field(default=None, max_length=64)
    module_prize: int | None = Field(default=None, ge=1)
    module_config: dict[str, Any] = Field(default_factory=dict)
    module_start_text: str | None = Field(default=None, max_length=500)
    user_cooldown_seconds: str | None = Field(default=None, max_length=16)
    daily_limit_per_user: int | None = Field(default=None, ge=1, le=1000)
    open_commands: list[str] = Field(default_factory=list, max_length=20)
    close_commands: list[str] = Field(default_factory=list, max_length=20)
    status_commands: list[str] = Field(default_factory=list, max_length=20)
    disabled_message: str | None = Field(default=DEFAULT_INTERACTION_DISABLED_MESSAGE, max_length=500)
    valid_seconds: int = Field(default=600, ge=30, le=86400)
    concurrency: InteractionConcurrency = "chat"
    response_template: str = Field(
        default=DEFAULT_INTERACTION_RESPONSE_TEMPLATE,
        max_length=1000,
    )
    transfer_notice_template: str = Field(
        default=DEFAULT_TRANSFER_NOTICE_TEMPLATE,
        max_length=1000,
    )
    rules: list[AccountBotInteractionRule] = Field(default_factory=list, max_length=20)

    @field_validator(
        "receiver_text",
        "interaction_bot_token",
        "transfer_bot_token",
        "module_key",
        "module_action",
        "module_start_text",
        "user_cooldown_seconds",
        "disabled_message",
    )
    @classmethod
    def _trim_optional_text(cls, v: str | None) -> str | None:
        if v is None:
            return None
        value = v.strip()
        if value and "\n" in value:
            raise ValueError("不能包含换行")
        return value or None

    @field_validator("trigger_text", "response_template", "transfer_notice_template")
    @classmethod
    def _trim_required_text(cls, v: str) -> str:
        value = str(v or "").strip()
        if not value:
            raise ValueError("不能为空")
        return value

    @field_validator("chat_ids")
    @classmethod
    def _normalize_chat_ids(cls, v: list[int]) -> list[int]:
        return _normalize_chat_id_list(v)

    @field_validator("trigger_texts")
    @classmethod
    def _normalize_trigger_texts(cls, v: list[str]) -> list[str]:
        return _normalize_string_list(v, default=["转账成功"])

    @field_validator("module_start_keywords", "open_commands", "close_commands", "status_commands")
    @classmethod
    def _normalize_optional_string_list(cls, v: list[str]) -> list[str]:
        return _normalize_string_list(v)


AccountBotTransferNoticeConfig = AccountBotInteractionConfig


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
