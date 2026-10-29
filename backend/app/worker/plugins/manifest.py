"""插件 Manifest 数据类。

每个插件目录里的 ``manifest.py`` 顶层导出 ``MANIFEST: Manifest`` 实例，
loader 在扫描目录阶段读取这个常量来决定加载方式、显示名、版本以及（阶段 C 引入的）权限范围。

字段说明：
- ``key``：插件唯一 key（与 ``Plugin.key`` 一致；同时也是 ``feature.key``）
- ``display_name``：用户可见名称
- ``version``：语义化版本号；第三方插件通过 zip / 仓库升级时按此对比
- ``author``：作者；核心兼容代码可以沿用历史默认值，第三方插件应填写真实作者或组织名
- ``description``：一句话描述（前端"插件管理"列表展示）
- ``usage``：插件必须声明的详细使用说明；插件配置页和规范警告会据此提示安装者
- ``requires_features``：声明依赖的其它插件 key 列表（先注册了才能加载本插件）
- ``min_telepilot_version``：声明最低 TelePilot 版本（0.107.0 起可带 beta 号）；旧 ``min_telebot_version`` 保留兼容（已弃用）
- ``plugin_api``：Plugin API 兼容范围（0.107.0+，推荐只写在 plugin.json）
- ``middleware`` / ``render_profile``：消息管线中间件与出站渲染声明（0.109.0+，推荐只写在 plugin.json）
- ``provides``：扩展点声明（0.112.0+，如 ``["ai.image_backend"]``，推荐只写在 plugin.json）
- ``config_schema``：``rule.config`` 的 JSON Schema，前端可据此生成动态表单
- ``config_actions``：配置页动作声明，前端按声明渲染按钮，后端调插件 ``on_config_action``
- ``category``：模块身份分类（interactive / automation / utility）
- ``interaction_entries``：声明可由 TelePilot 调度的插件入口，既可被管理员命令触发，也可由群内关键词/交互 Bot 触发；
  入口可声明 ``timeout_seconds``（默认 15 秒，平台上限 90 秒）放宽单次执行时限
- ``event_subscriptions``：声明插件希望从 Event Bus 接收的 Telegram 事件
- ``interaction_profile``：声明交互入口属于哪类玩法（如群局抢答、对战、奖池）
- ``command_fallback``：交互入口无法直接接入时的受控回退声明
- ``preserve_command_trigger``：是否保留原有 UserBot 命令触发
- ``capabilities``：可信插件高风险能力声明，例如 ``telegram_native_raw``
- ``strict_trace``：插件是否要求路由投递层常驻全链路 trace（资金类 / payout 插件建议开启）
- ``experimental``：是否为实验性插件；前端可据此提示风险
- ``permissions``：可信插件的能力说明（如 ``send_message`` / ``edit_message``），用于安装提示、审计和 UI 展示
- ``allowed_hosts``：声明 ``external_http`` 能访问的外部域名白名单
- ``http``：``ctx.http`` 的扩展策略元数据（如是否允许 direct 出口）
"""

from __future__ import annotations

from dataclasses import InitVar, dataclass, field
from typing import Any

# 记录 ``properties`` 的声明顺序。PostgreSQL JSONB 不保留对象键顺序，
# 写库后 App/Web 只能靠这份列表还原插件作者声明的字段顺序。
PROPERTY_ORDER_KEY = "x-ui-property-order"


def with_property_order(schema: Any) -> Any:
    """返回带 ``x-ui-property-order`` 的 schema 副本（递归处理嵌套对象与数组项）。

    已有的声明会按当前 ``properties`` 重新计算，保证与字段一致；不修改入参。
    """

    if isinstance(schema, list):
        return [with_property_order(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    out = {key: with_property_order(value) for key, value in schema.items()}
    properties = out.get("properties")
    if isinstance(properties, dict) and properties:
        out[PROPERTY_ORDER_KEY] = [key for key in properties if isinstance(key, str)]
    return out


@dataclass
class Manifest:
    """插件元数据。"""

    key: str
    display_name: str
    version: str = "0.1.0"
    min_telepilot_version: str | None = None
    min_telebot_version: str | None = None
    # Plugin API 兼容范围（0.107.0+，如 "^1.0"）。推荐只写在 plugin.json：旧宿主的 Manifest
    # 不接受这个参数，写进 manifest.py 会在旧宿主上导入失败；宿主加载时会从 plugin.json 补齐。
    plugin_api: str | None = None
    # 声明式只读宿主查询（0.108.0+，Plugin API 1.1）：ctx.host.read 只能读这里列出的资源，
    # 如 ["account.admins@1", "ai.models@1"]。同 plugin_api，推荐只写在 plugin.json。
    reads: list[str] = field(default_factory=list)
    # 消息管线中间件声明（0.109.0+，Plugin API 1.2，见 middleware 模块）。同 plugin_api，推荐只写在 plugin.json。
    middleware: list[dict[str, Any]] = field(default_factory=list)
    # 出站渲染声明（0.109.0+），如 {"table": {"compact": true}}；由 core.render_profile 读取。推荐只写在 plugin.json。
    render_profile: dict[str, Any] | None = None
    # 声明式 UI（0.110.0+，Plugin API 1.3）：{"cards": [...]}，见 schemas/plugin-ui-widgets.json。
    # 同 reads，推荐只写在 plugin.json（旧宿主的 Manifest 不接受这个参数）。
    ui: dict[str, Any] | None = None
    # 扩展点声明（0.112.0+，Plugin API 1.5），如 ["ai.image_backend"]：宿主按能力选择插件，
    # 不再按插件 key 写死。同 reads，推荐只写在 plugin.json（旧宿主的 Manifest 不接受这个参数）。
    provides: list[str] = field(default_factory=list)
    author: str = "builtin"
    description: str = ""
    usage: str = ""
    # 依赖其它插件的 key；loader 会拒绝加载缺少依赖的插件。
    requires_features: list[str] = field(default_factory=list)
    # 声明依赖的平台能力模块（ai / interaction_bot / webhooks / ledger / dispatch_debug）。
    # 旧插件未声明时继续加载；运行时由平台裁剪不可用入口。
    requires_platform_capabilities: list[str] = field(default_factory=list)
    # rule.config 的 JSON Schema，可选；前端编辑器据此渲染
    config_schema: dict[str, Any] | None = None
    # 配置页动作声明；也可放在 config_schema["x-config-actions"] 里。
    config_actions: list[dict[str, Any]] = field(default_factory=list)
    # 声明性 Agent 工具；执行 handler 仍必须由宿主显式注册。
    agent_tools: list[dict[str, Any]] = field(default_factory=list)
    # System Agent 路由关键词（可选，上限由宿主裁剪）
    agent_keywords: list[str] = field(default_factory=list)
    # 模块身份分类：interactive（互动娱乐）/ automation（自动化）/ utility（工具能力）
    category: str = "utility"
    # 可由交互 Bot 启动的入口声明；未声明则默认不出现在交互 Bot 模块列表里
    interaction_entries: list[dict[str, Any]] = field(default_factory=list)
    # Event Bus 订阅声明；新插件主路径，平台按声明投递标准事件信封。
    event_subscriptions: list[dict[str, Any]] = field(default_factory=list)
    # 交互玩法类型提示（session_game / challenge_game / reward_pool / utility_trigger）。
    # 平台和前端只把它当声明性元数据，不改变原命令语义。
    interaction_profile: str | None = None
    # 交互入口到原命令的受控回退声明；由平台按需解释
    command_fallback: dict[str, Any] | None = None
    # 是否保留原有 UserBot 命令触发；默认 True 以兼容旧插件
    preserve_command_trigger: bool = True
    # 可信插件高风险能力声明，例如 {"telegram_native_raw": {"enabled": true, ...}}。
    capabilities: dict[str, Any] | None = None
    # 是否要求路由投递层常驻全链路 trace；动作层 record_action 不受此开关影响。
    strict_trace: bool = False
    # 是否为实验性插件；前端可据此展示 warning badge
    experimental: bool = False
    # ===== 阶段 C 引入：能力清单 =====
    # 安装型插件必须显式声明权限；核心兼容代码也应在各自 manifest 中写清楚。
    permissions: list[str] = field(default_factory=list)
    # external_http 权限对应的域名白名单；支持 exact / *.domain / **.domain。
    allowed_hosts: list[str] = field(default_factory=list)
    # ctx.http 扩展策略，例如 {"allow_direct": true}。
    http: dict[str, Any] | None = None
    # 交互动作发送通道元数据，例如 {"interaction_bot", "userbot_reply"}。
    # 插件可在动作里选择单通道或候选通道；平台负责执行、审计和能力边界。
    interaction_send_via: list[str] = field(default_factory=list)
    # 仅保留旧第三方插件的构造参数兼容；loader 从未执行该钩子，实例不存储也不序列化。
    on_install: InitVar[str | None] = None

    def __post_init__(self, on_install: str | None = None) -> None:
        del on_install
        if isinstance(self.config_schema, dict):
            self.config_schema = with_property_order(self.config_schema)

    def to_dict(self) -> dict[str, Any]:
        """序列化成可写入 DB / JSON 的 dict。"""
        return {
            "key": self.key,
            "display_name": self.display_name,
            "version": self.version,
            "min_telepilot_version": self.min_telepilot_version,
            "min_telebot_version": self.min_telebot_version,
            "plugin_api": self.plugin_api,
            "reads": list(self.reads or []),
            "middleware": list(self.middleware or []),
            "render_profile": self.render_profile,
            "ui": self.ui,
            "provides": list(self.provides or []),
            "author": self.author,
            "description": self.description,
            "usage": self.usage,
            "requires_features": list(self.requires_features),
            "requires_platform_capabilities": list(self.requires_platform_capabilities),
            "config_schema": self.config_schema,
            "config_actions": list(self.config_actions),
            "agent_tools": list(self.agent_tools),
            "agent_keywords": list(self.agent_keywords),
            "category": self.category,
            "interaction_entries": list(self.interaction_entries),
            "event_subscriptions": list(self.event_subscriptions),
            "interaction_profile": self.interaction_profile,
            "command_fallback": self.command_fallback,
            "preserve_command_trigger": self.preserve_command_trigger,
            "capabilities": self.capabilities,
            "strict_trace": self.strict_trace,
            "x-experimental": self.experimental,
            "permissions": list(self.permissions),
            "allowed_hosts": list(self.allowed_hosts),
            "http": self.http,
            "interaction_send_via": list(self.interaction_send_via),
        }


__all__ = ["PROPERTY_ORDER_KEY", "Manifest", "with_property_order"]
