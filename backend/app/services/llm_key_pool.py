"""Provider 密钥池运行时：候选排序、密钥级冷却、每日用量与会话亲和。

设计边界：
- 候选 = 主密钥（``LLMProvider.api_key_enc``，若有）+ 启用中的池内密钥。没有池内密钥时
  ``KeySelection.apply`` 原样返回 Provider，三条调用链路行为与单密钥时期完全一致。
- 密钥级健康只在本模块维护（进程内存），与 ``provider_health`` 的 Provider 级健康分离：
  429 / 401 / 403 / 额度耗尽只冷却当前密钥，不拖累整个 Provider。
- Redis 负责两件跨进程的事：按密钥的当日用量（可选上限依据）与按会话的密钥亲和
  （让同一对话在 prompt cache 有效期内粘在同一把密钥上）。Redis 不可用时退化为
  无亲和、无用量约束，但选钥本身照常进行。
"""

from __future__ import annotations

import dataclasses
import hashlib
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ..redis_client import get_redis
from .llm_dto import LLMProviderDTO
from .provider_health import ErrorClass, classify_error

log = logging.getLogger(__name__)

MAIN_KEY_LABEL = "主密钥"

_AFFINITY_TTL_SECONDS = 30 * 60
_USAGE_TTL_SECONDS = 2 * 24 * 3600
_RATE_LIMIT_COOLDOWN_SECONDS = 30
_CREDENTIAL_COOLDOWN_SECONDS = 10 * 60
_MAX_COOLDOWN_SECONDS = 10 * 60

_KEY_SCOPED_ERROR_CLASSES = {ErrorClass.CREDENTIAL, ErrorClass.RATE_LIMIT}


@dataclass(frozen=True)
class KeyCandidate:
    """一把可用于出站调用的凭据；``key_id`` 为 ``None`` 表示主密钥。"""

    key_id: int | None
    label: str
    api_key_enc: str | None
    proxy_url: str | None
    priority: int = 0
    daily_request_limit: int | None = None
    daily_token_limit: int | None = None

    @property
    def is_main(self) -> bool:
        return self.key_id is None


@dataclass
class _KeyHealth:
    consecutive_failures: int = 0
    cooldown_until: float | None = None
    last_error_class: str | None = None
    last_error_message: str | None = None
    last_failure_at: float | None = None
    last_success_at: float | None = None

    def cooldown_remaining(self, now: float | None = None) -> float:
        if self.cooldown_until is None:
            return 0.0
        return max(0.0, self.cooldown_until - (now or time.time()))

    def to_public(self, now: float | None = None) -> dict[str, Any]:
        remaining = self.cooldown_remaining(now)
        return {
            "state": "cooling" if remaining > 0 else "healthy",
            "consecutive_failures": int(self.consecutive_failures),
            "cooldown_remaining_seconds": int(remaining),
            "last_error_class": self.last_error_class,
            "last_error_message": self.last_error_message,
            "last_failure_at": self.last_failure_at,
            "last_success_at": self.last_success_at,
        }


_health: dict[str, _KeyHealth] = {}
_rotation_cursor: dict[int, int] = {}


def _health_key(provider_id: int, key_id: int | None) -> str:
    return f"{int(provider_id)}:{'main' if key_id is None else int(key_id)}"


def _utc_day() -> str:
    return datetime.now(UTC).strftime("%Y%m%d")


def _usage_redis_key(provider_id: int, key_id: int, day: str | None = None) -> str:
    return f"telepilot:llm_key_usage:{int(provider_id)}:{int(key_id)}:{day or _utc_day()}"


def _affinity_redis_key(provider_id: int, scope: str) -> str:
    digest = hashlib.sha1(scope.encode("utf-8")).hexdigest()[:20]  # noqa: S324 - 仅做键名缩短
    return f"telepilot:llm_key_affinity:{int(provider_id)}:{digest}"


def _cooldown_for(error_class: ErrorClass, failures: int) -> int:
    if error_class == ErrorClass.RATE_LIMIT:
        return _RATE_LIMIT_COOLDOWN_SECONDS
    if error_class == ErrorClass.CREDENTIAL:
        # 凭据失效通常不会自愈，但也不能永久拉黑：用户可能刚在上游充值/解封。
        return _CREDENTIAL_COOLDOWN_SECONDS
    if error_class == ErrorClass.CAPABILITY:
        return 0
    return min(_MAX_COOLDOWN_SECONDS, 15 * (2 ** max(0, failures - 1)))


def record_key_failure(provider_id: int, key_id: int | None, exc: BaseException | str | None) -> ErrorClass:
    error_class = classify_error(exc)
    key = _health_key(provider_id, key_id)
    rec = _health.get(key) or _KeyHealth()
    now = time.time()
    rec.last_failure_at = now
    rec.last_error_class = error_class.value
    rec.last_error_message = str(exc or "")[:300]
    if error_class != ErrorClass.CAPABILITY:
        rec.consecutive_failures += 1
        cool = _cooldown_for(error_class, rec.consecutive_failures)
        rec.cooldown_until = now + cool if cool > 0 else None
    _health[key] = rec
    return error_class


def record_key_success(provider_id: int, key_id: int | None) -> None:
    key = _health_key(provider_id, key_id)
    rec = _health.get(key) or _KeyHealth()
    rec.consecutive_failures = 0
    rec.cooldown_until = None
    rec.last_error_class = None
    rec.last_error_message = None
    rec.last_success_at = time.time()
    _health[key] = rec


def key_health(provider_id: int, key_id: int | None) -> dict[str, Any]:
    rec = _health.get(_health_key(provider_id, key_id))
    if rec is None:
        return _KeyHealth().to_public()
    return rec.to_public()


def reset_state_for_tests() -> None:
    _health.clear()
    _rotation_cursor.clear()


def is_key_scoped_error(exc: BaseException | str | None) -> bool:
    """错误是否只应归咎于当前密钥（而非 Provider / 请求本身）。"""
    return classify_error(exc) in _KEY_SCOPED_ERROR_CLASSES


# ── Redis：每日用量与亲和 ──────────────────────────────────


async def load_daily_usage(provider_id: int, key_ids: list[int]) -> dict[int, tuple[int, int]]:
    """返回 ``{key_id: (requests, tokens)}``；Redis 不可用时返回空 dict。"""
    if not key_ids:
        return {}
    try:
        redis = get_redis()
        pipe = redis.pipeline(transaction=False)
        day = _utc_day()
        for key_id in key_ids:
            pipe.hmget(_usage_redis_key(provider_id, key_id, day), "requests", "tokens")
        rows = await pipe.execute()
    except Exception:  # noqa: BLE001
        log.debug("llm key pool: 读取每日用量失败，按无约束处理", exc_info=True)
        return {}
    out: dict[int, tuple[int, int]] = {}
    for key_id, row in zip(key_ids, rows, strict=False):
        try:
            requests = int((row or [None, None])[0] or 0)
            tokens = int((row or [None, None])[1] or 0)
        except (TypeError, ValueError):
            requests, tokens = 0, 0
        out[int(key_id)] = (requests, tokens)
    return out


async def add_daily_usage(provider_id: int, key_id: int, *, requests: int = 1, tokens: int = 0) -> None:
    try:
        redis = get_redis()
        name = _usage_redis_key(provider_id, key_id)
        pipe = redis.pipeline(transaction=False)
        if requests:
            pipe.hincrby(name, "requests", int(requests))
        if tokens:
            pipe.hincrby(name, "tokens", int(tokens))
        pipe.expire(name, _USAGE_TTL_SECONDS)
        await pipe.execute()
    except Exception:  # noqa: BLE001
        log.debug("llm key pool: 写入每日用量失败", exc_info=True)


async def get_affinity(provider_id: int, scope: str | None) -> int | None | bool:
    """返回亲和的 key_id（``None`` 表示主密钥）；无记录返回 ``False``。"""
    if not scope:
        return False
    try:
        raw = await get_redis().get(_affinity_redis_key(provider_id, scope))
    except Exception:  # noqa: BLE001
        log.debug("llm key pool: 读取亲和失败", exc_info=True)
        return False
    if raw is None:
        return False
    text = str(raw)
    if text == "main":
        return None
    try:
        return int(text)
    except ValueError:
        return False


async def set_affinity(provider_id: int, scope: str | None, key_id: int | None) -> None:
    if not scope:
        return
    try:
        await get_redis().set(
            _affinity_redis_key(provider_id, scope),
            "main" if key_id is None else str(int(key_id)),
            ex=_AFFINITY_TTL_SECONDS,
        )
    except Exception:  # noqa: BLE001
        log.debug("llm key pool: 写入亲和失败", exc_info=True)


# ── 选钥会话 ──────────────────────────────────────────────


def resolve_pool_key_proxies(
    provider: LLMProviderDTO,
    proxies_by_id: dict[int, Any],
    build_url: Any,
    *,
    log_prefix: str = "LLM Provider",
) -> None:
    """为池内密钥解析出口代理 URL；代理缺失或不可用的密钥会被禁用而不是回落直连。

    ``build_url(proxy_row) -> str`` 由调用方提供（worker / 插件各自有现成实现），
    抛出任何异常都视为该密钥不可用。
    """
    for entry in provider.keys:
        proxy_id = entry.get("proxy_id")
        if proxy_id is None:
            continue
        proxy_row = proxies_by_id.get(int(proxy_id))
        try:
            if proxy_row is None:
                raise LookupError(f"代理 {proxy_id} 不存在")
            entry["proxy_url"] = build_url(proxy_row)
        except Exception as exc:  # noqa: BLE001
            log.error(
                "%s %s 的密钥「%s」引用的代理 %s 不可用，已禁用该密钥：%s",
                log_prefix,
                provider.id,
                entry.get("label"),
                proxy_id,
                exc,
            )
            entry["enabled"] = False
            entry["proxy_url"] = None


def build_candidates(provider: LLMProviderDTO) -> list[KeyCandidate]:
    """主密钥（priority 0）+ 启用池内密钥，按 (priority, id) 排序。"""
    out: list[KeyCandidate] = []
    if provider.api_key_enc or not provider.enabled_keys():
        # 没有任何池内密钥时也放入"主密钥"候选（可能为空，交给 build_client 处理 ollama 等情况）。
        out.append(
            KeyCandidate(
                key_id=None,
                label=MAIN_KEY_LABEL,
                api_key_enc=provider.api_key_enc,
                proxy_url=provider.proxy_url,
                priority=0,
            )
        )
    pool = [
        KeyCandidate(
            key_id=int(entry.get("id") or 0),
            label=str(entry.get("label") or f"key-{entry.get('id')}"),
            api_key_enc=str(entry.get("api_key_enc")),
            proxy_url=entry.get("proxy_url") or provider.proxy_url,
            priority=int(entry.get("priority") or 100),
            daily_request_limit=_positive_or_none(entry.get("daily_request_limit")),
            daily_token_limit=_positive_or_none(entry.get("daily_token_limit")),
        )
        for entry in provider.enabled_keys()
    ]
    pool.sort(key=lambda c: (c.priority, c.key_id or 0))
    out.extend(pool)
    return out


def _positive_or_none(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


class KeySelection:
    """一次 Provider 尝试内的选钥状态机。

    用法：
        selection = await open_selection(provider, scope=...)
        while True:
            effective = selection.apply(provider)
            try:
                ...call with effective...
                await selection.report_success(tokens)
                break
            except Exception as exc:
                if await selection.report_failure(exc):
                    continue   # 换钥重试，不消耗 Provider 级重试预算
                raise
    """

    def __init__(
        self,
        provider: LLMProviderDTO,
        ordered: list[KeyCandidate],
        *,
        scope: str | None,
        has_pool: bool,
    ) -> None:
        self.provider_id = int(provider.id)
        self.scope = scope
        self.has_pool = has_pool
        self._ordered = list(ordered)
        self._cursor = 0
        self.current: KeyCandidate | None = self._ordered[0] if self._ordered else None
        self.tried: list[KeyCandidate] = []

    @property
    def remaining(self) -> int:
        return max(0, len(self._ordered) - self._cursor - 1)

    def apply(self, provider: LLMProviderDTO) -> LLMProviderDTO:
        """把当前候选的凭据/代理套到 Provider 上；主密钥且无覆盖时原样返回。"""
        candidate = self.current
        if candidate is None or (candidate.is_main and candidate.proxy_url == provider.proxy_url):
            return provider
        return dataclasses.replace(
            provider,
            api_key_enc=candidate.api_key_enc,
            proxy_url=candidate.proxy_url,
        )

    def usage_fields(self) -> dict[str, Any]:
        candidate = self.current
        if candidate is None or candidate.is_main:
            return {"provider_key_id": None, "provider_key_label": None}
        return {"provider_key_id": candidate.key_id, "provider_key_label": candidate.label}

    async def report_failure(self, exc: BaseException) -> bool:
        """记录当前密钥失败；若错误属于密钥作用域且还有未试密钥则切换并返回 True。"""
        candidate = self.current
        if candidate is None:
            return False
        error_class = record_key_failure(self.provider_id, candidate.key_id, exc)
        if self.has_pool and candidate.key_id is not None:
            await add_daily_usage(self.provider_id, candidate.key_id, requests=1, tokens=0)
        if not self.has_pool or error_class not in _KEY_SCOPED_ERROR_CLASSES or self.remaining <= 0:
            return False
        self.tried.append(candidate)
        self._cursor += 1
        self.current = self._ordered[self._cursor]
        log.info(
            "[llm-key-pool] provider=%s 密钥「%s」%s，切换到「%s」",
            self.provider_id,
            candidate.label,
            error_class.value,
            self.current.label,
        )
        return True

    async def report_success(self, *, tokens: int = 0) -> None:
        candidate = self.current
        if candidate is None:
            return
        record_key_success(self.provider_id, candidate.key_id)
        if not self.has_pool:
            return
        if candidate.key_id is not None:
            await add_daily_usage(self.provider_id, candidate.key_id, requests=1, tokens=max(0, int(tokens)))
        await set_affinity(self.provider_id, self.scope, candidate.key_id)


async def open_selection(provider: LLMProviderDTO, *, scope: str | None = None) -> KeySelection:
    """按 Provider 策略排好候选顺序并返回选钥会话。

    候选为空（所有池内密钥都超当日上限且无主密钥）时返回的会话 ``current`` 为 ``None``，
    调用方应把它当作该 Provider 本轮不可用。
    """
    candidates = build_candidates(provider)
    has_pool = any(not c.is_main for c in candidates)
    if not has_pool:
        return KeySelection(provider, candidates, scope=scope, has_pool=False)

    provider_id = int(provider.id)
    pool_ids = [c.key_id for c in candidates if c.key_id is not None]
    usage = await load_daily_usage(provider_id, pool_ids)

    def over_limit(c: KeyCandidate) -> bool:
        if c.key_id is None:
            return False
        requests, tokens = usage.get(c.key_id, (0, 0))
        if c.daily_request_limit and requests >= c.daily_request_limit:
            return True
        return bool(c.daily_token_limit and tokens >= c.daily_token_limit)

    available = [c for c in candidates if not over_limit(c)]
    if not available:
        log.warning("[llm-key-pool] provider=%s 所有密钥均已达当日上限", provider_id)
        return KeySelection(provider, [], scope=scope, has_pool=True)

    now = time.time()

    def cooldown(c: KeyCandidate) -> float:
        rec = _health.get(_health_key(provider_id, c.key_id))
        return rec.cooldown_remaining(now) if rec else 0.0

    strategy = provider.key_strategy
    if strategy == "least_used":
        available.sort(
            key=lambda c: (usage.get(c.key_id, (0, 0))[1] if c.key_id is not None else 0, c.priority)
        )
    elif strategy == "rotate":
        cursor = _rotation_cursor.get(provider_id, 0)
        _rotation_cursor[provider_id] = cursor + 1
        offset = cursor % len(available)
        available = available[offset:] + available[:offset]

    # 冷却中的密钥统一排到末尾（按剩余时间升序），而不是直接剔除：全部冷却时仍要有钥可试。
    available.sort(key=lambda c: (cooldown(c) > 0, cooldown(c)))

    affinity = await get_affinity(provider_id, scope)
    if affinity is not False:
        for idx, c in enumerate(available):
            if c.key_id == affinity and cooldown(c) <= 0:
                available.insert(0, available.pop(idx))
                break

    return KeySelection(provider, available, scope=scope, has_pool=True)


async def open_selection_or_raise(provider: LLMProviderDTO, *, scope: str | None = None) -> KeySelection:
    """打开选钥会话；所有密钥达当日上限时抛 Provider 本地错误，让 fallback 继续。

    转写、插件流式等旁路主链的入口此前自己 build_client，完全绕过密钥池，导致
    pool-only Provider（无主 Key、只有池内 Key）在这些路径上直接 401。统一走这个
    公共入口，保证换 Key / 用量记录 / 冷却行为与主链路一致。
    """
    from .llm_client import LLMError, LLMErrorScope

    selection = await open_selection(provider, scope=scope)
    if selection.current is None:
        raise LLMError(
            f"Provider「{provider.name}」的所有密钥均已达到当日用量上限",
            retryable=False,
            scope=LLMErrorScope.PROVIDER_LOCAL,
            category="key_pool_exhausted",
        )
    return selection


__all__ = [
    "MAIN_KEY_LABEL",
    "KeyCandidate",
    "KeySelection",
    "build_candidates",
    "is_key_scoped_error",
    "key_health",
    "load_daily_usage",
    "open_selection",
    "open_selection_or_raise",
    "record_key_failure",
    "record_key_success",
    "reset_state_for_tests",
]
