"""插件同步阻塞检测（0.109.0）：把阻塞事件循环 ≥200ms 的插件代码归到该插件的 ``blocking_ms``。

worker 的事件循环同时跑所有插件、Telethon 收发与 IPC。一个插件在 ``async def`` 里做了同步的
重活（``time.sleep``、大 JSON、同步 HTTP、CPU 计算），整个账号的消息都会卡住，而 ``asyncio`` 的
超时也打断不了它（超时只能在 ``await`` 点生效）。

做法与 ``loop.slow_callback_duration`` 同口径（200ms），但不依赖 asyncio debug 模式（debug 模式
开销大且只打日志、不知道是哪个插件）：插件钩子的协程由 :class:`BlockingProbe` 逐步驱动，
每一步（两次 ``await`` 之间那段同步执行）计时，超过阈值就按 [账号 × 插件 × 钩子] 记一次
``blocked`` 与累计 ``blocking_ms``（插件统计、Web 插件详情可见），并写 debug 日志。

单步计时只是两次 ``time.perf_counter()``，对正常插件的开销可以忽略。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable, Generator
from typing import Any

from . import plugin_stats

log = logging.getLogger(__name__)

SLOW_CALLBACK_SECONDS = plugin_stats.BLOCKING_THRESHOLD_MS / 1000


class BlockingProbe:
    """包装一个协程：逐步驱动并对每一步同步执行计时。``await BlockingProbe(coro, ...)``。"""

    __slots__ = ("_coro", "_on_block", "_threshold", "blocked_ms", "blocked_steps")

    def __init__(
        self,
        coro: Awaitable[Any],
        on_block: Callable[[float], None],
        *,
        threshold_seconds: float = SLOW_CALLBACK_SECONDS,
    ) -> None:
        self._coro = coro
        self._on_block = on_block
        self._threshold = threshold_seconds
        self.blocked_ms = 0.0
        self.blocked_steps = 0

    def _note(self, started: float) -> None:
        elapsed = time.perf_counter() - started
        if elapsed >= self._threshold:
            ms = elapsed * 1000
            self.blocked_ms += ms
            self.blocked_steps += 1
            try:
                self._on_block(ms)
            except Exception:  # noqa: BLE001 - 统计不能影响插件
                log.debug("记录插件阻塞失败", exc_info=True)

    def __await__(self) -> Generator[Any, Any, Any]:
        iterator = self._coro.__await__()
        send_value: Any = None
        error: BaseException | None = None
        while True:
            started = time.perf_counter()
            try:
                if error is not None:
                    pending, error = error, None
                    yielded = iterator.throw(pending)
                else:
                    yielded = iterator.send(send_value)
            except StopIteration as stop:
                self._note(started)
                return stop.value
            except BaseException:
                self._note(started)
                raise
            self._note(started)
            try:
                send_value = yield yielded
            except GeneratorExit:
                close = getattr(iterator, "close", None)
                if close is not None:
                    close()
                raise
            except BaseException as exc:  # noqa: BLE001 - 原样抛回被包装的协程（含 CancelledError）
                error = exc
                send_value = None


def probe(
    coro: Awaitable[Any],
    *,
    account_id: int | None,
    plugin_key: str,
    hook: str,
    event_type: str | None = None,
) -> BlockingProbe:
    """给插件钩子协程挂上阻塞检测，超过 200ms 的单步记到 ``plugin_stats``。"""

    def _on_block(ms: float) -> None:
        plugin_stats.record_blocking(
            account_id=account_id,
            plugin_key=plugin_key,
            hook=hook,
            event_type=event_type,
            blocked_ms=ms,
        )
        log.debug("插件同步阻塞事件循环 %.0fms plugin=%s hook=%s", ms, plugin_key, hook)

    return BlockingProbe(coro, _on_block)


def configure_loop(loop: Any) -> None:
    """worker 事件循环与插件阻塞检测同口径：``slow_callback_duration`` = 200ms（asyncio debug 时生效）。"""

    try:
        loop.slow_callback_duration = SLOW_CALLBACK_SECONDS
    except Exception:  # noqa: BLE001
        log.debug("设置 slow_callback_duration 失败", exc_info=True)


__all__ = ["BlockingProbe", "SLOW_CALLBACK_SECONDS", "configure_loop", "probe"]
