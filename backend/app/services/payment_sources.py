"""``payment_sources`` 扩展点：转账通知解析由插件提供（B1 下沉，0.111.0+）。

插件怎么接入
------------

``plugin.json`` 声明 ``"payment_sources": [{"id": "cn_transfer", "title": "中文转账通知"}]``，插件模块
（``plugin.py`` 或包 ``__init__``）导出纯函数::

    def parse_payment_notice(text: str, meta: dict) -> dict | None:
        # 返回 {"payer_name", "receiver_name", "amount", "payer_user_id"?, "receiver_user_id"?, "unit"?}
        # 不是转账通知时返回 None

可以是 ``async def``。宿主只在**可信 Bot ID 白名单**通过之后调用它（交互 Bot 配置的
``trusted_bot_ids`` / ``transfer_bot_id``）；``meta`` 只含 ``account_id / chat_id / message_id /
sender_id / source``（``text`` 或 ``reply_to_text``），插件拿不到 Telegram client、数据库或任何
结算接口——插件**只返回解析结果**，结算仍走宿主受控链路（规则匹配、幂等、补偿都不变）。

双跑对照
--------

- 默认以宿主旧解析器（:mod:`payment_notice_legacy`）为准，插件并行解析只做对照；两边不一致时记
  warning 日志、Trace span（``reason_code=payment_source_mismatch``）和插件运行统计
  （钩子 ``parse_payment_notice``，事件 ``agree`` / ``mismatch`` / ``plugin_failed``）。
- 运行设置 ``log_retention.payment_source_plugin_authoritative``（「以插件结果为准」，默认关闭）
  打开后改用插件结果；插件超时（:data:`PARSE_TIMEOUT_SECONDS`）、抛异常、返回值不合法、未安装或
  未启用时一律回到宿主旧解析（fail-open）。
- 比较字段：:data:`COMPARED_FIELDS`。``unit`` 只记录、不参与比较，也不进入结算数据。

旧宿主不认识 ``payment_sources``，插件装上只是一个没有任何订阅的普通插件，不会被调用；没有声明
``payment_sources`` 的旧插件也永远不会被这里加载。
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import compat_usage

log = logging.getLogger(__name__)

HOOK = "parse_payment_notice"
MANIFEST_KEY = "payment_sources"
PARSE_TIMEOUT_SECONDS = 1.0
SOURCES_CACHE_TTL_SECONDS = 30.0
STATS_FLUSH_DELAY_SECONDS = 5.0

COMPARED_FIELDS: tuple[str, ...] = (
    "payer_name",
    "receiver_name",
    "amount",
    "payer_user_id",
    "receiver_user_id",
)
SETTLEMENT_FIELDS = COMPARED_FIELDS

VERDICT_AGREE = "agree"
VERDICT_MISMATCH = "mismatch"
VERDICT_PLUGIN_FAILED = "plugin_failed"
VERDICT_NO_SOURCE = "no_source"

REASON_MISMATCH = "payment_source_mismatch"
REASON_FAILED = "payment_source_failed"

MAX_NAME_CHARS = 256
MAX_UNIT_CHARS = 32
MAX_ID_SOURCE_CHARS = 64


class PaymentResultError(ValueError):
    """插件返回值不合法（按插件失败处理，回退宿主旧解析）。"""


@dataclass(frozen=True)
class PaymentSource:
    key: str
    parser: Callable[[str, dict[str, Any]], Any]
    version: str = ""
    source_ids: tuple[str, ...] = ()


@dataclass
class SourceCall:
    key: str
    outcome: str  # ok / fail / timeout（与 plugin_stats 同口径）
    result: dict[str, Any] | None = None
    unit: str | None = None
    error: BaseException | None = None
    duration_ms: float = 0.0
    source_text: str | None = None  # text / reply_to_text


@dataclass
class DualParseOutcome:
    result: dict[str, Any] | None
    legacy: dict[str, Any] | None
    verdict: str
    plugin: dict[str, Any] | None = None
    plugin_key: str | None = None
    plugin_unit: str | None = None
    used_plugin: bool = False
    authoritative: bool = False
    diffs: list[str] = field(default_factory=list)
    calls: list[SourceCall] = field(default_factory=list)


# ─────────────────────────────────────────────────────
# 返回值规范化与比较
# ─────────────────────────────────────────────────────


def _as_int(value: Any, field_name: str, *, optional: bool) -> int | None:
    if value is None:
        if optional:
            return None
        raise PaymentResultError(f"{field_name} 缺失")
    if isinstance(value, bool):
        raise PaymentResultError(f"{field_name} 必须是整数")
    if isinstance(value, int):
        return int(value)
    if isinstance(value, str) and value.strip().isdigit() and len(value.strip()) <= MAX_ID_SOURCE_CHARS:
        return int(value.strip())
    raise PaymentResultError(f"{field_name} 必须是整数")


def _as_name(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise PaymentResultError(f"{field_name} 必须是字符串")
    text = value.strip()
    if not text:
        raise PaymentResultError(f"{field_name} 不能为空")
    if len(text) > MAX_NAME_CHARS:
        raise PaymentResultError(f"{field_name} 超过 {MAX_NAME_CHARS} 字")
    return text


def normalize_result(raw: Any) -> tuple[dict[str, Any] | None, str | None]:
    """插件返回值 → ``(结算字段 dict 或 None, unit)``；不合法时抛 :class:`PaymentResultError`。

    接受 ``payer`` / ``receiver`` 作为 ``payer_name`` / ``receiver_name`` 的别名，``currency`` 作为
    ``unit`` 的别名。金额必须是 ≥0 的整数（与宿主旧解析器一致，``\\d+``）。
    """

    if raw is None:
        return None, None
    if not isinstance(raw, Mapping):
        raise PaymentResultError("返回值必须是 dict 或 None")
    payer = raw.get("payer_name", raw.get("payer"))
    receiver = raw.get("receiver_name", raw.get("receiver"))
    out: dict[str, Any] = {
        "payer_name": _as_name(payer, "payer_name"),
        "receiver_name": _as_name(receiver, "receiver_name"),
    }
    amount = _as_int(raw.get("amount"), "amount", optional=False)
    if amount is None or amount < 0:
        raise PaymentResultError("amount 必须是非负整数")
    out["amount"] = amount
    for key in ("payer_user_id", "receiver_user_id"):
        value = _as_int(raw.get(key), key, optional=True)
        if value is not None:
            out[key] = value
    unit_raw = raw.get("unit", raw.get("currency"))
    unit = None
    if isinstance(unit_raw, str) and unit_raw.strip():
        unit = unit_raw.strip()[:MAX_UNIT_CHARS]
    return out, unit


def settlement_view(parsed: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if parsed is None:
        return None
    return {key: parsed[key] for key in SETTLEMENT_FIELDS if key in parsed and parsed[key] is not None}


def compare(legacy: Mapping[str, Any] | None, plugin: Mapping[str, Any] | None) -> list[str]:
    """返回不一致的字段说明；空列表表示一致（两边都是 None 也算一致）。"""

    if legacy is None and plugin is None:
        return []
    if legacy is None:
        return ["宿主未识别，插件识别为转账通知"]
    if plugin is None:
        return ["宿主识别为转账通知，插件未识别"]
    diffs: list[str] = []
    for key in COMPARED_FIELDS:
        left, right = legacy.get(key), plugin.get(key)
        if left != right:
            diffs.append(f"{key}: 宿主={left!r} 插件={right!r}")
    return diffs


# ─────────────────────────────────────────────────────
# 插件发现（主进程，30 秒缓存）
# ─────────────────────────────────────────────────────

_SOURCES_CACHE: tuple[float, list[PaymentSource]] = (0.0, [])
_LOADED: dict[str, tuple[str, PaymentSource]] = {}
_DECLARATION_CACHE: dict[str, tuple[float, tuple[str, ...]]] = {}
_AUTHORITATIVE_HINT = False
_STATS_FLUSH_TASK: asyncio.Task[Any] | None = None


def reset_cache() -> None:
    """清空发现缓存（插件安装 / 启停 / 升级后、测试里调用）。"""

    global _SOURCES_CACHE
    _SOURCES_CACHE = (0.0, [])
    _LOADED.clear()
    _DECLARATION_CACHE.clear()


def set_sources_for_test(sources: Iterable[PaymentSource]) -> None:
    global _SOURCES_CACHE
    _SOURCES_CACHE = (time.monotonic() + 10**9, list(sources))


def cached_sources() -> list[PaymentSource]:
    return list(_SOURCES_CACHE[1])


def set_authoritative_hint(value: bool) -> None:
    global _AUTHORITATIVE_HINT
    _AUTHORITATIVE_HINT = bool(value)


def plugin_route_hint() -> bool:
    """同步路由分类用：「以插件结果为准」已打开且已发现至少一个插件来源。

    只有这时，可信 Bot 发来的、宿主旧解析认不出的消息也要进入付款通知链路交给插件判断；
    对照模式（默认）下路由与旧版本完全一样。
    """

    return _AUTHORITATIVE_HINT and bool(_SOURCES_CACHE[1])


def declared_source_ids(raw: Any) -> tuple[str, ...]:
    """``plugin.json`` 的 ``payment_sources`` 声明 → 来源 id 列表（不合法的条目忽略）。"""

    if not isinstance(raw, list):
        return ()
    ids: list[str] = []
    for item in raw:
        if isinstance(item, Mapping):
            value = str(item.get("id") or "").strip()
        elif isinstance(item, str):
            value = item.strip()
        else:
            continue
        if value and value not in ids:
            ids.append(value[:64])
    return tuple(ids)


def _declaration_from_plugin_json(installed_path: str | None, manifest_json: Any) -> tuple[str, ...]:
    path = str(installed_path or "").strip()
    if path:
        metadata = Path(path) / "plugin.json"
        try:
            mtime = metadata.stat().st_mtime
        except OSError:
            mtime = -1.0
        cached = _DECLARATION_CACHE.get(path)
        if cached is not None and cached[0] == mtime:
            return cached[1]
        ids: tuple[str, ...] = ()
        if mtime >= 0:
            try:
                raw = json.loads(metadata.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raw = {}
            if isinstance(raw, Mapping):
                ids = declared_source_ids(raw.get(MANIFEST_KEY))
        _DECLARATION_CACHE[path] = (mtime, ids)
        if ids:
            return ids
    if isinstance(manifest_json, Mapping):
        return declared_source_ids(manifest_json.get(MANIFEST_KEY))
    return ()


def _block_reason(installed: Any) -> str | None:
    from ..db.models.plugin import PLUGIN_TRUST_ORPHAN
    from ..settings import settings

    if not bool(getattr(installed, "enabled", False)):
        return "disabled"
    if str(getattr(installed, "trust_tier", "") or "") == PLUGIN_TRUST_ORPHAN:
        return "orphan"
    signature_ok = getattr(installed, "signature_ok", None)
    if signature_ok is False:
        return "signature_failed"
    if signature_ok is None and not bool(settings.plugin_allow_legacy_unsigned_plugins):
        return "signature_unknown"
    if str(getattr(installed, "last_install_error", "") or "").strip():
        return "install_error"
    return None


def _find_parser(plugin_key: str) -> Callable[..., Any] | None:
    import sys

    from ..worker.plugins.loader import _installed_module_name, _load_installed_plugin

    loaded = _load_installed_plugin(plugin_key)
    if plugin_key not in loaded:
        return None
    package_name = _installed_module_name(plugin_key)
    for name in (f"{package_name}.plugin", package_name):
        module = sys.modules.get(name)
        parser = getattr(module, HOOK, None) if module is not None else None
        if callable(parser):
            return parser
    return None


async def load_payment_sources(*, force: bool = False) -> list[PaymentSource]:
    """已安装、全局启用、签名策略允许、且声明了 ``payment_sources`` 的插件（按 key 排序）。"""

    global _SOURCES_CACHE
    now = time.monotonic()
    cached_at, cached = _SOURCES_CACHE
    if not force and now - cached_at < SOURCES_CACHE_TTL_SECONDS:
        return list(cached)
    sources: list[PaymentSource] = []
    try:
        from sqlalchemy import select

        from ..db.base import AsyncSessionLocal
        from ..db.models.plugin import InstalledPlugin

        async with AsyncSessionLocal() as db:
            rows = list((await db.execute(select(InstalledPlugin).where(InstalledPlugin.enabled.is_(True)))).scalars())
        for row in sorted(rows, key=lambda item: str(item.key)):
            key = str(row.key)
            ids = _declaration_from_plugin_json(row.installed_path, row.manifest_json)
            if not ids:
                continue
            reason = _block_reason(row)
            if reason is not None:
                log.info("payment source %s 未启用（%s），转账通知继续用宿主旧解析", key, reason)
                continue
            version = str(row.version or "")
            stamp = f"{version}|{getattr(row, 'updated_at', '')}"
            previous = _LOADED.get(key)
            if previous is not None and previous[0] == stamp:
                sources.append(previous[1])
                continue
            try:
                parser = _find_parser(key)
            except Exception as exc:  # noqa: BLE001 - 插件加载失败只影响它自己
                log.warning("payment source %s 加载失败：%s", key, type(exc).__name__)
                parser = None
            if parser is None:
                log.warning("payment source %s 声明了 payment_sources 但没有导出 %s，已忽略", key, HOOK)
                continue
            source = PaymentSource(key=key, parser=parser, version=version, source_ids=ids)
            _LOADED[key] = (stamp, source)
            sources.append(source)
    except Exception:  # noqa: BLE001 - 发现失败时保持旧缓存，宿主旧解析照常工作
        log.debug("payment source 发现失败，沿用上次结果", exc_info=True)
        sources = list(cached)
    _SOURCES_CACHE = (now, sources)
    return list(sources)


# ─────────────────────────────────────────────────────
# 调用与双跑
# ─────────────────────────────────────────────────────


async def _invoke(parser: Callable[..., Any], text: str, meta: dict[str, Any], timeout: float) -> Any:
    if inspect.iscoroutinefunction(parser):
        return await asyncio.wait_for(parser(text, dict(meta)), timeout=timeout)
    # 同步解析函数放到线程里跑，超时后不等它（正则失控也卡不住账号的消息处理）。
    value = await asyncio.wait_for(asyncio.to_thread(parser, text, dict(meta)), timeout=timeout)
    if inspect.isawaitable(value):
        value = await asyncio.wait_for(value, timeout=timeout)
    return value


async def call_source(
    source: PaymentSource,
    texts: list[tuple[str, str]],
    meta: Mapping[str, Any],
    *,
    timeout: float = PARSE_TIMEOUT_SECONDS,
) -> SourceCall:
    """按宿主同样的顺序（正文 → 被回复消息）逐段交给插件，第一段识别成功即返回。"""

    from ..worker.plugins import plugin_stats

    started = time.monotonic()
    call = SourceCall(key=source.key, outcome=plugin_stats.OUTCOME_OK)
    try:
        for origin, text in texts:
            raw = await _invoke(source.parser, text, {**dict(meta), "source": origin}, timeout)
            parsed, unit = normalize_result(raw)
            if parsed is not None:
                call.result, call.unit, call.source_text = parsed, unit, origin
                break
    except Exception as exc:  # noqa: BLE001 - 插件失败 → fail-open
        call.outcome = plugin_stats.classify_exception(exc)
        call.error = exc
        call.result = None
        call.unit = None
    call.duration_ms = (time.monotonic() - started) * 1000
    return call


def _record_stats(call: SourceCall, *, account_id: int | None, verdict: str) -> None:
    from ..worker.plugins import plugin_stats

    plugin_stats.record_hook(
        account_id=account_id,
        plugin_key=call.key,
        hook=HOOK,
        event_type=verdict,
        outcome=call.outcome,
        duration_ms=call.duration_ms,
        error=call.error,
    )
    _schedule_stats_flush()


def _schedule_stats_flush() -> None:
    """主进程没有 worker 那样的统计 Redis 工厂：这里攒 5 秒批量写一次。"""

    global _STATS_FLUSH_TASK
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = _STATS_FLUSH_TASK
    if task is not None and not task.done() and task.get_loop() is loop:
        return

    async def _flush() -> None:
        from ..redis_client import get_redis
        from ..worker.plugins import plugin_stats

        try:
            await asyncio.sleep(STATS_FLUSH_DELAY_SECONDS)
            await plugin_stats.get_recorder().flush(get_redis())
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - 统计失败不影响解析
            log.debug("payment source 统计写入失败", exc_info=True)

    _STATS_FLUSH_TASK = loop.create_task(_flush(), name="payment-source-stats-flush")


async def dual_parse(
    texts: list[tuple[str, str]],
    meta: Mapping[str, Any],
    legacy_parse: Callable[[str], dict[str, Any] | None],
    *,
    authoritative: bool = False,
    sources: list[PaymentSource] | None = None,
    timeout: float = PARSE_TIMEOUT_SECONDS,
) -> DualParseOutcome:
    """宿主旧解析 + 插件解析双跑；返回最终采用的结果与对照结论。

    - 宿主旧解析：与旧版本完全相同（逐段，第一段识别成功即止）；
    - 插件：按 key 顺序调用每个来源；第一个识别成功的来源作为对照对象，都没识别时取第一个正常
      返回的来源（结果 None），全部失败记 ``plugin_failed``；
    - ``authoritative`` 且有正常返回的来源时采用插件结果（含“插件判断不是转账通知”的 None），
      否则采用宿主旧解析结果。
    """

    legacy: dict[str, Any] | None = None
    for _origin, text in texts:
        parsed = legacy_parse(text)
        if parsed is not None:
            legacy = parsed
            break
    if sources is None:
        sources = await load_payment_sources()
    if not sources:
        if legacy is not None:
            compat_usage.hit("C20", None)
        return DualParseOutcome(result=legacy, legacy=legacy, verdict=VERDICT_NO_SOURCE, authoritative=authoritative)

    calls: list[SourceCall] = []
    chosen: SourceCall | None = None
    first_ok: SourceCall | None = None
    for source in sources:
        call = await call_source(source, texts, meta, timeout=timeout)
        calls.append(call)
        if call.error is not None:
            continue
        if first_ok is None:
            first_ok = call
        if call.result is not None:
            chosen = call
            break
    primary = chosen or first_ok
    account_id = _int_or_none(meta.get("account_id"))
    if primary is None:
        for call in calls:
            _record_stats(call, account_id=account_id, verdict=VERDICT_PLUGIN_FAILED)
        if legacy is not None:
            compat_usage.hit("C20", calls[0].key if calls else None)
        return DualParseOutcome(
            result=legacy,
            legacy=legacy,
            verdict=VERDICT_PLUGIN_FAILED,
            plugin_key=calls[0].key if calls else None,
            authoritative=authoritative,
            calls=calls,
        )
    diffs = compare(settlement_view(legacy), primary.result)
    verdict = VERDICT_MISMATCH if diffs else VERDICT_AGREE
    for call in calls:
        _record_stats(
            call,
            account_id=account_id,
            verdict=(VERDICT_PLUGIN_FAILED if call.error is not None else verdict),
        )
    used_plugin = bool(authoritative)
    result = dict(primary.result) if used_plugin and primary.result is not None else None
    if not used_plugin:
        result = legacy
        if legacy is not None:
            compat_usage.hit("C20", primary.key)
    return DualParseOutcome(
        result=result,
        legacy=legacy,
        verdict=verdict,
        plugin=primary.result,
        plugin_key=primary.key,
        plugin_unit=primary.unit,
        used_plugin=used_plugin,
        authoritative=authoritative,
        diffs=diffs,
        calls=calls,
    )


def _int_or_none(value: Any) -> int | None:
    try:
        return None if value is None or isinstance(value, bool) else int(value)
    except (TypeError, ValueError):
        return None



__all__ = [
    "COMPARED_FIELDS",
    "DualParseOutcome",
    "HOOK",
    "MANIFEST_KEY",
    "PARSE_TIMEOUT_SECONDS",
    "PaymentResultError",
    "PaymentSource",
    "REASON_FAILED",
    "REASON_MISMATCH",
    "SourceCall",
    "VERDICT_AGREE",
    "VERDICT_MISMATCH",
    "VERDICT_NO_SOURCE",
    "VERDICT_PLUGIN_FAILED",
    "call_source",
    "cached_sources",
    "compare",
    "declared_source_ids",
    "dual_parse",
    "load_payment_sources",
    "normalize_result",
    "plugin_route_hint",
    "reset_cache",
    "set_authoritative_hint",
    "set_sources_for_test",
    "settlement_view",
]
