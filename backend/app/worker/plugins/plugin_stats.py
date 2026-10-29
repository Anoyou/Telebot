"""插件钩子级统计：每个 [账号 × 插件 × 钩子 × 事件类型] 的调用数、耗时与失败数。

设计：

- 记录在进程内存里累加（worker 子进程与主进程各自一份），不阻塞插件调用；
- 有新记录时最多每 :data:`FLUSH_DELAY_SECONDS` 秒把增量批量写入 Redis（``HINCRBY``），
  主进程 API 从 Redis 汇总，因此跨进程、跨 worker 重启都能累计；Redis 不可用时只丢失统计，
  不影响插件运行；
- 不保存消息正文；``last_error`` 只存异常类型 + 脱敏、截断后的错误摘要；
- 统计键 7 天无新记录自动过期，``DELETE /api/plugins/{key}/stats`` 可手动清零。

Redis 结构（每个插件一个 hash，便于整体读取 / 清零）::

    telepilot:plugin_stats:v1:<plugin_key>
        <account_id>|<hook>|<event_type>|calls        HINCRBY
        <account_id>|<hook>|<event_type>|ok / fail / timeout / circuit_open / skipped
        <account_id>|<hook>|<event_type>|sum_ms / max_ms
        <account_id>|<hook>|<event_type>|le_<bucket>  耗时直方图（估算 p50 / p95）
        <account_id>|<hook>|<event_type>|last_at / last_error / last_error_at / since
        <account_id>|@deprecated|<deprecation_id>|count / last_at / detail
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

STATS_KEY_PREFIX = "telepilot:plugin_stats:v1:"
STATS_TTL_SECONDS = 7 * 24 * 3600
FLUSH_DELAY_SECONDS = 5.0
LAST_ERROR_MAX_CHARS = 300
# 耗时直方图上界（毫秒）；最后一档为溢出桶 ``le_inf``。
LATENCY_BUCKETS_MS: tuple[int, ...] = (10, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000)
# 单进程内存里最多跟踪的统计行，防止异常插件制造海量事件类型撑爆内存。
MAX_TRACKED_ROWS = 2000

OUTCOME_OK = "ok"
OUTCOME_FAIL = "fail"
OUTCOME_TIMEOUT = "timeout"
OUTCOME_CIRCUIT_OPEN = "circuit_open"
OUTCOME_SKIPPED = "skipped"
OUTCOMES: tuple[str, ...] = (OUTCOME_OK, OUTCOME_FAIL, OUTCOME_TIMEOUT, OUTCOME_CIRCUIT_OPEN, OUTCOME_SKIPPED)
COUNTER_FIELDS: tuple[str, ...] = ("calls", *OUTCOMES, "sum_ms")
DEPRECATED_HOOK = "@deprecated"

RowKey = tuple[int, str, str, str]  # (account_id, plugin_key, hook, event_type)


def _bucket_name(duration_ms: int) -> str:
    for bound in LATENCY_BUCKETS_MS:
        if duration_ms <= bound:
            return f"le_{bound}"
    return "le_inf"


BUCKET_FIELDS: tuple[str, ...] = (*(f"le_{bound}" for bound in LATENCY_BUCKETS_MS), "le_inf")


def _clean_token(value: Any, *, limit: int = 48) -> str:
    text = str(value or "").strip()
    if not text:
        return "-"
    # 字段分隔符 "|" 不能出现在维度值里
    return text.replace("|", "/")[:limit]


def summarize_error(exc: BaseException | str | None) -> str:
    """把异常压成不含敏感信息的短摘要。"""

    if exc is None:
        return ""
    if isinstance(exc, BaseException):
        message = str(exc).strip()
        text = f"{type(exc).__name__}: {message}" if message else type(exc).__name__
    else:
        text = str(exc)
    try:
        from ...services.redactor import redact_text

        text = redact_text(text)
    except Exception:  # noqa: BLE001 - 脱敏失败时退回截断
        pass
    text = " ".join(text.split())
    if len(text) > LAST_ERROR_MAX_CHARS:
        text = text[: LAST_ERROR_MAX_CHARS - 1] + "…"
    return text


@dataclass
class _Row:
    counters: dict[str, int] = field(default_factory=dict)
    max_ms: int = 0
    last_at: float = 0.0
    last_error: str = ""
    last_error_at: float = 0.0

    def add(self, name: str, amount: int = 1) -> None:
        self.counters[name] = self.counters.get(name, 0) + amount


@dataclass
class _DeprecationRow:
    count: int = 0
    last_at: float = 0.0
    detail: str = ""


class PluginStatsRecorder:
    """进程内统计聚合器。``pending`` 是尚未写入 Redis 的增量，``totals`` 是本进程累计值。"""

    def __init__(self) -> None:
        self.pending: dict[RowKey, _Row] = {}
        self.totals: dict[RowKey, _Row] = {}
        self.pending_deprecations: dict[RowKey, _DeprecationRow] = {}
        self.totals_deprecations: dict[RowKey, _DeprecationRow] = {}
        self._redis_factory: Callable[[], Any] | None = None
        self._flush_task: asyncio.Task[Any] | None = None
        self.dropped_rows = 0

    # ── 配置 ──────────────────────────────────────────
    def set_redis_factory(self, factory: Callable[[], Any] | None) -> None:
        """设置 Redis 客户端来源；worker 传入账号 state.redis，主进程用 ``get_redis``。"""

        self._redis_factory = factory

    # ── 记录 ──────────────────────────────────────────
    def record(
        self,
        *,
        account_id: int | None,
        plugin_key: str,
        hook: str,
        outcome: str,
        duration_ms: float | int = 0,
        event_type: str | None = None,
        error: BaseException | str | None = None,
        now: float | None = None,
    ) -> None:
        if not plugin_key:
            return
        if outcome not in OUTCOMES:
            outcome = OUTCOME_FAIL
        key: RowKey = (
            int(account_id or 0),
            _clean_token(plugin_key, limit=96),
            _clean_token(hook),
            _clean_token(event_type),
        )
        if key not in self.totals and len(self.totals) >= MAX_TRACKED_ROWS:
            self.dropped_rows += 1
            return
        stamp = time.time() if now is None else now
        ms = max(0, int(round(float(duration_ms or 0))))
        # 熔断 / 更新中跳过不是插件自身的错误，不覆盖 last_error
        summary = summarize_error(error) if outcome in (OUTCOME_FAIL, OUTCOME_TIMEOUT) and error is not None else ""
        for store in (self.pending, self.totals):
            row = store.setdefault(key, _Row())
            row.add("calls")
            row.add(outcome)
            if outcome != OUTCOME_SKIPPED and outcome != OUTCOME_CIRCUIT_OPEN:
                row.add("sum_ms", ms)
                row.add(_bucket_name(ms))
                row.max_ms = max(row.max_ms, ms)
            row.last_at = stamp
            if summary:
                row.last_error = summary
                row.last_error_at = stamp
        self._schedule_flush()

    def record_deprecation(
        self,
        *,
        account_id: int | None,
        plugin_key: str,
        deprecation_id: str,
        detail: str | None = None,
        now: float | None = None,
    ) -> None:
        if not plugin_key or not deprecation_id:
            return
        key: RowKey = (
            int(account_id or 0),
            _clean_token(plugin_key, limit=96),
            DEPRECATED_HOOK,
            _clean_token(deprecation_id, limit=96),
        )
        stamp = time.time() if now is None else now
        text = summarize_error(detail) if detail else ""
        for store in (self.pending_deprecations, self.totals_deprecations):
            row = store.setdefault(key, _DeprecationRow())
            row.count += 1
            row.last_at = stamp
            if text:
                row.detail = text
        self._schedule_flush()

    # ── 写入 Redis ────────────────────────────────────
    def _schedule_flush(self) -> None:
        if self._redis_factory is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = self._flush_task
        if task is not None and not task.done() and task.get_loop() is loop and not loop.is_closed():
            return
        self._flush_task = loop.create_task(self._delayed_flush())

    async def _delayed_flush(self) -> None:
        try:
            await asyncio.sleep(FLUSH_DELAY_SECONDS)
            await self.flush()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - 统计失败不能影响插件
            log.debug("插件钩子统计写入 Redis 失败", exc_info=True)

    async def flush(self, redis: Any | None = None) -> int:
        """把待写增量写入 Redis，返回写入的行数。失败时增量放回待写队列。"""

        if not self.pending and not self.pending_deprecations:
            return 0
        client = redis
        if client is None and self._redis_factory is not None:
            client = self._redis_factory()
        if client is None:
            return 0
        pending, self.pending = self.pending, {}
        pending_dep, self.pending_deprecations = self.pending_deprecations, {}
        try:
            pipe = client.pipeline(transaction=False)
            touched: set[str] = set()
            for (account_id, plugin_key, hook, event_type), row in pending.items():
                redis_key = STATS_KEY_PREFIX + plugin_key
                touched.add(redis_key)
                prefix = f"{account_id}|{hook}|{event_type}|"
                for name, amount in row.counters.items():
                    if amount:
                        pipe.hincrby(redis_key, prefix + name, int(amount))
                mapping: dict[str, str] = {prefix + "last_at": f"{row.last_at:.3f}"}
                if row.last_error:
                    mapping[prefix + "last_error"] = row.last_error
                    mapping[prefix + "last_error_at"] = f"{row.last_error_at:.3f}"
                pipe.hset(redis_key, mapping=mapping)
                pipe.hsetnx(redis_key, prefix + "since", f"{row.last_at:.3f}")
            for (account_id, plugin_key, hook, dep_id), dep_row in pending_dep.items():
                redis_key = STATS_KEY_PREFIX + plugin_key
                touched.add(redis_key)
                prefix = f"{account_id}|{hook}|{dep_id}|"
                pipe.hincrby(redis_key, prefix + "count", int(dep_row.count))
                dep_mapping = {prefix + "last_at": f"{dep_row.last_at:.3f}"}
                if dep_row.detail:
                    dep_mapping[prefix + "detail"] = dep_row.detail
                pipe.hset(redis_key, mapping=dep_mapping)
            for redis_key in touched:
                pipe.expire(redis_key, STATS_TTL_SECONDS)
            await pipe.execute()
            await self._update_max(client, pending)
        except Exception:
            self._requeue(pending, pending_dep)
            raise
        return len(pending) + len(pending_dep)

    async def _update_max(self, client: Any, pending: Mapping[RowKey, _Row]) -> None:
        # Redis 没有原子 max：读改写的竞争只会让 max 偏小，统计场景可以接受。
        for (account_id, plugin_key, hook, event_type), row in pending.items():
            if not row.max_ms:
                continue
            redis_key = STATS_KEY_PREFIX + plugin_key
            field_name = f"{account_id}|{hook}|{event_type}|max_ms"
            try:
                current = int(float(await client.hget(redis_key, field_name) or 0))
            except (TypeError, ValueError):
                current = 0
            if row.max_ms > current:
                await client.hset(redis_key, field_name, str(row.max_ms))

    def _requeue(self, pending: dict[RowKey, _Row], pending_dep: dict[RowKey, _DeprecationRow]) -> None:
        for key, row in pending.items():
            target = self.pending.setdefault(key, _Row())
            for name, amount in row.counters.items():
                target.add(name, amount)
            target.max_ms = max(target.max_ms, row.max_ms)
            target.last_at = max(target.last_at, row.last_at)
            if row.last_error and row.last_error_at >= target.last_error_at:
                target.last_error = row.last_error
                target.last_error_at = row.last_error_at
        for key, dep_row in pending_dep.items():
            target_dep = self.pending_deprecations.setdefault(key, _DeprecationRow())
            target_dep.count += dep_row.count
            target_dep.last_at = max(target_dep.last_at, dep_row.last_at)
            target_dep.detail = dep_row.detail or target_dep.detail

    # ── 本进程快照（测试与诊断） ──────────────────────────
    def local_snapshot(self, plugin_key: str | None = None) -> dict[str, Any]:
        hooks = []
        for (account_id, key, hook, event_type), row in self.totals.items():
            if plugin_key and key != plugin_key:
                continue
            hooks.append(
                {
                    "account_id": account_id,
                    "plugin_key": key,
                    "hook": hook,
                    "event_type": event_type,
                    **{name: row.counters.get(name, 0) for name in COUNTER_FIELDS},
                    "max_ms": row.max_ms,
                    "last_error": row.last_error or None,
                }
            )
        deprecations = [
            {"account_id": account_id, "plugin_key": key, "id": dep_id, "count": row.count}
            for (account_id, key, _hook, dep_id), row in self.totals_deprecations.items()
            if not plugin_key or key == plugin_key
        ]
        return {"hooks": hooks, "deprecations": deprecations}

    def reset_local(self) -> None:
        self.pending.clear()
        self.totals.clear()
        self.pending_deprecations.clear()
        self.totals_deprecations.clear()
        self.dropped_rows = 0


_RECORDER = PluginStatsRecorder()


def get_recorder() -> PluginStatsRecorder:
    return _RECORDER


def record_hook(**kwargs: Any) -> None:
    """记录一次钩子调用；任何异常都吞掉（统计不能影响插件）。"""

    try:
        _RECORDER.record(**kwargs)
    except Exception:  # noqa: BLE001
        log.debug("记录插件钩子统计失败", exc_info=True)


def classify_exception(exc: BaseException) -> str:
    """把插件调用异常归类为 timeout / circuit_open / skipped / fail。"""

    message = str(exc)
    if isinstance(exc, TimeoutError) or "PLUGIN_INVOKE_TIMEOUT" in message:
        return OUTCOME_TIMEOUT
    if "PLUGIN_CIRCUIT_OPEN" in message:
        return OUTCOME_CIRCUIT_OPEN
    if "PLUGIN_UPDATE_IN_PROGRESS" in message:
        return OUTCOME_SKIPPED
    return OUTCOME_FAIL


# ─────────────────────────────────────────────────────
# 读取（主进程 API）
# ─────────────────────────────────────────────────────
def _estimate_percentile(buckets: Mapping[str, int], total: int, quantile: float) -> int | None:
    if total <= 0:
        return None
    target = max(1, int(round(total * quantile + 0.4999)))
    seen = 0
    for bound in LATENCY_BUCKETS_MS:
        seen += int(buckets.get(f"le_{bound}", 0) or 0)
        if seen >= target:
            return bound
    return None  # 落在溢出桶：调用方用 max_ms 表示


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_stats_hash(raw: Mapping[str, Any], *, account_id: int | None = None) -> dict[str, Any]:
    """把 Redis hash 解析成 API 结构（hooks / deprecations / totals）。"""

    rows: dict[tuple[int, str, str], dict[str, Any]] = {}
    deps: dict[tuple[int, str], dict[str, Any]] = {}
    for raw_field, raw_value in (raw or {}).items():
        field_name = raw_field.decode() if isinstance(raw_field, bytes) else str(raw_field)
        value = raw_value.decode() if isinstance(raw_value, bytes) else raw_value
        parts = field_name.split("|")
        if len(parts) != 4:
            continue
        try:
            acc = int(parts[0])
        except ValueError:
            continue
        if account_id is not None and acc != account_id:
            continue
        hook, dim, metric = parts[1], parts[2], parts[3]
        if hook == DEPRECATED_HOOK:
            dep = deps.setdefault((acc, dim), {"account_id": acc, "id": dim, "count": 0})
            if metric == "count":
                dep["count"] = int(_to_float(value) or 0)
            elif metric == "last_at":
                dep["last_at"] = _to_float(value)
            elif metric == "detail":
                dep["detail"] = str(value)
            continue
        row = rows.setdefault((acc, hook, dim), {"account_id": acc, "hook": hook, "event_type": dim, "_b": {}})
        if metric in COUNTER_FIELDS or metric == "max_ms":
            row[metric] = int(_to_float(value) or 0)
        elif metric in BUCKET_FIELDS:
            row["_b"][metric] = int(_to_float(value) or 0)
        elif metric in {"last_at", "last_error_at", "since"}:
            row[metric] = _to_float(value)
        elif metric == "last_error":
            row[metric] = str(value)

    hooks: list[dict[str, Any]] = []
    totals = {name: 0 for name in COUNTER_FIELDS}
    for row in rows.values():
        buckets = row.pop("_b")
        for name in COUNTER_FIELDS:
            row.setdefault(name, 0)
            totals[name] += row[name]
        row.setdefault("max_ms", 0)
        timed = sum(buckets.values())
        row["avg_ms"] = round(row["sum_ms"] / timed, 1) if timed else None
        p50 = _estimate_percentile(buckets, timed, 0.5)
        p95 = _estimate_percentile(buckets, timed, 0.95)
        row["p50_ms"] = p50 if p50 is not None or not timed else row["max_ms"]
        row["p95_ms"] = p95 if p95 is not None or not timed else row["max_ms"]
        row["failed"] = row[OUTCOME_FAIL]
        row["failure_rate"] = (
            round((row[OUTCOME_FAIL] + row[OUTCOME_TIMEOUT]) / row["calls"], 4) if row["calls"] else 0.0
        )
        row.setdefault("last_at", None)
        row.setdefault("last_error", None)
        row.setdefault("last_error_at", None)
        row.setdefault("since", None)
        hooks.append(row)
    hooks.sort(key=lambda item: (-(item.get("calls") or 0), item["hook"], item["event_type"], item["account_id"]))
    deprecations = sorted(deps.values(), key=lambda item: (-(item.get("count") or 0), item["id"], item["account_id"]))
    for dep in deprecations:
        dep.setdefault("last_at", None)
        dep.setdefault("detail", None)
    return {"hooks": hooks, "deprecations": deprecations, "totals": totals}


async def read_plugin_stats(redis: Any, plugin_key: str, *, account_id: int | None = None) -> dict[str, Any]:
    raw = await redis.hgetall(STATS_KEY_PREFIX + plugin_key)
    return parse_stats_hash(raw or {}, account_id=account_id)


async def reset_plugin_stats(redis: Any, plugin_key: str) -> bool:
    removed = await redis.delete(STATS_KEY_PREFIX + plugin_key)
    stale = [key for key in _RECORDER.totals if key[1] == plugin_key]
    for key in stale:
        _RECORDER.totals.pop(key, None)
        _RECORDER.pending.pop(key, None)
    stale_dep = [key for key in _RECORDER.totals_deprecations if key[1] == plugin_key]
    for key in stale_dep:
        _RECORDER.totals_deprecations.pop(key, None)
        _RECORDER.pending_deprecations.pop(key, None)
    return bool(removed)


__all__ = [
    "BUCKET_FIELDS",
    "COUNTER_FIELDS",
    "FLUSH_DELAY_SECONDS",
    "LATENCY_BUCKETS_MS",
    "OUTCOMES",
    "OUTCOME_CIRCUIT_OPEN",
    "OUTCOME_FAIL",
    "OUTCOME_OK",
    "OUTCOME_SKIPPED",
    "OUTCOME_TIMEOUT",
    "PluginStatsRecorder",
    "STATS_KEY_PREFIX",
    "classify_exception",
    "get_recorder",
    "parse_stats_hash",
    "read_plugin_stats",
    "record_hook",
    "reset_plugin_stats",
    "summarize_error",
]
