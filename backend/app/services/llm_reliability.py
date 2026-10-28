"""LLM 调用可靠性：总时限（deadline）、快速切备用 Provider、按 Provider 熔断。

第 11 轮审计 B9：旧实现每个 Provider 重试 3 次、指数退避 1/2/4 s，每次都按完整
超时算，**没有总时限**。插件（如 AI 答疑）只有 45 s，主 Provider 一次超时就把预算
用光，fallback 形同虚设。

本模块提供三件事，由 ``llm_runtime`` 的重试 / fallback 循环调用：

1. **总时限**：``deadline_scope(seconds)`` 用 contextvar 记录本次调用链的截止时刻。
   每次尝试的超时被收紧到剩余时间；存在备用 Provider 时还会给备用预留一段时间
   （``fallback_reserve``），保证主 Provider 超时后还能走到备用。退避等待也受剩余
   时间约束，放不下就不再重试。
2. **快速切换**：超时 / 连接失败 / 5xx / 429 这类“Provider 不可用”错误，在还有备用
   Provider 时不在同一 Provider 上重试，直接切下一个。
3. **熔断**：同一 Provider 连续 ``CIRCUIT_FAILURE_THRESHOLD`` 次不可用就熔断
   ``CIRCUIT_OPEN_SECONDS`` 秒；熔断期间（且还有其它候选时）直接跳过，冷却到期后放行
   一次试探（half-open），成功即恢复。只记进程内状态，测活 / 诊断来源不计入。
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import time
from collections.abc import Awaitable, Callable, Iterator
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

# 有备用 Provider 时给备用预留的时间：剩余时间的 35%，至少 8 s，但不超过剩余的一半。
FALLBACK_RESERVE_RATIO = 0.35
FALLBACK_RESERVE_MIN_SECONDS = 8.0
# 一次重试至少要有这么多时间才值得发起（含退避后的剩余）。
MIN_ATTEMPT_SECONDS = 3.0
# 切到备用 Provider 时剩余时间低于该值就不再发起（备用的时间已经预留过）。
MIN_FALLBACK_START_SECONDS = 1.0
# 熔断参数。
CIRCUIT_FAILURE_THRESHOLD = 3
CIRCUIT_OPEN_SECONDS = 30.0

_DEADLINE: ContextVar[float | None] = ContextVar("telepilot_llm_deadline", default=None)
_clock: Callable[[], float] = time.monotonic


def _now() -> float:
    return _clock()


@contextlib.contextmanager
def deadline_scope(seconds: float | None) -> Iterator[float | None]:
    """在当前上下文设置总时限；嵌套时取更早的截止时刻。``None`` / ≤0 表示不设。"""

    current = _DEADLINE.get()
    target = current
    if seconds is not None:
        try:
            value = float(seconds)
        except (TypeError, ValueError):
            value = 0.0
        if value > 0 and math.isfinite(value):
            candidate = _now() + value
            target = candidate if current is None else min(current, candidate)
    token = _DEADLINE.set(target)
    try:
        yield target
    finally:
        _DEADLINE.reset(token)


def deadline_active() -> bool:
    return _DEADLINE.get() is not None


def remaining_seconds() -> float | None:
    deadline = _DEADLINE.get()
    if deadline is None:
        return None
    return deadline - _now()


def fallback_reserve(remaining: float) -> float:
    if remaining <= 0:
        return 0.0
    return min(max(FALLBACK_RESERVE_MIN_SECONDS, remaining * FALLBACK_RESERVE_RATIO), remaining * 0.5)


def attempt_timeout(configured: int | float | None, *, has_fallback: bool) -> int | None:
    """本次尝试可用的超时（整数秒，≥1）；没有 deadline 时原样返回 ``configured``。"""

    remaining = remaining_seconds()
    if remaining is None:
        return int(configured) if configured else None
    budget = remaining - (fallback_reserve(remaining) if has_fallback else 0.0)
    if configured:
        budget = min(budget, float(configured))
    return max(1, int(math.floor(budget)))


def deadline_exhausted(*, minimum: float = MIN_ATTEMPT_SECONDS) -> bool:
    remaining = remaining_seconds()
    return remaining is not None and remaining < minimum


def can_wait_and_retry(delay: float, *, has_fallback: bool) -> bool:
    """退避 ``delay`` 秒后是否还有时间在同一 Provider 上再试一次。"""

    remaining = remaining_seconds()
    if remaining is None:
        return True
    left = remaining - max(0.0, delay)
    if has_fallback:
        left -= fallback_reserve(remaining)
    return left >= MIN_ATTEMPT_SECONDS


def is_availability_error(exc: BaseException | str | None) -> bool:
    """超时 / 网络 / 5xx / 429：Provider 暂时不可用，适合直接切备用并计入熔断。"""

    from .provider_health import ErrorClass, classify_error

    return classify_error(exc) in {ErrorClass.TRANSIENT, ErrorClass.RATE_LIMIT}


def should_switch_provider(exc: BaseException, *, has_fallback: bool) -> bool:
    return has_fallback and is_availability_error(exc)


async def run_within_deadline[T](awaitable: Awaitable[T], timeout_seconds: float | None) -> T:
    """有 deadline 时给单次尝试加一道硬时限（httpx 的读超时是分段的，挡不住慢速滴流）。"""

    if timeout_seconds is None or not deadline_active():
        return await awaitable
    try:
        return await asyncio.wait_for(awaitable, timeout=max(0.5, float(timeout_seconds) + 0.25))
    except TimeoutError as exc:
        from .llm_client import LLMError

        raise LLMError(
            f"模型调用超过本次时限（{int(timeout_seconds)}s）",
            retryable=True,
            category="timeout",
        ) from exc


@dataclass
class _CircuitState:
    failures: int = 0
    opened_until: float = 0.0
    probing: bool = False
    last_error: str = ""


class ProviderCircuitBreaker:
    def __init__(
        self,
        *,
        threshold: int = CIRCUIT_FAILURE_THRESHOLD,
        open_seconds: float = CIRCUIT_OPEN_SECONDS,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.threshold = max(1, int(threshold))
        self.open_seconds = max(1.0, float(open_seconds))
        self._clock = clock
        self._states: dict[int, _CircuitState] = {}

    def _time(self) -> float:
        return (self._clock or _now)()

    def reset(self) -> None:
        self._states.clear()

    def allow(self, provider_id: int | None) -> bool:
        """是否放行；熔断冷却到期后只放行一次试探，试探结果出来前其余请求继续跳过。"""

        if provider_id is None:
            return True
        state = self._states.get(int(provider_id))
        if state is None or state.failures < self.threshold:
            return True
        now = self._time()
        if now < state.opened_until:
            return False
        if state.probing:
            return False
        state.probing = True
        return True

    def is_open(self, provider_id: int | None) -> bool:
        if provider_id is None:
            return False
        state = self._states.get(int(provider_id))
        return bool(state and state.failures >= self.threshold and self._time() < state.opened_until)

    def record_success(self, provider_id: int | None) -> None:
        if provider_id is None:
            return
        self._states.pop(int(provider_id), None)

    def record_failure(self, provider_id: int | None, error: Any) -> None:
        if provider_id is None or not is_availability_error(error):
            return
        state = self._states.setdefault(int(provider_id), _CircuitState())
        state.failures += 1
        state.probing = False
        state.last_error = str(error or "")[:200]
        if state.failures >= self.threshold:
            state.opened_until = self._time() + self.open_seconds

    def snapshot(self) -> dict[int, dict[str, Any]]:
        now = self._time()
        return {
            pid: {
                "failures": st.failures,
                "open": st.failures >= self.threshold and now < st.opened_until,
                "open_remaining_seconds": max(0.0, st.opened_until - now),
                "last_error": st.last_error,
            }
            for pid, st in self._states.items()
        }


_BREAKER = ProviderCircuitBreaker()


def get_circuit_breaker() -> ProviderCircuitBreaker:
    return _BREAKER


def record_usage_outcome(
    provider_id: int | None,
    *,
    success: bool,
    error_type: str | None,
    source: str | None,
) -> None:
    """由 usage 汇报统一喂给熔断器（测活 / 诊断 / 记忆压缩来源不计入）。"""

    from .provider_health import _skip_health_source

    if provider_id is None or _skip_health_source(source):
        return
    if success:
        _BREAKER.record_success(provider_id)
    else:
        _BREAKER.record_failure(provider_id, error_type or "failed")


__all__ = [
    "ProviderCircuitBreaker",
    "attempt_timeout",
    "can_wait_and_retry",
    "deadline_active",
    "deadline_exhausted",
    "deadline_scope",
    "fallback_reserve",
    "get_circuit_breaker",
    "is_availability_error",
    "record_usage_outcome",
    "remaining_seconds",
    "run_within_deadline",
    "should_switch_provider",
]
