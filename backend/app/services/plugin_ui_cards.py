"""插件原生卡片（``plugin.json`` 的 ``ui.cards``）：列出、取数、按钮动作。

- slot ``plugin.overview``：插件详情顶部。宿主内置的「运行统计」卡（``_host.stats``，数据来自
  ``plugin.stats@1``）总排在第一张，是卡片 DSL 的第一个用户；
- slot ``account.detail``：账号详情，只列在该账号启用的插件；
- slot ``dashboard``：概览末尾，只列全局启用、且至少一个账号启用的插件；
- slot ``interaction.rule_editor``（0.112.0+）：交互规则编辑器的插件参数区，必须带 ``plugin_key``。

取数失败（插件未加载、超时 3 秒、抛异常、返回值不对）只影响那一张卡：返回 ``status="error"``，
客户端显示「已停止工作 · 重试」，HTTP 仍是 200。
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.account import Account, SudoUser
from ..db.models.feature import AccountFeature, Feature
from ..db.models.ignored_peer import IgnoredPeer
from ..db.models.plugin import InstalledPlugin
from ..worker.plugins import plugin_ui
from ..worker.plugins.host_read import HostReadError, normalize_resource_ref

log = logging.getLogger(__name__)

HOST_STATS_CARD_ID = "_host.stats"
HOST_STATS_RESET_ACTION = "reset_stats"
FAILURE_MESSAGE = "已停止工作"

_HOOK_LABELS = {
    "on_event": "事件处理",
    "on_interaction": "交互入口（旧）",
    "on_message": "消息",
    "on_message_edited": "消息编辑",
    "on_direct_message": "低延时直通",
    "on_command": "命令",
    "on_startup": "启动",
    "on_config_action": "配置动作",
    "on_config_validate": "配置校验",
    "on_config_saved": "配置保存通知",
    "on_ui_card": "卡片数据",
    "on_ui_action": "卡片按钮",
    "system_agent_tool": "助手工具",
    "middleware.inbound": "入站中间件",
    "middleware.outbound": "出站中间件",
    "middleware.delivered": "投递回调",
    "parse_payment_notice": "转账通知解析",
}

# payment_sources 双跑对照的事件维度（payment_sources.VERDICT_*）→ 卡片上的中文。
_PAYMENT_VERDICT_LABELS = {
    "agree": "与宿主一致",
    "mismatch": "与宿主不一致",
    "plugin_failed": "插件失败（已回退宿主）",
}


class PluginCardError(RuntimeError):
    """卡片或按钮不存在 / 不可用。"""


def _host_stats_card() -> dict[str, Any]:
    return {
        "id": HOST_STATS_CARD_ID,
        "slot": "plugin.overview",
        "title": "运行统计",
        "subtitle": "按钩子统计调用数、耗时与失败数，保留到最近一次调用后 7 天",
        "source": {"read": "plugin.stats@1"},
        "refresh_seconds": None,
        "host": True,
        "blocks": [
            {
                "type": "stats",
                "items": [
                    {"label": "调用", "path": "summary.calls", "format": "number", "tone_path": "summary.calls_tone"},
                    {"label": "失败", "path": "summary.failed", "format": "number", "tone_path": "summary.failed_tone"},
                    {"label": "超时", "path": "summary.timeout", "format": "number", "tone_path": "summary.timeout_tone"},
                    {"label": "平均耗时", "path": "summary.avg_ms", "format": "duration_ms"},
                ],
            },
            {
                # 插件代码在事件循环上同步执行 ≥200ms（P2 运行统计 blocked / blocking_ms），没有时不显示。
                "type": "list",
                "id": "blocking",
                "title": "同步阻塞（会卡住整个账号的消息处理）",
                "items_path": "blocking",
                "hide_when_empty": True,
            },
            {
                "type": "list",
                "id": "deprecations",
                "title": "使用了已弃用接口",
                "items_path": "deprecations",
                "hide_when_empty": True,
            },
            {
                "type": "table",
                "id": "hooks",
                "rows_path": "hooks",
                "row_key": "key",
                "empty": "还没有调用记录。插件收到事件、执行配置动作或启动后，这里会显示统计。",
                "columns": [
                    {"key": "hook", "label": "钩子"},
                    {"key": "event_type", "label": "事件"},
                    {"key": "account", "label": "账号"},
                    {"key": "health", "label": "状态", "tone_key": "health_tone"},
                    {"key": "calls", "label": "调用", "format": "number"},
                    {"key": "failure_rate", "label": "失败率", "format": "percent"},
                    {"key": "avg_ms", "label": "平均", "format": "duration_ms"},
                    {"key": "p95_ms", "label": "P95", "format": "duration_ms"},
                    {"key": "last_at", "label": "最近", "format": "relative_time"},
                    {"key": "last_error", "label": "最近错误"},
                ],
            },
            {
                "type": "buttons",
                "items": [
                    {
                        "id": HOST_STATS_RESET_ACTION,
                        "label": "清零",
                        "style": "destructive",
                        "confirm": "清零这个插件的运行统计和弃用计数？",
                    }
                ],
            },
        ],
    }


# ─────────────────────────────────────────────────────
# 声明读取
# ─────────────────────────────────────────────────────


def _plugin_json_from_dir(installed: InstalledPlugin | None) -> dict[str, Any]:
    path = str(getattr(installed, "installed_path", "") or "").strip()
    if not path:
        return {}
    metadata = Path(path) / "plugin.json"
    try:
        raw = json.loads(metadata.read_text(encoding="utf-8")) if metadata.is_file() else {}
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def ui_declaration(feature: Feature | None, installed: InstalledPlugin | None) -> tuple[Any, list[str]]:
    """``(ui, reads)``：已安装记录 → 插件目录 plugin.json → feature 索引，取第一个有 ``ui`` 的来源。"""

    sources: list[Mapping[str, Any]] = []
    manifest_json = getattr(installed, "manifest_json", None)
    if isinstance(manifest_json, Mapping):
        sources.append(manifest_json)
    sources.append(_plugin_json_from_dir(installed))
    feature_manifest = getattr(feature, "manifest", None)
    if isinstance(feature_manifest, Mapping):
        sources.append(feature_manifest)
    ui: Any = None
    reads: list[str] = []
    for source in sources:
        if ui is None and isinstance(source.get("ui"), Mapping):
            ui = source.get("ui")
        if not reads and isinstance(source.get("reads"), list):
            reads = [str(item) for item in source.get("reads") or []]
    return ui, reads


async def plugin_cards(db: AsyncSession, plugin_key: str) -> tuple[list[dict[str, Any]], list[str]]:
    feature = await db.get(Feature, plugin_key)
    installed = await db.get(InstalledPlugin, plugin_key)
    ui, reads = ui_declaration(feature, installed)
    return plugin_ui.normalize_ui_cards(ui, reads=reads)


async def _enabled_account_ids(db: AsyncSession, plugin_key: str) -> list[int]:
    rows = await db.execute(
        select(AccountFeature.account_id).where(
            AccountFeature.feature_key == plugin_key, AccountFeature.enabled.is_(True)
        )
    )
    return [int(item) for item in rows.scalars().all()]


async def _plugin_display_name(db: AsyncSession, plugin_key: str) -> str:
    feature = await db.get(Feature, plugin_key)
    return str(getattr(feature, "display_name", "") or plugin_key)


def _public_card(card: Mapping[str, Any], plugin_key: str, plugin_name: str) -> dict[str, Any]:
    return {
        "plugin_key": plugin_key,
        "plugin_name": plugin_name,
        "card_id": card["id"],
        "slot": card["slot"],
        "title": card["title"],
        "subtitle": card.get("subtitle"),
        "source_kind": "read" if "read" in card["source"] else "action",
        "blocks": list(card["blocks"]),
        "refresh_seconds": card.get("refresh_seconds"),
        "host": bool(card.get("host")),
    }


async def list_cards(
    db: AsyncSession,
    *,
    slot: str,
    plugin_key: str | None = None,
    account_id: int | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """列出某个 slot 的卡片声明（不含数据）。返回 ``(cards, warnings)``。"""

    if slot not in plugin_ui.card_slots():
        raise PluginCardError(f"未知 slot：{slot}")
    keys: list[str]
    if plugin_key:
        keys = [plugin_key]
    elif slot == "interaction.rule_editor":
        raise PluginCardError("interaction.rule_editor 需要 plugin_key（规则所选插件）")
    elif slot == "account.detail":
        if account_id is None:
            raise PluginCardError("account.detail 需要 account_id")
        rows = await db.execute(
            select(AccountFeature.feature_key).where(
                AccountFeature.account_id == account_id, AccountFeature.enabled.is_(True)
            )
        )
        keys = sorted({str(item) for item in rows.scalars().all()})
    else:
        installed_rows = await db.execute(select(InstalledPlugin.key).where(InstalledPlugin.enabled.is_(True)))
        installed_keys = {str(item) for item in installed_rows.scalars().all()}
        enabled_rows = await db.execute(
            select(AccountFeature.feature_key).where(AccountFeature.enabled.is_(True)).distinct()
        )
        keys = sorted(installed_keys & {str(item) for item in enabled_rows.scalars().all()})
    out: list[dict[str, Any]] = []
    warnings: list[str] = []
    for key in keys:
        name = await _plugin_display_name(db, key)
        if slot == "plugin.overview" and plugin_key:
            out.append(_public_card(_host_stats_card(), key, name))
        cards, problems = await plugin_cards(db, key)
        warnings.extend(f"{key}: {item}" for item in problems)
        for card in cards:
            if card["slot"] == slot:
                out.append(_public_card(card, key, name))
    return out, warnings


async def find_card(db: AsyncSession, plugin_key: str, card_id: str) -> dict[str, Any]:
    if card_id == HOST_STATS_CARD_ID:
        return _host_stats_card()
    cards, _warnings = await plugin_cards(db, plugin_key)
    for card in cards:
        if card["id"] == card_id:
            return card
    raise PluginCardError(f"插件 {plugin_key} 没有声明卡片 {card_id}")


# ─────────────────────────────────────────────────────
# 取数
# ─────────────────────────────────────────────────────


def _format_ms(value: Any) -> str:
    try:
        ms = float(value or 0)
    except (TypeError, ValueError):
        ms = 0.0
    return f"{ms / 1000:.1f} 秒" if ms >= 1000 else f"{round(ms)} 毫秒"


def _format_hook_rows(data: Mapping[str, Any], account_names: Mapping[int, str]) -> dict[str, Any]:
    hooks = list(data.get("hooks") or [])
    totals = data.get("totals") or {}
    timed = 0
    sum_ms = 0.0
    rows = []
    blocking = []
    for row in hooks:
        calls = int(row.get("calls") or 0)
        failed = int(row.get("failed") or 0)
        timeout = int(row.get("timeout") or 0)
        rate = float(row.get("failure_rate") or 0)
        if not calls:
            health, tone = "无调用", "neutral"
        elif rate >= 0.2:
            health, tone = "失败偏多", "danger"
        elif failed or timeout:
            health, tone = "有失败", "warning"
        else:
            health, tone = "正常", "success"
        timed += int(row.get("ok") or 0) + failed + timeout
        sum_ms += float(row.get("sum_ms") or 0)
        account_id = row.get("account_id")
        event_type = str(row.get("event_type") or "")
        if str(row.get("hook") or "") == "parse_payment_notice":
            if event_type == "mismatch" and calls:
                # 对照不一致不是插件失败，但需要人工看一眼（Trace / 日志里有逐字段差异）。
                health, tone = "对照不一致", "warning"
            event_type = _PAYMENT_VERDICT_LABELS.get(event_type, event_type)
        blocked = int(row.get("blocked") or 0)
        if blocked > 0:
            hook_label = _HOOK_LABELS.get(str(row.get("hook") or ""), str(row.get("hook") or ""))
            blocking.append(
                {
                    "title": hook_label + (f" · {event_type}" if event_type not in {"", "-"} else ""),
                    "subtitle": "插件代码在事件循环上同步执行超过 200 毫秒",
                    "trailing": f"{blocked} 次 · 累计 {_format_ms(row.get('blocking_ms'))}",
                    "tone": "warning",
                }
            )
        rows.append(
            {
                "key": str(row.get("hook") or ""),
                "hook": _HOOK_LABELS.get(str(row.get("hook") or ""), str(row.get("hook") or "")),
                "event_type": "" if event_type in {"", "-"} else event_type,
                "account": (account_names.get(int(account_id)) or f"账号 #{account_id}") if account_id else "",
                "health": health,
                "health_tone": tone,
                "calls": calls,
                "failure_rate": rate,
                "avg_ms": row.get("avg_ms"),
                "p95_ms": row.get("p95_ms"),
                "last_at": row.get("last_at"),
                "last_error": row.get("last_error") or "",
            }
        )
    calls = int(totals.get("calls") or 0)
    failed = int(totals.get("fail") or 0)
    timeout = int(totals.get("timeout") or 0)
    deprecations = []
    for item in data.get("deprecations") or []:
        parts = [str(item.get("summary") or "")]
        if item.get("replacement"):
            parts.append(f"请改用 {item['replacement']}")
        if item.get("removal"):
            parts.append(f"计划移除：{item['removal']}")
        deprecations.append(
            {
                "title": str(item.get("id") or ""),
                "subtitle": "；".join(part for part in parts if part),
                "trailing": f"{int(item.get('count') or 0)} 次",
                "tone": "warning",
            }
        )
    return {
        "summary": {
            "calls": calls,
            "failed": failed,
            "timeout": timeout,
            "avg_ms": round(sum_ms / timed) if timed else None,
            "calls_tone": "accent" if calls else "neutral",
            "failed_tone": "danger" if failed else "neutral",
            "timeout_tone": "warning" if timeout else "neutral",
        },
        "hooks": rows,
        "blocking": blocking,
        "deprecations": deprecations,
        "totals": dict(totals),
    }


async def _read_resource(
    db: AsyncSession,
    ref: str,
    *,
    plugin_key: str,
    account_id: int | None,
    host_card: bool,
) -> Any:
    from ..redis_client import get_redis
    from ..worker.plugins import plugin_stats

    ref = normalize_resource_ref(ref)
    if ref == "plugin.stats@1":
        try:
            await plugin_stats.get_recorder().flush(get_redis())
        except Exception:  # noqa: BLE001
            log.debug("flush local plugin stats failed", exc_info=True)
        data = await plugin_stats.read_plugin_stats(get_redis(), plugin_key, account_id=account_id)
        if not host_card:
            return data
        names: dict[int, str] = {}
        for row in data.get("hooks") or []:
            aid = row.get("account_id")
            if aid and int(aid) not in names:
                account = await db.get(Account, int(aid))
                names[int(aid)] = str(getattr(account, "display_name", "") or getattr(account, "phone", "") or "")
        return _format_hook_rows(data, names)
    if account_id is None:
        raise HostReadError(f"{ref} 需要账号上下文")
    if ref == "chat.allowed@1":
        rows = await db.execute(select(IgnoredPeer.peer_id).where(IgnoredPeer.account_id == account_id))
        chat_ids = sorted(int(item) for item in rows.scalars().all())
        return {"all": not chat_ids, "chat_ids": chat_ids}
    if ref == "account.admins@1":
        account = await db.get(Account, account_id)
        owner = [int(account.tg_user_id)] if account is not None and account.tg_user_id else []
        rows = await db.execute(select(SudoUser.tg_user_id).where(SudoUser.account_id == account_id))
        sudo = {int(item) for item in rows.scalars().all()}
        return {"owner_user_ids": owner, "admin_user_ids": sorted(set(owner) | sudo)}
    raise HostReadError(f"卡片上下文不提供 {ref}")


async def _action_context(
    db: AsyncSession,
    plugin_key: str,
    account_id: int | None,
) -> tuple[Feature, Account | None, type[Any], Any]:
    from . import feature_service
    from .plugin_config_actions import build_main_process_context, load_plugin_class

    feature = await db.get(Feature, plugin_key)
    if feature is None:
        raise PluginCardError(f"未注册的插件：{plugin_key}")
    account = await db.get(Account, account_id) if account_id else None
    installed = await db.get(InstalledPlugin, plugin_key)
    plugin_cls = await load_plugin_class(db, plugin_key, int(account_id or 0), installed_plugin=installed)
    if account is not None:
        config = await feature_service.get_effective_plugin_config(db, account.id, plugin_key)
    else:
        config = await feature_service.get_plugin_global_config(db, plugin_key)
    ctx = await build_main_process_context(
        db, account=account, feature=feature, plugin_cls=plugin_cls, config=config, readonly_storage=True
    )
    return feature, account, plugin_cls, ctx


async def card_data(
    db: AsyncSession,
    *,
    plugin_key: str,
    card_id: str,
    account_id: int | None = None,
    slot: str | None = None,
) -> dict[str, Any]:
    """取一张卡的数据；失败只体现在返回值的 ``status`` 上。"""

    from .plugin_config_hooks import call_plugin_hook, plugin_overrides

    started = time.monotonic()
    base = {"plugin_key": plugin_key, "card_id": card_id, "updated_at": int(time.time())}
    try:
        card = await find_card(db, plugin_key, card_id)
    except PluginCardError as exc:
        return {**base, "status": "error", "data": None, "message": str(exc), "duration_ms": 0}
    try:
        if "read" in card["source"]:
            data = await _read_resource(
                db,
                card["source"]["read"],
                plugin_key=plugin_key,
                account_id=account_id,
                host_card=bool(card.get("host")),
            )
        else:
            _feature, _account, plugin_cls, ctx = await _action_context(db, plugin_key, account_id)
            if not plugin_overrides(plugin_cls, "on_ui_card"):
                raise PluginCardError("插件没有实现 on_ui_card")
            context = {"slot": slot or card["slot"], "account_id": account_id, "action": card["source"]["action"]}
            data = await call_plugin_hook(plugin_cls, ctx, "on_ui_card", card_id, context, event_type=card_id)
            if data is None:
                data = {}
            if not isinstance(data, Mapping):
                raise PluginCardError("on_ui_card 必须返回对象")
            data = json.loads(json.dumps(data, ensure_ascii=False, default=str))
    except Exception as exc:  # noqa: BLE001 - 卡片失败只影响本卡
        reason = "读取超时（3 秒）" if isinstance(exc, TimeoutError) else (str(exc) if isinstance(exc, (PluginCardError, HostReadError)) else type(exc).__name__)
        log.info("插件卡片取数失败 plugin=%s card=%s: %s", plugin_key, card_id, reason)
        return {
            **base,
            "status": "error",
            "data": None,
            "message": f"{FAILURE_MESSAGE}：{reason}",
            "duration_ms": round((time.monotonic() - started) * 1000),
        }
    return {
        **base,
        "status": "ok",
        "data": data,
        "message": None,
        "duration_ms": round((time.monotonic() - started) * 1000),
    }


def _card_button(card: Mapping[str, Any], action_id: str) -> Mapping[str, Any] | None:
    for block in card.get("blocks") or []:
        if not isinstance(block, Mapping) or block.get("type") != "buttons":
            continue
        for item in block.get("items") or []:
            if isinstance(item, Mapping) and str(item.get("id") or "") == action_id:
                return item
    return None


async def card_action(
    db: AsyncSession,
    *,
    plugin_key: str,
    card_id: str,
    action_id: str,
    account_id: int | None = None,
    slot: str | None = None,
) -> dict[str, Any]:
    """执行卡片按钮。宿主卡片的按钮由宿主处理，插件卡片调 ``on_ui_action``（10 秒）。"""

    from ..redis_client import get_redis
    from ..worker.plugins import plugin_stats
    from .plugin_config_hooks import call_plugin_hook, plugin_overrides

    card = await find_card(db, plugin_key, card_id)
    if _card_button(card, action_id) is None:
        raise PluginCardError(f"卡片 {card_id} 没有按钮 {action_id}")
    if card.get("host"):
        if action_id == HOST_STATS_RESET_ACTION:
            await plugin_stats.reset_plugin_stats(get_redis(), plugin_key)
            return {"ok": True, "message": "已清零运行统计", "refresh": True}
        raise PluginCardError(f"未知的宿主按钮 {action_id}")
    _feature, _account, plugin_cls, ctx = await _action_context(db, plugin_key, account_id)
    if not plugin_overrides(plugin_cls, "on_ui_action"):
        raise PluginCardError("插件没有实现 on_ui_action")
    context = {"slot": slot or card["slot"], "account_id": account_id, "card_id": card_id}
    result = await call_plugin_hook(plugin_cls, ctx, "on_ui_action", action_id, context, event_type=action_id)
    if result is None:
        return {"ok": True, "message": None, "refresh": True}
    if not isinstance(result, Mapping):
        raise PluginCardError("on_ui_action 必须返回对象")
    message = result.get("message") or result.get("toast")
    return {
        "ok": result.get("ok") is not False,
        "message": str(message) if message else None,
        "refresh": result.get("refresh") is not False,
    }


__all__ = [
    "FAILURE_MESSAGE",
    "HOST_STATS_CARD_ID",
    "HOST_STATS_RESET_ACTION",
    "PluginCardError",
    "card_action",
    "card_data",
    "find_card",
    "list_cards",
    "plugin_cards",
    "ui_declaration",
]
