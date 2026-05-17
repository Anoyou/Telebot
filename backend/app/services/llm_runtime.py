"""LLM Runtime —— 调用层封装：fallback、retry、usage 记录。

设计目标：
1. **Runtime Fallback**：provider 失败后自动尝试 fallback chain
2. **Retry 策略**：只对 timeout/ConnectError/429/5xx 重试，指数退避
3. **Usage 记录**：记录每次调用的 provider/model/input/output tokens
4. **隐私安全**：日志不记录完整 prompt，只记录元数据

Fallback 优先级（从高到低）：
1. 显式 inline provider（用户 @provider 指定）
2. command/template configured provider
3. router fallback_provider_id
4. tag/capability 匹配且 cost_tier 更低的 provider
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

if TYPE_CHECKING:
    from .llm_client import LLMResult
    from .llm_dto import LLMProviderDTO

from ..db.base import AsyncSessionLocal
from ..db.models.llm_usage import LLMUsage
from ..db.models.system import SystemSetting
from ..settings import settings
from .llm_client import build_client_from_dto

log = logging.getLogger(__name__)

# 最大重试次数（不含首次调用）
_MAX_RETRIES = 3
# 重试延迟基数（秒）
_RETRY_BASE_DELAY = 1.0
# 最大退避时间（秒）
_RETRY_MAX_DELAY = 30.0


# ── Usage Record ────────────────────────────────────────────

@dataclass
class UsageRecord:
    """单次 LLM 调用的 usage 记录。"""
    provider_id: int | None = None
    account_id: int | None = None
    provider_name: str | None = None
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    success: bool = False
    error_type: str | None = None
    source: str | None = None
    used_fallback: bool = False
    fallback_chain: list[str] = field(default_factory=list)


# 全局 usage 回调（可注入到 DB / Redis / 日志）
_usage_callbacks: list[Callable[[UsageRecord], Coroutine[Any, Any, None]]] = []


def register_usage_callback(cb: Callable[[UsageRecord], Coroutine[Any, Any, None]]) -> None:
    """注册 usage 记录回调。

    用法：
        async def on_usage(record: UsageRecord):
            await db.save(record)

        register_usage_callback(on_usage)
    """
    if cb not in _usage_callbacks:
        _usage_callbacks.append(cb)


async def _emit_usage(record: UsageRecord) -> None:
    """将 usage 记录发送到所有注册的回调。"""
    for cb in _usage_callbacks:
        try:
            await cb(record)
        except Exception:
            # 不应因 usage 记录失败影响主流程
            log.exception("usage callback 失败")


# ── Retry 计算 ──────────────────────────────────────────────

def _compute_retry_delay(attempt: int) -> float:
    """计算指数退避延迟：base * 2^(attempt-1)，加抖动后限制在 max_delay 内。"""
    import random
    delay = _RETRY_BASE_DELAY * (2 ** (attempt - 1))
    jitter = delay * 0.25 * (2 * random.random() - 1)
    return min(delay + jitter, _RETRY_MAX_DELAY)


# ── Error 分类 ───────────────────────────────────────────────

def _is_retryable_error(exc: Exception, status_code: int | None = None) -> bool:
    """判断错误是否可重试。

    可重试：timeout / ConnectError / 网络错误 / 429 / 5xx
    不可重试：400 / 401 / 403 / 404（认证/配置错误，重试无意义）
    """
    from .llm_client import LLMCallFailed, LLMError

    if isinstance(exc, LLMCallFailed):
        return exc.retryable
    if isinstance(exc, LLMError):
        return exc.retryable

    if status_code is not None:
        if status_code == 429:
            return True
        if 500 <= status_code < 600:
            return True
        return False

    exc_name = type(exc).__name__
    retryable_types = {
        "TimeoutException", "ConnectTimeout", "ReadTimeout", "WriteTimeout",
        "PoolTimeout", "ConnectError", "ReadError", "WriteError",
        "ProxyError", "SSLError", "ProtocolError", "HTTPError",
        "asyncio.TimeoutError",
    }
    return exc_name in retryable_types


def _classify_error(exc: Exception) -> str:
    """分类错误类型（用于日志）。"""
    from .llm_client import LLMCallFailed, LLMError

    if isinstance(exc, LLMCallFailed):
        return exc.error_type or "unknown"
    if isinstance(exc, LLMError):
        msg = str(exc).lower()
        if "timeout" in msg:
            return "timeout"
        if "connect" in msg or "network" in msg or "proxy" in msg:
            return "network"
        if "429" in msg or "限流" in msg:
            return "rate_limit"
        if "401" in msg or "403" in msg or "auth" in msg or "unauthorized" in msg:
            return "auth"
        if "5" in msg[:3]:
            return "server_error"
        return "unknown"
    return type(exc).__name__.lower()


# ── Call with Fallback ───────────────────────────────────────

@dataclass
class FallbackChain:
    """Fallback provider 链。"""
    primary: LLMProviderDTO
    fallbacks: list[LLMProviderDTO] = field(default_factory=list)

    @property
    def all_providers(self) -> list[LLMProviderDTO]:
        """返回所有可用 provider（primary + fallbacks）。"""
        return [self.primary] + self.fallbacks

    def get_provider_names(self) -> list[str]:
        """返回 provider 名称列表（用于日志）。"""
        return [p.name for p in self.all_providers]


async def call_with_fallback(
    chain: FallbackChain,
    system: str,
    user: str,
    override_model: str | None = None,
    max_tokens: int = 512,
    images: list[bytes] | None = None,
    web_search: bool = False,
    web_search_context_size: str | None = None,
    *,
    # 隐私控制
    log_prompt_preview: bool = False,  # 设为 True 时只记录前 100 字符
    client_factory: Callable[..., Any | Awaitable[Any]] | None = None,
    account_id: int | None = None,
    source: str | None = None,
    # 调试
    _debug: bool = False,
) -> tuple[LLMResult, LLMProviderDTO, bool]:
    """使用 fallback 链调用 LLM。

    策略：
    1. 先用 primary provider
    2. 如果失败且可 fallback，尝试 fallback chain 中的 provider
    3. 每个 provider 最多重试 _MAX_RETRIES 次（指数退避）
    4. 最终返回 (result, used_provider, used_fallback)

    Args:
        chain: FallbackChain，包含 primary 和 fallback providers
        system: 系统提示词
        user: 用户消息
        override_model: 覆盖模型名
        max_tokens: 最大输出 token 数
        images: 图片字节列表（vision 模型用）
        log_prompt_preview: 是否在日志中记录 prompt 预览

    Returns:
        (LLMResult, used_provider, used_fallback)
        - LLMResult: 成功时返回
        - used_provider: 实际使用的 provider
        - used_fallback: 是否使用了 fallback（非 primary）

    Raises:
        LLMCallFailed: 所有 provider 都失败时抛出
    """
    from .llm_client import LLMCallFailed

    all_providers = chain.all_providers
    max_tokens = _apply_output_token_cap(max_tokens)
    budget_error = await _check_budget(account_id, all_providers[0])
    if budget_error:
        usage_record = UsageRecord(
            provider_id=all_providers[0].id if all_providers else None,
            account_id=account_id,
            provider_name=all_providers[0].name if all_providers else None,
            model=override_model or (all_providers[0].default_model if all_providers else None),
            success=False,
            error_type="budget_exceeded",
            source=source,
            used_fallback=False,
            fallback_chain=chain.get_provider_names(),
        )
        await _emit_usage(usage_record)
        raise LLMCallFailed(
            budget_error,
            provider_id=all_providers[0].id if all_providers else None,
            provider_name=all_providers[0].name if all_providers else None,
            error_type="budget_exceeded",
            retryable=False,
        )

    tried_providers: list[str] = []
    last_error: Exception | None = None
    last_status_code: int | None = None

    for idx, provider_dto in enumerate(all_providers):
        is_fallback = idx > 0
        tried_providers.append(provider_dto.name)

        # 记录当前尝试的 provider（不记录完整 prompt）
        log.info(
            "[llm-runtime] 尝试 provider=%s (fallback=%s) model=%s",
            provider_dto.name,
            is_fallback,
            override_model or provider_dto.default_model,
        )

        try:
            result = await _call_with_retry(
                provider_dto,
                system,
                user,
                override_model=override_model,
                max_tokens=max_tokens,
                images=images,
                web_search=web_search,
                web_search_context_size=web_search_context_size,
                log_prompt_preview=log_prompt_preview,
                client_factory=client_factory,
            )
            # 成功
            used_fallback = is_fallback
            # 记录 usage
            usage_record = UsageRecord(
                provider_id=provider_dto.id,
                account_id=account_id,
                provider_name=provider_dto.name,
                model=result.model,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                success=True,
                source=source,
                used_fallback=used_fallback,
                fallback_chain=chain.get_provider_names(),
            )
            await _emit_usage(usage_record)

            if _debug:
                log.debug(
                    "[llm-runtime] 成功 provider=%s tokens=%d/%d",
                    provider_dto.name,
                    result.input_tokens,
                    result.output_tokens,
                )

            return result, provider_dto, used_fallback

        except Exception as exc:
            last_error = exc
            error_type = _classify_error(exc)
            retryable = _is_retryable_error(exc, last_status_code)

            log.warning(
                "[llm-runtime] provider=%s 调用失败 error=%s retryable=%s",
                provider_dto.name,
                error_type,
                retryable,
            )

            # 认证/配置类错误不应 fallback，否则会把不可恢复配置错误伪装成线路问题。
            if idx == len(all_providers) - 1 or not retryable:
                # 记录失败 usage
                usage_record = UsageRecord(
                    provider_id=provider_dto.id,
                    account_id=account_id,
                    provider_name=provider_dto.name,
                    model=override_model or provider_dto.default_model,
                    success=False,
                    error_type=error_type,
                    source=source,
                    used_fallback=is_fallback,
                    fallback_chain=chain.get_provider_names(),
                )
                await _emit_usage(usage_record)

                raise LLMCallFailed(
                    f"所有 provider 都失败。最后错误: {type(last_error).__name__}: {last_error}",
                    provider_id=provider_dto.id,
                    provider_name=provider_dto.name,
                    error_type=error_type,
                    retryable=False,
                ) from last_error

    # 理论上不会走到这里
    raise LLMCallFailed(
        f"未预期的错误链 exhausted: {last_error}",
        provider_id=all_providers[-1].id if all_providers else None,
        error_type="exhausted",
        retryable=False,
    )


def _apply_output_token_cap(max_tokens: int) -> int:
    """应用全局 LLM 输出 token 上限；0 表示不限制。"""
    cap = int(getattr(settings, "llm_max_output_tokens", 0) or 0)
    if cap <= 0:
        return max_tokens
    if max_tokens <= 0:
        return cap
    return min(max_tokens, cap)


async def _check_budget(account_id: int | None, provider_dto: LLMProviderDTO) -> str | None:
    """检查账号级 LLM 预算。

    这是成本控制的硬门禁：限制命中时不再调用任何 provider。DB 查询失败时
    不阻断业务，只打 debug，让生产在迁移窗口内仍可降级运行。
    """
    if account_id is None:
        return None

    now = datetime.now(UTC)
    minute_start = now - timedelta(minutes=1)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    try:
        async with AsyncSessionLocal() as db:
            limits = await _load_budget_limits(db)
            per_minute = int(limits["per_minute"])
            daily_requests = int(limits["daily_requests"])
            daily_tokens = int(limits["daily_tokens"])
            premium_daily = int(limits["premium_daily"])
            if per_minute <= 0 and daily_requests <= 0 and daily_tokens <= 0 and premium_daily <= 0:
                return None

            if per_minute > 0:
                minute_count = await db.scalar(
                    select(func.count(LLMUsage.id)).where(
                        LLMUsage.account_id == account_id,
                        LLMUsage.created_at >= minute_start,
                    )
                )
                if int(minute_count or 0) >= per_minute:
                    return f"LLM 每分钟调用次数已达上限（{per_minute}/min），请稍后再试。"

            if daily_requests > 0:
                day_count = await db.scalar(
                    select(func.count(LLMUsage.id)).where(
                        LLMUsage.account_id == account_id,
                        LLMUsage.created_at >= day_start,
                    )
                )
                if int(day_count or 0) >= daily_requests:
                    return f"LLM 今日调用次数已达上限（{daily_requests}/day）。"

            if daily_tokens > 0:
                used_tokens = await db.scalar(
                    select(func.coalesce(func.sum(LLMUsage.input_tokens + LLMUsage.output_tokens), 0)).where(
                        LLMUsage.account_id == account_id,
                        LLMUsage.created_at >= day_start,
                        LLMUsage.success.is_(True),
                    )
                )
                if int(used_tokens or 0) >= daily_tokens:
                    return f"LLM 今日 token 用量已达上限（{daily_tokens}/day）。"

            if premium_daily > 0 and int(getattr(provider_dto, "cost_tier", 2) or 2) >= 3:
                premium_count = await db.scalar(
                    select(func.count(LLMUsage.id)).where(
                        LLMUsage.account_id == account_id,
                        LLMUsage.created_at >= day_start,
                        LLMUsage.success.is_(True),
                        LLMUsage.provider_id == provider_dto.id,
                    )
                )
                if int(premium_count or 0) >= premium_daily:
                    return f"高价 LLM 今日调用次数已达上限（{premium_daily}/day）。"
    except Exception:  # noqa: BLE001
        log.debug("LLM budget 检查失败，降级为不阻断 account=%s", account_id, exc_info=True)
        return None
    return None


async def _load_budget_limits(db) -> dict[str, int]:
    """读取 DB 覆盖的 LLM 限额；没有配置时回落到环境变量。"""
    limits = {
        "per_minute": int(getattr(settings, "llm_per_minute_request_limit_per_account", 0) or 0),
        "daily_requests": int(getattr(settings, "llm_daily_request_limit_per_account", 0) or 0),
        "daily_tokens": int(getattr(settings, "llm_daily_token_limit_per_account", 0) or 0),
        "premium_daily": int(getattr(settings, "llm_premium_daily_request_limit_per_account", 0) or 0),
    }
    row = await db.get(SystemSetting, "llm_limits")
    value = row.value if row is not None else None
    if isinstance(value, dict):
        limits["per_minute"] = _non_negative_int(value.get("per_minute"), limits["per_minute"])
        limits["daily_requests"] = _non_negative_int(value.get("daily_requests"), limits["daily_requests"])
        limits["daily_tokens"] = _non_negative_int(value.get("daily_tokens"), limits["daily_tokens"])
        limits["premium_daily"] = _non_negative_int(value.get("premium_daily"), limits["premium_daily"])
    return limits


def _non_negative_int(value: Any, default: int) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


async def _call_with_retry(
    provider_dto: LLMProviderDTO,
    system: str,
    user: str,
    override_model: str | None,
    max_tokens: int,
    images: list[bytes] | None,
    web_search: bool,
    web_search_context_size: str | None,
    log_prompt_preview: bool,
    client_factory: Callable[..., Any | Awaitable[Any]] | None = None,
    max_retries: int = _MAX_RETRIES,
) -> LLMResult:
    """使用指数退避重试调用单个 provider。"""
    import time

    from .llm_client import (
        LLMError,
    )

    last_error: Exception | None = None

    for attempt in range(max_retries + 1):
        start_time = time.monotonic()

        try:
            builder = client_factory or build_client_from_dto
            client = builder(
                provider_dto,
                override_model=override_model,
                proxy_url=provider_dto.proxy_url,
            )
            if inspect.isawaitable(client):
                client = await client
            if web_search:
                result = await client.complete(
                    system,
                    user,
                    max_tokens=max_tokens,
                    images=images,
                    web_search=True,
                    web_search_context_size=web_search_context_size,
                )
            else:
                result = await client.complete(
                    system,
                    user,
                    max_tokens=max_tokens,
                    images=images,
                )
            latency_ms = int((time.monotonic() - start_time) * 1000)

            if attempt > 0:
                log.info(
                    "[llm-runtime] 重试成功 provider=%s attempt=%d latency=%dms",
                    provider_dto.name,
                    attempt,
                    latency_ms,
                )

            return result

        except LLMError as exc:
            last_error = exc
            # 从错误消息中提取 status_code
            msg = str(exc)
            status_code = None
            for part in msg.split():
                if part.isdigit() and 100 <= int(part) < 600:
                    status_code = int(part)
                    break

            if not _is_retryable_error(exc, status_code):
                # 不可重试的错误（如 401/403）直接抛出
                raise

            if attempt < max_retries:
                delay = _compute_retry_delay(attempt + 1)
                log.warning(
                    "[llm-runtime] provider=%s attempt=%d/%d 失败 error=%s 等待 %.1fs",
                    provider_dto.name,
                    attempt + 1,
                    max_retries + 1,
                    str(exc)[:100],
                    delay,
                )
                await asyncio.sleep(delay)
                continue
            else:
                raise

        except Exception as exc:
            last_error = exc
            if not _is_retryable_error(exc):
                raise

            if attempt < max_retries:
                delay = _compute_retry_delay(attempt + 1)
                log.warning(
                    "[llm-runtime] provider=%s 网络错误 attempt=%d/%d 等待 %.1fs",
                    provider_dto.name,
                    attempt + 1,
                    max_retries + 1,
                    delay,
                )
                await asyncio.sleep(delay)
                continue
            else:
                raise

    # 理论上不会走到这里
    raise last_error or RuntimeError("重试耗尽但无错误信息")


# ── 辅助函数 ────────────────────────────────────────────────

def build_fallback_chain(
    primary: LLMProviderDTO,
    providers: dict[int, LLMProviderDTO] | None = None,
    fallback_provider_id: int | None = None,
    matched_tag: str | None = None,
) -> FallbackChain:
    """根据配置构建 fallback chain。

    优先级：
    1. primary（显式指定）
    2. fallback_provider_id（router 配置）
    3. 同 tag 但 cost_tier 更低的 provider

    Args:
        primary: 主要 provider
        providers: 所有可用 provider 字典
        fallback_provider_id: 路由配置的 fallback
        matched_tag: 匹配的 tag（用于找同 tag 低价 provider）
    """
    fallbacks: list[LLMProviderDTO] = []

    # 1. fallback_provider_id
    if providers and fallback_provider_id is not None:
        fb = providers.get(fallback_provider_id)
        if fb and fb.id != primary.id and fb.has_api_key:
            fallbacks.append(fb)

    # 2. 同 tag 低价 provider
    if providers and matched_tag:
        same_tag = [
            p for p in providers.values()
            if p.id != primary.id
            and matched_tag in p.tags
            and p.cost_tier < primary.cost_tier
            and p.has_api_key
        ]
        same_tag.sort(key=lambda p: p.cost_tier)
        for p in same_tag:
            if p not in fallbacks:
                fallbacks.append(p)

    # 3. 其他有 key 的 provider
    if providers:
        others = [
            p for p in providers.values()
            if p.id != primary.id
            and p not in fallbacks
            and p.has_api_key
        ]
        others.sort(key=lambda p: p.cost_tier)
        fallbacks.extend(others[:2])  # 最多再加 2 个通用 fallback

    return FallbackChain(primary=primary, fallbacks=fallbacks)


__all__ = [
    "FallbackChain",
    "UsageRecord",
    "build_fallback_chain",
    "call_with_fallback",
    "register_usage_callback",
]
