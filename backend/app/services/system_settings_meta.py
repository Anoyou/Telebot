"""服务器参数元数据（第 21 轮）：每个 ``/api/system/settings`` 参数的中文名、英文原名、大白话说明、
类型 / 单位 / 范围 / 默认值，以及「这个参数在别的专门页面里也能改」的位置。

这是 iOS 与 Web 显示服务器参数时的**单一来源**：

- ``GET /api/system/settings/meta`` 原样返回 :data:`SETTINGS_META`；
- ``PATCH /api/system/settings`` 的请求 schema 字段 ``title`` / ``description`` 也从这里取，
  所以 OpenAPI 生成的通用表单同样显示「中文名 · 英文原名」。

只描述参数，不改变任何读写行为：键名、默认值与范围与 ``app.api.rate_limit`` 里的
``get_system_settings`` / ``patch_system_settings`` 一致（测试 ``test_system_settings_meta`` 校验每个
GET 返回的键都有元数据、每个 PATCH 字段都有元数据）。旧服务器没有这个接口时，客户端回退到原键名显示。
"""

from __future__ import annotations

from typing import Any

META_VERSION = 1

# 分组顺序即显示顺序。
GROUPS: list[dict[str, str]] = [
    {"id": "command", "label": "指令", "description": "Telegram 里怎么触发指令。修改后各账号自动热加载，不用重启。"},
    {"id": "time", "label": "时区", "description": "定时任务和各种时间显示按这个时区计算。"},
    {"id": "logs", "label": "运行日志", "description": "运行日志保存多少、保存多久。日志等级保存后立即生效。"},
    {
        "id": "trace",
        "label": "调用链与事件投递",
        "description": "调用链（Trace）记录每条消息在系统内部怎么走；事件投递（Event Bus）把消息事件交给订阅它的插件。",
    },
    {"id": "retention", "label": "其它保留期限", "description": "各类记录自动清理前保留的天数，0 = 不自动删除。"},
    {"id": "updates", "label": "插件与服务器更新", "description": "自动检查插件更新、服务器从哪里取更新。"},
    {"id": "ai", "label": "AI", "description": "AI 模块开关与预算。"},
    {"id": "limits", "label": "出款上限", "description": "群内游戏币出款的单笔与每日上限，0 = 不限制。"},
    {"id": "security", "label": "登录安全", "description": "管理台登录失败后的验证码与验证器保护。"},
    {"id": "permissions", "label": "权限与紧急控制", "description": "Sudo 功能与全局紧急停止。"},
    {"id": "interface", "label": "Web 界面偏好", "description": "Web 管理台的导航与排序偏好，跨设备同步。"},
]


def _loc(label: str, *, web: str | None = None, ios: str | None = None, ios_label: str | None = None) -> dict[str, Any]:
    """一个「也在别处可改」的位置。

    - ``label``：Web 上的位置名（例如「服务器设置 › 限流与风控」）；
    - ``web``：Web 路由，前端据此做「去修改」链接；
    - ``ios``：iOS 原生页面标识（客户端按标识跳转，不认识的标识只显示文字）；
    - ``ios_label``：iOS 上的位置名，没有 iOS 页面时为空。
    """

    return {"label": label, "web_route": web, "ios_target": ios, "ios_label": ios_label}


# iOS 原生页面标识（客户端内置映射；新增标识时老 App 只显示文字，不跳转）。
IOS_AI_GLOBAL = "ai_global_settings"
IOS_EMERGENCY_STOP = "emergency_stop"
IOS_RUNTIME_TREE = "runtime_tree"
IOS_SOFTWARE_UPDATE = "software_update"
IOS_SUDO = "sudo"
IOS_TOTP = "totp"

_WEB_PLATFORM = "/settings?tab=platform"
_WEB_SECURITY = "/settings?tab=security"
_WEB_ACCOUNT = "/settings?tab=account"
_WEB_INTERFACE = "/settings?tab=interface"


def _p(
    key: str,
    label: str,
    description: str,
    type_: str,
    group: str,
    *,
    default: Any = None,
    unit: str | None = None,
    minimum: int | None = None,
    maximum: int | None = None,
    max_length: int | None = None,
    options: list[dict[str, str]] | None = None,
    editable: bool = True,
    read_only_reason: str | None = None,
    also_in: list[dict[str, Any]] | None = None,
    advanced: bool = False,
    zero_means: str | None = None,
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "description": description,
        "type": type_,
        "group": group,
        "default": default,
        "unit": unit,
        "min": minimum,
        "max": maximum,
        "max_length": max_length,
        "options": options or [],
        "editable": editable,
        "read_only_reason": read_only_reason,
        "also_in": also_in or [],
        "advanced": advanced,
        "zero_means": zero_means,
    }


_DAYS = "天"
_NO_DELETE = "不自动删除"
_UNLIMITED = "不限制"

SETTINGS_META: list[dict[str, Any]] = [
    # ── 指令 ──
    _p("command_prefix", "指令前缀", "在 Telegram 里发指令时开头的符号，比如“,ai”里的“,”。最多 3 个字符。",
       "string", "command", default=",", max_length=3,
       also_in=[_loc("服务器设置 › 平台与模块", web=_WEB_PLATFORM)]),
    _p("command_prefix_required", "账号本人必须带前缀",
       "关掉后，只有你这个账号本人发的消息可以不带前缀直接用指令名触发；群里其他人仍然不能用裸指令触发。",
       "bool", "command", default=True,
       also_in=[_loc("服务器设置 › 平台与模块", web=_WEB_PLATFORM)]),
    _p("ai_context_messages", "AI 指令上下文条数",
       "用 AI 指令时顺带把最近几条聊天记录发给模型，让它看懂前因后果。0 = 不带。",
       "integer", "command", default=0, unit="条", minimum=0, maximum=20, zero_means="不带上下文",
       also_in=[
           _loc("服务器设置 › 平台与模块", web=_WEB_PLATFORM, ios=IOS_AI_GLOBAL, ios_label="AI › AI 全局设置"),
       ]),
    _p("command_echo_guard_previous_messages", "命令回声防误触",
       "群里有人原样复读一条指令时，往前看这么多条消息，避免被复读误触发。0 = 关闭。",
       "integer", "command", default=8, unit="条", minimum=0, maximum=50, zero_means="关闭",
       also_in=[_loc("指令与任务 › 指令模板", web="/operations/templates")]),
    # ── 时区 ──
    _p("timezone", "服务器时区",
       "IANA 时区名，例如 Asia/Shanghai。定时任务的下次 / 上次触发时间都按它算。",
       "timezone", "time", default="Asia/Shanghai",
       also_in=[_loc("服务器设置 › 平台与模块", web=_WEB_PLATFORM)]),
    # ── 运行日志 ──
    _p("log_retention.runtime_log_min_level", "运行日志等级",
       "比这个等级低的日志不保存。排查问题时可以临时调到“调试”，平时用“信息”。",
       "enum", "logs", default="info",
       options=[
           {"value": "debug", "label": "调试（最详细）"},
           {"value": "info", "label": "信息（默认）"},
           {"value": "warn", "label": "警告（只留告警和错误）"},
           {"value": "error", "label": "错误（只留错误）"},
       ],
       also_in=[_loc("服务器设置 › 平台与模块", web=_WEB_PLATFORM)]),
    _p("log_retention.runtime_log_retention_days", "运行日志保留天数", "运行日志保存多少天后自动删除。",
       "integer", "logs", default=30, unit=_DAYS, minimum=0, maximum=3650, zero_means=_NO_DELETE,
       also_in=[_loc("服务器设置 › 平台与模块", web=_WEB_PLATFORM)]),
    _p("log_retention.runtime_log_max_message_chars", "日志正文最多字符", "一条日志的正文太长时，超出部分截掉再保存。",
       "integer", "logs", default=2000, unit="字", minimum=200, maximum=20000,
       also_in=[_loc("服务器设置 › 平台与模块", web=_WEB_PLATFORM)]),
    _p("log_retention.runtime_log_max_detail_chars", "日志附加详情最多字符",
       "日志附带的结构化详情（排查用的额外字段）最多保存多少字。0 = 不保存详情。",
       "integer", "logs", default=8000, unit="字", minimum=0, maximum=50000, zero_means="不保存详情",
       also_in=[_loc("服务器设置 › 平台与模块", web=_WEB_PLATFORM)]),
    # ── 调用链与事件投递 ──
    _p("log_retention.trace_enabled", "记录调用链（Trace）",
       "记录每条消息在系统里经过了哪些步骤，方便排查。只有调用链存储出问题时才需要关。",
       "bool", "trace", default=True, advanced=True,
       also_in=[_loc("服务器设置 › 平台与模块", web=_WEB_PLATFORM)]),
    _p("log_retention.event_bus_delivery_enabled", "事件投递给插件（Event Bus）",
       "把收到的消息事件交给订阅它的插件处理。关掉会退回旧的交互规则链路，只在回滚观察时用。",
       "bool", "trace", default=True, advanced=True,
       also_in=[_loc("服务器设置 › 平台与模块", web=_WEB_PLATFORM)]),
    _p("log_retention.inline_updates_enabled", "接收内联查询（Inline）",
       "接收别人在输入框里 @Bot 发起的内联查询和选中结果。关掉后 Bot 不再响应内联查询。",
       "bool", "trace", default=True,
       also_in=[_loc("服务器设置 › 平台与模块", web=_WEB_PLATFORM)]),
    _p("log_retention.guest_updates_enabled", "接收访客消息（Guest Mode）",
       "Bot 没进的群里有人 @它时也能收到并回复。关掉后不再接收这类消息。",
       "bool", "trace", default=True,
       also_in=[_loc("服务器设置 › 平台与模块", web=_WEB_PLATFORM)]),
    _p("log_retention.trace_retention_days", "调用链保留天数", "调用链记录保存多少天后自动删除。",
       "integer", "trace", default=30, unit=_DAYS, minimum=0, maximum=3650, zero_means=_NO_DELETE,
       also_in=[_loc("服务器设置 › 平台与模块", web=_WEB_PLATFORM)]),
    _p("log_retention.trace_payload_snapshot_retention_days", "消息内容快照保留天数",
       "调用链里附带的消息原文快照保存多少天；到期只清快照，调用链本身还在。",
       "integer", "trace", default=7, unit=_DAYS, minimum=0, maximum=3650, zero_means=_NO_DELETE,
       also_in=[_loc("服务器设置 › 平台与模块", web=_WEB_PLATFORM)]),
    _p("log_retention.native_raw_persist_enabled", "保存 Telegram 原始数据（native_raw）",
       "把 Telegram 推来的原始更新完整存下来，用于深度排查，会占更多空间。平时保持关闭。",
       "bool", "trace", default=False, advanced=True,
       also_in=[_loc("服务器设置 › 平台与模块", web=_WEB_PLATFORM)]),
    _p("log_retention.native_raw_retention_days", "原始数据保留天数", "Telegram 原始数据保存多少天。",
       "integer", "trace", default=1, unit=_DAYS, minimum=0, maximum=30, advanced=True,
       also_in=[_loc("服务器设置 › 平台与模块", web=_WEB_PLATFORM)]),
    _p("log_retention.payment_source_plugin_authoritative", "转账通知以插件结果为准",
       "默认关闭：转账通知以系统自带的解析为准，插件只做对照。对照长期一致后才建议打开。",
       "bool", "trace", default=False, editable=False, advanced=True,
       read_only_reason="涉及转账通知判定，App 里只读，请在 Web 修改。",
       also_in=[_loc("服务器设置 › 平台与模块", web=_WEB_PLATFORM)]),
    # ── 其它保留期限 ──
    _p("log_retention.llm_usage_retention_days", "AI 用量记录保留天数", "每次 AI 调用的用量记录保存多少天。",
       "integer", "retention", default=30, unit=_DAYS, minimum=0, maximum=3650, zero_means=_NO_DELETE,
       also_in=[_loc("服务器设置 › 平台与模块", web=_WEB_PLATFORM, ios=IOS_AI_GLOBAL, ios_label="AI › AI 全局设置")]),
    _p("log_retention.rate_limit_event_retention_days", "风控事件保留天数", "限流 / 风控拦截记录保存多少天。",
       "integer", "retention", default=30, unit=_DAYS, minimum=0, maximum=3650, zero_means=_NO_DELETE,
       also_in=[_loc("服务器设置 › 平台与模块", web=_WEB_PLATFORM)]),
    # ── 插件与服务器更新 ──
    _p("remote_plugin_update_check.enabled", "自动检查插件更新", "定期去插件仓库看看已安装的插件有没有新版本。",
       "bool", "updates", default=True,
       also_in=[_loc("插件 › 已安装", web="/plugins/manage")]),
    _p("remote_plugin_update_check.interval_minutes", "插件更新检查间隔", "多久检查一次插件更新。",
       "integer", "updates", default=360, unit="分钟", minimum=30, maximum=10080,
       also_in=[_loc("插件 › 已安装", web="/plugins/manage")]),
    _p("app_update_target.remote", "服务器更新来源（Git 远端）", "服务器更新时从哪个 Git 远端拉代码，一般是 origin。",
       "string", "updates", default="origin", advanced=True,
       also_in=[_loc("顶栏 › 检查更新", ios=IOS_SOFTWARE_UPDATE, ios_label="App 设置 › 软件更新")]),
    _p("app_update_target.branch", "服务器更新通道（分支）", "跟随哪个分支更新：main 是正式版，Beta 是测试版。",
       "string", "updates", default="main",
       also_in=[_loc("顶栏 › 检查更新", ios=IOS_SOFTWARE_UPDATE, ios_label="App 设置 › 软件更新")]),
    # ── AI ──
    _p("ai_enabled", "AI 模块总开关", "关掉后所有 AI 指令和插件里的 AI 调用都停用，配置保留。",
       "bool", "ai", default=True,
       also_in=[
           _loc("服务器设置 › 平台与模块", web=_WEB_PLATFORM, ios=IOS_AI_GLOBAL, ios_label="AI › AI 全局设置"),
           _loc("插件 › 运行（模块开关）", web="/plugins", ios=IOS_RUNTIME_TREE, ios_label="插件 › 运行"),
       ]),
    _p("llm_limits.per_minute", "AI 每分钟调用上限", "每个账号每分钟最多调用 AI 几次，防止循环触发。",
       "integer", "ai", default=0, unit="次", minimum=0, zero_means=_UNLIMITED,
       also_in=[_loc("服务器设置 › 限流与风控", web=_WEB_SECURITY, ios=IOS_AI_GLOBAL, ios_label="AI › AI 全局设置")]),
    _p("llm_limits.daily_requests", "AI 每日调用上限", "每个账号每天最多调用 AI 几次。",
       "integer", "ai", default=0, unit="次", minimum=0, zero_means=_UNLIMITED,
       also_in=[_loc("服务器设置 › 限流与风控", web=_WEB_SECURITY, ios=IOS_AI_GLOBAL, ios_label="AI › AI 全局设置")]),
    _p("llm_limits.daily_tokens", "AI 每日 Token 上限", "每个账号每天最多用掉多少 Token（模型计费的字数单位）。",
       "integer", "ai", default=0, unit="Token", minimum=0, zero_means=_UNLIMITED,
       also_in=[_loc("服务器设置 › 限流与风控", web=_WEB_SECURITY, ios=IOS_AI_GLOBAL, ios_label="AI › AI 全局设置")]),
    _p("llm_limits.premium_daily", "高价模型每日调用上限", "每个账号每天最多调用几次贵的模型（成本等级 3 及以上）。",
       "integer", "ai", default=0, unit="次", minimum=0, zero_means=_UNLIMITED,
       also_in=[_loc("服务器设置 › 限流与风控", web=_WEB_SECURITY, ios=IOS_AI_GLOBAL, ios_label="AI › AI 全局设置")]),
    # ── 出款上限 ──
    _p("payout_limits.single_max", "单笔出款上限", "一次最多发出多少游戏币（按你的业务币种填写）。",
       "integer", "limits", default=0, minimum=0, zero_means=_UNLIMITED,
       also_in=[_loc("服务器设置 › 限流与风控", web=_WEB_SECURITY)]),
    _p("payout_limits.daily_max", "每日出款上限", "一天累计最多发出多少游戏币。",
       "integer", "limits", default=0, minimum=0, zero_means=_UNLIMITED,
       also_in=[_loc("服务器设置 › 限流与风控", web=_WEB_SECURITY)]),
    # ── 登录安全 ──
    _p("login_security.notify_otp_enabled", "登录失败后发通知验证码",
       "管理台连续登录失败后，要求输入发到通知 Bot 的验证码才能继续。",
       "bool", "security", default=False,
       also_in=[_loc("服务器设置 › 账号与安全", web=_WEB_ACCOUNT)]),
    _p("login_security.notify_otp_failed_attempt_threshold", "失败几次后要通知验证码",
       "连续输错密码达到这个次数后，开始要求通知验证码。0 = 不启用。",
       "integer", "security", default=0, unit="次", minimum=0, maximum=50, zero_means="不启用",
       also_in=[_loc("服务器设置 › 账号与安全", web=_WEB_ACCOUNT)]),
    _p("login_security.notify_otp_fail_window_seconds", "失败次数统计时长", "在这段时间内累计的失败次数才算数。",
       "integer", "security", default=900, unit="秒", minimum=60, maximum=86400, advanced=True,
       also_in=[_loc("服务器设置 › 账号与安全", web=_WEB_ACCOUNT)]),
    _p("login_security.notify_otp_ttl_seconds", "通知验证码有效期", "发出的通知验证码多久后失效。",
       "integer", "security", default=300, unit="秒", minimum=60, maximum=1800, advanced=True,
       also_in=[_loc("服务器设置 › 账号与安全", web=_WEB_ACCOUNT)]),
    _p("login_security.notify_otp_max_attempts", "通知验证码最多试几次", "同一个验证码最多允许输错几次。",
       "integer", "security", default=3, unit="次", minimum=1, maximum=10, advanced=True,
       also_in=[_loc("服务器设置 › 账号与安全", web=_WEB_ACCOUNT)]),
    _p("login_security.totp_enabled", "验证器（TOTP）保护",
       "登录时要求输入手机验证器 App 里的 6 位动态码。请先在验证器页面完成绑定再打开。",
       "bool", "security", default=False,
       also_in=[_loc("服务器设置 › 账号与安全", web=_WEB_ACCOUNT, ios=IOS_TOTP, ios_label="服务器设置 › 验证器与恢复码")]),
    _p("login_security.totp_mode", "何时要求验证器码", "每次登录都要，还是连续失败几次后才要。",
       "enum", "security", default="after_failures",
       options=[
           {"value": "always", "label": "每次登录"},
           {"value": "after_failures", "label": "连续失败后"},
       ],
       also_in=[_loc("服务器设置 › 账号与安全", web=_WEB_ACCOUNT)]),
    _p("login_security.totp_failed_attempt_threshold", "失败几次后要验证器码", "“连续失败后”模式下，输错几次开始要求验证器码。",
       "integer", "security", default=5, unit="次", minimum=1, maximum=50,
       also_in=[_loc("服务器设置 › 账号与安全", web=_WEB_ACCOUNT)]),
    _p("login_security.recovery_code_ttl_seconds", "恢复码有效期", "申请的一次性恢复码多久后失效。",
       "integer", "security", default=900, unit="秒", minimum=60, maximum=86400, advanced=True,
       also_in=[_loc("服务器设置 › 账号与安全", web=_WEB_ACCOUNT)]),
    # ── 权限与紧急控制 ──
    _p("sudo_enabled", "Sudo 功能", "允许你授权的用户在 Telegram 里用 sudo 指令代你执行操作。",
       "bool", "permissions", default=False,
       also_in=[_loc("服务器设置 › 账号与安全", web=_WEB_ACCOUNT, ios=IOS_SUDO, ios_label="服务器设置 › Sudo 权限")]),
    _p("kill_switch", "全局紧急停止", "开启后停止全部消息处理，数据和管理台不受影响。",
       "bool", "permissions", default=False, editable=False,
       read_only_reason="只能用「紧急停止」按钮开关（需要确认），这里只显示状态。",
       also_in=[_loc("顶栏 › 紧急停止", ios=IOS_EMERGENCY_STOP, ios_label="更多 › 紧急停止")]),
    # ── Web 界面偏好 ──
    _p("ui_preferences.primary_nav", "常用入口", "Web 侧栏第一组和手机网页底栏放哪几个页面（1～4 个）。空 = 默认。",
       "list", "interface", default=[], editable=False, read_only_reason="请在 Web「界面偏好」里拖动调整。",
       also_in=[_loc("服务器设置 › 界面偏好", web=_WEB_INTERFACE)]),
    _p("ui_preferences.sidebar_order", "侧栏组内顺序", "Web 侧栏「管理」「工具与设置」两组里页面的先后顺序。",
       "list", "interface", default=[], editable=False, read_only_reason="请在 Web「界面偏好」里拖动调整。",
       also_in=[_loc("服务器设置 › 界面偏好", web=_WEB_INTERFACE)]),
    _p("ui_preferences.mobile_nav_order", "手机底栏顺序（旧）", "第 20 轮前的手机网页底栏顺序，现在由“常用入口”代替，保留兼容。",
       "list", "interface", default=[], editable=False, advanced=True, read_only_reason="已由“常用入口”代替。",
       also_in=[_loc("服务器设置 › 界面偏好", web=_WEB_INTERFACE)]),
    _p("ui_preferences.provider_order", "AI 供应商排序", "AI 供应商列表里的先后顺序（供应商编号）。",
       "list", "interface", default=[], editable=False, read_only_reason="请在 AI › 供应商里拖动调整。",
       also_in=[_loc("AI › 供应商", web="/ai")]),
]

_BY_KEY: dict[str, dict[str, Any]] = {item["key"]: item for item in SETTINGS_META}


def meta_for(key: str) -> dict[str, Any] | None:
    return _BY_KEY.get(key)


def field_title(key: str) -> str:
    """PATCH schema 字段标题：「中文名 · 英文原名」，例如「运行日志保留天数 · runtime_log_retention_days」。"""

    item = _BY_KEY.get(key)
    leaf = key.rsplit(".", 1)[-1]
    return f"{item['label']} · {leaf}" if item else leaf


def field_description(key: str) -> str | None:
    item = _BY_KEY.get(key)
    if item is None:
        return None
    parts = [item["description"]]
    if item.get("unit"):
        parts.append(f"单位：{item['unit']}。")
    if item.get("min") is not None and item.get("max") is not None:
        parts.append(f"范围 {item['min']}～{item['max']}。")
    if item.get("zero_means"):
        parts.append(f"0 = {item['zero_means']}。")
    return "".join(parts)


def object_title(prefix: str, label: str) -> str:
    return f"{label} · {prefix}"


def payload() -> dict[str, Any]:
    return {"version": META_VERSION, "groups": GROUPS, "params": SETTINGS_META}
