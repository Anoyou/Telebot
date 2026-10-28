"""Bot polling 的「拉取 / 处理」解耦调度器。

旧实现每轮 ``getUpdates`` 后要等整批 update 全部处理完（包括 45–90 s 的 AI
回答、几分钟的 Agent 运行）才发下一次 ``getUpdates``，任何会话的新消息都被
上一批里最慢的那条挡住（队头阻塞）。

本模块把两件事拆开：

- polling 只负责把 update 按会话（chat）放进各自的有序队列，然后立刻发下一次
  ``getUpdates``；
- 每个会话一个消费 task 持续处理自己的队列：**同会话严格保序、跨会话并行**，
  全局并发由信号量限制；待处理总数超过上限时 polling 暂停拉取（背压）；
- 每条 update 用 ``update_id`` 去重：进程内最近 id 集合 + Redis claim
  （``run`` 处理中 / ``done`` 已完成，键里带 bot_id，换 token 不串号）；
- Telegram 的 ``getUpdates`` 一旦带上更大的 offset 就会确认（丢弃）更早的
  update，所以「已拉取、未处理完」的 update 写进 Redis pending 哈希，处理完
  才删除；进程重启或崩溃后由新的 polling 先补投 pending 里的 update。处理中
  被取消（重启）的 update 会释放 claim，由下一任 polling 重新处理，与旧实现
  「整批没处理完就重放」的语义一致。

Redis 不可用时降级为纯内存去重（不持久化 pending），不影响消息处理。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable
from typing import Any

log = logging.getLogger(__name__)

PENDING_KEY_PREFIX = "account_bot:polling_pending:"
CLAIM_KEY_PREFIX = "account_bot:polling_claim:"
CLAIM_RUNNING = "run"
CLAIM_DONE = "done"
# 处理中 claim 的寿命：要长于单条 update 的最长处理时间（插件声明上限 90 s +
# polling 层 1 s/3 s 重试）。进程崩溃后 claim 过期，pending 里的 update 会被补投。
CLAIM_RUNNING_TTL_SECONDS = 300
CLAIM_DONE_TTL_SECONDS = 24 * 3600
PENDING_TTL_SECONDS = 24 * 3600
SEEN_CACHE_SIZE = 4096
DEFAULT_MAX_PENDING = 256
PENDING_SWEEP_INTERVAL_SECONDS = 30.0
REDIS_BACKOFF_SECONDS = 30.0
REDIS_OP_TIMEOUT_SECONDS = 2.0
# 测试夹具关掉它，避免共享 Redis（CI 的 localhost:6379）里残留的 claim 在用例间串号；
# 需要验证持久化的用例显式打开并注入内存 Redis。
REDIS_PERSISTENCE_ENABLED = True

ProcessFn = Callable[[str, dict[str, Any]], Awaitable[str | None]]
KeyFn = Callable[[str, dict[str, Any]], str]
FailureFn = Callable[[int | None, str], Awaitable[None]]


def bot_id_from_token(token: str) -> str:
    """Bot token 的数字前缀就是 bot id；取不到时退回固定占位，避免泄露 token。"""

    head = str(token or "").split(":", 1)[0].strip()
    return head if head.isdigit() else "unknown"


def _update_id(update: dict[str, Any]) -> int | None:
    try:
        value = update.get("update_id")
        return int(value) if value is not None else None
    except (TypeError, ValueError, AttributeError):
        return None


class PollingUpdateDispatcher:
    """每会话有序队列 + 常驻消费 + update_id 去重 + pending 持久化。"""

    def __init__(
        self,
        *,
        aid: int,
        loop: str,
        process: ProcessFn,
        key_fn: KeyFn,
        concurrency: int,
        max_pending: int = DEFAULT_MAX_PENDING,
        redis_getter: Callable[[], Any] | None = None,
        on_failure: FailureFn | None = None,
        name: str = "polling",
    ) -> None:
        self.aid = int(aid)
        self.loop = str(loop)
        self.name = name
        self._process = process
        self._key_fn = key_fn
        self._semaphore = asyncio.Semaphore(max(1, int(concurrency)))
        self.max_pending = max(1, int(max_pending))
        self._redis_getter = redis_getter
        self._on_failure = on_failure
        self._queues: dict[str, deque[tuple[str, dict[str, Any], int | None, float]]] = {}
        self._consumers: dict[str, asyncio.Task[None]] = {}
        self._inflight: set[int] = set()
        self._running: dict[int, str] = {}
        self._seen: OrderedDict[int, None] = OrderedDict()
        self._pending_count = 0
        self._capacity = asyncio.Event()
        self._capacity.set()
        self._idle = asyncio.Event()
        self._idle.set()
        self._max_enqueued: int | None = None
        self._error_pending = False
        self._last_error: str | None = None
        self._redis_disabled_until = 0.0
        self._last_sweep = 0.0
        self._closed = False
        self.stats: dict[str, int] = {
            "enqueued": 0,
            "completed": 0,
            "failed": 0,
            "deduped": 0,
            "replayed": 0,
            "max_queue_wait_ms": 0,
        }

    # ------------------------------------------------------------------ 状态

    @property
    def pending(self) -> int:
        return self._pending_count

    @property
    def max_enqueued_update_id(self) -> int | None:
        return self._max_enqueued

    def next_offset(self, persisted_last_update_id: int | None) -> int | None:
        """getUpdates 的 offset：已入队最大 id 与持久化游标取较大者 + 1。"""

        candidates = [v for v in (self._max_enqueued, persisted_last_update_id) if v is not None]
        return (max(candidates) + 1) if candidates else None

    def take_error(self) -> tuple[bool, str | None]:
        """返回自上次读取以来是否有失败及最近一次错误；读取后清零。"""

        had = self._error_pending
        error = self._last_error if had else None
        self._error_pending = False
        return had, error

    def snapshot(self) -> dict[str, Any]:
        return {
            **self.stats,
            "pending": self._pending_count,
            "running": len(self._running),
            "chats": len(self._queues),
        }

    # ------------------------------------------------------------------ Redis

    def _pending_key(self, token: str) -> str:
        return f"{PENDING_KEY_PREFIX}{self.loop}:{self.aid}:{bot_id_from_token(token)}"

    def _claim_key(self, token: str, update_id: int) -> str:
        return f"{CLAIM_KEY_PREFIX}{self.loop}:{self.aid}:{bot_id_from_token(token)}:{int(update_id)}"

    def _redis(self) -> Any | None:
        if self._redis_getter is None or not REDIS_PERSISTENCE_ENABLED:
            return None
        if time.monotonic() < self._redis_disabled_until:
            return None
        try:
            return self._redis_getter()
        except Exception:  # noqa: BLE001
            self._redis_failed("get_redis")
            return None

    def _redis_failed(self, op: str) -> None:
        self._redis_disabled_until = time.monotonic() + REDIS_BACKOFF_SECONDS
        log.debug("%s dispatcher redis %s failed aid=%s; degrade to memory", self.name, op, self.aid, exc_info=True)

    async def _redis_call(self, op: str, coro_factory: Callable[[Any], Awaitable[Any]]) -> tuple[bool, Any]:
        redis = self._redis()
        if redis is None:
            return False, None
        try:
            return True, await asyncio.wait_for(coro_factory(redis), timeout=REDIS_OP_TIMEOUT_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            self._redis_failed(op)
            return False, None

    async def _persist_pending(self, token: str, items: list[tuple[int, dict[str, Any]]]) -> None:
        if not items:
            return
        key = self._pending_key(token)
        mapping = {str(uid): json.dumps(update, ensure_ascii=False, default=str) for uid, update in items}

        async def _write(redis: Any) -> None:
            await redis.hset(key, mapping=mapping)
            await redis.expire(key, PENDING_TTL_SECONDS)

        await self._redis_call("hset", _write)

    async def _claim(self, token: str, update_id: int) -> str:
        """返回 ``ok``（拿到处理权）/ ``done``（已处理过）/ ``busy``（别处正在处理）。"""

        key = self._claim_key(token, update_id)

        async def _set(redis: Any) -> Any:
            return await redis.set(key, CLAIM_RUNNING, ex=CLAIM_RUNNING_TTL_SECONDS, nx=True)

        ok, acquired = await self._redis_call("claim", _set)
        if not ok or acquired:
            return "ok"

        async def _get(redis: Any) -> Any:
            return await redis.get(key)

        ok, value = await self._redis_call("claim_get", _get)
        if not ok:
            return "ok"
        if isinstance(value, bytes):
            value = value.decode("utf-8", errors="ignore")
        if value == CLAIM_DONE:
            return "done"
        if value is None:
            # claim 刚好过期，再抢一次
            ok, acquired = await self._redis_call("claim_retry", _set)
            return "ok" if (not ok or acquired) else "busy"
        return "busy"

    async def _finish_claim(self, token: str, update_id: int) -> None:
        claim_key = self._claim_key(token, update_id)
        pending_key = self._pending_key(token)

        async def _done(redis: Any) -> None:
            await redis.set(claim_key, CLAIM_DONE, ex=CLAIM_DONE_TTL_SECONDS)
            await redis.hdel(pending_key, str(update_id))

        await self._redis_call("finish", _done)

    async def _release_claim(self, token: str, update_id: int) -> None:
        claim_key = self._claim_key(token, update_id)

        async def _release(redis: Any) -> None:
            await redis.delete(claim_key)

        await self._redis_call("release", _release)

    async def _drop_pending(self, token: str, update_id: int) -> None:
        pending_key = self._pending_key(token)

        async def _drop(redis: Any) -> None:
            await redis.hdel(pending_key, str(update_id))

        await self._redis_call("hdel", _drop)

    # ------------------------------------------------------------------ 入队

    def _remember_seen(self, update_id: int) -> None:
        self._seen[update_id] = None
        self._seen.move_to_end(update_id)
        while len(self._seen) > SEEN_CACHE_SIZE:
            self._seen.popitem(last=False)

    def _enqueue_local(self, token: str, update: dict[str, Any], update_id: int | None) -> None:
        try:
            key = self._key_fn(token, update)
        except Exception:  # noqa: BLE001
            key = f"update:{update_id}" if update_id is not None else f"object:{id(update)}"
        queue = self._queues.setdefault(key, deque())
        queue.append((token, update, update_id, time.monotonic()))
        if update_id is not None:
            self._inflight.add(update_id)
            self._remember_seen(update_id)
            if self._max_enqueued is None or update_id > self._max_enqueued:
                self._max_enqueued = update_id
        self._pending_count += 1
        self.stats["enqueued"] += 1
        self._idle.clear()
        if self._pending_count >= self.max_pending:
            self._capacity.clear()
        consumer = self._consumers.get(key)
        if consumer is None or consumer.done():
            self._consumers[key] = asyncio.create_task(
                self._consume(key),
                name=f"{self.name}:{self.aid}:{key}",
            )

    async def submit(self, token: str, updates: list[dict[str, Any]]) -> int:
        """把一批 update 按会话入队；返回实际入队数量（去重后）。不等待处理。"""

        if self._closed:
            return 0
        accepted: list[tuple[int | None, dict[str, Any]]] = []
        batch_ids: set[int] = set()
        for update in updates:
            if not isinstance(update, dict):
                continue
            update_id = _update_id(update)
            if update_id is not None and (
                update_id in self._seen or update_id in self._inflight or update_id in batch_ids
            ):
                self.stats["deduped"] += 1
                continue
            if update_id is not None:
                batch_ids.add(update_id)
            accepted.append((update_id, update))
        if not accepted:
            return 0
        await self._persist_pending(token, [(uid, upd) for uid, upd in accepted if uid is not None])
        for update_id, update in accepted:
            self._enqueue_local(token, update, update_id)
        return len(accepted)

    async def restore_pending(self, token: str, *, force: bool = False) -> int:
        """补投 Redis pending 里「已拉取、未处理完」的 update（重启/崩溃后）。"""

        now = time.monotonic()
        if not force and now - self._last_sweep < PENDING_SWEEP_INTERVAL_SECONDS:
            return 0
        self._last_sweep = now
        key = self._pending_key(token)

        async def _load(redis: Any) -> Any:
            return await redis.hgetall(key)

        ok, raw = await self._redis_call("hgetall", _load)
        if not ok or not raw:
            return 0
        items: list[tuple[int, dict[str, Any]]] = []
        for field, value in dict(raw).items():
            if isinstance(field, bytes):
                field = field.decode("utf-8", errors="ignore")
            if isinstance(value, bytes):
                value = value.decode("utf-8", errors="ignore")
            try:
                update_id = int(field)
                update = json.loads(value)
            except (TypeError, ValueError):
                continue
            if not isinstance(update, dict) or update_id in self._inflight:
                continue
            items.append((update_id, update))
        restored = 0
        for update_id, update in sorted(items, key=lambda item: item[0]):
            # 补投是重放，不受进程内 seen 去重影响；最终由 Redis claim 判定
            self._seen.pop(update_id, None)
            self._enqueue_local(token, update, update_id)
            restored += 1
        if restored:
            self.stats["replayed"] += restored
            log.info("%s polling 补投未处理完的 update aid=%s count=%s", self.name, self.aid, restored)
        return restored

    async def wait_for_capacity(self) -> None:
        """待处理达到上限时阻塞 polling（背压），直到有空位。"""

        await self._capacity.wait()

    async def wait_idle(self, timeout: float | None = None) -> bool:
        """测试/基准用：等待所有已入队 update 处理完。"""

        try:
            await asyncio.wait_for(self._idle.wait(), timeout=timeout)
            return True
        except TimeoutError:
            return False

    # ------------------------------------------------------------------ 消费

    async def _consume(self, key: str) -> None:
        queue = self._queues.get(key)
        try:
            while queue:
                token, update, update_id, enqueued_at = queue[0]
                try:
                    async with self._semaphore:
                        wait_ms = int((time.monotonic() - enqueued_at) * 1000)
                        if wait_ms > self.stats["max_queue_wait_ms"]:
                            self.stats["max_queue_wait_ms"] = wait_ms
                        await self._run_one(token, update, update_id)
                finally:
                    if queue and queue[0][1] is update:
                        queue.popleft()
                    self._mark_done(update_id)
        finally:
            if self._queues.get(key) is queue and not queue:
                self._queues.pop(key, None)
            if self._consumers.get(key) is asyncio.current_task():
                self._consumers.pop(key, None)

    async def _run_one(self, token: str, update: dict[str, Any], update_id: int | None) -> None:
        if update_id is not None:
            claim = await self._claim(token, update_id)
            if claim == "done":
                self.stats["deduped"] += 1
                await self._drop_pending(token, update_id)
                return
            if claim == "busy":
                # 别的 polling（重启交接中的旧任务）正在处理；pending 保留，过期后由扫尾补投
                self.stats["deduped"] += 1
                return
            self._running[update_id] = token
        finished = False
        try:
            error = await self._process(token, update)
            finished = True
        except asyncio.CancelledError:
            if update_id is not None:
                self._running.pop(update_id, None)
                # 处理被取消（重启/停机）：释放 claim，pending 保留给下一任 polling 重新处理
                with contextlib.suppress(Exception):
                    await asyncio.shield(self._release_claim(token, update_id))
            raise
        except Exception as exc:  # noqa: BLE001
            # process 自身应已吞掉异常（重试 + DLQ）；这里兜底，避免消费 task 退出
            error = f"{type(exc).__name__}: {exc}"
            finished = True
            log.warning("%s polling update 处理异常 aid=%s update_id=%s: %s", self.name, self.aid, update_id, error)
        if finished:
            if update_id is not None:
                self._running.pop(update_id, None)
                await self._finish_claim(token, update_id)
            if error is None:
                self.stats["completed"] += 1
            else:
                self.stats["failed"] += 1
                self._error_pending = True
                self._last_error = error
                if self._on_failure is not None:
                    try:
                        await self._on_failure(update_id, error)
                    except asyncio.CancelledError:
                        raise
                    except Exception:  # noqa: BLE001
                        log.debug("%s polling failure hook failed aid=%s", self.name, self.aid, exc_info=True)

    def _mark_done(self, update_id: int | None) -> None:
        if update_id is not None:
            self._inflight.discard(update_id)
        self._pending_count = max(0, self._pending_count - 1)
        if self._pending_count < self.max_pending:
            self._capacity.set()
        if self._pending_count == 0:
            self._idle.set()

    # ------------------------------------------------------------------ 停机

    async def aclose(self) -> None:
        """取消全部消费 task；处理中的 update 释放 claim，pending 留给下一任补投。"""

        self._closed = True
        consumers = [task for task in self._consumers.values() if not task.done()]
        self._consumers.clear()
        for task in consumers:
            task.cancel()
        if consumers:
            await asyncio.gather(*consumers, return_exceptions=True)
        self._queues.clear()
        self._capacity.set()
        self._idle.set()


__all__ = [
    "CLAIM_DONE",
    "CLAIM_KEY_PREFIX",
    "CLAIM_RUNNING",
    "PENDING_KEY_PREFIX",
    "PollingUpdateDispatcher",
    "bot_id_from_token",
]
