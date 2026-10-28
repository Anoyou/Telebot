"""插件运行时平台信息：能力注册表、Plugin API 版本、钩子统计与弃用登记。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class PluginCapabilityOut(BaseModel):
    name: str = Field(description="能力名，如 messages.guest_reply_markup；插件用 ctx.capabilities.has(name) 判断")
    version: int = Field(description="能力版本，从 1 开始")
    since: str = Field(description="首个提供该能力的 TelePilot 版本（可带 beta 号）")
    plugin_api: str = Field(description="引入该能力的 Plugin API 版本")
    summary: str
    legacy_attr: str | None = Field(default=None, description="兼容旧探测方式的 ctx.messages 类属性名")
    facades: list[str] = Field(default_factory=list, description="只对这些 MessageOps facade 成立；空表示宿主级能力")
    doc: str = ""
    deprecated_in: str | None = None
    removed_in: str | None = None


class PluginDeprecationOut(BaseModel):
    id: str
    summary: str
    replacement: str
    deprecated_since: str
    removal: str
    doc: str = ""


class PluginCapabilitiesResponse(BaseModel):
    host_version: str = Field(description="当前 TelePilot 版本")
    plugin_api_version: str = Field(description="宿主提供的 Plugin API 版本（MAJOR.MINOR）")
    capabilities: list[PluginCapabilityOut]
    deprecations: list[PluginDeprecationOut]


class PluginHookStatOut(BaseModel):
    account_id: int = Field(description="账号 ID；0 表示与账号无关")
    hook: str = Field(description="钩子名：on_event / on_interaction / on_message / on_direct_message / on_startup / on_config_action / system_agent_tool …")
    event_type: str = Field(description="事件类型或动作名；'-' 表示未区分")
    calls: int = 0
    ok: int = 0
    failed: int = Field(default=0, description="抛异常的次数（不含超时）")
    timeout: int = 0
    circuit_open: int = Field(default=0, description="熔断中被跳过的次数")
    skipped: int = Field(default=0, description="插件更新中被跳过的次数")
    sum_ms: int = 0
    avg_ms: float | None = None
    p50_ms: int | None = Field(default=None, description="按耗时直方图估算的中位数上界")
    p95_ms: int | None = Field(default=None, description="按耗时直方图估算的 P95 上界")
    max_ms: int = 0
    failure_rate: float = Field(default=0.0, description="(failed + timeout) / calls")
    last_at: float | None = Field(default=None, description="最近一次调用（Unix 秒）")
    last_error: str | None = Field(default=None, description="最近一次失败摘要（已脱敏、截断）")
    last_error_at: float | None = None
    since: float | None = Field(default=None, description="开始统计的时间（Unix 秒）")


class PluginDeprecationUsageOut(BaseModel):
    account_id: int
    id: str
    count: int
    last_at: float | None = None
    detail: str | None = None
    summary: str | None = None
    replacement: str | None = None
    removal: str | None = None


class PluginHookTotalsOut(BaseModel):
    calls: int = 0
    ok: int = 0
    fail: int = 0
    timeout: int = 0
    circuit_open: int = 0
    skipped: int = 0
    sum_ms: int = 0


class PluginHookStatsResponse(BaseModel):
    plugin_key: str
    account_id: int | None = Field(default=None, description="按账号过滤时的账号 ID")
    available: bool = Field(default=True, description="False 表示统计存储（Redis）暂不可用")
    message: str | None = None
    hooks: list[PluginHookStatOut] = Field(default_factory=list)
    totals: PluginHookTotalsOut = Field(default_factory=PluginHookTotalsOut)
    deprecations: list[PluginDeprecationUsageOut] = Field(default_factory=list)
    retention_days: int = 7
