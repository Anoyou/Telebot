"""r11：Bot 响应不再互相堵塞——polling 解耦、保序/并发、去重、退出 flush、连接释放、RPC 分通道。"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import BigInteger, Integer, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import AsyncAdaptedQueuePool

from app.db.models.log import PluginRuntimeStatus
from app.services import account_bot_runtime, account_bot_service, event_trace, polling_dispatcher
from app.services.interaction import session_index
from app.services.polling_dispatcher import PollingUpdateDispatcher
from app.worker.ipc import CMD_RUN_INTERACTION_ENTRY, IPCMessage


class _FakeRedis:
    """够调度器 / 会话索引用的内存 Redis（忽略 TTL，提供 expire_now 模拟过期）。"""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.sets: dict[str, set[str]] = {}
        self.calls: list[str] = []

    async def set(self, key: str, value: str, **kwargs: Any) -> bool:
        self.calls.append("set")
        if kwargs.get("nx") and key in self.data:
            return False
        self.data[key] = value
        return True

    async def get(self, key: str) -> str | None:
        self.calls.append("get")
        return self.data.get(key)

    async def mget(self, keys: list[str]) -> list[str | None]:
        self.calls.append("mget")
        return [self.data.get(key) for key in keys]

    async def delete(self, *keys: str) -> int:
        self.calls.append("delete")
        return sum(1 for key in keys if self.data.pop(key, None) is not None)

    async def hset(self, key: str, mapping: dict[str, str]) -> int:
        self.calls.append("hset")
        self.hashes.setdefault(key, {}).update(mapping)
        return len(mapping)

    async def hgetall(self, key: str) -> dict[str, str]:
        self.calls.append("hgetall")
        return dict(self.hashes.get(key, {}))

    async def hdel(self, key: str, *fields: str) -> int:
        self.calls.append("hdel")
        bucket = self.hashes.get(key, {})
        return sum(1 for field in fields if bucket.pop(field, None) is not None)

    async def expire(self, _key: str, _seconds: int) -> bool:
        return True

    async def exists(self, key: str) -> int:
        self.calls.append("exists")
        return int(key in self.data or key in self.sets or key in self.hashes)

    async def sadd(self, key: str, *members: str) -> int:
        self.calls.append("sadd")
        bucket = self.sets.setdefault(key, set())
        before = len(bucket)
        bucket.update(members)
        return len(bucket) - before

    async def srem(self, key: str, *members: str) -> int:
        self.calls.append("srem")
        bucket = self.sets.get(key, set())
        removed = sum(1 for member in members if member in bucket)
        bucket.difference_update(members)
        return removed

    async def smembers(self, key: str) -> set[str]:
        self.calls.append("smembers")
        return set(self.sets.get(key, set()))

    async def scan_iter(self, match: str):  # noqa: ANN201
        self.calls.append("scan")
        prefix = match.rstrip("*")
        for key in list(self.data):
            if key.startswith(prefix):
                yield key


async def _create_status_table(engine: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr(PluginRuntimeStatus.__table__.c.id, "type", BigInteger().with_variant(Integer(), "sqlite"))
    async with engine.begin() as conn:
        await conn.run_sync(PluginRuntimeStatus.__table__.create)


def _upd(update_id: int, chat_id: int, text_value: str = "hi") -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "text": text_value,
            "from": {"id": 7},
            "chat": {"id": chat_id, "type": "group"},
        },
    }


def _chat_key(_token: str, update: dict[str, Any]) -> str:
    return f"chat:{update['message']['chat']['id']}"


def _dispatcher(process: Any, *, redis: _FakeRedis | None = None, **kwargs: Any) -> PollingUpdateDispatcher:
    return PollingUpdateDispatcher(
        aid=1,
        loop="interaction",
        process=process,
        key_fn=_chat_key,
        concurrency=kwargs.pop("concurrency", 8),
        redis_getter=(lambda: redis) if redis is not None else None,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# 调度器：保序 / 并发 / 去重 / 背压 / pending 补投
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatcher_keeps_same_chat_order_and_runs_other_chats_concurrently() -> None:
    events: list[tuple[str, int]] = []
    gate = asyncio.Event()

    async def process(_token: str, update: dict[str, Any]) -> None:
        uid = update["update_id"]
        events.append(("start", uid))
        if uid == 1:
            await gate.wait()
        events.append(("end", uid))

    dispatcher = _dispatcher(process)
    await dispatcher.submit("1:t", [_upd(1, -100), _upd(2, -100), _upd(3, -200)])
    for _ in range(20):
        await asyncio.sleep(0)
    # 慢的 update 1 卡住 chat -100；chat -200 已处理完，chat -100 的 update 2 必须等
    assert ("end", 3) in events
    assert ("start", 2) not in events
    gate.set()
    assert await dispatcher.wait_idle(timeout=2)
    assert events.index(("end", 1)) < events.index(("start", 2))
    await dispatcher.aclose()


@pytest.mark.asyncio
async def test_dispatcher_dedupes_repeated_update_ids_in_memory() -> None:
    seen: list[int] = []

    async def process(_token: str, update: dict[str, Any]) -> None:
        seen.append(update["update_id"])

    dispatcher = _dispatcher(process)
    assert await dispatcher.submit("1:t", [_upd(5, -1), _upd(5, -1)]) == 1
    await dispatcher.wait_idle(timeout=1)
    assert await dispatcher.submit("1:t", [_upd(5, -1), _upd(6, -1)]) == 1
    await dispatcher.wait_idle(timeout=1)
    assert seen == [5, 6]
    assert dispatcher.stats["deduped"] == 2
    assert dispatcher.next_offset(None) == 7
    assert dispatcher.next_offset(10) == 11
    await dispatcher.aclose()


@pytest.mark.asyncio
async def test_dispatcher_backpressure_blocks_polling_until_capacity(monkeypatch) -> None:
    gate = asyncio.Event()

    async def process(_token: str, _update: dict[str, Any]) -> None:
        await gate.wait()

    dispatcher = _dispatcher(process, max_pending=2)
    await dispatcher.submit("1:t", [_upd(1, -1), _upd(2, -2)])
    waiter = asyncio.create_task(dispatcher.wait_for_capacity())
    await asyncio.sleep(0.01)
    assert not waiter.done()
    gate.set()
    await asyncio.wait_for(waiter, timeout=1)
    await dispatcher.aclose()


@pytest.mark.asyncio
async def test_dispatcher_records_failures_for_cursor_error_state() -> None:
    failures: list[tuple[int | None, str]] = []

    async def process(_token: str, update: dict[str, Any]) -> str | None:
        return "boom" if update["update_id"] == 2 else None

    async def on_failure(update_id: int | None, error: str) -> None:
        failures.append((update_id, error))

    dispatcher = _dispatcher(process, on_failure=on_failure)
    await dispatcher.submit("1:t", [_upd(1, -1), _upd(2, -2)])
    await dispatcher.wait_idle(timeout=1)
    assert failures == [(2, "boom")]
    assert dispatcher.take_error() == (True, "boom")
    assert dispatcher.take_error() == (False, None)
    await dispatcher.aclose()


@pytest.mark.asyncio
async def test_dispatcher_cancelled_update_is_replayed_by_next_poller(monkeypatch) -> None:
    """重启：处理中的 update 被取消 → 释放 claim、pending 保留 → 下一任补投且只处理一次。"""

    monkeypatch.setattr(polling_dispatcher, "REDIS_PERSISTENCE_ENABLED", True)
    redis = _FakeRedis()
    started = asyncio.Event()
    first_runs: list[int] = []

    async def slow(_token: str, update: dict[str, Any]) -> None:
        first_runs.append(update["update_id"])
        started.set()
        await asyncio.sleep(30)

    old = _dispatcher(slow, redis=redis)
    await old.submit("123:abc", [_upd(41, -1), _upd(42, -1)])
    await asyncio.wait_for(started.wait(), timeout=1)
    await old.aclose()
    assert first_runs == [41]
    pending_key = "account_bot:polling_pending:interaction:1:123"
    assert set(redis.hashes[pending_key]) == {"41", "42"}
    assert "account_bot:polling_claim:interaction:1:123:41" not in redis.data

    second_runs: list[int] = []

    async def fast(_token: str, update: dict[str, Any]) -> None:
        second_runs.append(update["update_id"])

    new = _dispatcher(fast, redis=redis)
    assert await new.restore_pending("123:abc", force=True) == 2
    await new.wait_idle(timeout=1)
    assert second_runs == [41, 42]
    assert redis.hashes[pending_key] == {}
    assert redis.data["account_bot:polling_claim:interaction:1:123:41"] == polling_dispatcher.CLAIM_DONE
    # 再补投一次（例如 HDEL 失败残留）也不会重复处理
    redis.hashes[pending_key]["42"] = json.dumps(_upd(42, -1))
    newer = _dispatcher(fast, redis=redis)
    await newer.restore_pending("123:abc", force=True)
    await newer.wait_idle(timeout=1)
    assert second_runs == [41, 42]
    assert redis.hashes[pending_key] == {}
    for dispatcher in (new, newer):
        await dispatcher.aclose()


@pytest.mark.asyncio
async def test_dispatcher_skips_update_running_elsewhere_until_claim_expires(monkeypatch) -> None:
    """重启交接/崩溃：别的进程还持有 run claim 时不重复处理；claim 过期后补投。"""

    monkeypatch.setattr(polling_dispatcher, "REDIS_PERSISTENCE_ENABLED", True)
    redis = _FakeRedis()
    claim_key = "account_bot:polling_claim:interaction:1:123:9"
    redis.data[claim_key] = polling_dispatcher.CLAIM_RUNNING
    redis.hashes["account_bot:polling_pending:interaction:1:123"] = {"9": json.dumps(_upd(9, -1))}
    runs: list[int] = []

    async def process(_token: str, update: dict[str, Any]) -> None:
        runs.append(update["update_id"])

    dispatcher = _dispatcher(process, redis=redis)
    await dispatcher.restore_pending("123:abc", force=True)
    await dispatcher.wait_idle(timeout=1)
    assert runs == []
    redis.data.pop(claim_key)  # 模拟 run claim 过期
    await dispatcher.restore_pending("123:abc", force=True)
    await dispatcher.wait_idle(timeout=1)
    assert runs == [9]
    await dispatcher.aclose()


@pytest.mark.asyncio
async def test_dispatcher_claim_keys_are_scoped_by_bot_id(monkeypatch) -> None:
    monkeypatch.setattr(polling_dispatcher, "REDIS_PERSISTENCE_ENABLED", True)
    redis = _FakeRedis()
    runs: list[str] = []

    async def process(token: str, _update: dict[str, Any]) -> None:
        runs.append(token)

    first = _dispatcher(process, redis=redis)
    await first.submit("111:a", [_upd(1, -1)])
    await first.wait_idle(timeout=1)
    # 换了 bot token 后同一个 update_id 不应被旧 bot 的 done claim 误判为重复
    second = _dispatcher(process, redis=redis)
    await second.submit("222:b", [_upd(1, -1)])
    await second.wait_idle(timeout=1)
    assert runs == ["111:a", "222:b"]
    for dispatcher in (first, second):
        await dispatcher.aclose()


# ---------------------------------------------------------------------------
# 真实 polling 循环：慢请求不挡 getUpdates 与其它会话
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_interaction_polling_fetches_next_batch_while_slow_update_runs(monkeypatch) -> None:
    gate = asyncio.Event()
    handled: list[int] = []
    second_poll_seen_while_slow = asyncio.Event()
    calls = 0

    async def handler(_aid: int, _token: str, update: dict[str, Any]) -> None:
        if update["update_id"] == 1:
            await gate.wait()
        handled.append(update["update_id"])

    async def call_bot_api(_token: str, _method: str, payload: dict[str, Any]):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"result": [_upd(1, -100, "AI 提问")]}
        if calls == 2:
            assert payload["offset"] == 2
            assert handled == []  # 慢请求仍在处理，就已经发出下一次 getUpdates
            second_poll_seen_while_slow.set()
            return {"result": [_upd(2, -200, "别的群"), _upd(3, -100, "同群后续")]}
        for _ in range(50):
            if 2 in handled:
                break
            await asyncio.sleep(0.01)
        assert handled == [2]  # 其它会话已处理；同会话的 3 仍在 1 后面排队
        gate.set()
        for _ in range(50):
            if handled == [2, 1, 3]:
                break
            await asyncio.sleep(0.01)
        raise asyncio.CancelledError()

    monkeypatch.setattr(
        account_bot_runtime,
        "_load_interaction_runtime_config",
        AsyncMock(return_value=("1:bbot", {"enabled": True, "interaction_last_update_id": None})),
    )
    monkeypatch.setattr(account_bot_runtime, "_event_framework_flags", AsyncMock(return_value={}))
    monkeypatch.setattr(account_bot_service, "call_bot_api", call_bot_api)
    monkeypatch.setattr(account_bot_runtime, "_handle_interaction_update", handler)
    state = AsyncMock()
    monkeypatch.setattr(account_bot_runtime, "_set_interaction_runtime_state", state)

    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return None

    monkeypatch.setattr(account_bot_runtime, "AsyncSessionLocal", lambda: _DB())

    with pytest.raises(asyncio.CancelledError):
        await account_bot_runtime._interaction_polling_loop(1)

    assert second_poll_seen_while_slow.is_set()
    assert handled == [2, 1, 3]
    persisted = [call.kwargs.get("last_update_id") for call in state.await_args_list]
    assert 1 in persisted and 3 in persisted


@pytest.mark.asyncio
async def test_management_agent_query_runs_in_background_and_keeps_user_order(monkeypatch) -> None:
    from types import SimpleNamespace

    from app.services.system_agent import bot_bridge

    order: list[str] = []
    gate = asyncio.Event()

    async def run_agent_query(**kwargs: Any) -> None:
        order.append(f"start:{kwargs['text']}")
        if kwargs["text"] == "第一问":
            await gate.wait()
        order.append(f"end:{kwargs['text']}")

    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return None

        async def commit(self):
            return None

    user = SimpleNamespace(enabled=True, role="admin", last_chat_id=None, display_name="x")
    monkeypatch.setattr(bot_bridge, "is_agent_mode", AsyncMock(return_value=True))
    monkeypatch.setattr(bot_bridge, "run_agent_query", run_agent_query)
    monkeypatch.setattr(account_bot_runtime, "_event_framework_flags", AsyncMock(return_value={"trace_enabled": False}))
    monkeypatch.setattr(account_bot_runtime, "AsyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(account_bot_service, "find_bot_user", AsyncMock(return_value=user))
    monkeypatch.setattr(account_bot_runtime, "_router_debug_trace_enabled", AsyncMock(return_value=False))

    def _private(update_id: int, text_value: str) -> dict[str, Any]:
        return {
            "update_id": update_id,
            "message": {
                "message_id": update_id,
                "text": text_value,
                "from": {"id": 42, "first_name": "A"},
                "chat": {"id": 42, "type": "private"},
            },
        }

    # handler 立即返回，不等 Agent 一轮跑完
    await asyncio.wait_for(account_bot_runtime._handle_update(5, "5:mbot", _private(1, "第一问")), timeout=1)
    await asyncio.wait_for(account_bot_runtime._handle_update(5, "5:mbot", _private(2, "第二问")), timeout=1)
    await asyncio.sleep(0.01)
    assert order == ["start:第一问"]  # 同一用户第二问排在第一问之后
    gate.set()
    await account_bot_runtime._drain_management_agent_tasks(5, timeout=1)
    assert order == ["start:第一问", "end:第一问", "start:第二问", "end:第二问"]
    assert not account_bot_runtime._MANAGEMENT_AGENT_LOCKS


# ---------------------------------------------------------------------------
# 等 worker 时不持有 DB 连接
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_interaction_handler_releases_db_connection_while_waiting_worker(monkeypatch, tmp_path) -> None:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/pool.db",
        poolclass=AsyncAdaptedQueuePool,
        pool_size=1,
        max_overflow=0,
        pool_timeout=0.5,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(account_bot_runtime, "AsyncSessionLocal", factory)
    api_wait: dict[str, float] = {}

    class _Broker:
        async def request(self, **_kwargs: Any):
            # worker 正在处理：此时另一个请求（Web API）要能借到唯一的连接
            started = time.perf_counter()
            async with factory() as other:
                await other.execute(text("select 1"))
            api_wait["ms"] = (time.perf_counter() - started) * 1000
            return True, 1, {"ok": True, "actions": []}, None

    monkeypatch.setattr(account_bot_runtime, "get_interaction_rpc_broker", AsyncMock(return_value=_Broker()))
    monkeypatch.setattr(account_bot_runtime, "get_redis", lambda: None)
    monkeypatch.setattr(account_bot_runtime, "update_plugin_runtime_status", AsyncMock())
    monkeypatch.setattr(account_bot_runtime, "record_span", AsyncMock())
    incoming = account_bot_runtime._extract_incoming(1, "1:t", _upd(1, -1))
    async with account_bot_runtime._interaction_handler_session() as db:
        await db.execute(text("select 1"))
        ok, _error, _actions = await account_bot_runtime._run_worker_interaction_entry(
            incoming,
            plugin_key="demo",
            entry_key="main",
            payload={},
        )
        await db.execute(text("select 1"))  # session 仍可继续使用
    await engine.dispose()
    assert ok is True
    assert api_wait["ms"] < 400


@pytest.mark.asyncio
async def test_release_keeps_connection_when_session_has_uncommitted_writes(monkeypatch, tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/w.db")
    await _create_status_table(engine, monkeypatch)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(account_bot_runtime, "AsyncSessionLocal", factory)
    async with account_bot_runtime._interaction_handler_session() as db:
        db.add(PluginRuntimeStatus(account_id=1, plugin_key="p", enabled=True, load_status="ok"))
        assert await account_bot_runtime._release_interaction_db_before_wait() is False
        await db.flush()
        assert await account_bot_runtime._release_interaction_db_before_wait() is False
        await db.rollback()
        await db.execute(select(PluginRuntimeStatus))
        assert await account_bot_runtime._release_interaction_db_before_wait() is True
    async with factory() as check:
        assert (await check.execute(select(PluginRuntimeStatus))).first() is None
    # 不在 handler 里（或由后台协程调用）时不碰任何 session
    assert await account_bot_runtime._release_interaction_db_before_wait() is False
    await engine.dispose()


# ---------------------------------------------------------------------------
# 插件状态后台批量写 + 退出 flush
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plugin_runtime_status_batches_and_flushes_on_exit(monkeypatch, tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/s.db")
    await _create_status_table(engine, monkeypatch)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sessions = 0

    def counting_factory():
        nonlocal sessions
        sessions += 1
        return factory()

    monkeypatch.setattr(event_trace, "AsyncSessionLocal", counting_factory)
    monkeypatch.setattr(event_trace, "PLUGIN_STATUS_FLUSH_INTERVAL_SECONDS", 60.0)
    await event_trace.update_plugin_runtime_status(account_id=1, plugin_key="ai", load_status="error", last_load_error="x")
    await event_trace.update_plugin_runtime_status(account_id=1, plugin_key="ai", last_invocation_status="ok", last_trace_id="t1")
    await event_trace.update_plugin_runtime_status(account_id=1, plugin_key="ai", load_status="active", last_trace_id="t2")
    await event_trace.update_plugin_runtime_status(account_id=2, plugin_key="game", last_invocation_status="failed")
    # 热路径不落库
    assert sessions == 0
    assert event_trace.plugin_status_writer_stats()["pending"] == 2
    # 进程退出：stop_trace_writer 必须把待写状态写完
    await event_trace.stop_trace_writer()
    assert sessions == 1
    async with factory() as db:
        rows = {row.plugin_key: row for row in (await db.execute(select(PluginRuntimeStatus))).scalars()}
    assert rows["ai"].load_status == "active"
    assert rows["ai"].last_load_error is None
    assert rows["ai"].last_invocation_status == "ok"
    assert rows["ai"].last_trace_id == "t2"
    assert rows["ai"].last_invoked_at is not None
    assert rows["game"].last_invocation_status == "failed"
    assert event_trace.plugin_status_writer_stats()["pending"] == 0
    await engine.dispose()


@pytest.mark.asyncio
async def test_plugin_runtime_status_background_writer_flushes_periodically(monkeypatch, tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/p.db")
    await _create_status_table(engine, monkeypatch)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(event_trace, "AsyncSessionLocal", factory)
    monkeypatch.setattr(event_trace, "PLUGIN_STATUS_FLUSH_INTERVAL_SECONDS", 0.01)
    await event_trace.update_plugin_runtime_status(account_id=3, plugin_key="p", last_invocation_status="ok")
    for _ in range(100):
        if event_trace.plugin_status_writer_stats()["pending"] == 0:
            break
        await asyncio.sleep(0.01)
    async with factory() as db:
        row = (await db.execute(select(PluginRuntimeStatus))).scalar_one()
    assert row.last_invocation_status == "ok"
    await event_trace.stop_plugin_status_writer()
    await engine.dispose()


# ---------------------------------------------------------------------------
# worker RPC 长短通道
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_short_requests_do_not_queue_behind_long_ai_requests(monkeypatch) -> None:
    from app.worker import runtime as runtime_mod
    from app.worker.plugins import loader as loader_mod

    release = asyncio.Event()
    short_done = asyncio.Event()

    async def entry(*_args: Any, **kwargs: Any):
        payload = kwargs.get("payload") if "payload" in kwargs else (_args[3] if len(_args) > 3 else {})
        if isinstance(payload, dict) and payload.get("kind") == "short":
            short_done.set()
            return []
        await release.wait()
        return []

    class _Redis:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str):
            return self.store.get(key)

        async def set(self, key: str, value: str, **_kwargs: Any) -> bool:
            self.store[key] = value
            return True

        async def publish(self, *_args: Any) -> int:
            return 1

        async def rpush(self, *_args: Any) -> int:
            return 1

    monkeypatch.setattr(loader_mod, "invoke_interaction_entry", entry)
    executor = runtime_mod._RpcCommandExecutor(redis=_Redis(), client=AsyncMock(), account_id=301, platform_scheduler=None)
    executor.start()
    deadline = int(time.time() * 1000) + 90_000
    for index in range(runtime_mod._RPC_MAX_CONCURRENCY + 2):
        await executor.submit(
            IPCMessage(
                CMD_RUN_INTERACTION_ENTRY,
                {
                    "request_id": f"ai-{index}",
                    "deadline_at_ms": deadline,
                    "reply_to": f"ai-reply-{index}",
                    "plugin_key": "ai",
                    "entry_key": "ask",
                    "payload": {"kind": "long"},
                    "rpc_lane": "long",
                },
            )
        )
    await asyncio.sleep(0)
    await executor.submit(
        IPCMessage(
            CMD_RUN_INTERACTION_ENTRY,
            {
                "request_id": "game-1",
                "deadline_at_ms": int(time.time() * 1000) + 15_000,
                "reply_to": "game-reply",
                "plugin_key": "game",
                "entry_key": "click",
                "payload": {"kind": "short"},
            },
        )
    )
    await asyncio.wait_for(short_done.wait(), timeout=1)
    stats = runtime_mod.worker_rpc_executor_stats(301)
    assert stats["long_running"] == runtime_mod._RPC_MAX_CONCURRENCY
    assert stats["long_queued"] == 2
    assert stats["short_accepted"] == 1
    release.set()
    await executor.stop()


def test_rpc_lane_inference() -> None:
    from app.worker import runtime as runtime_mod
    from app.worker.ipc import CMD_AGENT_PLUGIN_TOOL, rpc_lane_for_timeout

    now_ms = int(time.time() * 1000)
    assert rpc_lane_for_timeout(90) == "long"
    assert rpc_lane_for_timeout(15) == "short"
    assert runtime_mod._rpc_command_lane(IPCMessage(CMD_RUN_INTERACTION_ENTRY, {"rpc_lane": "short", "deadline_at_ms": now_ms + 90_000})) == "short"
    assert runtime_mod._rpc_command_lane(IPCMessage(CMD_RUN_INTERACTION_ENTRY, {"deadline_at_ms": now_ms + 90_000})) == "long"
    assert runtime_mod._rpc_command_lane(IPCMessage(CMD_RUN_INTERACTION_ENTRY, {"deadline_at_ms": now_ms + 14_900})) == "short"
    assert runtime_mod._rpc_command_lane(IPCMessage(CMD_AGENT_PLUGIN_TOOL, {})) == "long"


# ---------------------------------------------------------------------------
# 热路径 N+1：账号会话索引 + 交互配置缓存
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_active_session_chats_use_account_index_instead_of_scan(monkeypatch) -> None:
    redis = _FakeRedis()
    prefix = account_bot_runtime._INTERACTION_SESSION_PREFIX
    live = f"{prefix}1:rule:-100"
    other_channel = f"{prefix}1:rule:-300"
    redis.data[live] = json.dumps({"chat_id": -100, "channel": "interaction_bot"})
    redis.data[other_channel] = json.dumps({"chat_id": -300, "channel": "userbot"})
    monkeypatch.setattr(account_bot_runtime, "get_redis", lambda: redis)

    # 首次：索引未建 → SCAN 重建一次
    assert await account_bot_runtime._active_interaction_session_chat_ids(1) == {-100}
    assert redis.calls.count("scan") == 1

    # 之后：SMEMBERS + MGET，不再 SCAN；新会话经 index_session_key 进入账号索引
    new_key = f"{prefix}1:rule:-200"
    redis.data[new_key] = json.dumps({"chat_id": -200})
    await session_index.index_session_key(redis, account_id=1, chat_id=-200, session_key=new_key)
    redis.data.pop(live)  # 过期
    redis.calls.clear()
    assert await account_bot_runtime._active_interaction_session_chat_ids(1) == {-200}
    assert "scan" not in redis.calls
    assert "mget" in redis.calls
    assert live not in redis.sets[session_index.account_index_key(1)]

    # 结束会话：从账号索引移除
    await session_index.unindex_session_key(redis, account_id=1, chat_id=-200, session_key=new_key)
    assert new_key not in redis.sets[session_index.account_index_key(1)]


@pytest.mark.asyncio
async def test_interaction_runtime_config_cached_and_invalidated_on_restart(monkeypatch) -> None:
    loads = AsyncMock(return_value=("1:tok", {"enabled": True, "interaction_last_update_id": 5}))
    monkeypatch.setattr(account_bot_runtime, "_load_interaction_runtime_config_uncached", loads)
    first = await account_bot_runtime._load_interaction_runtime_config(1)
    second = await account_bot_runtime._load_interaction_runtime_config(1)
    assert first == second
    assert loads.await_count == 1
    account_bot_runtime._note_interaction_runtime_cursor(1, 9)
    _token, cfg = await account_bot_runtime._load_interaction_runtime_config(1)
    assert cfg["interaction_last_update_id"] == 9
    monkeypatch.setattr(account_bot_runtime, "_interaction_bot_capability_enabled", AsyncMock(return_value=False))
    await account_bot_runtime.restart_interaction_bot(1)
    await account_bot_runtime._load_interaction_runtime_config(1)
    assert loads.await_count == 2
    account_bot_runtime.invalidate_account_allowed_chats_cache(1)
    await account_bot_runtime._load_interaction_runtime_config(1)
    assert loads.await_count == 3
