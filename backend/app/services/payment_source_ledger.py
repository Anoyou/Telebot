"""转账通知识别记录（第 22 轮）：给资金类插件详情的只读卡片用。

每次 payment_sources 双跑（可信 Bot 白名单之后、有插件来源时）按插件记一笔：

- 小时桶计数（Redis hash，字段 ``<小时起点 epoch>|<指标>``）：``recognized``（插件识别为转账通知的条数）、
  ``amount``（这些通知的金额合计）、``agree`` / ``mismatch`` / ``plugin_failed``（双跑对照结论，
  ``agree`` 只算至少一边识别出转账的消息，两边都判断“不是转账通知”的普通消息不计）、``used_plugin``
  （以插件结果为准的条数）。按小时分桶，读取时再按服务器时区切“今日”，不用在写入路径查时区。
- 最近 :data:`RECENT_LIMIT` 条识别记录（Redis list，JSON）：时间、账号、会话、付款人、收款人、金额、单位、
  对照结论、采用了哪边。

只做记录与读取：不参与结算，不影响解析结果；Redis 不可用时静默丢弃（与 plugin_stats 同口径）。
写入在后台任务里进行，不拖慢转账通知处理。数据保留 :data:`RETENTION_SECONDS`（8 天，够算近 7 日）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Mapping
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

KEY_PREFIX = "telepilot:payment_source_ledger:v1:"
RECENT_SUFFIX = ":recent"
RECENT_LIMIT = 20
RETENTION_SECONDS = 8 * 24 * 3600
HOUR = 3600
METRICS: tuple[str, ...] = ("recognized", "amount", "agree", "mismatch", "plugin_failed", "used_plugin")
DEFAULT_TIMEZONE = "Asia/Shanghai"

_PENDING: set[asyncio.Task[Any]] = set()


def _hour_bucket(ts: float) -> int:
    return int(ts // HOUR) * HOUR


def _int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def build_observation(outcome: Any, meta: Mapping[str, Any], *, now: float | None = None) -> list[dict[str, Any]]:
    """双跑结果 → 每个插件要记的增量与（可选）一条识别记录。纯函数，便于测试。

    返回 ``[{"plugin_key", "counters": {...}, "recent": {...} | None}]``。
    """

    from . import payment_sources

    stamp = time.time() if now is None else float(now)
    verdict = str(getattr(outcome, "verdict", "") or "")
    if verdict == payment_sources.VERDICT_NO_SOURCE:
        return []
    out: dict[str, dict[str, Any]] = {}

    def entry(key: str) -> dict[str, Any]:
        return out.setdefault(key, {"plugin_key": key, "counters": {}, "recent": None})

    for call in getattr(outcome, "calls", None) or []:
        if getattr(call, "error", None) is not None:
            entry(str(call.key))["counters"]["plugin_failed"] = 1
    primary = getattr(outcome, "plugin_key", None)
    if not primary or verdict == payment_sources.VERDICT_PLUGIN_FAILED:
        return list(out.values())
    plugin = getattr(outcome, "plugin", None)
    legacy = payment_sources.settlement_view(getattr(outcome, "legacy", None))
    if plugin is None and legacy is None:
        # 两边都认为不是转账通知（打开“以插件为准”后可信 Bot 的普通消息也会走到这里），不计数。
        return list(out.values())
    item = entry(str(primary))
    counters = item["counters"]
    if verdict in (payment_sources.VERDICT_AGREE, payment_sources.VERDICT_MISMATCH):
        counters[verdict] = 1
    if getattr(outcome, "used_plugin", False):
        counters["used_plugin"] = 1
    if plugin is not None:
        counters["recognized"] = 1
        counters["amount"] = _int(plugin.get("amount"))
    shown = plugin if plugin is not None else legacy or {}
    item["recent"] = {
        "at": int(stamp),
        "account_id": meta.get("account_id"),
        "chat_id": meta.get("chat_id"),
        "payer_name": shown.get("payer_name"),
        "receiver_name": shown.get("receiver_name"),
        "amount": shown.get("amount"),
        "legacy_amount": None if legacy is None else legacy.get("amount"),
        "unit": getattr(outcome, "plugin_unit", None),
        "recognized_by_plugin": plugin is not None,
        "verdict": verdict,
        "used": "plugin" if getattr(outcome, "used_plugin", False) else "legacy",
    }
    return list(out.values())


async def write_observation(redis: Any, items: list[dict[str, Any]], *, now: float | None = None) -> None:
    stamp = time.time() if now is None else float(now)
    bucket = _hour_bucket(stamp)
    for item in items:
        key = KEY_PREFIX + str(item["plugin_key"])
        counters = {name: int(value) for name, value in (item.get("counters") or {}).items() if name in METRICS}
        for name, value in counters.items():
            await redis.hincrby(key, f"{bucket}|{name}", value)
        if counters:
            await redis.expire(key, RETENTION_SECONDS)
        recent = item.get("recent")
        if recent:
            recent_key = key + RECENT_SUFFIX
            await redis.lpush(recent_key, json.dumps(recent, ensure_ascii=False, default=str))
            await redis.ltrim(recent_key, 0, RECENT_LIMIT - 1)
            await redis.expire(recent_key, RETENTION_SECONDS)


def observe(outcome: Any, meta: Mapping[str, Any]) -> None:
    """在后台记一次双跑结果；任何异常都只写 debug 日志。"""

    try:
        items = build_observation(outcome, meta)
    except Exception:  # noqa: BLE001 - 记录失败不影响转账通知
        log.debug("payment source ledger build failed", exc_info=True)
        return
    if not items:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    async def _write() -> None:
        from ..redis_client import get_redis

        try:
            await write_observation(get_redis(), items)
        except Exception:  # noqa: BLE001
            log.debug("payment source ledger write failed", exc_info=True)

    task = loop.create_task(_write(), name="payment-source-ledger")
    _PENDING.add(task)
    task.add_done_callback(_PENDING.discard)


def day_start(now: float, timezone_name: str | None) -> float:
    try:
        tz = ZoneInfo(str(timezone_name or DEFAULT_TIMEZONE))
    except Exception:  # noqa: BLE001
        tz = ZoneInfo(DEFAULT_TIMEZONE)
    local = datetime.fromtimestamp(now, tz)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def summarize(
    raw: Mapping[Any, Any],
    recent_raw: list[Any],
    *,
    now: float,
    today_start: float,
) -> dict[str, Any]:
    """Redis 原始数据 → 今日 / 近 7 日汇总 + 最近记录（近 7 日内）。"""

    week_start = now - 7 * 24 * 3600
    today = {name: 0 for name in METRICS}
    week = {name: 0 for name in METRICS}
    for raw_field, raw_value in (raw or {}).items():
        field_name = raw_field.decode() if isinstance(raw_field, bytes) else str(raw_field)
        bucket_text, _, metric = field_name.partition("|")
        if metric not in METRICS:
            continue
        try:
            bucket = int(bucket_text)
        except ValueError:
            continue
        value = _int(raw_value.decode() if isinstance(raw_value, bytes) else raw_value)
        # 小时桶整体落在窗口里才算；“今日”按服务器时区的 0 点起算（整点时区精确，半点时区误差 ≤30 分钟）。
        if bucket + HOUR > week_start:
            week[metric] += value
        if bucket + HOUR > today_start:
            today[metric] += value
    recent: list[dict[str, Any]] = []
    for item in recent_raw or []:
        text = item.decode() if isinstance(item, bytes) else str(item)
        try:
            row = json.loads(text)
        except (TypeError, ValueError):
            continue
        if not isinstance(row, dict) or _int(row.get("at")) < week_start:
            continue
        recent.append(row)
    return {"today": today, "week": week, "recent": recent}


async def read_summary(redis: Any, plugin_key: str, *, timezone_name: str | None, now: float | None = None) -> dict[str, Any]:
    stamp = time.time() if now is None else float(now)
    key = KEY_PREFIX + plugin_key
    raw = await redis.hgetall(key)
    recent_raw = await redis.lrange(key + RECENT_SUFFIX, 0, RECENT_LIMIT - 1)
    data = summarize(raw or {}, list(recent_raw or []), now=stamp, today_start=day_start(stamp, timezone_name))
    # 顺手清掉 8 天前的小时桶（hash 整体 TTL 会被持续写入续期）。
    stale_before = stamp - RETENTION_SECONDS
    stale = []
    for raw_field in (raw or {}):
        field_name = raw_field.decode() if isinstance(raw_field, bytes) else str(raw_field)
        bucket_text = field_name.partition("|")[0]
        if bucket_text.isdigit() and int(bucket_text) < stale_before:
            stale.append(field_name)
    if stale:
        try:
            await redis.hdel(key, *stale)
        except Exception:  # noqa: BLE001
            log.debug("payment source ledger prune failed", exc_info=True)
    return data


__all__ = [
    "KEY_PREFIX",
    "METRICS",
    "RECENT_LIMIT",
    "RETENTION_SECONDS",
    "build_observation",
    "day_start",
    "observe",
    "read_summary",
    "summarize",
    "write_observation",
]
