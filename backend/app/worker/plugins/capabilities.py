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
# 这一版（P1 通用交互原语）发布的宿主版本；合并时随版本号顺延（只影响注册表里“本版新增”的条目）。
_THIS_RELEASE = "0.108.0-beta.1"

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
        since=_THIS_RELEASE,
        plugin_api="1.1",
        facades=ALL_MESSAGE_FACADES,
        summary='按钮写 {"text", "callback": {"data"}}，宿主签发 cb1.<token> 并把点击按 token 路由回本插件（订阅 scope "own_buttons"，payload.callback）',
        doc=f"{_DOC}#通用回调路由own_buttons",
    ),
    Capability(
        name="messages.unified_ops",
        since=_THIS_RELEASE,
        plugin_api="1.1",
        facades=ALL_MESSAGE_FACADES,
        summary="三种 ctx.messages 共用一套动作构造与执行器：实时 facade 补齐 send_photo / send_file / edit_rich / edit_caption，单动作方法失败时带回 errors",
        doc=f"{_DOC}#统一-messageops",
    ),
    Capability(
        name="messages.bot_api_extra",
        since=_THIS_RELEASE,
        plugin_api="1.1",
        facades=ALL_MESSAGE_FACADES,
        summary="动作可带 bot_api_extra（按动作类型白名单校验，不得覆盖平台字段，交互 Bot 渠道生效；不合规整条拒绝并返回原因）",
        doc=f"{_DOC}#bot-api-字段透传bot_api_extra",
    ),
    Capability(
        name="ai.extra_body",
        since=_THIS_RELEASE,
        plugin_api="1.1",
        summary="ctx.ai.complete(extra_body={...}) 按上游格式白名单透传请求体字段（如 reasoning_effort），被拒字段明确报错",
        doc=f"{_DOC}#ai-请求体透传extra_body",
    ),
    Capability(
        name="host.read",
        since=_THIS_RELEASE,
        plugin_api="1.1",
        summary='ctx.host.read("资源@版本") 声明式只读宿主查询；plugin.json 用 reads 声明，未声明的资源拒绝',
        doc=f"{_DOC}#宿主只读查询ctxhostread",
    ),
    Capability(
        name="host.read.account.admins@1",
        since=_THIS_RELEASE,
        plugin_api="1.1",
        summary="账号本人与 TelePilot 管理员的 Telegram user id（代替 payload.owner_user_ids / admin_user_ids）",
        doc=f"{_DOC}#宿主只读查询ctxhostread",
    ),
    Capability(
        name="host.read.ai.models@1",
        since=_THIS_RELEASE,
        plugin_api="1.1",
        summary="插件可用模型清单（enabled_only=True 只列已启用），需要 ai_text / ai_agent 权限",
        doc=f"{_DOC}#宿主只读查询ctxhostread",
    ),
    Capability(
        name="host.read.chat.allowed@1",
        since=_THIS_RELEASE,
        plugin_api="1.1",
        summary="账号「允许会话」名单（all=true 表示未限制）",
        doc=f"{_DOC}#宿主只读查询ctxhostread",
    ),
    Capability(
        name="host.read.plugin.stats@1",
        since=_THIS_RELEASE,
        plugin_api="1.1",
        summary="本插件在当前账号的钩子统计",
        doc=f"{_DOC}#宿主只读查询ctxhostread",
    ),
    Capability(
        name="runtime.hook_trace",
        since=_THIS_RELEASE,
        plugin_api="1.1",
        summary="钩子失败 / 超时 / 熔断在 Trace 记 plugin_hook span（reason_code=plugin_timeout / plugin_error / plugin_circuit_open），熔断与统计同口径（连续失败 + 窗口失败率）",
        doc=f"{_DOC}#钩子统计与弃用登记",
    ),
    Capability(
        name="runtime.entry_policy",
        since=_THIS_RELEASE,
        plugin_api="1.1",
        summary="interaction_entries[].policy 声明参与方式 / 是否有奖 / 默认启动词，宿主不再按插件 key 写死",
        doc=f"{_DOC}#入口策略policy",
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
