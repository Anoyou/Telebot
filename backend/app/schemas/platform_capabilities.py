"""平台能力热插拔状态模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

RuntimeState = Literal["starting", "ready", "quiescing", "stopped", "failed"]
ModuleKey = Literal["ai", "interaction_bot", "webhooks", "ledger", "dispatch_debug"]
ChannelKey = Literal["userbot", "interaction_bot", "webhook"]
BlockedReasonCode = Literal[
    "platform_module_disabled",
    "channel_disabled",
    "channel_not_configured",
    "capability_unavailable",
    "platform_module_transitioning",
]


class CapabilityModuleState(BaseModel):
    key: ModuleKey
    label: str
    desired_enabled: bool = True
    forced_off: bool = False
    generation: int = 0
    runtime_state: RuntimeState = "starting"
    last_error: str | None = None
    last_transition_at: datetime | None = None
    resource_summary: dict[str, Any] = Field(default_factory=dict)


class CapabilityChannelState(BaseModel):
    key: ChannelKey
    label: str
    fixed: bool = True
    managed_by: ModuleKey | None = None
    available: bool = True
    reason_code: BlockedReasonCode | None = None
    reason_text: str | None = None


class CapabilityWorkerConvergence(BaseModel):
    total_accounts: int = 0
    notified: int = 0
    acked: int = 0
    pending: int = 0
    offline_or_timeout: int = 0
    last_broadcast_at: datetime | None = None
    notes: list[str] = Field(default_factory=list)


# 服务器能力探测标志：客户端（iOS / Web）据此判断服务器是否支持某项新接口行为，
# 只追加、不删除；老服务器没有 features 字段 = 全部不支持。
PLATFORM_FEATURE_FLAGS: dict[str, bool] = {
    # 0.113.0-beta.2：GET /api/platform/tree 支持 ?account_id= 按账号口径，并返回
    # channel / entry_kinds / display_name / demanded_by_global / blocked_reason 等字段。
    "platform_tree_account_scope": True,
    # 第 20 轮：GET /api/secrets/reveal 按需查看已保存的密钥（登录、审计、no-store）。
    "secret_reveal": True,
}


class PlatformCapabilitiesOut(BaseModel):
    features: dict[str, bool] = Field(
        default_factory=lambda: dict(PLATFORM_FEATURE_FLAGS),
        description="服务器能力探测标志；如 platform_tree_account_scope=true 表示运行树支持按账号口径",
    )
    modules: list[CapabilityModuleState]
    channels: list[CapabilityChannelState]
    worker_convergence: CapabilityWorkerConvergence = Field(
        default_factory=CapabilityWorkerConvergence
    )
    cache_ready: bool = False
    updated_at: datetime | None = None


class CapabilityModulePatch(BaseModel):
    enabled: bool


class CapabilityModulePatchOut(BaseModel):
    module: CapabilityModuleState
    worker_convergence: CapabilityWorkerConvergence
    ok: bool = True
    message: str | None = None


__all__ = [
    "BlockedReasonCode",
    "CapabilityChannelState",
    "CapabilityModulePatch",
    "CapabilityModulePatchOut",
    "CapabilityModuleState",
    "CapabilityWorkerConvergence",
    "ChannelKey",
    "ModuleKey",
    "PlatformCapabilitiesOut",
    "RuntimeState",
]
