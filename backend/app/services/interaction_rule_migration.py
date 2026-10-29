"""交互规则数据迁移：旧动作 ``action=math10`` → 通用 ``action="module"``（B2，0.112.0 起提供，可回滚）。

为什么需要：0.2x 时代的“算数题”规则用 ``action=math10`` 保存，宿主运行时临时转换成
``module_key=math10`` 的玩法规则（``account_bot_runtime._math10_compat_module_rule``）。迁移后宿主就能在
Plugin API 2.0 删掉这条按插件名写死的分支。

等价性（golden 测试 ``test_interaction_rule_migration.py`` 守护）：

- **启动玩法 / 发奖链路**：迁移后的规则经 ``normalize_interaction_rules`` 得到的字典，与旧规则经
  ``normalize_interaction_rules`` + ``_math10_compat_module_rule`` 得到的字典逐字段相同（入口
  ``start_math10``、奖金、会话范围、启动词、触发方式、插件参数都一样），交给插件的入口与奖金不变；
- **不能证明等价的部分**（迁移后规则就是普通 ``module`` 规则，下面这些旧规则不走的通用路径会开始生效）：
  活跃会话里的群消息 / 按钮续玩投递（``_route_interaction_session_message``）、关闭命令通知插件
  ``session_close``、Event Bus 预过滤把该群算作玩法群、规则查询里显示“玩法 math10”而不是“算数题”。
  因此本脚本**不会自动运行**，默认只预演（``--dry-run``）；是否 ``--apply`` 由管理员在真群验证后决定。

存量规则保存在 ``SystemSetting[account_bot_transfer_notice:<aid>].value["rules"]``。``--apply`` 时原规则按
id 备份到 ``SystemSetting[interaction_rule_migration:math10:<aid>]``；``--rollback`` 只恢复仍保持迁移后形态的
规则（之后被编辑过的跳过并报告）。旧式“单规则”配置（没有 ``rules`` 列表、顶层 ``action=math10``）不迁移，只报告。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .. import plugin_compat

BACKUP_SETTING_PREFIX = "interaction_rule_migration:math10:"
LEGACY_COMPAT_ENTRY_KEY = "start_math10"
_VALID_SCOPES = {"chat", "user", "none"}
_VALID_TRIGGER_MODES = {"payment", "keyword", "both"}


def backup_setting_key(aid: int) -> str:
    return f"{BACKUP_SETTING_PREFIX}{int(aid)}"


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _string_list(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def is_legacy_math10_rule(rule: Any) -> bool:
    return isinstance(rule, dict) and plugin_compat.is_legacy_math10_action(rule.get("action"))


def migrate_legacy_math10_rule(rule: dict[str, Any], *, default_start_keywords: list[str] | None = None) -> dict[str, Any]:
    """把一条已保存的 ``action=math10`` 规则改写成等价的 ``action="module"`` 规则（纯函数）。

    只把旧路径里“隐式”的部分写成显式字段，其它字段原样保留：

    - ``module_start_keywords`` 为空时写入默认启动词（与 ``normalize_interaction_rules`` 的旧规则补全相同）；
    - 没有金额、触发方式为默认转账时改为 ``both``（同上）；
    - ``module_key=math10``、``module_action``（缺省 ``start_math10``）、``module_prize``（缺省用 ``math_prize``，再缺省 123）、
      ``module_session_scope``（缺省用 ``concurrency``，再缺省 chat）与 ``_math10_compat_module_rule`` 相同。
    """

    if not is_legacy_math10_rule(rule):
        return dict(rule)
    out = copy.deepcopy(rule)
    keywords = _string_list(rule.get("module_start_keywords"))
    if not keywords:
        keywords = list(default_start_keywords or plugin_compat.DEFAULT_MATH10_START_KEYWORDS)
    out["module_start_keywords"] = keywords
    amount = _int_or_none(rule.get("amount"))
    trigger_mode = str(rule.get("trigger_mode") or "payment").strip()
    if trigger_mode not in _VALID_TRIGGER_MODES:
        trigger_mode = "payment"
    if amount is None and trigger_mode == "payment" and rule.get("trigger_mode") in (None, "", "payment"):
        out["trigger_mode"] = "both"
    out["action"] = "module"
    out["module_key"] = plugin_compat.MATH10
    out["module_action"] = str(rule.get("module_action") or "").strip() or LEGACY_COMPAT_ENTRY_KEY
    module_prize = _int_or_none(rule.get("module_prize"))
    if module_prize is None or module_prize <= 0:
        math_prize = _int_or_none(rule.get("math_prize"))
        module_prize = math_prize if math_prize is not None and math_prize > 0 else 123
    out["module_prize"] = module_prize
    scope = str(rule.get("module_session_scope") or "").strip()
    if scope not in _VALID_SCOPES:
        concurrency = str(rule.get("concurrency") or "chat").strip()
        scope = concurrency if concurrency in _VALID_SCOPES else "chat"
    out["module_session_scope"] = scope
    return out


def migrated_rule_still_untouched(current: Any, migrated: dict[str, Any]) -> bool:
    """回滚前判断：当前规则是否仍是迁移脚本写入的形态（之后没被编辑过）。"""

    if not isinstance(current, dict):
        return False
    keys = ("action", "module_key", "module_action", "module_prize", "module_session_scope", "trigger_mode", "module_start_keywords")
    return all(current.get(key) == migrated.get(key) for key in keys)


@dataclass
class MigrationReport:
    dry_run: bool
    mode: str  # apply | rollback
    accounts_scanned: int = 0
    rules_migrated: list[dict[str, Any]] = field(default_factory=list)
    rules_restored: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "mode": self.mode,
            "accounts_scanned": self.accounts_scanned,
            "rules_migrated": self.rules_migrated,
            "rules_restored": self.rules_restored,
            "skipped": self.skipped,
        }


def plan_account_migration(aid: int, stored: Any, *, default_start_keywords: list[str] | None = None) -> tuple[dict[str, Any] | None, dict[str, Any], MigrationReport]:
    """为一个账号计算迁移结果（纯函数）。返回 ``(新配置或 None, 备份 {rule_id: 原规则}, 局部报告)``。"""

    report = MigrationReport(dry_run=True, mode="apply")
    if not isinstance(stored, dict):
        return None, {}, report
    rules = stored.get("rules")
    if not isinstance(rules, list) or not rules:
        if plugin_compat.is_legacy_math10_action(stored.get("action")):
            report.skipped.append({"account_id": aid, "rule_id": "legacy", "reason": "旧式单规则配置（没有 rules 列表），请在规则编辑器里重新保存后再迁移"})
        return None, {}, report
    new_rules: list[Any] = []
    backup: dict[str, Any] = {}
    for index, rule in enumerate(rules):
        if not is_legacy_math10_rule(rule):
            new_rules.append(rule)
            continue
        rule_id = str(rule.get("id") or f"rule-{index + 1}")
        migrated = migrate_legacy_math10_rule(rule, default_start_keywords=default_start_keywords)
        backup[rule_id] = copy.deepcopy(rule)
        new_rules.append(migrated)
        report.rules_migrated.append(
            {
                "account_id": aid,
                "rule_id": rule_id,
                "name": rule.get("name"),
                "module_action": migrated["module_action"],
                "module_prize": migrated["module_prize"],
                "module_session_scope": migrated["module_session_scope"],
                "trigger_mode": migrated.get("trigger_mode"),
            }
        )
    if not backup:
        return None, {}, report
    new_stored = dict(stored)
    new_stored["rules"] = new_rules
    return new_stored, backup, report


def plan_account_rollback(aid: int, stored: Any, backup_value: Any) -> tuple[dict[str, Any] | None, MigrationReport]:
    report = MigrationReport(dry_run=True, mode="rollback")
    if not isinstance(stored, dict) or not isinstance(backup_value, dict):
        return None, report
    originals = backup_value.get("rules")
    if not isinstance(originals, dict) or not originals:
        return None, report
    rules = stored.get("rules")
    if not isinstance(rules, list):
        return None, report
    changed = False
    new_rules: list[Any] = []
    seen: set[str] = set()
    for index, rule in enumerate(rules):
        rule_id = str(rule.get("id") or f"rule-{index + 1}") if isinstance(rule, dict) else ""
        original = originals.get(rule_id)
        if not isinstance(original, dict):
            new_rules.append(rule)
            continue
        seen.add(rule_id)
        expected = migrate_legacy_math10_rule(original)
        if migrated_rule_still_untouched(rule, expected):
            new_rules.append(copy.deepcopy(original))
            report.rules_restored.append({"account_id": aid, "rule_id": rule_id, "name": original.get("name")})
            changed = True
        else:
            new_rules.append(rule)
            report.skipped.append({"account_id": aid, "rule_id": rule_id, "reason": "迁移后已被编辑，保持现状"})
    for rule_id in sorted(set(originals) - seen):
        report.skipped.append({"account_id": aid, "rule_id": rule_id, "reason": "规则已删除，无法恢复"})
    if not changed:
        return None, report
    new_stored = dict(stored)
    new_stored["rules"] = new_rules
    return new_stored, report


def compat_entry_conflict() -> str | None:
    """安全闸：已装的 math10 若把 ``start_math10`` 声明成正式入口，入口声明可能改变奖金 / 参与策略推断，拒绝迁移。"""

    try:
        from .account_bot_service import declared_module_entry_manifest

        if declared_module_entry_manifest(plugin_compat.MATH10, LEGACY_COMPAT_ENTRY_KEY) is not None:
            return f"已安装的 math10 插件声明了入口 {LEGACY_COMPAT_ENTRY_KEY}，迁移前请人工核对奖金与参与策略"
    except Exception:  # noqa: BLE001
        return None
    return None


async def run_migration(*, dry_run: bool = True, rollback: bool = False) -> MigrationReport:
    from sqlalchemy import select
    from sqlalchemy.orm.attributes import flag_modified

    from ..db.base import AsyncSessionLocal
    from ..db.models.system import SystemSetting
    from .account_bot_service import TRANSFER_NOTICE_SETTING_PREFIX

    report = MigrationReport(dry_run=dry_run, mode="rollback" if rollback else "apply")
    conflict = None if rollback else compat_entry_conflict()
    if conflict:
        report.skipped.append({"account_id": None, "rule_id": None, "reason": conflict})
        return report
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(select(SystemSetting).where(SystemSetting.key.like(f"{TRANSFER_NOTICE_SETTING_PREFIX}%")))
        ).scalars().all()
        for row in rows:
            try:
                aid = int(str(row.key)[len(TRANSFER_NOTICE_SETTING_PREFIX):])
            except ValueError:
                continue
            report.accounts_scanned += 1
            backup_row = await db.get(SystemSetting, backup_setting_key(aid))
            if rollback:
                new_value, part = plan_account_rollback(aid, row.value, backup_row.value if backup_row else None)
                report.rules_restored.extend(part.rules_restored)
                report.skipped.extend(part.skipped)
                if new_value is not None and not dry_run:
                    row.value = new_value
                    flag_modified(row, "value")
                    if backup_row is not None:
                        await db.delete(backup_row)
                continue
            new_value, backup, part = plan_account_migration(aid, row.value)
            report.rules_migrated.extend(part.rules_migrated)
            report.skipped.extend(part.skipped)
            if new_value is None or dry_run:
                continue
            previous = dict(backup_row.value.get("rules") or {}) if backup_row is not None and isinstance(backup_row.value, dict) else {}
            previous.update(backup)
            payload = {"migrated_at": datetime.now(UTC).isoformat(), "rules": previous}
            if backup_row is None:
                db.add(SystemSetting(key=backup_setting_key(aid), value=payload))
            else:
                backup_row.value = payload
                flag_modified(backup_row, "value")
            row.value = new_value
            flag_modified(row, "value")
        if not dry_run:
            await db.commit()
    return report


__all__ = [
    "BACKUP_SETTING_PREFIX",
    "LEGACY_COMPAT_ENTRY_KEY",
    "MigrationReport",
    "backup_setting_key",
    "compat_entry_conflict",
    "is_legacy_math10_rule",
    "migrate_legacy_math10_rule",
    "migrated_rule_still_untouched",
    "plan_account_migration",
    "plan_account_rollback",
    "run_migration",
]
