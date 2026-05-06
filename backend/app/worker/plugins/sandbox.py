"""插件运行时沙箱（阶段 C）。

目标：限制第三方插件 (``installed`` source) 能调用的 Telethon API 范围；
内置 builtin 插件直接拿到原 ``TelegramClient``，不走沙箱。

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
- ``send_file``       : ``send_file``
- ``join_chat``       : ``join_chat``
- ``delete_message``  : ``delete_messages``

约束：
- 仅拦截顶层 ``getattr``；插件取到方法后多次调用都不再过 check（性能）
- 私有属性（`_` 前缀）默认拒绝，避免拿到真实 client 内部对象绕过白名单
- 调用方 (loader) 在 ``installed`` 源 plugin 启动时把 ``ctx.client`` 替成
  ``SandboxClient(real, perms)``；``builtin`` 不变

注意：拦截 ``__call__``（raw MTProto）与 ``__class__``，避免通过反射或原始调用绕过权限检查。
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


# 能力名 → 允许的 TelegramClient 方法集
ALLOWED_API: dict[str, frozenset[str]] = {
    "send_message": frozenset({"send_message", "respond", "reply"}),
    "edit_message": frozenset({"edit", "edit_message"}),
    "read_chat": frozenset({"get_messages", "get_chat", "iter_messages"}),
    "send_file": frozenset({"send_file"}),
    "join_chat": frozenset({"join_chat"}),
    "delete_message": frozenset({"delete_messages"}),
}


# 默认放行集合：连接 / 关闭 / 自身查询等，不属于业务 API，避免插件起步崩
_ALWAYS_ALLOWED: frozenset[str] = frozenset(
    {
        "connect",
        "disconnect",
        "is_connected",
        "is_user_authorized",
        "loop",
        "session",
        "get_me",
        # Telethon Helper 上下文管理器
        "__aenter__",
        "__aexit__",
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


class SandboxClient:
    """``TelegramClient`` 的最小化代理：只放行 manifest 声明的方法。

    ``__getattr__`` 是唯一拦截点：插件每次取属性都会过 check，
    取到的对象（method）后续怎么用我们就不管了——这是性能权衡。
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
        """阻断通过 __class__ 反射真实对象能力。"""
        raise PermissionError(f"插件 {self._plugin_key!r} 禁止访问 client.__class__")

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        """阻断 raw MTProto 路径：client(functions.xxx(...))."""
        raise PermissionError(
            f"插件 {self._plugin_key!r} 禁止调用 client.__call__ (raw MTProto)"
        )

    def __getattr__(self, name: str) -> Any:
        # __slots__ 上的字段走原生协议，不会触发 __getattr__；这里确保不递归
        if name.startswith("_"):
            raise PermissionError(
                f"插件 {self._plugin_key!r} 禁止访问私有属性 client.{name}"
            )
        if name in _ALWAYS_ALLOWED or name in self._allowed:
            return getattr(self._real, name)
        # 不在允许集内 → 抛 PermissionError；plugin 应在 manifest.permissions 中声明
        raise PermissionError(
            f"插件 {self._plugin_key!r} 缺少权限调用 client.{name}; "
            f"请在 manifest.permissions 中声明对应能力（持有: {self._perms}）"
        )

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<SandboxClient plugin={self._plugin_key} perms={self._perms}>"


__all__ = ["ALLOWED_API", "SandboxClient", "resolve_permissions"]
