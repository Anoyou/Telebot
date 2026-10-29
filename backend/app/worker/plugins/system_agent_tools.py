"""Worker 侧：插件暴露给 System Agent 的只读工具 handler 注册表。"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

log = logging.getLogger(__name__)

# (plugin_key, tool_name) -> async handler(ctx, arguments) -> Any
SystemAgentToolHandler = Callable[[Any, dict[str, Any]], Awaitable[Any]]

_HANDLERS: dict[tuple[str, str], SystemAgentToolHandler] = {}
# plugin_key -> live Plugin instance（on_startup 时登记，供无独立 handler 时委托）
_PLUGIN_INSTANCES: dict[str, Any] = {}


def register_system_agent_tool_handler(
    plugin_key: str,
    tool_name: str,
    handler: SystemAgentToolHandler,
) -> None:
    key = (str(plugin_key), str(tool_name))
    _HANDLERS[key] = handler
    log.debug("registered system_agent tool handler %s.%s", plugin_key, tool_name)


def unregister_plugin_system_agent_tools(plugin_key: str) -> None:
    prefix = str(plugin_key)
    for key in list(_HANDLERS):
        if key[0] == prefix:
            _HANDLERS.pop(key, None)
    _PLUGIN_INSTANCES.pop(prefix, None)


def bind_plugin_instance(plugin_key: str, instance: Any) -> None:
    _PLUGIN_INSTANCES[str(plugin_key)] = instance


def unbind_plugin_instance(plugin_key: str) -> None:
    _PLUGIN_INSTANCES.pop(str(plugin_key), None)


async def invoke_system_agent_tool(
    *,
    plugin_key: str,
    tool_name: str,
    arguments: dict[str, Any],
    plugin_context: Any | None = None,
) -> Any:
    """执行已注册 handler；若插件实例实现 system_agent_tool(name, args) 也可委托。

    每次调用按 ``system_agent_tool`` 钩子记入插件统计（事件类型 = 工具名）。
    """

    import time

    from .plugin_stats import OUTCOME_FAIL, OUTCOME_OK, classify_exception, get_recorder, record_hook

    started = time.monotonic()
    account_id = getattr(plugin_context, "account_id", None)
    redis = getattr(plugin_context, "redis", None)
    if redis is not None:
        get_recorder().set_redis_factory(lambda _redis=redis: _redis)
    try:
        result = await _invoke_system_agent_tool(
            plugin_key=plugin_key,
            tool_name=tool_name,
            arguments=arguments,
            plugin_context=plugin_context,
        )
    except LookupError:
        raise
    except Exception as exc:
        outcome = classify_exception(exc)
        record_hook(
            account_id=account_id,
            plugin_key=str(plugin_key),
            hook="system_agent_tool",
            event_type=str(tool_name),
            outcome=outcome if outcome != OUTCOME_OK else OUTCOME_FAIL,
            duration_ms=(time.monotonic() - started) * 1000,
            error=exc,
        )
        raise
    record_hook(
        account_id=account_id,
        plugin_key=str(plugin_key),
        hook="system_agent_tool",
        event_type=str(tool_name),
        outcome=OUTCOME_OK,
        duration_ms=(time.monotonic() - started) * 1000,
    )
    return result


async def _invoke_system_agent_tool(
    *,
    plugin_key: str,
    tool_name: str,
    arguments: dict[str, Any],
    plugin_context: Any | None = None,
) -> Any:
    key = (str(plugin_key), str(tool_name))
    handler = _HANDLERS.get(key)
    if handler is not None:
        return await handler(plugin_context, dict(arguments or {}))

    inst = _PLUGIN_INSTANCES.get(str(plugin_key))
    if inst is not None:
        method = getattr(inst, "system_agent_tool", None)
        if callable(method):
            return await method(tool_name, dict(arguments or {}), plugin_context)
        named = getattr(inst, f"system_agent_{tool_name}", None)
        if callable(named):
            return await named(dict(arguments or {}), plugin_context)

    raise LookupError(f"未注册 system_agent 工具 {plugin_key}.{tool_name}")


__all__ = [
    "bind_plugin_instance",
    "invoke_system_agent_tool",
    "register_system_agent_tool_handler",
    "unbind_plugin_instance",
    "unregister_plugin_system_agent_tools",
]
