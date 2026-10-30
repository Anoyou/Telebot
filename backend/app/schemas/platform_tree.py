"""运行树（GET /api/platform/tree）响应模型。

旧字段含义不变；0.113.0-beta.2 起只追加字段（按账号口径、真实接入方式、关不掉的原因、中文名）。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .platform_capabilities import ModuleKey, RuntimeState


class PlatformTreeWorker(BaseModel):
    account_id: int
    pid: int | None = None
    alive: bool = False
    desired: str = "running"
    fail_count: int = 0
    queued: bool = False
    starting: bool = False


class PlatformTreeUserbot(BaseModel):
    workers: list[PlatformTreeWorker] = Field(default_factory=list)
    total: int = 0
    alive: int = 0


class PlatformTreeTrunk(BaseModel):
    userbot: PlatformTreeUserbot
    kill_switch: bool = False
    current_profile: Literal["production", "safe_watch", "custom"] = "production"


class PlatformTreeDemander(BaseModel):
    key: str = Field(description="需要该模块的插件 key")
    display_name: str = Field(description="插件中文名（缺省为 key）")
    account_ids: list[int] = Field(
        default_factory=list, description="在哪些账号上启用了这个插件（全局事实）"
    )


class PlatformTreeBranch(BaseModel):
    label: str = Field(default="", description="模块中文名")
    state: RuntimeState
    desired: bool
    forced_off: bool = False
    forced_off_reason: str | None = Field(
        default=None, description="被强制关闭时的中文原因（值守模式 / 管理员手动关闭）"
    )
    demanded_by: list[str] = Field(
        default_factory=list,
        description="需要该模块的插件 key；传 account_id 时只算该账号启用的插件，否则为全局（任一账号）",
    )
    demanded_by_global: list[str] = Field(
        default_factory=list,
        description="全局口径：任一账号启用且需要该模块的插件 key（决定能不能关）",
    )
    demanders: list[PlatformTreeDemander] = Field(
        default_factory=list, description="全局口径的需要者明细（中文名 + 在哪些账号启用）"
    )
    can_turn_off: bool = Field(
        default=False, description="现在能否关闭：模块开着且全局没有插件需要它（模块全服共用）"
    )
    blocked_code: Literal[
        "already_off",
        "required_by_plugins",
        "required_by_this_account",
        "required_by_other_accounts",
    ] | None = Field(
        default=None,
        description=(
            "can_turn_off 为 false 的原因代码：already_off 已关闭；required_by_plugins 有插件需要（服务器口径）；"
            "required_by_this_account 本账号启用的插件需要；required_by_other_accounts 本账号没人用但其他账号的插件需要"
        ),
    )
    blocked_reason: str | None = Field(
        default=None, description="关不掉的中文说明，可直接展示；能关时为 null"
    )


PlatformTreeChannel = Literal["passthrough", "main", "interaction"]
PlatformTreeEntryKind = Literal[
    "passthrough",
    "command",
    "event",
    "message",
    "schedule",
    "webhook",
    "interaction",
    "middleware",
    "payment_source",
    "agent_tool",
]


class PlatformTreeLeaf(BaseModel):
    key: str
    display_name: str = Field(default="", description="插件中文名（缺省为 key）")
    attachment: Literal["直通", "命令", "交互"] = Field(
        description="旧字段，保留兼容：其中「命令」实际指主通道（含事件订阅、定时任务等），新客户端请用 channel"
    )
    channel: PlatformTreeChannel = Field(
        default="main",
        description="接入通道：passthrough 直通（最先拿到原始消息）/ main 主通道（账号自己的消息链路）/ interaction 交互 Bot",
    )
    channel_label: str = Field(default="主通道", description="接入通道中文名：直通 / 主通道 / 交互 Bot")
    entry_kinds: list[PlatformTreeEntryKind] = Field(
        default_factory=list,
        description="插件真实声明或实现的入口：命令、事件订阅、消息监听、定时任务、Webhook、交互入口、中间件、转账通知解析、Agent 工具等",
    )
    entry_labels: list[str] = Field(default_factory=list, description="entry_kinds 对应的中文名，顺序一致")
    enabled: bool = Field(
        default=False, description="是否启用；传 account_id 时为该账号口径，否则为任一账号启用即算"
    )
    enabled_global: bool = Field(default=False, description="全局口径：任一账号启用即为 true")
    enabled_account_ids: list[int] = Field(default_factory=list, description="在哪些账号上启用")
    package_enabled: bool = Field(default=True, description="插件包本身是否启用（内置插件恒为 true）")
    account_state: str | None = Field(
        default=None, description="传 account_id 时该账号上的状态：active / failed / disabled；否则为 null"
    )
    account_error: str | None = Field(default=None, description="传 account_id 时该账号上最近的失败原因")
    requires: list[ModuleKey] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    source_missing: bool = False


class PlatformTreeScope(BaseModel):
    kind: Literal["server", "account"] = Field(
        default="server", description="server 全服口径（未传 account_id）/ account 按账号口径"
    )
    account_id: int | None = None
    account_name: str | None = None


class PlatformTreeOut(BaseModel):
    title: str = Field(default="运行树", description="三端统一名称")
    subtitle: str = Field(default="这台服务器开了哪些模块、哪些插件在用")
    scope: PlatformTreeScope = Field(default_factory=PlatformTreeScope)
    trunk: PlatformTreeTrunk
    branches: dict[ModuleKey, PlatformTreeBranch]
    leaves: list[PlatformTreeLeaf] = Field(default_factory=list)


__all__ = ["PlatformTreeOut"]
