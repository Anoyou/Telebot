"""插件能力单一注册表（Plugin API 能力协商的唯一来源）。

历史上能力标记以类属性的形式分别写在 ``BufferedMessageOps`` / ``_LiveMessageOps`` /
``_InteractionEntryMessageOps`` 三个类里，彼此不一致。0.107.0 起：

- 每项能力只在 :data:`CAPABILITIES` 里登记一次（名称、版本、引入的宿主版本、适用的 facade、
  兼容用的旧属性名、说明与文档锚点）；
- 三个 MessageOps 类上的旧属性（``ctx.messages.rich_table_compact`` 等）由
  :func:`apply_legacy_flags` 按注册表生成，旧插件 ``getattr(ctx.messages, "...", False) is True``
  的探测写法继续可用；
- 新插件用 ``ctx.capabilities.has("messages.guest_reply_markup")`` 判断，``GET /api/plugins/capabilities``
  对外列出同一份数据，文档里的版本门槛表也以这里为准。

本模块只依赖标准库与 :mod:`.versioning`，可被主进程、worker 与插件库兼容检查脚本导入。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from .versioning import PLUGIN_API_VERSION

# MessageOps facade 种类（``_tp_facade_kind`` 类属性）：
# - ``buffered``：纯缓冲（UserBot Event Bus 投递、测试）；动作随返回值交给平台执行，没有实时 apply；
# - ``live``：常驻上下文（命令、后台任务）里的实时 facade；
# - ``entry``：交互 Bot 入口内的缓冲 facade，同时可以实时 apply（延迟编辑）。
FACADE_BUFFERED = "buffered"
FACADE_LIVE = "live"
FACADE_ENTRY = "entry"
ALL_MESSAGE_FACADES: frozenset[str] = frozenset({FACADE_BUFFERED, FACADE_LIVE, FACADE_ENTRY})


@dataclass(frozen=True)
class Capability:
    """一项插件可探测的宿主能力。"""

    name: str
    since: str
    summary: str
    version: int = 1
    # 兼容旧探测方式的类属性名（挂在 ctx.messages 上）；None 表示只能用 ctx.capabilities.has()。
    legacy_attr: str | None = None
    # 只对这些 MessageOps facade 成立；空集合表示与 facade 无关的宿主级能力。
    facades: frozenset[str] = field(default_factory=frozenset)
    plugin_api: str = "1.0"
    doc: str = ""
    deprecated_in: str | None = None
    removed_in: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "since": self.since,
            "plugin_api": self.plugin_api,
            "summary": self.summary,
            "legacy_attr": self.legacy_attr,
            "facades": sorted(self.facades),
            "doc": self.doc,
            "deprecated_in": self.deprecated_in,
            "removed_in": self.removed_in,
        }


# P0（插件平台化地基）发布的宿主版本。
_P0_RELEASE = "0.107.0-beta.1"
# P1（通用交互原语）发布的宿主版本。
_P1_RELEASE = "0.108.0-beta.1"
# P2（消息管线中间件）发布的宿主版本。
_P2_RELEASE = "0.109.0-beta.1"
# 这一版（P3 声明式 UI）发布的宿主版本；合并时随版本号顺延（只影响注册表里“本版新增”的条目）。
_THIS_RELEASE = "0.110.0-beta.1"
# P3 第二部分（W1 迁移：通用规则页、试运行协议）发布的宿主版本；合并时随版本号顺延。
_W1_RELEASE = "0.110.0-beta.2"
# P4c（B1 转账通知解析下沉：payment_sources 扩展点）发布的宿主版本；合并时随版本号顺延。
_PAYMENT_RELEASE = "0.111.0-beta.1"
# P4a（插件自带页面与菜单）发布的宿主版本；合并时随版本号顺延。
_P4A_RELEASE = "0.112.0-beta.1"
# P4b（非资金业务下沉：入口策略声明、扩展点 provides、规则编辑器 slot）发布的宿主版本；合并时随版本号顺延。
_P4B_RELEASE = "0.112.0-beta.2"

_DOC = "docs/PLUGIN-API-REFERENCE.md"

CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        name="messages.inline_rich_edit",
        legacy_attr="inline_rich_edit",
        since="0.106.0-beta.2",
        # 0.108.0 统一执行器后，纯缓冲 facade 返回的 inline 编辑动作也由同一执行器经交互 Bot 完成。
        facades=ALL_MESSAGE_FACADES,
        summary="edit_message 同时给 inline_message_id 与 rich_message 时用 editMessageText 原地编辑 inline 消息（访客占位）",
        doc=f"{_DOC}#访客模式guest-mode",
    ),
    Capability(
        name="messages.live_inline_edit",
        legacy_attr="live_inline_edit",
        since="0.106.0-beta.3",
        facades=frozenset({FACADE_LIVE, FACADE_ENTRY}),
        summary="实时 ctx.messages.apply 支持 inline_message_id 编辑（文本与 Rich），结果 errors 带回失败原因",
        doc=f"{_DOC}#访客模式guest-mode",
    ),
    Capability(
        name="messages.rich_table_compact",
        legacy_attr="rich_table_compact",
        since="0.106.0-beta.12",
        facades=ALL_MESSAGE_FACADES,
        summary="send_rich / edit_rich / answer_guest_query 接受 compact_tables=True（Bot API 10.3 紧凑表格）",
        doc=f"{_DOC}#紧凑表格",
    ),
    Capability(
        name="messages.rich_table_default_style",
        legacy_attr="rich_table_default_style",
        since="0.106.0-beta.15",
        facades=ALL_MESSAGE_FACADES,
        summary="发送出口给 rich_message 表格默认补边框 + 隔行着色，显式值优先",
        doc=f"{_DOC}#紧凑表格",
    ),
    Capability(
        name="messages.guest_reply_markup",
        legacy_attr="guest_reply_markup",
        since="0.106.0-beta.17",
        facades=ALL_MESSAGE_FACADES,
        summary="answer_guest_query 保留 callback_data 按钮，访客回复上的点击以 inline callback 投递给 guest_all / inline_all",
        doc=f"{_DOC}#访客回复上的-callback-按钮",
    ),
    Capability(
        name="messages.streaming_edit",
        legacy_attr="streaming_edit",
        since="0.106.0-beta.20",
        # 流式编辑依赖实时 apply / 交互 Bot 直发；纯缓冲 facade（UserBot Event Bus）不提供。
        facades=frozenset({FACADE_LIVE, FACADE_ENTRY}),
        summary="ctx.messages.stream_editor() / stream_edit() 带节流的流式编辑 / 私聊草稿（中间态处理 429，最终一次走标准动作）",
        doc="docs/PLUGIN-STREAMING.md",
    ),
    Capability(
        name="runtime.capabilities",
        since=_P0_RELEASE,
        summary="ctx.capabilities 能力协商（has / version / names）与 GET /api/plugins/capabilities",
        doc=f"{_DOC}#能力协商与-plugin-api-版本",
    ),
    Capability(
        name="manifest.plugin_api",
        since=_P0_RELEASE,
        summary="plugin.json 可声明 plugin_api 兼容范围（如 ^1.0），宿主加载前校验",
        doc=f"{_DOC}#能力协商与-plugin-api-版本",
    ),
    Capability(
        name="manifest.min_version_prerelease",
        since=_P0_RELEASE,
        summary="min_telepilot_version 比较考虑 beta 号（可声明 0.106.0-beta.12）",
        doc=f"{_DOC}#能力协商与-plugin-api-版本",
    ),
    Capability(
        name="runtime.hook_stats",
        since=_P0_RELEASE,
        summary="平台按插件 × 钩子统计调用数、耗时与失败数（GET /api/plugins/{key}/stats，Web 插件详情可见）",
        doc=f"{_DOC}#钩子统计与弃用登记",
    ),
    Capability(
        name="runtime.deprecations",
        since=_P0_RELEASE,
        summary="调用已弃用接口时记录警告与次数，插件详情可见",
        doc=f"{_DOC}#钩子统计与弃用登记",
    ),
    # ── P1：通用交互原语（Plugin API 1.1）──
    Capability(
        name="messages.callback_routing",
        since=_P1_RELEASE,
        plugin_api="1.1",
        facades=ALL_MESSAGE_FACADES,
        summary='按钮写 {"text", "callback": {"data"}}，宿主签发 cb1.<token> 并把点击按 token 路由回本插件（订阅 scope "own_buttons"，payload.callback）',
        doc=f"{_DOC}#通用回调路由own_buttons",
    ),
    Capability(
        name="messages.unified_ops",
        since=_P1_RELEASE,
        plugin_api="1.1",
        facades=ALL_MESSAGE_FACADES,
        summary="三种 ctx.messages 共用一套动作构造与执行器：实时 facade 补齐 send_photo / send_file / edit_rich / edit_caption，单动作方法失败时带回 errors",
        doc=f"{_DOC}#统一-messageops",
    ),
    Capability(
        name="messages.bot_api_extra",
        since=_P1_RELEASE,
        plugin_api="1.1",
        facades=ALL_MESSAGE_FACADES,
        summary="动作可带 bot_api_extra（按动作类型白名单校验，不得覆盖平台字段，交互 Bot 渠道生效；不合规整条拒绝并返回原因）",
        doc=f"{_DOC}#bot-api-字段透传bot_api_extra",
    ),
    Capability(
        name="ai.extra_body",
        since=_P1_RELEASE,
        plugin_api="1.1",
        summary="ctx.ai.complete(extra_body={...}) 按上游格式白名单透传请求体字段（如 reasoning_effort），被拒字段明确报错",
        doc=f"{_DOC}#ai-请求体透传extra_body",
    ),
    Capability(
        name="host.read",
        since=_P1_RELEASE,
        plugin_api="1.1",
        summary='ctx.host.read("资源@版本") 声明式只读宿主查询；plugin.json 用 reads 声明，未声明的资源拒绝',
        doc=f"{_DOC}#宿主只读查询ctxhostread",
    ),
    Capability(
        name="host.read.account.admins@1",
        since=_P1_RELEASE,
        plugin_api="1.1",
        summary="账号本人与 TelePilot 管理员的 Telegram user id（代替 payload.owner_user_ids / admin_user_ids）",
        doc=f"{_DOC}#宿主只读查询ctxhostread",
    ),
    Capability(
        name="host.read.ai.models@1",
        since=_P1_RELEASE,
        plugin_api="1.1",
        summary="插件可用模型清单（enabled_only=True 只列已启用），需要 ai_text / ai_agent 权限",
        doc=f"{_DOC}#宿主只读查询ctxhostread",
    ),
    Capability(
        name="host.read.chat.allowed@1",
        since=_P1_RELEASE,
        plugin_api="1.1",
        summary="账号「允许会话」名单（all=true 表示未限制）",
        doc=f"{_DOC}#宿主只读查询ctxhostread",
    ),
    Capability(
        name="host.read.plugin.stats@1",
        since=_P1_RELEASE,
        plugin_api="1.1",
        summary="本插件在当前账号的钩子统计",
        doc=f"{_DOC}#宿主只读查询ctxhostread",
    ),
    Capability(
        name="runtime.hook_trace",
        since=_P1_RELEASE,
        plugin_api="1.1",
        summary="钩子失败 / 超时 / 熔断在 Trace 记 plugin_hook span（reason_code=plugin_timeout / plugin_error / plugin_circuit_open），熔断与统计同口径（连续失败 + 窗口失败率）",
        doc=f"{_DOC}#钩子统计与弃用登记",
    ),
    Capability(
        name="runtime.entry_policy",
        since=_P1_RELEASE,
        plugin_api="1.1",
        summary="interaction_entries[] 的 session_scope / participant_policy / 入口 schema 的 prize 声明参与方式与是否有奖，宿主不再按插件 key 写死（未声明的旧插件走兜底入口表）",
        doc=f"{_DOC}#入口策略policy",
    ),
    # ── P3：声明式 UI（Plugin API 1.3）──
    Capability(
        name="ui.cards",
        since=_THIS_RELEASE,
        plugin_api="1.3",
        summary="plugin.json ui.cards 声明原生卡片（stats / table / kv / list / buttons），slot：plugin.overview / account.detail / dashboard；数据来自 reads 资源或 on_ui_card（3 秒），按钮调 on_ui_action",
        doc=f"{_DOC}#声明式卡片uicards",
    ),
    Capability(
        name="config.validate",
        since=_THIS_RELEASE,
        plugin_api="1.3",
        summary="on_config_validate(ctx, config, scope) 保存前同步校验 / 规范化（2 秒，超时放行），返回 {config} 或 {errors: {字段: 原因}}",
        doc=f"{_DOC}#配置钩子on_config_validate--on_config_saved",
    ),
    Capability(
        name="config.saved",
        since=_THIS_RELEASE,
        plugin_api="1.3",
        summary="on_config_saved(ctx, config, scope) 保存成功后后台调用（10 秒），不影响保存结果",
        doc=f"{_DOC}#配置钩子on_config_validate--on_config_saved",
    ),
    Capability(
        name="config.secret_list",
        since=_THIS_RELEASE,
        plugin_api="1.3",
        summary="config_schema 数组字段声明 x-secret-list：宿主遮罩显示、按 id 保留原密钥并加密保存，Web / iOS 原生密钥列表控件",
        doc=f"{_DOC}#密钥列表x-secret-list",
    ),
    Capability(
        name="ui.widgets",
        version=1,
        since=_THIS_RELEASE,
        plugin_api="1.3",
        summary="配置控件契约 schemas/plugin-ui-widgets.json（contract_version 1）：Web 与 iOS 都实现全部控件，未知控件只读并提示更新 App",
        doc=f"{_DOC}#配置控件契约",
    ),
    # ── P3 第二部分：W1 迁移（专属配置页改为通用声明式）──
    Capability(
        name="ui.rules_page",
        since=_W1_RELEASE,
        plugin_api="1.3",
        summary="x-ui-mode=rules 通用规则页：x-rule-schema（每条规则的表单）、x-rule-summary（列表摘要列）、x-dry-run（试运行卡片）、字段 x-ui-visible-if（按其它字段显示），契约 contract_version 2",
        doc=f"{_DOC}#通用规则页x-ui-moderules",
    ),
    Capability(
        name="rules.dry_run",
        since=_W1_RELEASE,
        plugin_api="1.3",
        summary="插件模块导出 dry_run_rule(config, sample)（可 async，3 秒）返回 {matched, output, logs}；宿主不再按插件 key 解释试运行，旧版 _dry_run_match 仍兼容",
        doc=f"{_DOC}#规则试运行dry_run_rule",
    ),
    # ── P4a：插件自带页面与菜单（Plugin API 1.4）──
    Capability(
        name="ui.pages",
        since=_P4A_RELEASE,
        plugin_api="1.4",
        summary="plugin.json ui.pages 声明插件自带 HTML 页面（插件目录内文件，严格 CSP 禁止联网；Web iframe sandbox / iOS WKWebView），Bridge 协议 1：ready / theme.get 总可用，config.read / cards.read / actions.invoke 需在页面 bridge 里声明",
        doc=f"{_DOC}#插件页面uipages",
    ),
    Capability(
        name="ui.actions",
        since=_P4A_RELEASE,
        plugin_api="1.4",
        summary="plugin.json ui.actions 声明菜单项（menu：plugin.detail / account.detail），点击调 on_ui_action(ctx, id, {menu, account_id, page_id: None, payload: {}}) 或打开 ui.pages 页面",
        doc=f"{_DOC}#菜单项uiactions",
    ),
    Capability(
        name="hooks.storage_read",
        since=_P4A_RELEASE,
        plugin_api="1.4",
        summary="卡片（on_ui_card / on_ui_action）、配置钩子（on_config_validate / on_config_saved）与页面动作里 ctx.storage 可只读访问（get / get_all / items），写入抛 PermissionError；之前这些钩子里 ctx.storage 为 None",
        doc=f"{_DOC}#钩子里的只读-ctxstorage",
    ),
    Capability(
        name="ai.models.metadata",
        since=_P4A_RELEASE,
        plugin_api="1.4",
        summary="ctx.host.read(\"ai.models@1\") 每项只增 display_name / label / available（有可用 Key 且模型启用）/ has_api_key / cost_tier（1 便宜 · 2 标准 · 3 旗舰）/ cost_tier_label，旧字段不变",
        doc=f"{_DOC}#宿主只读查询ctxhostread",
    ),
)

# ── P4b：非资金业务下沉（Plugin API 1.5）──
CAPABILITIES += (
    Capability(
        name="interaction.entry_policy",
        since=_P4B_RELEASE,
        plugin_api="1.5",
        summary="interaction_entries[].entry_policy 显式声明 session_scope / participant_policy / prize / default_start_keywords；任一入口声明后宿主不再查兜底入口表（旧插件不声明则行为不变）",
        doc=f"{_DOC}#入口策略声明entry_policy",
    ),
    Capability(
        name="provides.ai.image_backend",
        since=_P4B_RELEASE,
        plugin_api="1.5",
        summary='plugin.json provides: ["ai.image_backend"] 声明自己是 ,ai 图片后端；模板 image_backend="plugin"（新默认）按能力选择，没人声明时回退 plugin_compat 登记的旧默认图片插件',
        doc=f"{_DOC}#扩展点provides",
    ),
    Capability(
        name="ui.rule_editor",
        since=_P4B_RELEASE,
        plugin_api="1.5",
        summary="plugin.json ui.rule_editor 按入口声明交互规则编辑器参数区（schema / defaults / 启动词 / 提示），ui.cards 可用 slot interaction.rule_editor",
        doc=f"{_DOC}#规则编辑器参数区uirule_editor",
    ),
)

# ── P2：消息管线中间件（Plugin API 1.2）──
CAPABILITIES += (
    Capability(
        name="middleware.outbound",
        since=_P2_RELEASE,
        plugin_api="1.2",
        summary="plugin.json middleware 声明 outbound 中间件：投递前改正文 / Rich / 按钮 / render_profile 或 mw.reject；只读视图 + 字段白名单回滚 + fail-open，单钩子 250ms、每账号最多 8 个",
        doc=f"{_DOC}#消息管线中间件middleware",
    ),
    Capability(
        name="middleware.inbound",
        since=_P2_RELEASE,
        plugin_api="1.2",
        summary="inbound 中间件：事件路由前打标签（annotations）、规整 text 或 mw.drop 丢弃；需要 message_middleware 权限",
        doc=f"{_DOC}#消息管线中间件middleware",
    ),
    Capability(
        name="middleware.delivered",
        since=_P2_RELEASE,
        plugin_api="1.2",
        summary="delivered 中间件：投递结果确定后异步收到摘要（类型、成功与否、错误码、会话，不含正文），用于统计与审计",
        doc=f"{_DOC}#消息管线中间件middleware",
    ),
    Capability(
        name="middleware.stream_preview",
        since=_P2_RELEASE,
        plugin_api="1.2",
        summary='outbound 中间件在 match.action_types 声明 "stream_preview" 后也处理流式中间态文本（每个 50ms）',
        doc="docs/PLUGIN-STREAMING.md",
    ),
    Capability(
        name="messages.render_profile",
        since=_P2_RELEASE,
        plugin_api="1.2",
        facades=ALL_MESSAGE_FACADES,
        summary='动作或 plugin.json 声明 render_profile: {"table": {"compact", "bordered", "striped"}}，由平台 core.* 中间件统一渲染（compact_tables 是它的旧写法）',
        doc=f"{_DOC}#紧凑表格",
    ),
    Capability(
        name="ai.stream_extra_body",
        since=_P2_RELEASE,
        plugin_api="1.2",
        summary="ctx.ai.stream_complete(extra_body={...}) 与 complete 同一套白名单透传；选中 Provider 的格式不支持时明确报错（流式不中途换 Provider）",
        doc=f"{_DOC}#ai-请求体透传extra_body",
    ),
    Capability(
        name="runtime.blocking_monitor",
        since=_P2_RELEASE,
        plugin_api="1.2",
        summary="插件代码同步阻塞事件循环 ≥200ms 记入该插件钩子统计的 blocked / blocking_ms（与 loop.slow_callback_duration 同口径）",
        doc=f"{_DOC}#钩子统计与弃用登记",
    ),
)

# ── P4c：转账通知解析扩展点（B1 下沉）──
CAPABILITIES += (
    Capability(
        name="payment.sources",
        since=_PAYMENT_RELEASE,
        plugin_api="1.3",
        summary="plugin.json 声明 payment_sources，模块导出 parse_payment_notice(text, meta)（可 async，1 秒）返回 {payer_name, receiver_name, amount, payer_user_id?, receiver_user_id?, unit?} 或 None；只在可信 Bot ID 白名单通过后调用，只返回解析结果，结算仍由宿主执行；双跑对照期默认以宿主旧解析为准",
        doc=f"{_DOC}#转账通知解析payment_sources",
    ),
)

CAPABILITY_INDEX: dict[str, Capability] = {item.name: item for item in CAPABILITIES}
if len(CAPABILITY_INDEX) != len(CAPABILITIES):  # pragma: no cover - 注册表自检
    raise RuntimeError("插件能力注册表存在重名条目")


def legacy_flags_for(facade: str) -> dict[str, bool]:
    """某种 MessageOps facade 应当暴露的旧能力属性。"""

    return {
        item.legacy_attr: True
        for item in CAPABILITIES
        if item.legacy_attr and facade in item.facades and item.removed_in is None
    }


def apply_legacy_flags(cls: type, facade: str) -> type:
    """按注册表给 MessageOps 类写入 ``_tp_facade_kind`` 与旧能力属性（值恒为 ``True``）。

    只增不删：注册表里不适用于该 facade 的属性不会写入，子类继承到的父类属性由
    :func:`legacy_flags_for` 的结果覆盖成一致状态（不适用的写成 ``False``）。
    """

    if facade not in ALL_MESSAGE_FACADES:
        raise ValueError(f"未知 MessageOps facade: {facade}")
    cls._tp_facade_kind = facade
    flags = legacy_flags_for(facade)
    for item in CAPABILITIES:
        if not item.legacy_attr:
            continue
        if item.legacy_attr in flags:
            setattr(cls, item.legacy_attr, True)
        elif any(item.legacy_attr in vars(base) for base in cls.__mro__[1:] if base is not object):
            # 父类（例如 BufferedMessageOps）没有该能力时子类也不应“继承”出不一致的值。
            setattr(cls, item.legacy_attr, False)
    return cls


class PluginCapabilities:
    """``ctx.capabilities``：插件运行时的能力协商入口。

    ``messages.*`` 能力与当前 ``ctx.messages`` facade 有关（同一个插件在交互入口、后台任务、
    UserBot Event Bus 里拿到的 facade 不同），所以按调用时的 facade 判断。
    """

    __slots__ = ("_messages",)

    def __init__(self, messages: Any = None) -> None:
        self._messages = messages

    @property
    def plugin_api(self) -> str:
        return PLUGIN_API_VERSION

    @property
    def host_version(self) -> str:
        from ... import __version__

        return str(__version__)

    def _facade_kind(self) -> str | None:
        if self._messages is None:
            return None
        return getattr(type(self._messages), "_tp_facade_kind", None)

    def version(self, name: str) -> int:
        """能力版本；不支持（含当前 facade 不适用）时返回 0。"""

        item = CAPABILITY_INDEX.get(str(name or "").strip())
        if item is None or item.removed_in is not None:
            return 0
        if item.facades:
            kind = self._facade_kind()
            if kind is None:
                # 不是平台 facade（测试替身等）：退回旧属性探测
                if item.legacy_attr and getattr(self._messages, item.legacy_attr, False) is True:
                    return item.version
                return 0
            if kind not in item.facades:
                return 0
        return item.version

    def has(self, name: str, min_version: int = 1) -> bool:
        """当前上下文是否支持能力 ``name``（且版本不低于 ``min_version``）。"""

        return self.version(name) >= max(1, int(min_version or 1))

    def names(self) -> list[str]:
        """当前上下文支持的全部能力名。"""

        return [item.name for item in CAPABILITIES if self.version(item.name) > 0]

    def as_dict(self) -> dict[str, int]:
        return {name: self.version(name) for name in self.names()}

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and self.has(name)

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return f"PluginCapabilities(plugin_api={self.plugin_api!r}, facade={self._facade_kind()!r})"


def capability_catalog(names: Iterable[str] | None = None) -> list[dict[str, Any]]:
    wanted = set(names) if names is not None else None
    return [item.to_dict() for item in CAPABILITIES if wanted is None or item.name in wanted]


__all__ = [
    "ALL_MESSAGE_FACADES",
    "CAPABILITIES",
    "CAPABILITY_INDEX",
    "FACADE_BUFFERED",
    "FACADE_ENTRY",
    "FACADE_LIVE",
    "Capability",
    "PluginCapabilities",
    "apply_legacy_flags",
    "capability_catalog",
    "legacy_flags_for",
]
