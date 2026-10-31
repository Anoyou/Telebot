"""资金类插件的只读卡片与「转账通知以插件结果为准」开关状态（第 22 轮）。

两张宿主内置卡片（slot ``plugin.overview``，只出现在插件详情，不放概览），都只读、没有按钮：

- ``_host.payment_source``「转账识别（只读）」：声明了 ``payment_sources`` 的插件（官方
  ``payment-source-cn-transfer``）。数据来自 :mod:`payment_source_ledger`：今日 / 近 7 日识别笔数与合计、
  新旧解析对照一致 / 不一致 / 插件失败次数、最近几笔识别记录，以及开关当前状态。
- ``_host.funds``「资金往来（只读）」：会发游戏币的插件（``permissions`` 含 ``payout``，或
  ``requires_platform_capabilities`` 含 ``ledger``：红包、彩票、各类发奖游戏）。数据来自资金台账
  （``action_event``，与「资金台账」页同一口径）：今日 / 近 7 日收到与发出的笔数和合计、最近几笔，
  以及开关状态与全部转账通知插件的近 7 日对照结果（这些插件收到的转账正是由转账通知识别来的）。

旧服务器没有这两张卡，客户端按卡片列表渲染，自然不显示；旧插件不需要任何改动。
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.action_event import ActionEvent
from ..db.models.feature import Feature
from ..db.models.plugin import InstalledPlugin
from ..db.models.system import SystemSetting
from . import payment_source_ledger, payment_sources

PAYMENT_SOURCE_CARD_ID = "_host.payment_source"
FUNDS_CARD_ID = "_host.funds"
HOST_CARD_IDS = (PAYMENT_SOURCE_CARD_ID, FUNDS_CARD_ID)
SETTING_KEY = "log_retention.payment_source_plugin_authoritative"
RECENT_ROWS = 5

_INACTIVE_REASONS = {
    "disabled": "插件已停用（全局关闭），转账通知只用旧解析",
    "orphan": "插件来源已失效，转账通知只用旧解析",
    "signature_failed": "插件签名校验失败，转账通知只用旧解析",
    "signature_unknown": "插件未签名、服务器不允许未签名插件，转账通知只用旧解析",
    "install_error": "插件安装出错，转账通知只用旧解析",
    "not_loaded": "插件没有正确导出解析函数，转账通知只用旧解析",
}

_VERDICT_LABELS = {
    payment_sources.VERDICT_AGREE: ("一致", "success"),
    payment_sources.VERDICT_MISMATCH: ("不一致", "warning"),
    payment_sources.VERDICT_PLUGIN_FAILED: ("插件失败", "danger"),
}


# ─────────────────────────────────────────────────────
# 插件分类
# ─────────────────────────────────────────────────────


def _manifest_sources(feature: Any, installed: Any) -> list[Mapping[str, Any]]:
    from .plugin_ui_cards import _plugin_json_from_dir

    sources: list[Mapping[str, Any]] = []
    manifest_json = getattr(installed, "manifest_json", None)
    if isinstance(manifest_json, Mapping):
        sources.append(manifest_json)
    sources.append(_plugin_json_from_dir(installed))
    feature_manifest = getattr(feature, "manifest", None)
    if isinstance(feature_manifest, Mapping):
        sources.append(feature_manifest)
    return sources


def is_payment_source(feature: Any, installed: Any) -> bool:
    if installed is not None and payment_sources._declaration_from_plugin_json(
        getattr(installed, "installed_path", None), getattr(installed, "manifest_json", None)
    ):
        return True
    return any(payment_sources.declared_source_ids(src.get(payment_sources.MANIFEST_KEY)) for src in _manifest_sources(feature, installed))


def is_funds_plugin(feature: Any, installed: Any) -> bool:
    """会经平台发游戏币的插件：``payout`` 权限或依赖 ``ledger`` 平台能力。"""

    for src in _manifest_sources(feature, installed):
        permissions = src.get("permissions")
        if isinstance(permissions, list) and "payout" in {str(item) for item in permissions}:
            return True
        caps = src.get("requires_platform_capabilities")
        if isinstance(caps, list) and "ledger" in {str(item) for item in caps}:
            return True
    return False


async def host_cards_for(db: AsyncSession, plugin_key: str) -> list[dict[str, Any]]:
    feature = await db.get(Feature, plugin_key)
    installed = await db.get(InstalledPlugin, plugin_key)
    cards: list[dict[str, Any]] = []
    if is_payment_source(feature, installed):
        cards.append(payment_source_card())
    elif is_funds_plugin(feature, installed):
        cards.append(funds_card())
    return cards


# ─────────────────────────────────────────────────────
# 开关与来源状态
# ─────────────────────────────────────────────────────


async def read_authoritative(db: AsyncSession) -> bool:
    row = await db.get(SystemSetting, "log_retention")
    raw = row.value if row is not None and isinstance(row.value, dict) else {}
    return bool(raw.get("payment_source_plugin_authoritative", False))


async def timezone_name(db: AsyncSession) -> str:
    row = await db.get(SystemSetting, "timezone")
    value = row.value if row is not None else None
    if isinstance(value, dict):
        return str(value.get("value") or value.get("timezone") or payment_source_ledger.DEFAULT_TIMEZONE)
    return str(value or payment_source_ledger.DEFAULT_TIMEZONE)


async def describe_sources(db: AsyncSession) -> list[dict[str, Any]]:
    """已安装且声明了 ``payment_sources`` 的插件，以及它们此刻是否真正参与转账通知解析。"""

    rows = list((await db.execute(select(InstalledPlugin))).scalars())
    loaded = {source.key for source in await payment_sources.load_payment_sources()}
    out: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda item: str(item.key)):
        ids = payment_sources._declaration_from_plugin_json(row.installed_path, row.manifest_json)
        if not ids:
            continue
        key = str(row.key)
        reason = payment_sources._block_reason(row)
        if reason is None and key not in loaded:
            reason = "not_loaded"
        feature = await db.get(Feature, key)
        out.append(
            {
                "plugin_key": key,
                "display_name": str(getattr(feature, "display_name", "") or key),
                "version": str(row.version or ""),
                "source_ids": list(ids),
                "active": reason is None,
                "inactive_reason": None if reason is None else _INACTIVE_REASONS.get(reason, reason),
            }
        )
    return out


def switch_view(authoritative: bool, any_active: bool) -> dict[str, Any]:
    if not authoritative:
        return {
            "authoritative": False,
            "effective": "legacy",
            "label": "以旧解析为准（插件只做对照）",
            "tone": "neutral",
        }
    if any_active:
        return {
            "authoritative": True,
            "effective": "plugin",
            "label": "以插件为准（生效中）",
            "tone": "accent",
        }
    return {
        "authoritative": True,
        "effective": "legacy",
        "label": "已打开，但没有可用的转账通知插件，仍按旧解析",
        "tone": "warning",
    }


async def status(db: AsyncSession) -> dict[str, Any]:
    authoritative = await read_authoritative(db)
    sources = await describe_sources(db)
    switch = switch_view(authoritative, any(item["active"] for item in sources))
    return {
        "authoritative": authoritative,
        "effective": switch["effective"],
        "label": switch["label"],
        "setting_key": SETTING_KEY,
        "sources": sources,
    }


# ─────────────────────────────────────────────────────
# 卡片声明
# ─────────────────────────────────────────────────────


def payment_source_card() -> dict[str, Any]:
    return {
        "id": PAYMENT_SOURCE_CARD_ID,
        "slot": "plugin.overview",
        "title": "转账识别（只读）",
        "subtitle": "插件从可信 Bot 的转账通知里读出的付款人、收款人和金额。只显示，不改任何数据；保留最近 7 天。",
        "source": {"read": "host.payment_source@1"},
        "refresh_seconds": None,
        "host": True,
        "blocks": [
            {
                "type": "kv",
                "items": [
                    {"label": "转账通知判定", "path": "switch.label", "tone_path": "switch.tone"},
                    {"label": "这个插件", "path": "source.label", "tone_path": "source.tone"},
                ],
            },
            {
                "type": "stats",
                "title": "识别到的转账",
                "items": [
                    {"label": "今日笔数", "path": "today.recognized", "format": "number"},
                    {"label": "今日合计", "path": "today.amount", "format": "number"},
                    {"label": "近 7 日笔数", "path": "week.recognized", "format": "number"},
                    {"label": "近 7 日合计", "path": "week.amount", "format": "number"},
                ],
            },
            {
                "type": "stats",
                "title": "新旧解析对照（近 7 日，只算识别出转账的消息）",
                "items": [
                    {"label": "一致", "path": "week.agree", "format": "number", "tone_path": "tones.agree"},
                    {"label": "不一致", "path": "week.mismatch", "format": "number", "tone_path": "tones.mismatch"},
                    {"label": "插件失败", "path": "week.plugin_failed", "format": "number", "tone_path": "tones.plugin_failed"},
                ],
            },
            {
                "type": "table",
                "id": "recent",
                "title": "最近识别",
                "rows_path": "recent",
                "empty": "近 7 日还没有识别到转账通知。",
                "columns": [
                    {"key": "at", "label": "时间", "format": "relative_time"},
                    {"key": "payer_name", "label": "付款人"},
                    {"key": "receiver_name", "label": "收款人"},
                    {"key": "amount_text", "label": "金额"},
                    {"key": "verdict_label", "label": "对照", "tone_key": "verdict_tone"},
                    {"key": "used_label", "label": "采用"},
                ],
            },
        ],
    }


def funds_card() -> dict[str, Any]:
    return {
        "id": FUNDS_CARD_ID,
        "slot": "plugin.overview",
        "title": "资金往来（只读）",
        "subtitle": "这个插件经平台记账的游戏币收支（与「资金台账」同一口径）。只显示，不改任何数据，也不影响群内结算。",
        "source": {"read": "host.funds@1"},
        "refresh_seconds": None,
        "host": True,
        "blocks": [
            {
                "type": "stats",
                "title": "今日",
                "items": [
                    {"label": "收到笔数", "path": "today.in_count", "format": "number"},
                    {"label": "收到合计", "path": "today.income", "format": "number"},
                    {"label": "发出笔数", "path": "today.out_count", "format": "number"},
                    {"label": "发出合计", "path": "today.payout", "format": "number"},
                ],
            },
            {
                "type": "stats",
                "title": "近 7 日",
                "items": [
                    {"label": "收到笔数", "path": "week.in_count", "format": "number"},
                    {"label": "收到合计", "path": "week.income", "format": "number"},
                    {"label": "发出笔数", "path": "week.out_count", "format": "number"},
                    {"label": "发出合计", "path": "week.payout", "format": "number"},
                ],
            },
            {
                "type": "kv",
                "items": [
                    {"label": "转账通知判定", "path": "switch.label", "tone_path": "switch.tone"},
                    {"label": "转账识别对照（近 7 日）", "path": "verdicts.label", "tone_path": "verdicts.tone"},
                ],
            },
            {
                "type": "table",
                "id": "recent",
                "title": "最近几笔",
                "rows_path": "recent",
                "empty": "近 7 日没有记账记录。",
                "columns": [
                    {"key": "at", "label": "时间", "format": "relative_time"},
                    {"key": "direction_label", "label": "方向", "tone_key": "direction_tone"},
                    {"key": "counterpart", "label": "对方"},
                    {"key": "amount", "label": "金额", "format": "number"},
                    {"key": "chat", "label": "群"},
                ],
            },
        ],
    }


# ─────────────────────────────────────────────────────
# 取数（只读）
# ─────────────────────────────────────────────────────


def _count_tone(value: int, tone: str) -> str:
    return tone if value else "neutral"


async def _ledger_summaries(db: AsyncSession, keys: list[str], tz_name: str, now: float) -> dict[str, dict[str, Any]]:
    from ..redis_client import get_redis

    redis = get_redis()
    out: dict[str, dict[str, Any]] = {}
    for key in keys:
        out[key] = await payment_source_ledger.read_summary(redis, key, timezone_name=tz_name, now=now)
    return out


async def payment_source_card_data(db: AsyncSession, plugin_key: str, *, now: float | None = None) -> dict[str, Any]:
    stamp = time.time() if now is None else float(now)
    authoritative = await read_authoritative(db)
    sources = await describe_sources(db)
    mine = next((item for item in sources if item["plugin_key"] == plugin_key), None)
    switch = switch_view(authoritative, any(item["active"] for item in sources))
    if mine is not None and mine["active"]:
        source = {
            "label": "正在解析转账通知（以插件为准）" if authoritative else "正在参与对照（结果不用于结算）",
            "tone": "success",
        }
    else:
        source = {"label": (mine or {}).get("inactive_reason") or "未参与转账通知解析", "tone": "warning"}
    summary = (await _ledger_summaries(db, [plugin_key], await timezone_name(db), stamp))[plugin_key]
    week = summary["week"]
    recent = []
    for row in summary["recent"][:RECENT_ROWS]:
        label, tone = _VERDICT_LABELS.get(str(row.get("verdict") or ""), ("-", "neutral"))
        amount = row.get("amount")
        unit = str(row.get("unit") or "").strip()
        amount_text = "-" if amount is None else f"{int(amount):,}{(' ' + unit) if unit else ''}"
        if not row.get("recognized_by_plugin"):
            amount_text = f"{amount_text}（插件未识别）"
        recent.append(
            {
                "at": row.get("at"),
                "payer_name": row.get("payer_name") or "-",
                "receiver_name": row.get("receiver_name") or "-",
                "amount_text": amount_text,
                "verdict_label": label,
                "verdict_tone": tone,
                "used_label": "插件" if row.get("used") == "plugin" else "旧解析",
            }
        )
    return {
        "switch": switch,
        "source": source,
        "today": summary["today"],
        "week": week,
        "tones": {
            "agree": _count_tone(int(week.get("agree") or 0), "success"),
            "mismatch": _count_tone(int(week.get("mismatch") or 0), "warning"),
            "plugin_failed": _count_tone(int(week.get("plugin_failed") or 0), "danger"),
        },
        "recent": recent,
    }


def _number(value: Any) -> int | float:
    amount = value if isinstance(value, Decimal) else Decimal(str(value or 0))
    return int(amount) if amount == amount.to_integral_value() else float(amount)


async def _ledger_window(db: AsyncSession, plugin_key: str, since: datetime) -> dict[str, Any]:
    from .ledger_service import LedgerFilters, _ledger_row_conditions, resolve_summary_filters

    filters = resolve_summary_filters(LedgerFilters(plugin_key=plugin_key, since=since))
    stmt = (
        select(ActionEvent.direction, func.sum(func.abs(ActionEvent.amount)), func.count())
        .where(*_ledger_row_conditions(filters))
        .group_by(ActionEvent.direction)
    )
    out = {"in_count": 0, "income": 0, "out_count": 0, "payout": 0}
    for direction, total, count in (await db.execute(stmt)).all():
        if direction == "in":
            out["in_count"] += int(count or 0)
            out["income"] = _number(Decimal(str(out["income"])) + Decimal(str(total or 0)))
        else:
            out["out_count"] += int(count or 0)
            out["payout"] = _number(Decimal(str(out["payout"])) + Decimal(str(total or 0)))
    return out


async def funds_card_data(db: AsyncSession, plugin_key: str, *, now: float | None = None) -> dict[str, Any]:
    from .ledger_service import LedgerFilters, list_ledger_entries

    stamp = time.time() if now is None else float(now)
    tz_name = await timezone_name(db)
    today_start = datetime.fromtimestamp(payment_source_ledger.day_start(stamp, tz_name), UTC)
    week_start = datetime.fromtimestamp(stamp - 7 * 24 * 3600, UTC)
    today = await _ledger_window(db, plugin_key, today_start)
    week = await _ledger_window(db, plugin_key, week_start)
    entries = await list_ledger_entries(db, LedgerFilters(plugin_key=plugin_key, since=week_start, limit=RECENT_ROWS))
    recent = []
    for entry in entries:
        incoming = entry.direction == "in"
        counterpart = entry.payer_name if incoming else (entry.receiver_name or entry.receiver_username)
        created = entry.created_at if entry.created_at.tzinfo else entry.created_at.replace(tzinfo=UTC)
        recent.append(
            {
                "at": int(created.timestamp()),
                "direction_label": "收到" if incoming else "发出",
                "direction_tone": "success" if incoming else "accent",
                "counterpart": counterpart or "-",
                "amount": _number(entry.amount),
                "chat": entry.chat_title or (str(entry.chat_id) if entry.chat_id is not None else "-"),
            }
        )
    authoritative = await read_authoritative(db)
    sources = await describe_sources(db)
    switch = switch_view(authoritative, any(item["active"] for item in sources))
    if sources:
        summaries = await _ledger_summaries(db, [item["plugin_key"] for item in sources], tz_name, stamp)
        agree = sum(int(item["week"].get("agree") or 0) for item in summaries.values())
        mismatch = sum(int(item["week"].get("mismatch") or 0) for item in summaries.values())
        failed = sum(int(item["week"].get("plugin_failed") or 0) for item in summaries.values())
        verdicts = {
            "label": f"一致 {agree} · 不一致 {mismatch} · 插件失败 {failed}",
            "tone": "danger" if failed else ("warning" if mismatch else ("success" if agree else "neutral")),
        }
    else:
        verdicts = {"label": "没有安装转账通知插件，按旧解析", "tone": "neutral"}
    return {"switch": switch, "verdicts": verdicts, "today": today, "week": week, "recent": recent}


async def card_data(db: AsyncSession, card_id: str, plugin_key: str) -> dict[str, Any]:
    if card_id == PAYMENT_SOURCE_CARD_ID:
        return await payment_source_card_data(db, plugin_key)
    if card_id == FUNDS_CARD_ID:
        return await funds_card_data(db, plugin_key)
    raise KeyError(card_id)


def host_card(card_id: str) -> dict[str, Any] | None:
    if card_id == PAYMENT_SOURCE_CARD_ID:
        return payment_source_card()
    if card_id == FUNDS_CARD_ID:
        return funds_card()
    return None


__all__ = [
    "FUNDS_CARD_ID",
    "HOST_CARD_IDS",
    "PAYMENT_SOURCE_CARD_ID",
    "SETTING_KEY",
    "card_data",
    "describe_sources",
    "funds_card",
    "funds_card_data",
    "host_card",
    "host_cards_for",
    "is_funds_plugin",
    "is_payment_source",
    "payment_source_card",
    "payment_source_card_data",
    "read_authoritative",
    "status",
    "switch_view",
]
