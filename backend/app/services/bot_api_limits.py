"""Bot API 限流处理：429 ``retry_after``、按会话限速与结构化错误。

设计约束（第 11 轮审计 B6）：

- **只重试那一次 HTTP 调用**。Telegram 返回 429 时请求并未生效，按
  ``parameters.retry_after`` 等待后重发同一个请求是安全的；但绝不能把异常抛给
  polling 层去重跑整条消息处理流程（那会重复发「🤔」占位、重复调用 LLM、重复扣额度）。
- 等待有总预算：超过预算就抛 :class:`BotApiRateLimitedError`，由调用方决定降级。
  草稿（``sendMessageDraft``）这类可丢弃的中间态默认不等待，直接抛出，交给节流器跳过。
- 按会话限速：同一 Bot token 对同一 chat 的投递类请求走令牌桶（私聊约 1 条/秒，
  群约 20 条/分钟，允许小突发），再加一个 token 级全局桶（约 30 条/秒）。一旦某个
  会话被 Telegram 429，后续请求在 ``retry_after`` 到期前先在本地排队，不再撞墙。
- 只做进程内状态：主进程与每个 worker 子进程各自一份，足以消除同进程内的突发撞墙；
  跨进程并发由 Telegram 的 429 + 本模块的重试兜底。
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

# 单次 Bot API 调用因 429 / 本地限速累计等待的默认上限（秒）。
DEFAULT_RATE_LIMIT_WAIT_BUDGET_SECONDS = 15.0
# 单次调用因 429 最多重发几次（不含首发）。
MAX_RATE_LIMIT_RETRIES = 3
# 连接阶段失败（请求肯定没送达 Telegram）时重发次数。
MAX_CONNECT_RETRIES = 1
# 可丢弃的中间态方法：被限流时不等待，直接抛出由调用方跳过。
NO_WAIT_METHODS = frozenset({"sendMessageDraft", "sendRichMessageDraft", "sendChatAction"})


def counts_against_chat_bucket(method: str) -> bool:
    """只有“新消息”类请求走会话令牌桶（官方限频针对发送消息）。

    编辑、删除、置顶、草稿、chat action 不走本地桶：它们的限频 Telegram 没有公开，
    且游戏/进度类插件需要较快的编辑节奏；这些请求只在真被 429 后按 retry_after 封锁。
    """

    name = str(method or "")
    if name in NO_WAIT_METHODS:
        return False
    return name.startswith("send") or name in {"copyMessage", "copyMessages", "forwardMessage", "forwardMessages"}

# 令牌桶参数（Telegram 官方 FAQ：同一会话约 1 条/秒，群约 20 条/分钟，全局约 30 条/秒）。
PRIVATE_CHAT_RATE_PER_SECOND = 1.0
PRIVATE_CHAT_BURST = 3.0
GROUP_CHAT_RATE_PER_SECOND = 20.0 / 60.0
GROUP_CHAT_BURST = 5.0
GLOBAL_RATE_PER_SECOND = 30.0
GLOBAL_BURST = 30.0
_MAX_TRACKED_BUCKETS = 4096


class BotApiError(RuntimeError):
    """Telegram 明确返回 ``ok=false`` 的错误。

    继承 ``RuntimeError`` 且 ``str(exc)`` 仍是 Telegram 的 description，保持既有
    ``except RuntimeError`` / 文本匹配逻辑不变。
    """

    def __init__(
        self,
        description: str,
        *,
        method: str = "",
        error_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(description)
        self.description = description
        self.method = method
        self.error_code = error_code
        self.retry_after = retry_after


class BotApiRateLimitedError(BotApiError):
    """429 Too Many Requests，且在等待预算内没能发出去。"""


@dataclass
class _Bucket:
    rate: float
    capacity: float
    tokens: float
    updated: float
    blocked_until: float = 0.0

    def refill(self, now: float) -> None:
        if now > self.updated:
            self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
            self.updated = now

    def wait_time(self, now: float) -> float:
        self.refill(now)
        blocked = max(0.0, self.blocked_until - now)
        if self.tokens >= 1.0:
            return blocked
        return max(blocked, (1.0 - self.tokens) / self.rate)

    def take(self, now: float) -> None:
        self.refill(now)
        self.tokens -= 1.0


class BotApiRateLimiter:
    """进程内的按会话 + 全局令牌桶；时钟与 sleep 可注入，便于测试。"""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ) -> None:
        self._clock = clock
        self._sleep = sleep
        self._chats: dict[tuple[str, int], _Bucket] = {}
        self._globals: dict[str, _Bucket] = {}
        self.stats = {"waits": 0, "wait_seconds": 0.0, "rate_limited": 0}

    def reset(self) -> None:
        self._chats.clear()
        self._globals.clear()
        self.stats = {"waits": 0, "wait_seconds": 0.0, "rate_limited": 0}

    def _chat_bucket(self, token_key: str, chat_id: int, now: float) -> _Bucket:
        key = (token_key, int(chat_id))
        bucket = self._chats.get(key)
        if bucket is None:
            if len(self._chats) >= _MAX_TRACKED_BUCKETS:
                self._evict(now)
            group = int(chat_id) < 0
            rate = GROUP_CHAT_RATE_PER_SECOND if group else PRIVATE_CHAT_RATE_PER_SECOND
            capacity = GROUP_CHAT_BURST if group else PRIVATE_CHAT_BURST
            bucket = _Bucket(rate=rate, capacity=capacity, tokens=capacity, updated=now)
            self._chats[key] = bucket
        return bucket

    def _global_bucket(self, token_key: str, now: float) -> _Bucket:
        bucket = self._globals.get(token_key)
        if bucket is None:
            bucket = _Bucket(
                rate=GLOBAL_RATE_PER_SECOND,
                capacity=GLOBAL_BURST,
                tokens=GLOBAL_BURST,
                updated=now,
            )
            self._globals[token_key] = bucket
        return bucket

    def _evict(self, now: float) -> None:
        # 丢掉已经回满、且没有处于 429 封锁期的桶；仍不够就整体清空（只影响突发容量）。
        for key in [k for k, b in self._chats.items() if b.blocked_until <= now and b.wait_time(now) == 0.0]:
            self._chats.pop(key, None)
        if len(self._chats) >= _MAX_TRACKED_BUCKETS:
            self._chats.clear()

    def wait_needed(self, token_key: str, chat_id: int | None, *, take_chat_token: bool = True) -> float:
        now = self._clock()
        wait = self._global_bucket(token_key, now).wait_time(now)
        if chat_id is not None:
            bucket = self._chat_bucket(token_key, chat_id, now)
            if take_chat_token:
                wait = max(wait, bucket.wait_time(now))
            else:
                wait = max(wait, bucket.blocked_until - now)
        return max(0.0, wait)

    async def acquire(
        self,
        token_key: str,
        chat_id: int | None,
        *,
        budget: float,
        take_chat_token: bool = True,
    ) -> float:
        """等到本会话与全局都可以发送；返回实际等待秒数。

        ``take_chat_token=False`` 时只尊重该会话的 429 封锁期，不消耗会话令牌（编辑类请求）。
        需要等待的时间超过 ``budget`` 时不再等，直接放行（交给 Telegram 判定，
        真被 429 时再按 ``retry_after`` 处理），避免本地估算过严导致无谓的失败。
        """

        waited = 0.0
        for _ in range(8):
            wait = self.wait_needed(token_key, chat_id, take_chat_token=take_chat_token)
            if wait <= 0.0 or waited + wait > budget:
                break
            self.stats["waits"] += 1
            self.stats["wait_seconds"] += wait
            await self._sleep(wait)
            waited += wait
        now = self._clock()
        self._global_bucket(token_key, now).take(now)
        if chat_id is not None and take_chat_token:
            self._chat_bucket(token_key, chat_id, now).take(now)
        return waited

    def penalize(self, token_key: str, chat_id: int | None, retry_after: float) -> None:
        """Telegram 返回 429：该会话（或无会话时整个 token）在 retry_after 内封锁。"""

        self.stats["rate_limited"] += 1
        now = self._clock()
        until = now + max(0.0, float(retry_after))
        if chat_id is not None:
            bucket = self._chat_bucket(token_key, chat_id, now)
            bucket.blocked_until = max(bucket.blocked_until, until)
            bucket.tokens = min(bucket.tokens, 0.0)
        else:
            bucket = self._global_bucket(token_key, now)
            bucket.blocked_until = max(bucket.blocked_until, until)

    def blocked_for(self, token_key: str, chat_id: int | None) -> float:
        now = self._clock()
        blocked = max(0.0, self._global_bucket(token_key, now).blocked_until - now)
        if chat_id is not None:
            blocked = max(blocked, self._chat_bucket(token_key, chat_id, now).blocked_until - now)
        return blocked


def parse_retry_after(data: dict[str, Any] | None, *, headers: Any = None) -> float | None:
    """从 Bot API 响应体 ``parameters.retry_after``（或 Retry-After 头）取等待秒数。"""

    params = (data or {}).get("parameters") if isinstance(data, dict) else None
    raw: Any = params.get("retry_after") if isinstance(params, dict) else None
    if raw is None and headers is not None:
        try:
            raw = headers.get("retry-after")
        except Exception:  # noqa: BLE001
            raw = None
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return value


def token_key(token: str) -> str:
    """限速桶的 key：只取 bot id 段，不把完整 token 留在内存结构里。"""

    head, _, _rest = str(token or "").partition(":")
    return head or "unknown"


_LIMITER = BotApiRateLimiter()


def get_rate_limiter() -> BotApiRateLimiter:
    return _LIMITER


def set_rate_limiter(limiter: BotApiRateLimiter) -> BotApiRateLimiter:
    """测试用：替换进程内限速器，返回旧实例。"""

    global _LIMITER
    previous = _LIMITER
    _LIMITER = limiter
    return previous


__all__ = [
    "BotApiError",
    "BotApiRateLimitedError",
    "BotApiRateLimiter",
    "DEFAULT_RATE_LIMIT_WAIT_BUDGET_SECONDS",
    "MAX_RATE_LIMIT_RETRIES",
    "NO_WAIT_METHODS",
    "counts_against_chat_bucket",
    "get_rate_limiter",
    "parse_retry_after",
    "set_rate_limiter",
    "token_key",
]
