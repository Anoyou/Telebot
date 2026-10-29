"""通用 Rule 的无副作用试运行服务，REST API（``POST .../rules/{rid}/dry-run``）与 Agent 共用。

宿主只认两个内置功能（``forward`` 转发、``scheduler`` 定时任务）；其它插件规则一律走通用协议：

1. 插件模块导出 ``dry_run_rule(config, sample)``（可为 async，3 秒时限）——0.110.0-beta.2 起的
   新协议（能力 ``rules.dry_run``）。``sample = {"message", "chat_type", "chat_id"}``，返回
   ``{"matched": bool, "output": str | None, "logs": [{"step", "msg"}]}``（也接受 ``(matched, output)``）。
2. 没有导出时回退旧版 ``_dry_run_match(*args)``：参数形状与日志文案登记在
   ``plugin_compat.legacy_dry_run_args`` / ``legacy_dry_run_logs``，宿主这里不再按插件 key 分支。

dry-run 不会发送消息或修改业务数据；插件自己保证 ``dry_run_rule`` 无副作用。
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import sys
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .. import plugin_compat
from ..db.models.feature import FEATURE_FORWARD, FEATURE_SCHEDULER
from ..db.models.plugin import PLUGIN_TRUST_ORPHAN, InstalledPlugin
from ..db.models.rule import Rule
from ..settings import settings

log = logging.getLogger(__name__)

DRY_RUN_HOOK = "dry_run_rule"
LEGACY_DRY_RUN_HOOK = "_dry_run_match"
DRY_RUN_TIMEOUT_SECONDS = 3.0
PROTOCOL_NEW = "dry_run_rule"
PROTOCOL_LEGACY = "legacy"
PROTOCOL_NONE = "none"


async def load_rule_plugin_module(db: Any, feature_key: str) -> tuple[Any | None, str | None]:
    """加载已安装插件模块；返回 ``(module, None)`` 或 ``(None, 不能试运行的原因)``。"""

    installed = await db.get(InstalledPlugin, feature_key)
    if installed is None:
        return None, f"{feature_key} 仓库插件未安装，无法执行 dry-run。"
    if not bool(getattr(installed, "enabled", False)):
        return None, f"{feature_key} 已安装但全局开关关闭，无法执行 dry-run。"
    if str(getattr(installed, "trust_tier", "") or "") == PLUGIN_TRUST_ORPHAN:
        return None, f"{feature_key} 安装记录为 orphan，无法执行 dry-run。"
    signature_ok = getattr(installed, "signature_ok", None)
    if signature_ok is False:
        return None, f"{feature_key} 签名校验失败，无法执行 dry-run。"
    if signature_ok is None and not bool(settings.plugin_allow_legacy_unsigned_plugins):
        return None, f"{feature_key} 签名状态未知，当前策略不允许执行 dry-run。"
    install_error = str(getattr(installed, "last_install_error", "") or "").strip()
    if install_error:
        return None, f"{feature_key} 安装失败：{install_error}"

    from ..worker.plugins.loader import _installed_module_name, _load_installed_plugin

    loaded = _load_installed_plugin(feature_key)
    if feature_key not in loaded:
        return None, f"{feature_key} 仓库插件未安装或加载失败，无法执行 dry-run。"
    package_name = _installed_module_name(feature_key)
    modules = [sys.modules.get(name) for name in (f"{package_name}.plugin", package_name)]
    modules = [module for module in modules if module is not None]
    if not modules:
        return None, f"{feature_key} 仓库插件未安装或加载失败，无法执行 dry-run。"
    # 优先返回导出了试运行函数的那个模块（plugin.py 或包 __init__）。
    for hook in (DRY_RUN_HOOK, LEGACY_DRY_RUN_HOOK):
        for module in modules:
            if callable(getattr(module, hook, None)):
                return module, None
    return modules[0], None


async def _maybe_await(value: Any, timeout: float) -> Any:
    if inspect.isawaitable(value):
        return await asyncio.wait_for(value, timeout=timeout)
    return value


def _normalize_logs(raw: Any) -> list[dict[str, str]]:
    logs: list[dict[str, str]] = []
    if not isinstance(raw, list):
        return logs
    for item in raw[:50]:
        if isinstance(item, dict):
            msg = str(item.get("msg") or item.get("message") or "").strip()
            if msg:
                logs.append({"step": str(item.get("step") or "plugin")[:32], "msg": msg[:500]})
        elif isinstance(item, str) and item.strip():
            logs.append({"step": "plugin", "msg": item.strip()[:500]})
    return logs


def _normalize_result(result: Any) -> tuple[bool, str | None, list[dict[str, str]]]:
    if isinstance(result, dict):
        output = result.get("output")
        return (
            bool(result.get("matched")),
            str(output) if output is not None else None,
            _normalize_logs(result.get("logs")),
        )
    if isinstance(result, (tuple, list)) and len(result) >= 2:
        matched, output = result[0], result[1]
        return bool(matched), str(output) if output is not None else None, []
    raise TypeError("dry_run_rule 返回值必须是 dict 或 (matched, output)")


async def plugin_dry_run(
    db: Any,
    feature_key: str,
    config: dict[str, Any],
    *,
    sample_message: str,
    sample_chat_type: str | None = None,
    sample_chat_id: int | None = None,
) -> dict[str, Any]:
    """对插件规则执行试运行，返回 ``{matched, output, logs, protocol}``。"""

    cfg = dict(config or {})
    sample = {
        "message": sample_message,
        "chat_type": sample_chat_type or "private",
        "chat_id": sample_chat_id,
    }
    module, reason = await load_rule_plugin_module(db, feature_key)
    if module is None:
        return {"matched": False, "output": reason, "logs": [], "protocol": PROTOCOL_NONE}

    hook = getattr(module, DRY_RUN_HOOK, None)
    if callable(hook):
        try:
            result = await _maybe_await(hook(cfg, dict(sample)), DRY_RUN_TIMEOUT_SECONDS)
            matched, output, logs = _normalize_result(result)
        except TimeoutError:
            return {
                "matched": False,
                "output": f"{feature_key} 试运行超时（{DRY_RUN_TIMEOUT_SECONDS:.0f} 秒）",
                "logs": [],
                "protocol": PROTOCOL_NEW,
            }
        except Exception as exc:  # noqa: BLE001 - 插件代码失败只影响本次试运行
            log.info("插件 %s dry_run_rule 失败: %s", feature_key, exc, exc_info=True)
            return {
                "matched": False,
                "output": f"{feature_key} 试运行失败：{type(exc).__name__}: {exc}",
                "logs": [],
                "protocol": PROTOCOL_NEW,
            }
        return {"matched": matched, "output": output, "logs": logs, "protocol": PROTOCOL_NEW}

    legacy = getattr(module, LEGACY_DRY_RUN_HOOK, None)
    if not callable(legacy):
        return {
            "matched": False,
            "output": f"{feature_key} 插件未导出 dry_run_rule / _dry_run_match，无法执行 dry-run。",
            "logs": [],
            "protocol": PROTOCOL_NONE,
        }
    args = plugin_compat.legacy_dry_run_args(feature_key, cfg, sample)
    result = await _maybe_await(legacy(*args), DRY_RUN_TIMEOUT_SECONDS)
    matched, output, _ = _normalize_result(result)
    logs = plugin_compat.legacy_dry_run_logs(feature_key, cfg, sample, matched=matched, output=output)
    return {"matched": matched, "output": output, "logs": logs, "protocol": PROTOCOL_LEGACY}


async def legacy_installed_match(db: Any, feature_key: str, *args: Any) -> tuple[bool, str | None]:
    """旧接口：直接把位置参数交给已安装插件的 ``_dry_run_match``（保留给既有调用方与测试）。"""

    module, reason = await load_rule_plugin_module(db, feature_key)
    if module is None:
        return False, reason
    legacy = getattr(module, LEGACY_DRY_RUN_HOOK, None)
    if not callable(legacy):
        return False, f"{feature_key} 插件未导出 _dry_run_match，无法执行 dry-run。"
    result = await _maybe_await(legacy(*args), DRY_RUN_TIMEOUT_SECONDS)
    matched, output, _ = _normalize_result(result)
    return matched, output


async def dry_run_rule(
    db: AsyncSession,
    rule: Rule,
    *,
    sample_message: str,
    sample_chat_type: str = "private",
    sample_chat_id: int | None = None,
) -> dict[str, Any]:
    """执行无真实发送的匹配/调度预览（Agent 工具 ``rules.dry_run`` 使用）。"""

    key = str(rule.feature_key)
    cfg = dict(rule.config or {})
    logs: list[dict[str, str]] = []
    if key == FEATURE_FORWARD:
        from ..worker.plugins.builtin.forward.plugin import _dry_run_match

        matched, output = _dry_run_match(cfg, sample_message, sample_chat_id)
    elif key == FEATURE_SCHEDULER:
        from .rule_service import normalize_scheduler_config

        normalized = await normalize_scheduler_config(db, cfg)
        next_fire = normalized.get("next_fire")
        action = normalized.get("action") if isinstance(normalized.get("action"), dict) else {}
        matched = False
        output = f"next fire at {next_fire or 'N/A'}"
        return {
            "matched": matched,
            "output": output,
            "detail": {
                "feature": key,
                "rule_id": rule.id,
                "kind": normalized.get("kind", "cron"),
                "next_fire": next_fire,
                "action_type": action.get("type", "send_message"),
                "target_chat_id": action.get("target_chat_id"),
                "note": "仅计算调度与动作摘要，不会真实执行。",
            },
        }
    else:
        result = await plugin_dry_run(
            db,
            key,
            cfg,
            sample_message=sample_message,
            sample_chat_type=sample_chat_type or "private",
            sample_chat_id=sample_chat_id,
        )
        matched, output, logs = result["matched"], result["output"], result["logs"]
    detail: dict[str, Any] = {
        "feature": key,
        "rule_id": rule.id,
        "sample_chat_type": sample_chat_type,
        "sample_chat_id": sample_chat_id,
        "note": "dry-run 不会发送消息或修改业务数据。",
    }
    if logs:
        detail["logs"] = logs
    return {"matched": bool(matched), "output": output, "detail": detail}


__all__ = [
    "DRY_RUN_HOOK",
    "dry_run_rule",
    "legacy_installed_match",
    "load_rule_plugin_module",
    "plugin_dry_run",
]
