"""插件运行时沙箱（阶段 C，阶段 E 安全加固）。

目标：限制第三方插件 (``installed`` source) 能调用的 Telethon API 范围；
内置 builtin 插件直接拿到原 ``TelegramClient``，不走沙箱。

安全设计（阶段 E 修复）：
- 移除 ``_ALWAYS_ALLOWED`` 中的 ``session``，防止第三方插件访问真实 session
- 禁止通过 ``__class__`` 反射获取真实对象
- 禁止通过 ``__getattr__`` 访问私有属性（以 _ 开头）
- 禁止通过 ``__dict__`` 绕过权限检查
- 禁止通过 ``__globals__`` / ``__code__`` 等获取运行时信息

设计：
- ``ALLOWED_API`` 把 manifest 中声明的"能力名" (e.g. ``send_message``) 映射到一组
  允许调用的 ``TelegramClient`` 方法名。
- ``SandboxClient`` 是一个动态代理：``__getattr__`` 时检查目标属性是否在允许集中，
  否则抛 ``PermissionError``。
- ``_log_call``：每次调用都会写一条 debug 日志（非 await，避免污染主流程）。

权限名清单（一期）：
- ``send_message``    : ``send_message`` / ``respond`` / ``reply``
- ``edit_message``    : ``edit`` / ``edit_message``
- ``read_chat``       : ``get_messages`` / ``get_chat`` / ``iter_messages``
- ``resolve_entity``  : ``get_entity``
- ``send_file``       : ``send_file``
- ``join_chat``       : ``join_chat``
- ``delete_message``  : ``delete_messages``
- ``moderate_chat``   : ``ban_user`` / ``kick_user`` / ``mute_user`` / ``unban_user``

约束：
- 仅拦截顶层 ``getattr``；插件取到方法后多次调用都不再过 check（性能权衡）
- 私有属性（`_` 前缀）默认拒绝，避免拿到真实 client 内部对象绕过白名单
- 调用方 (loader) 在 ``installed`` 源 plugin 启动时把 ``ctx.client`` 替成
  ``SandboxClient(real, perms)``；``builtin`` 不变
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

log = logging.getLogger(__name__)


# 能力名 → 允许的 TelegramClient 方法集
ALLOWED_API: dict[str, frozenset[str]] = {
    "send_message": frozenset({"send_message", "respond", "reply"}),
    "edit_message": frozenset({"edit", "edit_message"}),
    "read_chat": frozenset({"get_messages", "get_chat", "iter_messages"}),
    "resolve_entity": frozenset({"get_entity"}),
    "send_file": frozenset({"send_file"}),
    "join_chat": frozenset({"join_chat"}),
    "delete_message": frozenset({"delete_messages"}),
    "moderate_chat": frozenset({"ban_user", "kick_user", "mute_user", "unban_user"}),
}


# 默认放行集合：连接 / 关闭 / 自身查询等，不属于业务 API，避免插件起步崩
# 注意：阶段 E 移除了 "session"，防止第三方插件访问真实 session 对象
_ALWAYS_ALLOWED: frozenset[str] = frozenset(
    {
        "connect",
        "disconnect",
        "is_connected",
        "is_user_authorized",
        "loop",
        "get_me",
        # Telethon Helper 上下文管理器
        "__aenter__",
        "__aexit__",
    }
)

# 危险属性黑名单：这些属性绝对禁止第三方插件访问
_BLOCKED_ATTRS: frozenset[str] = frozenset(
    {
        # 敏感对象
        "session",
        "session_name",
        # 反射相关
        "__class__",
        "__dict__",
        "__getattribute__",
        "__getattr__",  # 已覆盖但显式列禁止
        "__setattr__",
        "__globals__",
        "__code__",
        "__closure__",
        "__func__",
        "__module__",
        "__builtins__",
        "__subclasshook__",
        "__mro__",
        # 私有属性变体（插件可能尝试 _xxx 或 __xxx）
        "_client",
        "_api",
        "_sender",
        "_state",
        "_connection",
        "_dcs",
        "api",
        "sender",
        "state",
    }
)


def resolve_permissions(perms: list[str] | None) -> frozenset[str]:
    """把权限名列表展开成允许的方法名集合（去重）。

    未识别的权限名只写 warn 日志，不抛异常——插件 manifest 写错时业务可降级。
    """
    out: set[str] = set()
    for p in perms or []:
        methods = ALLOWED_API.get(p)
        if methods is None:
            log.warning("manifest 引用未知权限名 %r", p)
            continue
        out |= methods
    return frozenset(out)


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value


def _duration_to_until(duration_seconds: int | float | None) -> timedelta | None:
    if duration_seconds is None:
        return None
    try:
        seconds = int(duration_seconds)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    return timedelta(seconds=seconds)


def _require_allowed_method(client: SandboxClient, method_name: str) -> Any:
    allowed = object.__getattribute__(client, "_allowed")
    if method_name not in allowed:
        plugin_key = object.__getattribute__(client, "_plugin_key")
        perms = object.__getattribute__(client, "_perms")
        raise PermissionError(
            f"插件 {plugin_key!r} 缺少权限调用 client.{method_name}; "
            f"请在 manifest.permissions 中声明对应能力（持有: {perms}）"
        )
    return object.__getattribute__(client, "_real")


class SandboxClient:
    """``TelegramClient`` 的最小化代理：只放行 manifest 声明的方法。

    **安全设计**：
    - 禁止访问 ``session`` 等敏感属性
    - 禁止通过 ``__class__`` / ``__dict__`` 等反射获取真实对象
    - 禁止访问私有属性（以 _ 开头）
    - 每次 ``__getattr__`` 调用都会经过权限检查
    """

    __slots__ = ("_real", "_allowed", "_plugin_key", "_perms")

    def __init__(
        self,
        real: Any,
        perms: list[str] | None,
        *,
        plugin_key: str = "?",
    ) -> None:
        self._real = real
        # frozenset 避免被插件 mutate
        self._allowed = resolve_permissions(perms)
        self._plugin_key = plugin_key
        self._perms = list(perms or [])

    @property
    def __class__(self):  # type: ignore[override]
        """阻断通过 __class__ 反射获取真实对象类型。"""
        raise PermissionError(
            f"插件 {self._plugin_key!r} 禁止访问 client.__class__"
        )

    @property
    def __dict__(self) -> dict:  # type: ignore[override]
        """阻断通过 __dict__ 反射获取真实对象属性。"""
        raise PermissionError(
            f"插件 {self._plugin_key!r} 禁止访问 client.__dict__"
        )

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        """阻断 raw MTProto 路径：client(functions.xxx(...))."""
        plugin_key = object.__getattribute__(self, "_plugin_key")
        raise PermissionError(
            f"插件 {plugin_key!r} 禁止调用 client.__call__ (raw MTProto)"
        )

    def __getattribute__(self, name: str) -> Any:
        """元属性访问（__slots__ 字段走此路径）。"""
        # 危险属性直接拒绝
        if name in _BLOCKED_ATTRS or name.startswith("_"):
            plugin_key = object.__getattribute__(self, "_plugin_key")
            raise PermissionError(
                f"插件 {plugin_key!r} 禁止访问 client.{name}"
            )
        return super().__getattribute__(name)

    def __getattr__(self, name: str) -> Any:
        """主拦截点：每次插件取属性都会过检查。

        允许逻辑：
        1. 黑名单属性 → 拒绝
        2. _ 开头私有属性 → 拒绝
        3. _ALWAYS_ALLOWED 基础方法 → 放行
        4. manifest 声明的权限方法 → 放行
        5. 其它 → 拒绝
        """
        # 黑名单二次检查（即使上面 __getattribute__ 已经处理，这里作为纵深防御）
        plugin_key = object.__getattribute__(self, "_plugin_key")
        if name in _BLOCKED_ATTRS:
            raise PermissionError(
                f"插件 {plugin_key!r} 禁止访问 client.{name}"
            )
        # 私有属性
        if name.startswith("_"):
            raise PermissionError(
                f"插件 {plugin_key!r} 禁止访问私有属性 client.{name}"
            )
        allowed = object.__getattribute__(self, "_allowed")
        if name in _ALWAYS_ALLOWED or name in allowed:
            real = object.__getattribute__(self, "_real")
            return getattr(real, name)
        # 不在允许集内 → 抛 PermissionError
        perms = object.__getattribute__(self, "_perms")
        raise PermissionError(
            f"插件 {plugin_key!r} 缺少权限调用 client.{name}; "
            f"请在 manifest.permissions 中声明对应能力（持有: {perms}）"
        )

    async def ban_user(
        self,
        entity: Any,
        user: Any,
        *,
        duration_seconds: int | float | None = None,
    ) -> Any:
        """封禁指定成员，仅在 manifest 声明 ``moderate_chat`` 后可用。"""
        real = _require_allowed_method(self, "ban_user")
        return await _maybe_await(
            real.edit_permissions(
                entity,
                user,
                until_date=_duration_to_until(duration_seconds),
                view_messages=False,
            )
        )

    async def kick_user(self, entity: Any, user: Any) -> Any:
        """踢出指定成员，仅在 manifest 声明 ``moderate_chat`` 后可用。"""
        real = _require_allowed_method(self, "kick_user")
        return await _maybe_await(real.kick_participant(entity, user))

    async def mute_user(
        self,
        entity: Any,
        user: Any,
        *,
        duration_seconds: int | float | None = None,
    ) -> Any:
        """禁言指定成员，仅在 manifest 声明 ``moderate_chat`` 后可用。"""
        real = _require_allowed_method(self, "mute_user")
        return await _maybe_await(
            real.edit_permissions(
                entity,
                user,
                until_date=_duration_to_until(duration_seconds),
                send_messages=False,
                send_media=False,
                send_stickers=False,
                send_gifs=False,
                send_games=False,
                send_inline=False,
                embed_link_previews=False,
                send_polls=False,
            )
        )

    async def unban_user(self, entity: Any, user: Any) -> Any:
        """解除指定成员限制，仅在 manifest 声明 ``moderate_chat`` 后可用。"""
        real = _require_allowed_method(self, "unban_user")
        return await _maybe_await(real.edit_permissions(entity, user))

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        plugin_key = object.__getattribute__(self, "_plugin_key")
        perms = object.__getattribute__(self, "_perms")
        return f"<SandboxClient plugin={plugin_key} perms={perms}>"


__all__ = ["ALLOWED_API", "SandboxClient", "resolve_permissions"]
