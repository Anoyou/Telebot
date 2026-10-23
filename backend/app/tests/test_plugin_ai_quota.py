from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy.dialects import postgresql

from app.services import plugin_ai_quota


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, int] = {}
        self.hashes: dict[str, dict[str, int]] = {}
        self.zsets: dict[str, dict[str, int]] = {}
        self.lock = asyncio.Lock()

    async def ping(self) -> bool:
        return True

    async def eval(self, script: str, numkeys: int, *args):
        keys = list(args[:numkeys])
        argv = list(args[numkeys:])
        async with self.lock:
            if "minute_used" in script:
                minute_key, minute_amount_key, daily_key = keys
                estimate, per_minute, daily = map(int, argv[:3])
                now_ms = int(argv[5])
                window_ms = int(argv[6])
                reservation_id = str(argv[7])
                zset = self.zsets.setdefault(minute_key, {})
                amounts = self.hashes.setdefault(minute_amount_key, {})
                for rid, score in list(zset.items()):
                    if score <= now_ms - window_ms:
                        zset.pop(rid, None)
                        amounts.pop(rid, None)
                minute_used = sum(amounts.get(rid, 0) for rid in zset)
                daily_used = self.values.get(daily_key, 0)
                if per_minute > 0 and minute_used + estimate > per_minute:
                    return [0, "per_minute", minute_used, per_minute]
                if daily > 0 and daily_used + estimate > daily:
                    return [0, "daily", daily_used, daily]
                zset[reservation_id] = now_ms
                amounts[reservation_id] = estimate
                self.values[minute_key] = minute_used + estimate
                self.values[daily_key] = daily_used + estimate
                return [1, "ok", self.values[minute_key], self.values[daily_key]]

            delta = int(argv[0])
            reservation_id = str(argv[1])
            minute_key, minute_amount_key, daily_key = keys
            current = self.hashes.setdefault(minute_amount_key, {}).get(reservation_id)
            if current is not None:
                next_amount = current + delta
                if next_amount <= 0:
                    self.hashes[minute_amount_key].pop(reservation_id, None)
                    self.zsets.setdefault(minute_key, {}).pop(reservation_id, None)
                else:
                    self.hashes[minute_amount_key][reservation_id] = next_amount
                self.values[minute_key] = sum(
                    self.hashes[minute_amount_key].get(rid, 0)
                    for rid in self.zsets.setdefault(minute_key, {})
                )
            if daily_key in self.values:
                self.values[daily_key] = max(0, self.values.get(daily_key, 0) + delta)
            return [1]


class _FrozenDateTime(datetime):
    current: datetime = datetime(2026, 1, 1, 23, 59, 50, tzinfo=UTC)

    @classmethod
    def now(cls, tz=None):  # noqa: ANN001
        if tz is None:
            return cls.current
        return cls.current.astimezone(tz)


@pytest.mark.asyncio
async def test_acquire_uses_redis_pre_reservation_for_concurrent_limit(monkeypatch) -> None:
    fake = _FakeRedis()

    async def _limits(_plugin_key: str):
        return {"per_minute": 100, "daily": 1000}

    async def _noop_usage(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(plugin_ai_quota, "get_redis", lambda: fake)
    monkeypatch.setattr(plugin_ai_quota, "_load_quota_limits", _limits)
    monkeypatch.setattr(plugin_ai_quota, "_write_quota_error_usage", _noop_usage)

    results = await asyncio.gather(
        plugin_ai_quota.acquire("demo", 7, 60),
        plugin_ai_quota.acquire("demo", 7, 60),
        return_exceptions=True,
    )

    tickets = [item for item in results if isinstance(item, plugin_ai_quota.PluginAIQuotaTicket)]
    errors = [item for item in results if isinstance(item, plugin_ai_quota.PluginAIQuotaExceeded)]
    assert len(tickets) == 1
    assert len(errors) == 1
    assert tickets[0].backend == "redis"

    await plugin_ai_quota.release(tickets[0], actual_tokens=20)
    assert fake.values[tickets[0].minute_key] == 20
    assert fake.values[tickets[0].daily_key] == 20


@pytest.mark.asyncio
async def test_release_uses_acquire_day_across_midnight(monkeypatch) -> None:
    fake = _FakeRedis()

    async def _limits(_plugin_key: str):
        return {"per_minute": 100, "daily": 1000}

    async def _noop_usage(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(plugin_ai_quota, "get_redis", lambda: fake)
    monkeypatch.setattr(plugin_ai_quota, "_load_quota_limits", _limits)
    monkeypatch.setattr(plugin_ai_quota, "_write_quota_error_usage", _noop_usage)
    monkeypatch.setattr(plugin_ai_quota, "datetime", _FrozenDateTime)

    _FrozenDateTime.current = datetime(2026, 1, 1, 23, 59, 50, tzinfo=UTC)
    ticket = await plugin_ai_quota.acquire("demo", 7, 60)
    assert ticket.daily_key == "telepilot:plugin_ai_quota:7:demo:d:20260101"
    assert fake.values[ticket.daily_key] == 60

    _FrozenDateTime.current = datetime(2026, 1, 2, 0, 0, 10, tzinfo=UTC)
    today_minute_key, today_amount_key, today_daily_key = plugin_ai_quota._quota_keys("demo", 7)
    assert today_daily_key == "telepilot:plugin_ai_quota:7:demo:d:20260102"

    await plugin_ai_quota.release(ticket, actual_tokens=0)

    assert fake.values[ticket.daily_key] == 0
    assert fake.values.get(today_daily_key, 0) == 0
    assert today_daily_key not in fake.values
    assert today_minute_key == ticket.minute_key
    assert today_amount_key == ticket.minute_amount_key


@pytest.mark.asyncio
async def test_acquire_allows_when_quota_disabled(monkeypatch) -> None:
    async def _limits(_plugin_key: str):
        return {"per_minute": 0, "daily": 0}

    monkeypatch.setattr(plugin_ai_quota, "_load_quota_limits", _limits)

    ticket = await plugin_ai_quota.acquire("demo", 7, 999999)

    assert ticket.limited is False
    assert ticket.backend == "disabled"
    assert ticket.minute_key is None


@pytest.mark.asyncio
async def test_db_fallback_counts_plugin_agent_usage_source(monkeypatch) -> None:
    statements = []

    class _DB:
        async def scalar(self, statement):  # noqa: ANN001
            statements.append(statement)
            return 0

    class _Session:
        async def __aenter__(self):
            return _DB()

        async def __aexit__(self, exc_type, exc, tb) -> bool:  # noqa: ANN001
            return False

    monkeypatch.setattr(plugin_ai_quota, "AsyncSessionLocal", lambda: _Session())

    await plugin_ai_quota._check_db_usage(
        "demo",
        account_id=7,
        estimate=10,
        per_minute=100,
        daily=1000,
    )

    assert len(statements) == 2
    sql = str(
        statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "llm_usage.source = 'plugin:demo'" in sql
    assert "llm_usage.source LIKE 'plugin:demo:' || '%%'" in sql


def test_quota_keys_use_telepilot_namespace_prefix() -> None:
    minute_key, minute_amount_key, daily_key = plugin_ai_quota._quota_keys("demo", 7)

    # 对齐 telepilot:llm_key_usage 先例的命名空间前缀
    assert minute_key == "telepilot:plugin_ai_quota:7:demo:m"
    assert minute_amount_key == "telepilot:plugin_ai_quota:7:demo:ma"
    assert daily_key.startswith("telepilot:plugin_ai_quota:7:demo:d:")
    none_minute, _ma, _d = plugin_ai_quota._quota_keys("demo", None)
    assert none_minute == "telepilot:plugin_ai_quota:none:demo:m"


def test_normalize_plugin_key_long_keys_do_not_share_bucket() -> None:
    # 两个共享 48 字符前缀的不同长 key 不能共用同一个配额桶
    prefix = "p" * 48
    key_a = f"{prefix}_alpha"
    key_b = f"{prefix}_beta"

    normalized_a = plugin_ai_quota._normalize_plugin_key(key_a)
    normalized_b = plugin_ai_quota._normalize_plugin_key(key_b)

    assert normalized_a != normalized_b
    assert normalized_a.startswith(prefix)
    assert normalized_b.startswith(prefix)
    assert len(normalized_a) <= 48 + 1 + 20
    keys_a = plugin_ai_quota._quota_keys(normalized_a, 7)
    keys_b = plugin_ai_quota._quota_keys(normalized_b, 7)
    assert keys_a[0] != keys_b[0]
    assert keys_a[2].rsplit(":", 1)[0] != keys_b[2].rsplit(":", 1)[0]
    # 短 key 原样保留
    assert plugin_ai_quota._normalize_plugin_key("demo") == "demo"


@pytest.mark.asyncio
async def test_release_failure_writes_audit_event(monkeypatch) -> None:
    class _BrokenRedis:
        async def eval(self, *_args, **_kwargs):
            raise RuntimeError("redis eval down")

    monkeypatch.setattr(plugin_ai_quota, "get_redis", lambda: _BrokenRedis())

    audit_events: list[tuple] = []
    commits: list[bool] = []

    class _AuditStub:
        async def write(self, db, user_id, action, target=None, detail=None):  # noqa: ANN001
            audit_events.append((user_id, action, target, detail))

    class _DB:
        async def commit(self):
            commits.append(True)

    class _Session:
        async def __aenter__(self):
            return _DB()

        async def __aexit__(self, exc_type, exc, tb) -> bool:  # noqa: ANN001
            return False

    monkeypatch.setattr(plugin_ai_quota, "audit", _AuditStub())
    monkeypatch.setattr(plugin_ai_quota, "AsyncSessionLocal", lambda: _Session())

    ticket = plugin_ai_quota.PluginAIQuotaTicket(
        plugin_key="demo",
        account_id=7,
        estimated_tokens=60,
        minute_key="telepilot:plugin_ai_quota:7:demo:m",
        minute_amount_key="telepilot:plugin_ai_quota:7:demo:ma",
        daily_key="telepilot:plugin_ai_quota:7:demo:d:20260101",
        reservation_id="res-1",
        backend="redis",
        limited=True,
    )

    # 释放失败不能抛出（业务路径已结束），但必须留下审计
    await plugin_ai_quota.release(ticket, actual_tokens=0)

    assert commits == [True]
    assert len(audit_events) == 1
    user_id, action, target, detail = audit_events[0]
    assert user_id is None
    assert action == "plugin.ai_quota_release_failed"
    assert target == "plugin:demo"
    assert detail["account_id"] == 7
    assert detail["actual_tokens"] == 0
    assert detail["estimated_tokens"] == 60
    assert detail["reservation_id"] == "res-1"
