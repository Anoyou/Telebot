"""插件框架：``PluginContext`` + ``Plugin`` 基类 + 全局注册表。

设计要点：
- ``Plugin`` 是基类，所有内置 / 第三方插件继承它并通过 ``@register`` 注册到全局表。
- 注册表存放的是 **类对象**（不是实例），每账号在 loader 里各自实例化一次，避免共享状态。
- ``PluginContext`` 是给插件运行期使用的"上下文容器"：账号 id、配置、规则、Telethon
  client、风控引擎、redis、日志写入器；插件实现各 hook 时只需读它就够了。
- 严格遵循 ``CONTRACTS.md`` 的"插件 Hook"段；所有 hook 默认实现为 no-op，子类按需重写。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from telethon import TelegramClient, events


# ─────────────────────────────────────────────────────
# 运行时上下文（每个 [账号 × feature] 一份）
# ─────────────────────────────────────────────────────
@dataclass
class PluginContext:
    """插件运行上下文。

    字段：
      - ``account_id``：当前 worker 服务的账号 id
      - ``feature_key``：插件对应的 feature key（与 ``Plugin.key`` 一致）
      - ``config``：``account_feature.config`` 中保存的 dict（可由 reload_config 热更新）
      - ``rules``：该 [账号 × feature] 下所有 ``enabled=True`` 的 ``Rule``，按 priority 倒序
      - ``client``：Telethon 客户端（loader 注入）
      - ``engine``：风控引擎（C Agent 提供，支持 ``acquire`` 与各 ``on_*`` 回调）
      - ``redis``：异步 Redis 客户端
      - ``log``：写运行日志的协程；签名 ``async (level, message, **detail)``

    为避免循环 import，``rules`` / ``engine`` / ``redis`` 都用 ``Any`` 标注。
    """

    account_id: int
    feature_key: str
    config: dict[str, Any] = field(default_factory=dict)
    rules: list[Any] = field(default_factory=list)  # list[Rule] —— 这里用 Any 防循环引用
    client: TelegramClient | None = None
    engine: Any = None  # RateLimitEngine
    redis: Any = None  # redis.asyncio.Redis
    log: Callable[..., Awaitable[None]] | None = None


# ─────────────────────────────────────────────────────
# 插件基类
# ─────────────────────────────────────────────────────
class Plugin:
    """插件基类。

    子类必须设置类属性 ``key`` / ``display_name``；可重写以下 hook：
      - ``on_startup``：[账号 × feature] 被激活时调用一次
      - ``on_shutdown``：禁用 / 卸载 / 热重载前调用一次
      - ``on_message``：消息派发回调，具体接收哪些方向由 ``message_channels`` 声明
      - ``on_command``：插件可声明的"账号内命令"；返回 True 表示已处理

    ``message_channels`` 控制 loader 向该插件派发哪些方向的消息：
      - ``"incoming"``（默认）：群/私聊中别人发的消息
      - ``"outgoing"``：自己发送的消息
      插件可设 ``{"incoming", "outgoing"}`` 同时监听两个方向，
      在 ``on_message`` 内通过 ``event.outgoing`` 判断消息来源。

    插件如要追加 TG 内命令，可在类属性 ``commands`` 里登记
    （key 是命令名，value 是 ``async fn(client, event, args, account_id, ctx)``），
    loader 会在 ``on_startup`` 后通过 ``register_plugin_command`` 暴露给命令分发。
    """

    key: str = ""
    display_name: str = ""
    # 声明插件需要监听的消息方向；loader 据此决定是否向该插件派发对应事件
    message_channels: set[str] = {"incoming"}
    # 插件想暴露的 TG 内命令：cmd_name -> async handler
    # handler 签名: (client, event, args, account_id, ctx) -> None
    commands: dict[str, Callable[..., Awaitable[None]]] = {}

    async def on_startup(self, ctx: PluginContext) -> None:
        """[账号 × feature] 激活时的钩子；默认 no-op。"""
        return None

    async def on_shutdown(self, ctx: PluginContext) -> None:
        """[账号 × feature] 关停时的钩子；默认 no-op。"""
        return None

    async def on_message(self, ctx: PluginContext, event: events.NewMessage.Event) -> None:
        """消息事件回调；默认 no-op。

        接收的方向由 ``message_channels`` 类属性控制，
        可通过 ``event.outgoing`` 区分消息来源。
        """

    async def on_command(
        self,
        ctx: PluginContext,
        cmd: str,
        args: list[str],
        event: events.NewMessage.Event,
    ) -> bool:
        """命令派发回调；返回 True 表示已处理，否则继续向后传。默认 no-op 返回 False。"""
        return False


# ─────────────────────────────────────────────────────
# 全局注册表
# ─────────────────────────────────────────────────────
# feature_key -> Plugin 子类（不是实例！每账号都要新实例）
_REGISTRY: dict[str, type[Plugin]] = {}


def register(plugin_cls: type[Plugin]) -> type[Plugin]:
    """装饰器：把一个 ``Plugin`` 子类注册到全局表。

    用法：
        @register
        class AutoReplyPlugin(Plugin):
            key = "auto_reply"
            ...
    """
    if not getattr(plugin_cls, "key", ""):
        raise ValueError("Plugin.key 必须先设置")
    _REGISTRY[plugin_cls.key] = plugin_cls
    return plugin_cls


def get_plugin(key: str) -> type[Plugin] | None:
    """按 feature key 查找已注册的插件类，不存在返回 None。"""
    return _REGISTRY.get(key)


def all_plugins() -> dict[str, type[Plugin]]:
    """返回当前已注册的全部插件（拷贝）。"""
    return dict(_REGISTRY)


__all__ = [
    "Plugin",
    "PluginContext",
    "all_plugins",
    "get_plugin",
    "register",
]
