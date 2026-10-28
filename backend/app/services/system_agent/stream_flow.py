"""System Agent 流式链路的写入合并与订阅通知（第 11 轮审计 B3）。

旧链路每个 token delta 都要做 2 次带行锁的事务（``_link_user_message`` 空事务 +
``_append_event``），订阅端 ``stream_events`` 靠轮询数据库、空闲退避最高 2 s，首字
平均多等约 1 s。这里提供两件互相独立的小工具：

- :func:`coalesce_stream_deltas`：包在 Agent 事件流外面，把连续的
  ``assistant_delta`` / ``assistant_reasoning_delta`` 合并成一条再交给持久化。
  **第一条 delta 立即放行**（不拖慢首字），之后按 ``interval``（默认 0.2 s）或
  ``max_chars`` 合并；遇到任何其它事件（工具开始、``assistant_delta_reset``、终稿…）
  先把攒着的 delta 冲出去，事件顺序不变。上游事件读取在独立 task 里进行，定时冲刷
  不会取消上游的 ``anext``（避免打断其中的数据库读写）。
- :class:`RunEventSignals`：进程内的“有新事件”信号。执行侧每次落库后 ``notify``，
  订阅侧 ``wait`` 在通知到达时立即返回，否则按原退避超时返回（跨进程执行的 Run
  仍由轮询兜底）。用版本号避免“查询与等待之间到达的通知”被漏掉。
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

MERGEABLE_DELTA_TYPES = frozenset({"assistant_delta", "assistant_reasoning_delta"})
DEFAULT_DELTA_FLUSH_INTERVAL_SECONDS = 0.2
DEFAULT_DELTA_FLUSH_MAX_CHARS = 512


async def coalesce_stream_deltas(
    stream: AsyncIterator[dict[str, Any]],
    *,
    interval: float = DEFAULT_DELTA_FLUSH_INTERVAL_SECONDS,
    max_chars: int = DEFAULT_DELTA_FLUSH_MAX_CHARS,
    clock: Callable[[], float] = time.monotonic,
    stats: dict[str, int] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """合并连续的文本 / 推理 delta；其它事件原样、按序放行。"""

    counters = stats if stats is not None else {}
    counters.setdefault("in", 0)
    counters.setdefault("out", 0)
    pending: dict[str, Any] | None = None
    pending_deadline = 0.0
    first_sent: set[str] = set()
    inner: asyncio.Future[dict[str, Any]] | None = None

    def _take_pending() -> dict[str, Any]:
        nonlocal pending
        assert pending is not None
        event, pending = pending, None
        counters["out"] += 1
        return event

    try:
        while True:
            if inner is None:
                inner = asyncio.ensure_future(anext(stream))
            timeout = None if pending is None else max(0.0, pending_deadline - clock())
            done, _ = await asyncio.wait({inner}, timeout=timeout)
            if not done:
                yield _take_pending()
                continue
            finished, inner = inner, None
            try:
                event = finished.result()
            except StopAsyncIteration:
                if pending is not None:
                    yield _take_pending()
                return
            counters["in"] += 1
            event_type = str(event.get("type") or "") if isinstance(event, dict) else ""
            if event_type in MERGEABLE_DELTA_TYPES and isinstance(event.get("delta"), str):
                if pending is not None and pending.get("type") == event_type:
                    pending["delta"] = str(pending.get("delta") or "") + event["delta"]
                    pending["coalesced"] = int(pending.get("coalesced") or 1) + 1
                    if len(pending["delta"]) >= max_chars or clock() >= pending_deadline:
                        yield _take_pending()
                    continue
                if pending is not None:
                    yield _take_pending()
                if event_type not in first_sent:
                    # 首条 delta 立即落库 / 推送，首字延迟不受合并影响
                    first_sent.add(event_type)
                    counters["out"] += 1
                    yield event
                    continue
                pending = dict(event)
                pending_deadline = clock() + max(0.0, interval)
                continue
            if pending is not None:
                yield _take_pending()
            counters["out"] += 1
            yield event
    finally:
        if inner is not None and not inner.done():
            inner.cancel()
            with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration, Exception):
                await inner
        aclose = getattr(stream, "aclose", None)
        if aclose is not None:
            with contextlib.suppress(Exception):
                await aclose()


class _Signal:
    __slots__ = ("version", "event", "subscribers")

    def __init__(self) -> None:
        self.version = 0
        self.event = asyncio.Event()
        self.subscribers = 0


class RunEventSignals:
    """进程内按 run_id 的事件到达信号。"""

    def __init__(self) -> None:
        self._signals: dict[str, _Signal] = {}

    def subscribe(self, run_id: str) -> int:
        signal = self._signals.get(run_id)
        if signal is None:
            signal = _Signal()
            self._signals[run_id] = signal
        signal.subscribers += 1
        return signal.version

    def unsubscribe(self, run_id: str) -> None:
        signal = self._signals.get(run_id)
        if signal is None:
            return
        signal.subscribers -= 1
        if signal.subscribers <= 0:
            self._signals.pop(run_id, None)

    def version(self, run_id: str) -> int:
        signal = self._signals.get(run_id)
        return signal.version if signal is not None else 0

    def notify(self, run_id: str) -> None:
        signal = self._signals.get(run_id)
        if signal is None:
            return  # 没有本进程订阅者
        signal.version += 1
        event, signal.event = signal.event, asyncio.Event()
        event.set()

    async def wait(self, run_id: str, *, since_version: int, timeout: float) -> bool:
        """有新通知（版本已变化或等待期间到达）返回 True，超时返回 False。"""

        signal = self._signals.get(run_id)
        if signal is None:
            await asyncio.sleep(max(0.0, timeout))
            return False
        if signal.version != since_version:
            return True
        # 用 asyncio.sleep 作超时分支（而非 wait_for），测试替换 sleep 推进假时钟时语义不变。
        waiter = asyncio.ensure_future(signal.event.wait())
        sleeper = asyncio.ensure_future(asyncio.sleep(max(0.0, timeout)))
        try:
            done, _ = await asyncio.wait({waiter, sleeper}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (waiter, sleeper):
                if not task.done():
                    task.cancel()
        return waiter in done


__all__ = [
    "DEFAULT_DELTA_FLUSH_INTERVAL_SECONDS",
    "DEFAULT_DELTA_FLUSH_MAX_CHARS",
    "MERGEABLE_DELTA_TYPES",
    "RunEventSignals",
    "coalesce_stream_deltas",
]
