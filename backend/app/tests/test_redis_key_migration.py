from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import redis.asyncio as redis_async

from app.services import payout_limit
from app.services.redis_key_migration import eval_with_legacy_keys


@pytest.fixture
async def migration_redis():
    url = os.getenv("TELEPILOT_TEST_REDIS_URL")
    if not url:
        pytest.skip("设置 TELEPILOT_TEST_REDIS_URL 运行真实 Redis Lua 回归")
    client = redis_async.from_url(url, decode_responses=True)
    await client.ping()
    try:
        yield client
    finally:
        await client.aclose()


@pytest.mark.parametrize("kind", ["string", "hash", "zset", "set"])
@pytest.mark.asyncio
async def test_atomic_migration_preserves_type_value_and_ttl(migration_redis, kind) -> None:
    client = migration_redis
    old_key = f"payout_limit:test:{uuid4().hex}"
    new_key = f"telepilot:{old_key}"
    try:
        if kind == "string":
            await client.set(old_key, "900")
        elif kind == "hash":
            await client.hset(old_key, mapping={"reservation": "123"})
        elif kind == "zset":
            await client.zadd(old_key, {"reservation": 123})
        else:
            await client.sadd(old_key, "reservation")
        await client.pexpire(old_key, 90_000)
        await eval_with_legacy_keys(client, "return redis.call('TYPE', KEYS[1])", 1, new_key)
        assert await client.type(new_key) == kind
        if kind == "string":
            assert await client.get(new_key) == "900"
        elif kind == "hash":
            assert await client.hgetall(new_key) == {"reservation": "123"}
        elif kind == "zset":
            assert await client.zrange(new_key, 0, -1, withscores=True) == [("reservation", 123.0)]
        else:
            assert await client.smembers(new_key) == {"reservation"}
        assert 0 < await client.pttl(new_key) <= 90_000
        assert await client.exists(old_key)
    finally:
        await client.delete(old_key, new_key)


@pytest.mark.asyncio
async def test_concurrent_migration_and_consumption_never_reset_daily_total(migration_redis) -> None:
    client = migration_redis
    old_key = f"payout_limit:test:{uuid4().hex}"
    new_key = f"telepilot:{old_key}"
    unused = f"telepilot:payout_limit:test:{uuid4().hex}"
    try:
        await client.set(old_key, "900", ex=90)
        results = await asyncio.gather(*[
            eval_with_legacy_keys(client, payout_limit._CONSUME_SCRIPT, 2, new_key, unused, 1000, 20, 90, 0)
            for attempt in range(20)
        ])
        assert sum(int(result[0]) for result in results) == 5
        assert await client.get(new_key) == "1000"
        await client.set(old_key, "1")
        await eval_with_legacy_keys(client, "return 1", 1, new_key)
        assert await client.get(new_key) == "1000"
    finally:
        await client.delete(old_key, new_key, unused, unused.removeprefix("telepilot:"))


@pytest.mark.asyncio
async def test_migration_failure_propagates_and_next_attempt_retries() -> None:
    redis = AsyncMock()
    redis.eval.side_effect = [ConnectionError("migration write failed"), [1]]
    with pytest.raises(ConnectionError):
        await eval_with_legacy_keys(redis, "return {1}", 1, "telepilot:payout_limit:test")
    assert await eval_with_legacy_keys(redis, "return {1}", 1, "telepilot:payout_limit:test") == [1]
    assert redis.eval.await_count == 2
    assert "RESTORE" in redis.eval.await_args.args[0]
    assert redis.eval.await_args.args[1:] == (1, "telepilot:payout_limit:test")
