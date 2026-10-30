"""运行树：聚合树干、平台模块（枝）与插件（叶）的只读运行视图。"""

from __future__ import annotations

import ast
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.account import Account
from ..db.models.feature import AccountFeature, Feature
from ..db.models.plugin import InstalledPlugin
from ..worker.supervisor import get_worker_runtime_snapshot
from . import kill_switch_service, platform_capabilities, runtime_profile_service
from .platform_tree_shape import plugin_shape
from .plugin_capability_requirements import (
    PluginCapabilityRequirement,
    list_builtin_capability_requirements,
    list_installed_capability_requirements,
)


def _attachment_from_metadata(record: PluginCapabilityRequirement) -> str:
    plugin_json = record.path / "plugin.json"
    if plugin_json.is_file():
        try:
            raw = json.loads(plugin_json.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                capabilities = raw.get("capabilities")
                passthrough = (
                    capabilities.get("telegram_direct_passthrough")
                    if isinstance(capabilities, dict)
                    else None
                )
                if isinstance(passthrough, dict) and passthrough.get("enabled") is True:
                    return "直通"
                if raw.get("interaction_entries") or raw.get("interaction_profile"):
                    return "交互"
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    if "interaction_bot" in record.requires:
        return "交互"
    manifest_py = record.path / "manifest.py"
    if manifest_py.is_file():
        try:
            tree = ast.parse(manifest_py.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.keyword) or node.arg != "capabilities":
                    continue
                value: Any = ast.literal_eval(node.value)
                if (
                    isinstance(value, dict)
                    and isinstance(value.get("telegram_direct_passthrough"), dict)
                    and value["telegram_direct_passthrough"].get("enabled") is True
                ):
                    return "直通"
        except (OSError, SyntaxError, ValueError):
            pass
    return "命令"


MODULE_LABELS: dict[str, str] = {
    "ai": "AI",
    "interaction_bot": "交互 Bot",
    "webhooks": "入站 Webhook",
    "ledger": "资金台账",
    "dispatch_debug": "命中调试",
}
TREE_TITLE = "运行树"
TREE_SUBTITLE = "这台服务器开了哪些模块、哪些插件在用"


def _account_label(account_id: int, names: dict[int, str]) -> str:
    name = (names.get(account_id) or "").strip()
    return f"账号 #{account_id}（{name}）" if name else f"账号 #{account_id}"


def _join_names(items: list[str], limit: int = 5) -> str:
    if len(items) <= limit:
        return "、".join(items)
    return "、".join(items[:limit]) + f" 等 {len(items)} 个"


def _forced_off_reason(module_key: str, *, forced_off: bool, profile: dict[str, Any]) -> str | None:
    if not forced_off:
        return None
    if (
        profile.get("active_profile") == "safe_watch"
        or profile.get("current_profile") == "safe_watch"
    ) and runtime_profile_service.PRESETS["safe_watch"].get(module_key) is False:
        return "值守模式关闭了它；恢复生产后才会重新打开"
    return "管理员手动关闭了它；在平台能力里重新打开即可"


def _blocked(
    module_key: str,
    *,
    desired: bool,
    forced_off_reason: str | None,
    demanders: list[dict[str, Any]],
    account_id: int | None,
    names: dict[int, str],
) -> tuple[str | None, str | None]:
    """解释 ``can_turn_off`` 为 False 的原因；能关时返回 (None, None)。"""

    label = MODULE_LABELS[module_key]
    if not desired:
        reason = f"{label} 已经是关闭状态"
        if forced_off_reason:
            reason += f"（{forced_off_reason}）"
        return "already_off", reason
    if not demanders:
        return None, None

    def describe(rows: list[dict[str, Any]]) -> str:
        parts = []
        for row in rows:
            accounts = "、".join(f"#{aid}" for aid in row["account_ids"])
            parts.append(f"{row['display_name']}（{accounts}）" if accounts else row["display_name"])
        return _join_names(parts)

    if account_id is None:
        return (
            "required_by_plugins",
            f"有 {len(demanders)} 个已启用的插件需要 {label}：{describe(demanders)}；"
            "先在对应账号停用这些插件才能关闭。",
        )
    mine = [row for row in demanders if account_id in row["account_ids"]]
    others = [row for row in demanders if account_id not in row["account_ids"]]
    other_accounts = sorted(
        {aid for row in demanders for aid in row["account_ids"] if aid != account_id}
    )
    if mine:
        reason = f"本账号启用的 {len(mine)} 个插件需要 {label}：{describe(mine)}"
        if other_accounts:
            reason += f"；另外 {_join_names([_account_label(a, names) for a in other_accounts], 3)} 也在用"
        return "required_by_this_account", reason + "。模块是全服共用的，要先停用这些插件才能关闭。"
    return (
        "required_by_other_accounts",
        f"本账号没有插件需要 {label}，但其他账号启用的插件需要它：{describe(others)}。"
        "模块是全服共用的，要先在那些账号停用这些插件才能关闭。",
    )


async def build_platform_tree(
    db: AsyncSession,
    *,
    account_id: int | None = None,
    account_name: str | None = None,
) -> dict[str, Any]:
    """运行树。

    不传 ``account_id`` 时输出与旧版一致（全局口径：任一账号启用即算启用）；
    传了则 ``leaves[].enabled`` 与 ``branches[].demanded_by`` 改为该账号口径，
    同时 ``demanded_by_global`` / ``demanders`` / ``blocked_reason`` 永远给出全局事实，
    用来解释「为什么本账号没人用却关不掉」。新增字段只追加，不改旧字段含义。
    """

    if not platform_capabilities.get_snapshot().cache_ready:
        await platform_capabilities.refresh_cache_from_db(db)
    profile = await runtime_profile_service.get_status(db)
    demand = await platform_capabilities.compute_demand(db)
    snapshot = platform_capabilities.get_snapshot()
    workers = get_worker_runtime_snapshot()

    enabled_leaf_keys = set(
        (
            await db.execute(
                select(AccountFeature.feature_key).where(AccountFeature.enabled.is_(True))
            )
        ).scalars()
    )
    feature_rows = (
        await db.execute(
            select(
                AccountFeature.account_id,
                AccountFeature.feature_key,
                AccountFeature.enabled,
                AccountFeature.state,
                AccountFeature.last_error,
            )
        )
    ).all()
    enabled_accounts: dict[str, set[int]] = {}
    account_rows: dict[str, Any] = {}
    for row in feature_rows:
        if row.enabled:
            enabled_accounts.setdefault(row.feature_key, set()).add(int(row.account_id))
        if account_id is not None and int(row.account_id) == int(account_id):
            account_rows[row.feature_key] = row
    installed_rows = (await db.execute(select(InstalledPlugin))).scalars().all()
    installed_enabled = {row.key: bool(row.enabled) for row in installed_rows}
    display_names = {
        row.key: (row.display_name or "").strip()
        for row in (await db.execute(select(Feature.key, Feature.display_name))).all()
        if row.key
    }
    account_names = {
        int(row.id): (row.display_name or "")
        for row in (await db.execute(select(Account.id, Account.display_name))).all()
        if row.id is not None
    }
    if account_id is not None and account_name:
        account_names.setdefault(int(account_id), account_name)

    records = list_builtin_capability_requirements()
    records.extend(await list_installed_capability_requirements(db))
    leaves = []
    record_by_key: dict[str, PluginCapabilityRequirement] = {}
    for record in records:
        record_by_key[record.key] = record
        package_enabled = record.source == "builtin" or installed_enabled.get(record.key, False)
        enabled_global = record.key in enabled_leaf_keys and package_enabled
        account_ids = sorted(enabled_accounts.get(record.key, set())) if package_enabled else []
        attachment = _attachment_from_metadata(record)
        shape = plugin_shape(record, attachment)
        leaf: dict[str, Any] = {
            "key": record.key,
            "display_name": display_names.get(record.key) or record.key,
            "attachment": attachment,
            "channel": shape.channel,
            "channel_label": shape.channel_label,
            "entry_kinds": list(shape.entry_kinds),
            "entry_labels": shape.entry_labels,
            "enabled": enabled_global,
            "enabled_global": enabled_global,
            "enabled_account_ids": account_ids,
            "package_enabled": package_enabled,
            "requires": list(record.requires),
            "warnings": list(record.warnings),
            "source_missing": record.source_missing,
            "account_state": None,
            "account_error": None,
        }
        if account_id is not None:
            row = account_rows.get(record.key)
            leaf["enabled"] = bool(row is not None and row.enabled and package_enabled)
            leaf["account_state"] = (row.state if row is not None else None) or "disabled"
            leaf["account_error"] = row.last_error if row is not None else None
        leaves.append(leaf)

    branches: dict[str, Any] = {}
    for key in platform_capabilities.ALL_MODULE_KEYS:
        desired = bool(snapshot.desired.get(key, False))
        forced_off = bool(snapshot.forced_off.get(key, False))
        global_keys = list(demand[key])
        demanders = []
        for plugin_key in global_keys:
            account_ids = sorted(enabled_accounts.get(plugin_key, set()))
            demanders.append(
                {
                    "key": plugin_key,
                    "display_name": display_names.get(plugin_key) or plugin_key,
                    "account_ids": account_ids,
                }
            )
        if account_id is None:
            scoped = global_keys
        else:
            scoped = [row["key"] for row in demanders if int(account_id) in row["account_ids"]]
        forced_reason = _forced_off_reason(key, forced_off=forced_off, profile=profile)
        blocked_code, blocked_reason = _blocked(
            key,
            desired=desired,
            forced_off_reason=forced_reason,
            demanders=demanders,
            account_id=account_id,
            names=account_names,
        )
        branches[key] = {
            "label": MODULE_LABELS[key],
            "state": snapshot.runtime.get(key, "stopped"),
            "desired": desired,
            "forced_off": forced_off,
            "forced_off_reason": forced_reason,
            "demanded_by": scoped,
            "demanded_by_global": global_keys,
            "demanders": demanders,
            "can_turn_off": bool(desired and not global_keys),
            "blocked_code": blocked_code,
            "blocked_reason": blocked_reason,
        }

    return {
        "title": TREE_TITLE,
        "subtitle": TREE_SUBTITLE,
        "scope": {
            "kind": "server" if account_id is None else "account",
            "account_id": account_id,
            "account_name": (account_names.get(int(account_id)) or None) if account_id is not None else None,
        },
        "trunk": {
            "userbot": {
                "workers": workers,
                "total": len(workers),
                "alive": sum(1 for row in workers if row.get("alive") is True),
            },
            "kill_switch": await kill_switch_service.get_enabled(db),
            "current_profile": profile.get("current_profile", "production"),
        },
        "branches": branches,
        "leaves": sorted(leaves, key=lambda item: item["key"]),
    }


__all__ = ["MODULE_LABELS", "TREE_TITLE", "build_platform_tree"]
