"""把高频文本快照节流成低频 Telegram 编辑 / 草稿（第 11 轮审计 B3/B4）。

Telegram 对同一会话的高频编辑和草稿有频控（官方未公布精确值，社区经验约每秒 1 次）。
流式输出如果每个 token 都 ``editMessageText`` / ``sendMessageDraft``，大部分请求会被
429，还会挤占 Bot API 连接池；每次都对累计全文做 Markdown→HTML 渲染也是 O(n²) CPU。

:class:`ThrottledTextPusher` 只保留“最新快照”：

- ``update(text)`` 只记录、不发送（同步、O(1)），到点由后台定时器发送；
- 两次发送至少间隔 ``min_interval`` 秒，且（非强制时）新增字符不少于 ``min_chars``；
- 渲染（``render``）只在真正发送时对最新快照做一次；
- 推送失败只记日志；遇到 Bot API 429 按 ``retry_after`` 推迟下一次发送；
- 同一时刻最多一个发送在途，``flush()`` / ``aclose()`` 等在途请求结束，保证顺序。

插件侧的 ``ctx.messages.stream_editor`` 与管理 Bot 的 System Agent 草稿都复用它。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_MIN_INTERVAL_SECONDS = 1.0
GROUP_MIN_INTERVAL_SECONDS = 1.5
MIN_ALLOWED_INTERVAL_SECONDS = 0.5
MAX_ALLOWED_INTERVAL_SECONDS = 10.0


def default_stream_interval(chat_id: int | None) -> float:
    """私聊 / inline 约 1 次/秒；群聊放慢到 1.5 秒一次（群更容易触发频控）。"""

    if chat_id is not None and int(chat_id) < 0:
        return GROUP_MIN_INTERVAL_SECONDS
    return DEFAULT_MIN_INTERVAL_SECONDS


def clamp_interval(value: float | None, *, default: float) -> float:
    if value is None:
        return default
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return default
    return max(MIN_ALLOWED_INTERVAL_SECONDS, min(MAX_ALLOWED_INTERVAL_SECONDS, seconds))


class ThrottledTextPusher:
    def __init__(
        self,
        push: Callable[[str], Awaitable[Any]],
        *,
        min_interval: float = DEFAULT_MIN_INTERVAL_SECONDS,
        min_chars: int = 0,
        render: Callable[[str], str] | None = None,
        clock: Callable[[], float] = time.monotonic,
        name: str = "stream",
    ) -> None:
        self._push = push
        self._min_interval = max(0.0, float(min_interval))
        self._min_chars = max(0, int(min_chars))
        self._render = render
        self._clock = clock
        self._name = name
        self._latest: str | None = None
        self._latest_needs_render = True
        self._last_sent_raw: str | None = None
        self._last_sent_rendered: str | None = None
        self._last_push_at: float | None = None
        self._blocked_until = 0.0
        self._timer: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._closed = False
        self.stats: dict[str, int] = {
            "updates": 0,
            "pushes": 0,
            "renders": 0,
            "failures": 0,
            "rate_limited": 0,
            "skipped_unchanged": 0,
        }

    @property
    def last_sent_text(self) -> str | None:
        return self._last_sent_raw

    def update(self, text: str, *, rendered: bool = False) -> None:
        """记录最新快照；``rendered=True`` 表示已是最终格式（不再调用 ``render``）。"""

        if self._closed:
            return
        self.stats["updates"] += 1
        self._latest = "" if text is None else str(text)
        self._latest_needs_render = not rendered
        self._schedule()

    async def offer(self, text: str, *, rendered: bool = False) -> bool:
        """记录最新快照；已到发送时刻且没有在途请求时**当场**发出，否则交给定时器。

        流式开头（以及停顿后）的第一条更新因此没有额外延迟；返回本次是否当场发出。
        """

        self.update(text, rendered=rendered)
        if self._closed or self._latest is None:
            return False
        if self._due_in() > 0 or self._lock.locked() or not self._enough_new_chars():
            return False
        timer, self._timer = self._timer, None
        if timer is not None and not timer.done():
            timer.cancel()
        sent = await self._send_latest(force=False)
        if self._latest is not None:
            # 被限流放回的快照（或发送期间到达的新快照）交给定时器到点重发
            self._schedule()
        return sent

    def _due_in(self) -> float:
        now = self._clock()
        wait = max(0.0, self._blocked_until - now)
        if self._last_push_at is not None:
            wait = max(wait, self._last_push_at + self._min_interval - now)
        return wait

    def _schedule(self) -> None:
        if self._timer is not None and not self._timer.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._timer = loop.create_task(self._timer_body())

    async def _timer_body(self) -> None:
        try:
            while not self._closed and self._latest is not None:
                delay = self._due_in()
                if delay > 0:
                    await asyncio.sleep(delay)
                    continue
                if not self._enough_new_chars():
                    # 新增太少：等下一次 update 再说（finish/flush 会强制发出）
                    return
                await self._send_latest(force=False)
                if self._latest is None:
                    return
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.debug("%s throttled push timer failed", self._name, exc_info=True)

    def _enough_new_chars(self) -> bool:
        if self._min_chars <= 0 or self._last_sent_raw is None or self._latest is None:
            return True
        return len(self._latest) - len(self._last_sent_raw) >= self._min_chars

    async def _send_latest(self, *, force: bool) -> bool:
        async with self._lock:
            text = self._latest
            if text is None:
                return False
            needs_render = self._latest_needs_render
            self._latest = None
            if text == self._last_sent_raw and not force:
                self.stats["skipped_unchanged"] += 1
                return False
            payload = text
            if needs_render and self._render is not None:
                self.stats["renders"] += 1
                try:
                    payload = self._render(text)
                except Exception:  # noqa: BLE001
                    log.debug("%s render failed, sending raw text", self._name, exc_info=True)
                    payload = text
            if payload == self._last_sent_rendered:
                self._last_sent_raw = text
                self.stats["skipped_unchanged"] += 1
                return False
            self._last_push_at = self._clock()
            try:
                await self._push(payload)
            except Exception as exc:  # noqa: BLE001
                retry_after = getattr(exc, "retry_after", None)
                if getattr(exc, "error_code", None) == 429 or retry_after is not None:
                    self.stats["rate_limited"] += 1
                    self._blocked_until = self._clock() + float(retry_after or 1.0)
                    # 被限流的快照放回去，到期后用最新快照重发
                    if self._latest is None:
                        self._latest = text
                        self._latest_needs_render = needs_render
                else:
                    self.stats["failures"] += 1
                    if "message is not modified" in str(exc).lower():
                        self._last_sent_raw = text
                        self._last_sent_rendered = payload
                        return False
                log.debug("%s throttled push failed: %s", self._name, exc)
                return False
            self.stats["pushes"] += 1
            self._last_sent_raw = text
            self._last_sent_rendered = payload
            return True

    async def flush(self, *, wait_interval: bool = False) -> bool:
        """立即发出最新快照（``wait_interval=True`` 时先等够最小间隔）。"""

        await self._cancel_timer()
        if self._latest is None:
            return False
        if wait_interval:
            delay = self._due_in()
            if delay > 0:
                await asyncio.sleep(delay)
        return await self._send_latest(force=False)

    async def _cancel_timer(self) -> None:
        timer, self._timer = self._timer, None
        if timer is not None and not timer.done():
            timer.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await timer
        # 等在途发送结束，保证后续调用方的最终编辑一定在最后一次中间编辑之后
        async with self._lock:
            pass

    async def aclose(self, *, flush: bool = False) -> None:
        if flush:
            await self.flush()
        self._closed = True
        self._latest = None
        await self._cancel_timer()


__all__ = [
    "DEFAULT_MIN_INTERVAL_SECONDS",
    "GROUP_MIN_INTERVAL_SECONDS",
    "ThrottledTextPusher",
    "clamp_interval",
    "default_stream_interval",
]
