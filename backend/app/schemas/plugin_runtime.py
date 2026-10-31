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


class PluginHostResourceOut(BaseModel):
    ref: str = Field(description="资源引用（名称@版本），写进 plugin.json reads 后用 ctx.host.read(ref) 读取")
    name: str
    version: int
    summary: str
    since: str
    params: list[str] = Field(default_factory=list, description="ctx.host.read 接受的关键字参数")


class PluginCoreMiddlewareOut(BaseModel):
    name: str = Field(description="平台内置中间件名（core.*），顺序固定、不能卸载")
    stage: str = Field(description="outbound / transport / delivery")
    order: int
    critical: bool = Field(default=False, description="True 表示失败时不放行（fail-closed），只有平台中间件可以")
    summary: str


class PluginMiddlewareLimitsOut(BaseModel):
    stages: list[str] = Field(description="插件可声明的阶段")
    default_timeout_ms: int
    min_timeout_ms: int
    max_timeout_ms: int
    stream_preview_timeout_ms: int = Field(description="stream_preview（流式中间态）每个中间件的时限")
    chain_budget_ms: int = Field(description="一条消息整条链的预算；超出后剩余中间件跳过")
    max_chain_length: int = Field(description="每个账号每个阶段最多生效的插件中间件数")
    order_min: int
    order_max: int
    outbound_action_types: list[str]
    outbound_mutable_fields: list[str] = Field(description="outbound 中间件可以改的动作字段；改了别的字段整次修改回滚")
    inbound_patch_fields: list[str]


class PluginCapabilitiesResponse(BaseModel):
    host_version: str = Field(description="当前 TelePilot 版本")
    plugin_api_version: str = Field(description="宿主提供的 Plugin API 版本（MAJOR.MINOR）")
    contract_version: str | None = Field(
        default=None,
        description=(
            "宿主承诺的插件稳定契约版本（如 1.0，见 docs/PLUGIN-CONTRACT.md），0.115.0+ / Plugin API 1.7+；"
            "旧服务器不返回该字段，客户端按缺省（契约之前）处理"
        ),
    )
    contract_baseline_plugin_api: str | None = Field(
        default=None, description="契约冻结基线对应的 Plugin API 版本（契约 1.0 = 1.6），0.115.0+"
    )
    capabilities: list[PluginCapabilityOut]
    deprecations: list[PluginDeprecationOut]
    host_resources: list[PluginHostResourceOut] = Field(
        default_factory=list, description="ctx.host.read 可读的宿主资源（含 ai.models@1 模型清单），0.109.0+"
    )
    core_middleware: list[PluginCoreMiddlewareOut] = Field(default_factory=list, description="平台内置中间件，0.109.0+")
    middleware_limits: PluginMiddlewareLimitsOut | None = Field(default=None, description="插件中间件的预算与限制，0.109.0+")


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
    blocked: int = Field(default=0, description="插件代码同步阻塞事件循环 ≥200ms 的次数（0.109.0+）")
    blocking_ms: int = Field(default=0, description="同步阻塞累计毫秒（0.109.0+）；持续偏高说明该插件适合迁到独立进程")
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
    blocked: int = 0
    blocking_ms: int = 0


class PluginHookStatsResponse(BaseModel):
    plugin_key: str
    account_id: int | None = Field(default=None, description="按账号过滤时的账号 ID")
    available: bool = Field(default=True, description="False 表示统计存储（Redis）暂不可用")
    message: str | None = None
    hooks: list[PluginHookStatOut] = Field(default_factory=list)
    totals: PluginHookTotalsOut = Field(default_factory=PluginHookTotalsOut)
    deprecations: list[PluginDeprecationUsageOut] = Field(default_factory=list)
    retention_days: int = 7
