"""插件配置钩子：``on_config_validate``（同步 2 秒）与 ``on_config_saved``（异步 10 秒）。

两个钩子都在主进程里调用（与配置动作同一套受控上下文，没有 Telegram client），结果记入插件
运行统计（``GET /api/plugins/{key}/stats``）：

- ``on_config_validate``：保存前调用；返回 ``{"config": ...}`` 替换待保存配置、``{"errors": {...}}``
  拒绝保存；超时、抛异常或插件未加载时放行（fail-open），配置照常保存；
- ``on_config_saved``：保存提交后在后台调用，不影响保存结果。

插件没有实现 ``on_config_validate`` 时，宿主继续执行旧版本插件的历史配置改写
（``plugin_compat.normalize_legacy_feature_config``，例如 codex_image 下线模型改名），
新版本插件在自己的 ``on_config_validate`` 里完成同样的迁移。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .. import plugin_compat
from ..db.models.account import Account
from ..db.models.feature import Feature
from ..db.models.plugin import InstalledPlugin
from ..worker.plugins.base import Plugin
from ..worker.plugins.plugin_ui import hook_timeout_seconds

log = logging.getLogger(__name__)

SCOPE_ACCOUNT = "account"
SCOPE_GLOBAL = "global"
_BACKGROUND: set[asyncio.Task[Any]] = set()


@dataclass
class ConfigValidateOutcome:
    config: dict[str, Any]
    errors: dict[str, str] = field(default_factory=dict)
    # 插件实现了 on_config_validate 并在时限内正常返回
    handled: bool = False
    # 插件实现了钩子，但超时 / 抛异常（已放行）
    failed: str | None = None


def plugin_overrides(plugin_cls: type[Any] | None, hook: str) -> bool:
    if plugin_cls is None:
        return False
    return getattr(plugin_cls, hook, None) is not getattr(Plugin, hook, None)


async def call_plugin_hook(
    plugin_cls: type[Any],
    ctx: Any,
    hook: str,
    *args: Any,
    event_type: str = "-",
    timeout: float | None = None,
) -> Any:
    """以时限调用插件钩子并记入运行统计；超时抛 ``TimeoutError``，异常原样抛出。"""

    from ..redis_client import get_redis
    from ..worker.plugins import plugin_stats

    started = time.monotonic()
    outcome = plugin_stats.OUTCOME_OK
    error: BaseException | None = None
    plugin = plugin_cls()
    try:
        return await asyncio.wait_for(
            getattr(plugin, hook)(ctx, *args),
            timeout=timeout if timeout is not None else hook_timeout_seconds(hook),
        )
    except Exception as exc:
        outcome = plugin_stats.classify_exception(exc)
        error = exc
        raise
    finally:
        plugin_stats.record_hook(
            account_id=getattr(ctx, "account_id", None) or None,
            plugin_key=str(getattr(ctx, "feature_key", "") or ""),
            hook=hook,
            event_type=event_type,
            outcome=outcome,
            duration_ms=(time.monotonic() - started) * 1000,
            error=error,
        )
        try:
            await plugin_stats.get_recorder().flush(get_redis())
        except Exception:  # noqa: BLE001 - 统计写入失败不影响钩子
            log.debug("写入插件钩子统计失败", exc_info=True)


async def _plugin_class_or_none(
    db: AsyncSession,
    feature: Feature,
    account_id: int,
    hook: str,
) -> type[Any] | None:
    from .plugin_config_actions import load_plugin_class

    try:
        installed = await db.get(InstalledPlugin, feature.key)
        cls = await load_plugin_class(db, feature.key, account_id, installed_plugin=installed)
    except Exception as exc:  # noqa: BLE001 - 插件不可加载时放行
        log.debug("配置钩子跳过：插件 %s 未加载（%s）", feature.key, type(exc).__name__)
        return None
    return cls if plugin_overrides(cls, hook) else None


def _normalize_errors(raw: Any) -> dict[str, str]:
    if isinstance(raw, Mapping):
        return {str(k): str(v) for k, v in raw.items() if str(v or "").strip()}
    if isinstance(raw, list):
        return {"_": "；".join(str(item) for item in raw if str(item or "").strip())}
    if isinstance(raw, str) and raw.strip():
        return {"_": raw.strip()}
    return {}


async def run_config_validate(
    db: AsyncSession,
    *,
    feature: Feature,
    account: Account | None,
    config: Mapping[str, Any],
    scope: str,
) -> ConfigValidateOutcome:
    """保存前调用插件 ``on_config_validate``；未实现时套用旧插件的历史配置改写。"""

    current = dict(config or {})
    account_id = int(account.id) if account is not None else 0
    plugin_cls = await _plugin_class_or_none(db, feature, account_id, "on_config_validate")
    if plugin_cls is None:
        return ConfigValidateOutcome(config=plugin_compat.normalize_legacy_feature_config(feature.key, current))
    from .plugin_config_actions import build_main_process_context

    try:
        ctx = await build_main_process_context(
            db, account=account, feature=feature, plugin_cls=plugin_cls, config=current
        )
        result = await call_plugin_hook(plugin_cls, ctx, "on_config_validate", dict(current), scope, event_type=scope)
    except Exception as exc:  # noqa: BLE001 - 校验钩子失败放行
        reason = "timeout" if isinstance(exc, TimeoutError) else type(exc).__name__
        log.warning("插件 %s on_config_validate 失败（%s），已放行保存", feature.key, reason)
        return ConfigValidateOutcome(config=current, failed=reason)
    if result is None:
        return ConfigValidateOutcome(config=current, handled=True)
    if not isinstance(result, Mapping):
        log.warning("插件 %s on_config_validate 返回值不是对象，已忽略", feature.key)
        return ConfigValidateOutcome(config=current, handled=True)
    errors = _normalize_errors(result.get("errors"))
    next_config = result.get("config")
    if isinstance(next_config, Mapping):
        current = dict(next_config)
    return ConfigValidateOutcome(config=current, errors=errors, handled=True)


def format_validate_errors(errors: Mapping[str, str]) -> str:
    parts = [msg if key == "_" else f"{key}: {msg}" for key, msg in errors.items()]
    return "配置验证失败: " + "; ".join(parts)


async def schedule_config_saved(
    db: AsyncSession,
    *,
    feature: Feature,
    account: Account | None,
    config: Mapping[str, Any],
    scope: str,
) -> asyncio.Task[Any] | None:
    """保存提交后在后台调用 ``on_config_saved``；返回任务（测试可 await），未实现时返回 None。"""

    account_id = int(account.id) if account is not None else 0
    plugin_cls = await _plugin_class_or_none(db, feature, account_id, "on_config_saved")
    if plugin_cls is None:
        return None
    from .plugin_config_actions import build_main_process_context

    try:
        ctx = await build_main_process_context(
            db, account=account, feature=feature, plugin_cls=plugin_cls, config=dict(config or {})
        )
    except Exception:  # noqa: BLE001
        log.debug("on_config_saved 上下文构造失败 plugin=%s", feature.key, exc_info=True)
        return None

    async def _run() -> None:
        try:
            await call_plugin_hook(plugin_cls, ctx, "on_config_saved", dict(config or {}), scope, event_type=scope)
        except Exception as exc:  # noqa: BLE001 - 后台通知失败只记统计与日志
            log.warning("插件 %s on_config_saved 失败：%s", feature.key, type(exc).__name__)

    task = asyncio.create_task(_run(), name=f"plugin-config-saved:{feature.key}")
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)
    return task


__all__ = [
    "SCOPE_ACCOUNT",
    "SCOPE_GLOBAL",
    "ConfigValidateOutcome",
    "call_plugin_hook",
    "format_validate_errors",
    "plugin_overrides",
    "run_config_validate",
    "schedule_config_saved",
]
