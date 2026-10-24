"""一键体检：系统级与单账号的跨域只读诊断。

跨域排障（“账号 A 为什么不自动回复了”“系统整体有没有问题”）需要同时查看
账号、Worker、规则、插件、日志与 Provider，而单轮路由最多披露 3 个领域 / 16 个工具，
模型往往只查到一半。这里把这些只读检查收敛为两个工具：

- ``diagnostics.system_checkup``：整体体检快照；
- ``diagnostics.account_checkup``：单账号体检，并给出“为什么不回复”的可能原因排序。

约束：
- 只读；不调用上游模型（Provider 只看配置、进程内健康与近期用量记录）；
- 复用现有工具 handler / service，不新建 ``AsyncSessionLocal``、不 commit；
- 每个检查段独立 try/except + 超时，失败降级为 ``unknown`` 并给出脱敏原因；
- 错误文本先脱敏，再用 ``mark_external_text`` 标为外部数据。
"""

from __future__ import annotations

import asyncio
import inspect
import re
import sys
import time
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import desc, func, select

from ....db.models.account import (
    ACCOUNT_STATUS_ACTIVE,
    ACCOUNT_STATUS_DEAD,
    ACCOUNT_STATUS_FLOODWAIT,
    ACCOUNT_STATUS_LOGIN_REQUIRED,
    ACCOUNT_STATUS_PAUSED,
    Account,
)
from ....services.redactor import redact_text
from ..context import ToolContext
from ..registry import ToolRegistry, ToolSpec
from ._helpers import mark_external_text

STATUS_OK = "ok"
STATUS_WARN = "warn"
STATUS_ERROR = "error"
STATUS_UNKNOWN = "unknown"
_STATUS_RANK = {STATUS_ERROR: 3, STATUS_WARN: 2, STATUS_UNKNOWN: 1, STATUS_OK: 0}

DEFAULT_WINDOW_HOURS = 24
MAX_WINDOW_HOURS = 168
SECTION_TIMEOUT_SECONDS = 5.0
TOTAL_BUDGET_SECONDS = 25.0
_LOG_SCAN_LIMIT = 2000
_TOP_ERRORS = 5
_MAX_LISTED_ACCOUNTS = 20
_MAX_NEXT_STEPS = 10
_ERROR_LEVELS = ("ERROR", "error", "CRITICAL", "critical")
_WARN_LEVELS = ("WARN", "warn", "WARNING", "warning")


# ════════════════════════════════════════════════════════════
# 通用结构
# ════════════════════════════════════════════════════════════


@dataclass
class CheckOptions:
    now: datetime
    hours: int
    since: datetime
    scope_account_id: int | None = None
    # 段间共享的已知事实（如总闸状态），避免重复查询；缺失时各段按“未知”处理
    shared: dict[str, Any] = field(default_factory=dict)


def _worse(current: str, other: str) -> str:
    """返回两者中更严重的状态。"""

    return current if _STATUS_RANK.get(current, 0) >= _STATUS_RANK.get(other, 0) else other


_AUTH_ERROR_TYPES = frozenset({"auth_failed", "auth", "credential", "unauthorized", "forbidden"})


def _step(
    text: str,
    *,
    severity: str = STATUS_WARN,
    tool: str | None = None,
    requires_confirmation: bool = False,
) -> dict[str, Any]:
    return {
        "severity": severity,
        "text": text,
        "tool": tool,
        "requires_confirmation": bool(requires_confirmation),
    }


def _section(
    status: str,
    finding: str,
    facts: dict[str, Any] | None = None,
    next_steps: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "status": status if status in _STATUS_RANK else STATUS_UNKNOWN,
        "finding": finding,
        "facts": facts or {},
        "next_steps": list(next_steps or []),
    }


def _safe_reason(exc: BaseException) -> str:
    """异常原因只保留首行、脱敏并截断，避免把 SQL/参数或密钥带进模型上下文。"""

    first_line = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
    text = redact_text(f"{type(exc).__name__}: {first_line}".rstrip(": "))
    return text[:160]


def _external(text: Any, limit: int = 300) -> str | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    cleaned = redact_text(raw)
    if len(cleaned) > limit:
        cleaned = f"{cleaned[:limit]}…"
    return mark_external_text(cleaned)


def mask_phone(phone: Any) -> str | None:
    digits = str(phone or "").strip()
    if not digits:
        return None
    if len(digits) <= 4:
        return "*" * len(digits)
    return f"{digits[:3]}{'*' * max(2, len(digits) - 7)}{digits[-4:]}"


def _account_label(row: Any) -> str:
    name = str(getattr(row, "display_name", None) or "").strip()
    username = str(getattr(row, "tg_username", None) or "").strip()
    label = name or (f"@{username}" if username else "")
    return f"#{row.id} {label}".strip()


@asynccontextmanager
async def _savepoint(db: Any) -> AsyncIterator[None]:
    """为单个检查段开 SAVEPOINT，查询失败不污染当前只读事务。"""

    begin = getattr(db, "begin_nested", None)
    if begin is None:
        yield
        return
    cm = begin()
    if not hasattr(cm, "__aenter__"):
        if inspect.iscoroutine(cm):
            cm.close()
        yield
        return
    async with cm:
        yield


SectionFn = Callable[[ToolContext, CheckOptions], Awaitable[dict[str, Any]]]


async def run_sections(
    ctx: ToolContext,
    opts: CheckOptions,
    plan: tuple[tuple[str, str, str, bool], ...],
    *,
    section_timeout: float = SECTION_TIMEOUT_SECONDS,
    total_budget: float = TOTAL_BUDGET_SECONDS,
) -> list[dict[str, Any]]:
    """顺序执行检查段（共享同一 AsyncSession，不能并发）。

    plan 项为 ``(key, title, 函数名, 是否包 SAVEPOINT)``；函数按名称从模块全局解析，
    便于测试替换单个检查段。
    """

    started = time.monotonic()
    results: list[dict[str, Any]] = []
    module_globals = globals()
    for key, title, fn_name, use_savepoint in plan:
        section_started = time.monotonic()
        remaining = total_budget - (section_started - started)
        if remaining <= 0.05:
            out = _section(STATUS_UNKNOWN, "体检总时长已达上限，本项未检查。")
            out["reason"] = "total_budget_exceeded"
        else:
            fn: SectionFn = module_globals[fn_name]
            timeout = max(0.05, min(section_timeout, remaining))

            async def _invoke(fn: SectionFn = fn, use_savepoint: bool = use_savepoint) -> dict[str, Any]:
                if use_savepoint:
                    async with _savepoint(ctx.db):
                        return await fn(ctx, opts)
                return await fn(ctx, opts)

            try:
                out = await asyncio.wait_for(_invoke(), timeout=timeout)
                if not isinstance(out, dict) or "status" not in out:
                    out = _section(STATUS_UNKNOWN, "检查返回格式异常。")
            except TimeoutError:
                out = _section(STATUS_UNKNOWN, f"检查超时（>{timeout:.1f} 秒），已跳过。")
                out["reason"] = "timeout"
            except Exception as exc:  # noqa: BLE001
                out = _section(STATUS_UNKNOWN, "检查失败，已降级为未知。")
                out["reason"] = _safe_reason(exc)
        out = {"key": key, "title": title, **out}
        out["elapsed_ms"] = int((time.monotonic() - section_started) * 1000)
        results.append(out)
    return results


def summarize_sections(sections: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(str(item.get("status") or STATUS_UNKNOWN) for item in sections)
    if counts[STATUS_ERROR]:
        verdict = STATUS_ERROR
    elif counts[STATUS_WARN]:
        verdict = STATUS_WARN
    elif sections and counts[STATUS_UNKNOWN] == len(sections):
        verdict = STATUS_UNKNOWN
    else:
        verdict = STATUS_OK
    label = {
        STATUS_ERROR: "发现异常",
        STATUS_WARN: "基本可用，有需关注项",
        STATUS_UNKNOWN: "无法完成体检",
        STATUS_OK: "未发现问题",
    }[verdict]
    text = (
        f"{label}：{counts[STATUS_OK]} 项正常、{counts[STATUS_WARN]} 项需关注、"
        f"{counts[STATUS_ERROR]} 项异常、{counts[STATUS_UNKNOWN]} 项未能检查"
    )
    steps: list[dict[str, Any]] = []
    seen: set[tuple[str | None, str]] = set()
    for severity in (STATUS_ERROR, STATUS_WARN, "info"):
        for section in sections:
            for item in section.get("next_steps") or []:
                if str(item.get("severity")) != severity:
                    continue
                dedupe = (item.get("tool"), str(item.get("text")))
                if dedupe in seen:
                    continue
                seen.add(dedupe)
                steps.append({**item, "section": section.get("key")})
    for index, item in enumerate(steps[:_MAX_NEXT_STEPS], start=1):
        item["order"] = index
    return {
        "verdict": verdict,
        "verdict_text": text,
        "counts": {k: counts[k] for k in (STATUS_OK, STATUS_WARN, STATUS_ERROR, STATUS_UNKNOWN)},
        "unknown_sections": [s.get("key") for s in sections if s.get("status") == STATUS_UNKNOWN],
        "next_steps": steps[:_MAX_NEXT_STEPS],
    }


def _parse_hours(value: Any) -> int:
    try:
        hours = int(value)
    except (TypeError, ValueError):
        hours = DEFAULT_WINDOW_HOURS
    return max(1, min(hours, MAX_WINDOW_HOURS))


_SIG_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
_SIG_HEX = re.compile(r"\b0x[0-9a-f]+\b|\b[0-9a-f]{12,}\b", re.I)
_SIG_NUM = re.compile(r"-?\d+(?:\.\d+)?")
_SIG_SPACE = re.compile(r"\s+")


def error_signature(message: Any) -> str:
    """把错误消息归一成“同类”签名：去掉数字、ID 与十六进制串。"""

    text = redact_text(str(message or ""))
    text = text.splitlines()[0] if text else ""
    text = _SIG_UUID.sub("<uuid>", text)
    text = _SIG_HEX.sub("<hex>", text)
    text = _SIG_NUM.sub("<n>", text)
    text = _SIG_SPACE.sub(" ", text).strip()
    return text[:160] or "(空消息)"


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=UTC)).isoformat()
    return None


# ════════════════════════════════════════════════════════════
# 共享数据读取（复用现有工具 / service）
# ════════════════════════════════════════════════════════════


def _worker_snapshot() -> dict[int, dict[str, Any]]:
    from ....worker.supervisor import get_worker_runtime_snapshot

    return {int(row["account_id"]): dict(row) for row in get_worker_runtime_snapshot()}


def _runtime_components() -> dict[str, Any] | None:
    """读取主进程运行时组件状态（仅当 app.main 已加载；不触发导入副作用）。"""

    module = sys.modules.get("app.main")
    if module is None:
        return None
    states = getattr(module, "_RUNTIME_COMPONENTS", None)
    errors = getattr(module, "_RUNTIME_COMPONENT_ERRORS", None)
    if not isinstance(states, dict):
        return None
    return {
        "states": {str(k): v for k, v in states.items()},
        "errors": {
            str(k): _external(v, 200) for k, v in (errors or {}).items() if v
        },
    }


async def _kill_switch_enabled(ctx: ToolContext, opts: CheckOptions) -> bool | None:
    if "kill_switch" in opts.shared:
        return opts.shared["kill_switch"]
    from ....services import kill_switch_service

    enabled = bool(await kill_switch_service.get_enabled(ctx.db))
    opts.shared["kill_switch"] = enabled
    return enabled


async def _error_log_stats(
    ctx: ToolContext,
    *,
    since: datetime,
    account_id: int | None,
) -> dict[str, Any]:
    from ....db.models.log import RuntimeLog

    levels = (*_ERROR_LEVELS, *_WARN_LEVELS)
    count_q = (
        select(RuntimeLog.level, func.count(RuntimeLog.id))
        .where(RuntimeLog.ts >= since, RuntimeLog.level.in_(levels))
        .group_by(RuntimeLog.level)
    )
    scan_q = (
        select(RuntimeLog.id, RuntimeLog.ts, RuntimeLog.level, RuntimeLog.account_id, RuntimeLog.source, RuntimeLog.message)
        .where(RuntimeLog.ts >= since, RuntimeLog.level.in_(levels))
        .order_by(desc(RuntimeLog.ts))
        .limit(_LOG_SCAN_LIMIT)
    )
    if account_id is not None:
        count_q = count_q.where(RuntimeLog.account_id == account_id)
        scan_q = scan_q.where(RuntimeLog.account_id == account_id)
    error_count = 0
    warn_count = 0
    for level, count in (await ctx.db.execute(count_q)).all():
        if str(level) in _ERROR_LEVELS:
            error_count += int(count or 0)
        else:
            warn_count += int(count or 0)
    rows = list((await ctx.db.execute(scan_q)).all())
    groups: dict[str, dict[str, Any]] = {}
    by_account: Counter[int] = Counter()
    for row in rows:
        level = str(row.level or "").upper()
        if row.account_id is not None and level in {"ERROR", "CRITICAL"}:
            by_account[int(row.account_id)] += 1
        sig = error_signature(row.message)
        bucket = groups.get(sig)
        if bucket is None:
            bucket = groups[sig] = {
                "signature": sig,
                "count": 0,
                "level": level,
                "source": row.source,
                "accounts": set(),
                "last_ts": _iso(row.ts),
                "sample_log_id": row.id,
                "sample": _external(row.message, 240),
            }
        bucket["count"] += 1
        if level in {"ERROR", "CRITICAL"}:
            bucket["level"] = level
        if row.account_id is not None:
            bucket["accounts"].add(int(row.account_id))
    ranked = sorted(
        groups.values(),
        key=lambda item: (item["level"] not in {"ERROR", "CRITICAL"}, -item["count"]),
    )
    top = []
    for item in ranked[:_TOP_ERRORS]:
        top.append(
            {
                "count": item["count"],
                "level": item["level"],
                "source": item["source"],
                "account_ids": sorted(item["accounts"])[:10],
                "last_ts": item["last_ts"],
                "sample_log_id": item["sample_log_id"],
                "signature": mark_external_text(item["signature"]),
                "sample": item["sample"],
            }
        )
    floodwait_hits = sum(1 for row in rows if "flood" in str(row.message or "").lower())
    return {
        "error_count": error_count,
        "warn_count": warn_count,
        "scanned": len(rows),
        "scan_truncated": len(rows) >= _LOG_SCAN_LIMIT,
        "distinct_signatures": len(groups),
        "top": top,
        "errors_by_account": dict(by_account.most_common(10)),
        "floodwait_mentions": floodwait_hits,
    }


async def _usage_failures(
    ctx: ToolContext,
    *,
    since: datetime,
    account_id: int | None = None,
) -> dict[int | None, dict[str, Any]]:
    """按 Provider 聚合窗口内 LLM 调用成败与失败类型（只读 llm_usage，不调上游）。"""

    from ....db.models.llm_usage import LLMUsage

    q = (
        select(
            LLMUsage.provider_id,
            LLMUsage.success,
            LLMUsage.error_type,
            func.count(LLMUsage.id),
            func.max(LLMUsage.created_at),
        )
        .where(LLMUsage.created_at >= since)
        .group_by(LLMUsage.provider_id, LLMUsage.success, LLMUsage.error_type)
    )
    if account_id is not None:
        q = q.where(LLMUsage.account_id == account_id)
    out: dict[int | None, dict[str, Any]] = {}
    for provider_id, success, error_type, count, last_at in (await ctx.db.execute(q)).all():
        key = int(provider_id) if provider_id is not None else None
        bucket = out.setdefault(
            key,
            {"total": 0, "success": 0, "failed": 0, "error_types": {}, "last_call_at": None},
        )
        n = int(count or 0)
        bucket["total"] += n
        if success:
            bucket["success"] += n
        else:
            bucket["failed"] += n
            et = str(error_type or "unknown")
            bucket["error_types"][et] = bucket["error_types"].get(et, 0) + n
        last_iso = _iso(last_at)
        if last_iso and (bucket["last_call_at"] is None or last_iso > bucket["last_call_at"]):
            bucket["last_call_at"] = last_iso
    return out


def _cooling_models() -> dict[int, list[dict[str, Any]]]:
    from ....services import provider_health

    out: dict[int, list[dict[str, Any]]] = {}
    for item in provider_health.list_health():
        if item.get("state") != "cooling" and not int(item.get("consecutive_failures") or 0):
            continue
        out.setdefault(int(item["provider_id"]), []).append(
            {
                "model": item.get("model"),
                "state": item.get("state"),
                "consecutive_failures": item.get("consecutive_failures"),
                "last_error_class": item.get("last_error_class"),
                "cooldown_remaining_seconds": item.get("cooldown_remaining_seconds"),
            }
        )
    return out


def _provider_readiness(
    row: Any,
    usage: dict[str, Any] | None,
    cooling: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    from ....services.llm_dto import LLMProviderDTO
    from ..config import tools_model_for_dto

    dto = LLMProviderDTO.from_orm_row(row)
    enabled_models = dto.enabled_model_ids()
    issues: list[str] = []
    severity = STATUS_OK
    if not dto.has_api_key:
        issues.append("缺少 API Key")
        severity = STATUS_ERROR
    if dto.has_model_list() and not enabled_models:
        issues.append("没有已启用的模型")
        severity = STATUS_ERROR
    usage = usage or {}
    failed = int(usage.get("failed") or 0)
    total = int(usage.get("total") or 0)
    error_types = dict(usage.get("error_types") or {})
    if failed and total:
        rate = failed / total
        if any(str(k).lower() in _AUTH_ERROR_TYPES for k in error_types):
            issues.append("近期调用出现鉴权失败")
            severity = STATUS_ERROR
        elif total >= 5 and rate >= 0.8:
            issues.append(f"近期调用失败率 {int(rate * 100)}%")
            severity = STATUS_ERROR
        elif rate >= 0.2:
            issues.append(f"近期调用失败率 {int(rate * 100)}%")
            severity = _worse(severity, STATUS_WARN)
    if cooling:
        cooling_now = [c for c in cooling if c.get("state") == "cooling"]
        if cooling_now:
            issues.append(f"{len(cooling_now)} 个模型处于冷却")
            severity = _worse(severity, STATUS_WARN)
    return {
        "id": dto.id,
        "name": dto.name,
        "provider": dto.provider,
        "has_api_key": bool(dto.has_api_key),
        "enabled_models": len(enabled_models),
        "tools_model": tools_model_for_dto(dto),
        "recent_calls": total,
        "recent_failed": failed,
        "recent_error_types": error_types,
        "last_call_at": usage.get("last_call_at"),
        "cooling_models": cooling or [],
        "status": severity,
        "issues": issues,
    }


# ════════════════════════════════════════════════════════════
# 系统级检查段
# ════════════════════════════════════════════════════════════


async def _check_health(ctx: ToolContext, opts: CheckOptions) -> dict[str, Any]:
    from . import system as system_tools

    health = await system_tools.get_health(ctx, {})
    checks = health.get("checks") or {}
    db = checks.get("db") or {}
    redis = checks.get("redis") or {}
    facts = {
        "db_ok": bool(db.get("ok")),
        "migration_revision": db.get("migration_revision"),
        "redis_ok": bool(redis.get("ok")),
    }
    steps: list[dict[str, Any]] = []
    if not db.get("ok"):
        facts["db_error"] = _external(db.get("error"), 200)
        steps.append(_step("数据库不可用：检查 PostgreSQL 容器与连接配置，可用 logs.system_console 查看 postgres 日志。", severity=STATUS_ERROR, tool="logs.system_console"))
        return _section(STATUS_ERROR, "数据库不可用。", facts, steps)
    status = STATUS_OK
    findings = ["数据库正常"]
    if db.get("migration_warning"):
        facts["migration_warning"] = _external(db.get("migration_warning"), 200)
        status = STATUS_WARN
        findings.append("迁移版本未读取到")
    if not redis.get("ok"):
        facts["redis_error"] = _external(redis.get("error"), 200)
        status = STATUS_WARN
        findings.append("Redis 不可用（Bot 确认、Worker IPC 可能受影响）")
        steps.append(_step("Redis 不可用：检查 redis 容器，可用 logs.system_console 读取 redis 日志。", tool="logs.system_console"))
    else:
        findings.append("Redis 正常")
    components = _runtime_components()
    if components is not None:
        facts["runtime_components"] = components["states"]
        if components["errors"]:
            facts["runtime_component_errors"] = components["errors"]
        failed = [k for k, v in components["states"].items() if v is False]
        if failed:
            status = STATUS_ERROR if "worker_supervisor" in failed else _worse(status, STATUS_WARN)
            findings.append(f"运行时组件未就绪：{'、'.join(failed)}")
            steps.append(_step("运行时组件启动失败：查看 Web 日志定位原因，必要时用 system.restart 重启应用（需确认）。", severity=status, tool="logs.system_console"))
    return _section(status, "；".join(findings) + "。", facts, steps)


async def _check_versions(ctx: ToolContext, opts: CheckOptions) -> dict[str, Any]:
    from ... import runtime_versions as rv

    app = rv.collect_app_info()
    telethon = rv.collect_telethon_details()
    gateway = await rv.collect_gateway_info(probe_binary=False)
    facts = {
        "telepilot": app.get("version"),
        "revision": app.get("revision_short"),
        "python": rv.collect_python_info().get("version"),
        "telethon": telethon.get("version"),
        "telethon_layer": telethon.get("layer"),
        "gateway_state": gateway.get("state"),
        "gateway_required": gateway.get("required"),
        "gateway_version": gateway.get("version"),
    }
    expected = gateway.get("expected_protocol_version")
    actual = gateway.get("protocol_version")
    if gateway.get("required") and gateway.get("state") not in {"ready", "starting"}:
        return _section(
            STATUS_WARN,
            f"内置 Gateway 需要运行但当前状态为 {gateway.get('state')}，codex_gateway 类 Provider 可能不可用。",
            facts,
            [_step("Gateway 未就绪：用 system.get_versions 查看详情，并检查 codex_gateway 类 Provider。", tool="system.get_versions")],
        )
    if actual and expected and str(actual) != str(expected):
        facts["gateway_protocol"] = {"actual": actual, "expected": expected}
        return _section(STATUS_WARN, "Gateway 协议版本与后端期望不一致。", facts, [
            _step("Gateway 协议不一致：用 system.get_versions 核对版本并考虑 system.check_update。", tool="system.get_versions")
        ])
    return _section(
        STATUS_OK,
        f"TelePilot {facts['telepilot']}，Telethon {facts['telethon'] or '未知'}，Python {facts['python']}。",
        facts,
    )


async def _check_safety(ctx: ToolContext, opts: CheckOptions) -> dict[str, Any]:
    from ....services import platform_capabilities

    kill = await _kill_switch_enabled(ctx, opts)
    snapshot = platform_capabilities.get_snapshot()
    modules: dict[str, Any] = {}
    failed: list[str] = []
    paused: list[str] = []
    for key in platform_capabilities.ALL_MODULE_KEYS:
        runtime_state = snapshot.runtime.get(key)
        desired = bool(snapshot.desired.get(key, False)) if snapshot.cache_ready else None
        modules[key] = {
            "desired": desired,
            "runtime": runtime_state,
            "forced_off": bool(snapshot.forced_off.get(key, False)),
        }
        if runtime_state == "failed":
            failed.append(key)
            if snapshot.last_error.get(key):
                modules[key]["last_error"] = _external(snapshot.last_error.get(key), 200)
        if desired is False:
            paused.append(key)
    facts = {"kill_switch": kill, "modules": modules, "cache_ready": snapshot.cache_ready}
    steps: list[dict[str, Any]] = []
    status = STATUS_OK
    findings: list[str] = []
    if kill:
        status = STATUS_WARN
        findings.append("系统总闸已开启：全部 UserBot Worker、管理 Bot 与交互 Bot 均已停止")
        steps.append(_step("总闸开启中：若非有意，用 safety.set_kill_switch 关闭（危险操作，需确认）。", severity=STATUS_ERROR, tool="safety.set_kill_switch", requires_confirmation=True))
    if failed:
        status = STATUS_ERROR
        findings.append(f"平台模块运行失败：{'、'.join(failed)}")
        steps.append(_step(f"平台模块 {'、'.join(failed)} 运行失败：用 capabilities.list 查看详情后重新启用。", severity=STATUS_ERROR, tool="capabilities.list"))
    if paused:
        if "ai" in paused:
            status = _worse(status, STATUS_WARN)
            steps.append(_step("AI 模块已暂停：AI 指令与插件 AI 不会响应，如需恢复用 capabilities.set（需确认）。", tool="capabilities.set", requires_confirmation=True))
        findings.append(f"已暂停的平台模块：{'、'.join(paused)}")
    if not findings:
        findings.append("总闸关闭，平台模块运行正常")
    return _section(status, "；".join(findings) + "。", facts, steps)


async def _check_accounts(ctx: ToolContext, opts: CheckOptions) -> dict[str, Any]:
    q = select(Account.id, Account.display_name, Account.tg_username, Account.status).order_by(Account.id.asc())
    if opts.scope_account_id is not None:
        q = q.where(Account.id == opts.scope_account_id)
    rows = list((await ctx.db.execute(q)).all())
    if not rows:
        return _section(STATUS_WARN, "尚未添加任何 Telegram 账号。", {"total": 0}, [
            _step("还没有账号：在 Web 账号页登录 Telegram 账号。", severity="info")
        ])
    snapshot = _worker_snapshot()
    kill = opts.shared.get("kill_switch")
    by_status = Counter(str(r.status or "unknown") for r in rows)
    problems: list[dict[str, Any]] = []
    worker_down: list[str] = []
    worker_failing: list[str] = []
    for row in rows:
        status = str(row.status or "unknown")
        worker = snapshot.get(int(row.id))
        worker_view = (
            {
                "alive": bool(worker.get("alive")),
                "desired": worker.get("desired"),
                "fail_count": int(worker.get("fail_count") or 0),
                "queued": bool(worker.get("queued")),
                "starting": bool(worker.get("starting")),
            }
            if worker
            else None
        )
        issue: str | None = None
        if status == ACCOUNT_STATUS_ACTIVE:
            if not kill and (worker_view is None or not worker_view["alive"]):
                if worker_view and (worker_view["queued"] or worker_view["starting"]):
                    issue = "worker_starting"
                else:
                    issue = "worker_down"
                    worker_down.append(_account_label(row))
            elif worker_view and worker_view["fail_count"] > 0:
                issue = "worker_restarting"
                worker_failing.append(_account_label(row))
        elif status in {ACCOUNT_STATUS_DEAD, ACCOUNT_STATUS_LOGIN_REQUIRED, ACCOUNT_STATUS_FLOODWAIT, ACCOUNT_STATUS_PAUSED}:
            issue = status
        if issue:
            problems.append(
                {
                    "id": int(row.id),
                    "name": row.display_name or (f"@{row.tg_username}" if row.tg_username else None),
                    "status": status,
                    "issue": issue,
                    "worker": worker_view,
                }
            )
    alive = sum(1 for w in snapshot.values() if w.get("alive"))
    facts = {
        "total": len(rows),
        "by_status": dict(by_status),
        "workers_observed": len(snapshot),
        "workers_alive": alive,
        "kill_switch": kill,
        "problem_accounts": problems[:_MAX_LISTED_ACCOUNTS],
        "problem_accounts_truncated": len(problems) > _MAX_LISTED_ACCOUNTS,
    }
    steps: list[dict[str, Any]] = []
    status_out = STATUS_OK
    findings = [f"{len(rows)} 个账号，{by_status.get(ACCOUNT_STATUS_ACTIVE, 0)} 个运行中，Worker 存活 {alive} 个"]
    if worker_down:
        status_out = STATUS_ERROR
        findings.append(f"Worker 未运行：{'、'.join(worker_down[:5])}")
        steps.append(_step(f"账号 {'、'.join(worker_down[:5])} 的 Worker 未运行：先用 diagnostics.account_checkup 看原因，再用 accounts.restart_worker 重启（需确认）。", severity=STATUS_ERROR, tool="accounts.restart_worker", requires_confirmation=True))
    dead = by_status.get(ACCOUNT_STATUS_DEAD, 0) + by_status.get(ACCOUNT_STATUS_LOGIN_REQUIRED, 0)
    if dead:
        status_out = _worse(status_out, STATUS_WARN)
        findings.append(f"{dead} 个账号会话失效/需重新登录")
        steps.append(_step("有账号会话失效或需重新登录：请在 Web 账号页重新登录（Agent 不接管验证码/2FA）。", severity=STATUS_WARN))
    if by_status.get(ACCOUNT_STATUS_FLOODWAIT):
        status_out = _worse(status_out, STATUS_WARN)
        findings.append(f"{by_status[ACCOUNT_STATUS_FLOODWAIT]} 个账号处于 FloodWait")
        steps.append(_step("有账号处于 FloodWait：等待限制结束，并用 rate_limits.get_events 检查触发原因。", tool="rate_limits.get_events"))
    if by_status.get(ACCOUNT_STATUS_PAUSED):
        status_out = _worse(status_out, STATUS_WARN)
        findings.append(f"{by_status[ACCOUNT_STATUS_PAUSED]} 个账号已暂停")
        steps.append(_step("有账号被暂停（不会自动恢复）：若非有意，用 accounts.set_paused 恢复（需确认）。", tool="accounts.set_paused", requires_confirmation=True))
    if worker_failing:
        status_out = _worse(status_out, STATUS_WARN)
        findings.append(f"Worker 曾异常重启：{'、'.join(worker_failing[:5])}")
    if kill:
        findings.append("总闸开启中，Worker 停止属预期")
    return _section(status_out, "；".join(findings) + "。", facts, steps)


async def _check_errors(ctx: ToolContext, opts: CheckOptions) -> dict[str, Any]:
    stats = await _error_log_stats(ctx, since=opts.since, account_id=opts.scope_account_id)
    stats["window_hours"] = opts.hours
    errors = stats["error_count"]
    warns = stats["warn_count"]
    if not errors and not warns:
        return _section(STATUS_OK, f"最近 {opts.hours} 小时没有错误或警告日志。", stats)
    top = stats["top"][0] if stats["top"] else None
    status = STATUS_WARN
    if errors >= 50 or (top and top["count"] >= 20 and top["level"] in {"ERROR", "CRITICAL"}):
        status = STATUS_ERROR
    finding = f"最近 {opts.hours} 小时 {errors} 条错误、{warns} 条警告，{stats['distinct_signatures']} 类"
    if top:
        finding += f"；最多的一类出现 {top['count']} 次"
    steps = [
        _step(
            "按出现次数最多的错误用 logs.search_errors（带 keyword/account_id）查看明细，必要时用 logs.get_event_detail 与 source.search 定位代码。",
            severity=status,
            tool="logs.search_errors",
        )
    ]
    if stats["floodwait_mentions"]:
        steps.append(_step("日志中出现 FloodWait：用 rate_limits.get_events 检查限速与发送频率。", tool="rate_limits.get_events"))
    return _section(status, finding + "。", stats, steps)


async def _check_providers(ctx: ToolContext, opts: CheckOptions) -> dict[str, Any]:
    from ....db.models.command import LLMProvider

    rows = list((await ctx.db.execute(select(LLMProvider).order_by(LLMProvider.id.asc()))).scalars().all())
    if not rows:
        return _section(STATUS_WARN, "尚未配置任何模型 Provider，AI 指令与系统助手不可用。", {"total": 0}, [
            _step("在 AI 中心添加 Provider；也可把 Base URL 与 API Key 发给我，用 providers.probe_and_add 测活后添加（需确认）。", tool="providers.probe_and_add", requires_confirmation=True)
        ])
    usage = await _usage_failures(ctx, since=opts.since)
    cooling = _cooling_models()
    items = [_provider_readiness(r, usage.get(int(r.id)), cooling.get(int(r.id))) for r in rows]
    opts.shared["providers"] = {item["id"]: item for item in items}
    bad = [i for i in items if i["status"] == STATUS_ERROR]
    warn = [i for i in items if i["status"] == STATUS_WARN]
    ready = [i for i in items if i["has_api_key"] and i["enabled_models"] != 0]
    facts = {
        "total": len(items),
        "ready": len(ready),
        "with_tools_model": sum(1 for i in items if i["tools_model"]),
        "window_hours": opts.hours,
        "providers": items,
        "note": "只读配置、进程内冷却状态与近期调用记录，未调用上游模型。",
    }
    steps: list[dict[str, Any]] = []
    if not ready:
        status = STATUS_ERROR
    elif bad:
        status = STATUS_ERROR if len(bad) == len(items) else STATUS_WARN
    elif warn:
        status = STATUS_WARN
    else:
        status = STATUS_OK
    if bad or warn:
        names = "、".join(f"{i['name']}（{'；'.join(i['issues'])}）" for i in (bad + warn)[:5])
        finding = f"{len(items)} 个 Provider，{len(bad) + len(warn)} 个有问题：{names}"
        if any("缺少 API Key" in i["issues"] for i in bad):
            steps.append(_step("有 Provider 缺少 API Key：在 AI 中心补齐，或用 providers.save 更新（需确认）。", severity=STATUS_ERROR, tool="providers.save", requires_confirmation=True))
        steps.append(_step("对问题 Provider 用 providers.verify 复测，或用 providers.patrol 巡检全部已启用模型（会真实调用上游并消耗额度，需确认）。", severity=status, tool="providers.patrol", requires_confirmation=True))
        steps.append(_step("用 usage.recent 查看失败调用的具体错误类型与 fallback。", severity="info", tool="usage.recent"))
    else:
        finding = f"{len(items)} 个 Provider 均已配置凭据，近期调用无明显失败"
    return _section(status, finding + "。", facts, steps)


async def _check_agent_config(ctx: ToolContext, opts: CheckOptions) -> dict[str, Any]:
    from ..config import load_config, resolve_agent_providers

    cfg = await load_config(ctx.db)
    facts = {
        "enabled": bool(cfg.get("enabled")),
        "provider_id": cfg.get("provider_id"),
        "model": cfg.get("model"),
        "fallback_provider_ids": list(cfg.get("fallback_provider_ids") or []),
        "require_tool_approval": bool(cfg.get("require_tool_approval")),
    }
    resolved = await resolve_agent_providers(ctx.db, cfg)
    if isinstance(resolved, str):
        return _section(STATUS_WARN, f"系统助手配置不完整：{resolved}", facts, [
            _step("在系统助手「配置」中选择支持 tools 的 Provider 与模型。", tool=None)
        ])
    facts["resolved_provider"] = resolved.primary.name
    facts["resolved_model"] = resolved.model
    facts["usable_fallbacks"] = max(0, len(resolved.providers) - 1)
    return _section(
        STATUS_OK,
        f"系统助手使用「{resolved.primary.name}」/{resolved.model}，可用备选 {facts['usable_fallbacks']} 个。",
        facts,
    )


def _scheduler_overdue(cfg: dict[str, Any], now: datetime, timezone_name: str) -> str | None:
    """保守判断定时任务是否错过执行；无法判断时返回 None。"""

    def _dt(value: Any) -> datetime | None:
        if not value:
            return None
        try:
            if isinstance(value, (int, float)):
                return datetime.fromtimestamp(float(value), tz=UTC)
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except (TypeError, ValueError):
            return None

    kind = str(cfg.get("kind") or "cron").lower()
    last_fire = _dt(cfg.get("last_fire"))
    grace = timedelta(minutes=10)
    if kind == "once":
        run_at = _dt(cfg.get("run_at") or cfg.get("at") or cfg.get("once_at"))
        if run_at and run_at + grace < now and last_fire is None:
            return f"一次性任务计划于 {run_at.isoformat()} 执行，但没有执行记录"
        return None
    if kind == "interval":
        try:
            seconds = int(cfg.get("interval_seconds") or cfg.get("seconds") or 0)
        except (TypeError, ValueError):
            return None
        if seconds > 0 and last_fire and last_fire + timedelta(seconds=seconds * 3) + grace < now:
            return f"间隔 {seconds} 秒的任务上次执行于 {last_fire.isoformat()}，已超过 3 个周期"
        return None
    expr = str(cfg.get("cron") or "").strip()
    if not expr or last_fire is None:
        return None
    try:
        from zoneinfo import ZoneInfo

        from croniter import croniter

        tz = ZoneInfo(timezone_name or "UTC")
        itr = croniter(expr, now.astimezone(tz))
        prev = itr.get_prev(datetime)
        prev2 = itr.get_prev(datetime)
        if prev.tzinfo is None:
            prev = prev.replace(tzinfo=tz)
        if prev2.tzinfo is None:
            prev2 = prev2.replace(tzinfo=tz)
    except Exception:  # noqa: BLE001
        return None
    # 连续错过两次计划时间才判定，避免把刚到点尚未落库的任务误报
    if last_fire + grace < prev2.astimezone(UTC) and prev.astimezone(UTC) + grace < now:
        return f"cron 任务上次执行于 {last_fire.isoformat()}，已错过至少两次计划时间"
    return None


async def _check_scheduler(ctx: ToolContext, opts: CheckOptions) -> dict[str, Any]:
    from ....db.models.rule import Rule
    from ._helpers import get_timezone_name

    tz = await get_timezone_name(ctx.db)
    q = select(Rule.id, Rule.account_id, Rule.name, Rule.config).where(
        Rule.feature_key == "scheduler", Rule.enabled.is_(True)
    )
    if opts.scope_account_id is not None:
        q = q.where(Rule.account_id == opts.scope_account_id)
    rows = list((await ctx.db.execute(q.order_by(Rule.id.asc()).limit(500))).all())
    failing: list[dict[str, Any]] = []
    overdue: list[dict[str, Any]] = []
    for row in rows:
        cfg = row.config if isinstance(row.config, dict) else {}
        if cfg.get("last_error"):
            failing.append(
                {
                    "id": row.id,
                    "account_id": row.account_id,
                    "name": _external(row.name, 80),
                    "last_fire": cfg.get("last_fire"),
                    "last_error": _external(cfg.get("last_error"), 200),
                }
            )
        reason = _scheduler_overdue(cfg, opts.now, tz)
        if reason:
            overdue.append({"id": row.id, "account_id": row.account_id, "name": _external(row.name, 80), "reason": reason})
    facts = {
        "timezone": tz,
        "enabled_jobs": len(rows),
        "failing": failing[:10],
        "overdue": overdue[:10],
    }
    if not rows:
        return _section(STATUS_OK, "没有启用中的定时任务。", facts)
    if not failing and not overdue:
        return _section(STATUS_OK, f"{len(rows)} 个启用中的定时任务，未见失败或逾期。", facts)
    findings = []
    if failing:
        findings.append(f"{len(failing)} 个任务最近执行失败")
    if overdue:
        findings.append(f"{len(overdue)} 个任务疑似错过执行")
    return _section(STATUS_WARN, "；".join(findings) + "。", facts, [
        _step("用 scheduler.get 查看失败任务配置与 next_run_at，确认目标账号 Worker 在运行；需要时用 scheduler.execute_now 手动补跑（需确认）。", tool="scheduler.get")
    ])


async def _check_plugins(ctx: ToolContext, opts: CheckOptions) -> dict[str, Any]:
    from ....db.models.feature import FEATURE_STATE_FAILED, AccountFeature
    from ....db.models.plugin import InstalledPlugin
    from ....services import remote_plugin_service

    rows = list((await ctx.db.execute(select(InstalledPlugin).order_by(InstalledPlugin.key.asc()))).scalars().all())
    load_errors: list[dict[str, Any]] = []
    install_errors: list[dict[str, Any]] = []
    disabled: list[str] = []
    updates: list[dict[str, Any]] = []
    for row in rows:
        if not row.enabled:
            disabled.append(row.key)
        if row.last_load_error:
            load_errors.append({"key": row.key, "version": row.version, "error": _external(row.last_load_error, 200)})
        if row.last_install_error:
            install_errors.append({"key": row.key, "error": _external(row.last_install_error, 200)})
        try:
            view = remote_plugin_service.remote_plugin_view_from_installed(row)
            if view.update_available:
                updates.append({"key": row.key, "version": row.version, "latest_version": view.latest_version})
        except Exception:  # noqa: BLE001
            continue
    af_q = select(AccountFeature.account_id, AccountFeature.feature_key, AccountFeature.last_error).where(
        AccountFeature.enabled.is_(True),
        (AccountFeature.state == FEATURE_STATE_FAILED) | AccountFeature.last_error.is_not(None),
    )
    if opts.scope_account_id is not None:
        af_q = af_q.where(AccountFeature.account_id == opts.scope_account_id)
    feature_failures = [
        {"account_id": aid, "feature_key": key, "last_error": _external(err, 200)}
        for aid, key, err in (await ctx.db.execute(af_q.limit(50))).all()
    ]
    facts = {
        "installed": len(rows),
        "disabled_packages": disabled[:30],
        "load_errors": load_errors[:10],
        "install_errors": install_errors[:10],
        "updates_available": updates[:20],
        "account_feature_failures": feature_failures[:20],
    }
    steps: list[dict[str, Any]] = []
    status = STATUS_OK
    findings: list[str] = [f"已安装 {len(rows)} 个插件包"]
    if load_errors:
        status = STATUS_ERROR
        findings.append(f"{len(load_errors)} 个加载失败（{'、'.join(i['key'] for i in load_errors[:5])}）")
        steps.append(_step("插件加载失败：用 plugins.get 核对版本，再按插件名用 logs.search_errors 与 source.search 定位；可考虑 plugins.update（需确认）。", severity=STATUS_ERROR, tool="plugins.get"))
    if feature_failures:
        status = _worse(status, STATUS_WARN)
        findings.append(f"{len(feature_failures)} 个账号级功能处于失败状态")
        steps.append(_step("账号级功能失败：用 features.get_account_status 查看 last_error。", tool="features.get_account_status"))
    if install_errors:
        status = _worse(status, STATUS_WARN)
        findings.append(f"{len(install_errors)} 个最近安装/更新失败")
    if updates:
        findings.append(f"{len(updates)} 个有可用更新")
        steps.append(_step("有插件可更新：用 plugins.update 升级（需确认）。", severity="info", tool="plugins.update", requires_confirmation=True))
    if disabled:
        findings.append(f"{len(disabled)} 个安装包全局停用")
    return _section(status, "；".join(findings) + "。", facts, steps)


async def _check_notifications(ctx: ToolContext, opts: CheckOptions) -> dict[str, Any]:
    from ....services import apns
    from . import notifications as notification_tools

    bots = (await notification_tools.list_bots(ctx, {})).get("notify_bots") or []
    enabled = [b for b in bots if b.get("enabled")]
    broken = [b for b in enabled if not b.get("has_token") or not b.get("default_chat_id")]
    apns_cfg = await apns.get_config(ctx.db)
    facts = {
        "notify_bots": len(bots),
        "notify_bots_enabled": len(enabled),
        "notify_bots_incomplete": [b.get("name") for b in broken],
        "apns_enabled": bool(apns_cfg.get("enabled")),
        "apns_configured": bool(apns_cfg.get("configured")),
    }
    steps: list[dict[str, Any]] = []
    status = STATUS_OK
    findings: list[str] = []
    if broken:
        status = STATUS_WARN
        findings.append(f"{len(broken)} 个通知 Bot 缺少 Token 或默认 Chat")
        steps.append(_step("通知 Bot 配置不完整：用 notifications.list 查看并补齐，可用 notifications.test 测试发送（需确认）。", tool="notifications.list"))
    elif enabled:
        findings.append(f"{len(enabled)} 个通知 Bot 已启用")
    else:
        findings.append("未启用通知 Bot（可选；异常不会主动推送到 Telegram）")
    if apns_cfg.get("enabled") and not apns_cfg.get("configured"):
        status = STATUS_WARN
        findings.append("APNs 已开启但凭据不完整")
        steps.append(_step("APNs 已开启但缺少 Team ID/Key ID/Bundle ID/Auth Key：在 Web 设置中补齐。", severity=STATUS_WARN))
    elif apns_cfg.get("configured"):
        findings.append("APNs 推送已配置" + ("" if apns_cfg.get("enabled") else "（未开启）"))
    return _section(status, "；".join(findings) + "。", facts, steps)


SYSTEM_PLAN: tuple[tuple[str, str, str, bool], ...] = (
    ("health", "基础设施（数据库 / Redis / 运行时组件）", "_check_health", False),
    ("versions", "版本", "_check_versions", False),
    ("safety", "总闸与平台模块", "_check_safety", True),
    ("accounts", "账号与 Worker", "_check_accounts", True),
    ("errors", "错误日志", "_check_errors", True),
    ("providers", "模型 Provider", "_check_providers", True),
    ("system_agent", "系统助手配置", "_check_agent_config", True),
    ("scheduler", "定时任务", "_check_scheduler", True),
    ("plugins", "插件", "_check_plugins", True),
    ("notifications", "通知与推送", "_check_notifications", True),
)


async def system_checkup(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    hours = _parse_hours(args.get("hours"))
    now = datetime.now(UTC)
    scope = ctx.account_id if ctx.channel == "bot" else None
    opts = CheckOptions(now=now, hours=hours, since=now - timedelta(hours=hours), scope_account_id=scope)
    started = time.monotonic()
    sections = await run_sections(ctx, opts, SYSTEM_PLAN)
    summary = summarize_sections(sections)
    return {
        "kind": "system_checkup",
        "checked_at": now.isoformat(),
        "window_hours": hours,
        "scope": {"account_id": scope} if scope is not None else {"account_id": None, "note": "系统级"},
        **summary,
        "sections": sections,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "business_changed": False,
        "note": "只读体检，未修改任何配置、未调用上游模型。建议中的写操作均需用户确认后执行。",
    }


# ════════════════════════════════════════════════════════════
# 单账号体检
# ════════════════════════════════════════════════════════════


def _digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def match_accounts(rows: list[Any], query: str) -> list[Any]:
    """按 ID / @username / 显示名 / 手机号模糊匹配账号；精确命中优先。"""

    q = str(query or "").strip()
    if not q:
        return []
    lowered = q.lower().lstrip("@")
    id_text = q.lstrip("#").strip()
    if id_text.isdigit():
        exact_id = [r for r in rows if str(r.id) == id_text]
        if exact_id:
            return exact_id
    exact = [
        r
        for r in rows
        if lowered
        and (
            str(getattr(r, "tg_username", None) or "").lower() == lowered
            or str(getattr(r, "display_name", None) or "").strip().lower() == lowered
        )
    ]
    if exact:
        return exact
    digits = _digits(q)
    if len(digits) >= 4 and len(digits) >= len(q.replace(" ", "").replace("+", "").replace("-", "")) - 1:
        by_phone = [r for r in rows if digits in _digits(getattr(r, "phone", None))]
        if by_phone:
            return by_phone
    return [
        r
        for r in rows
        if lowered
        and (
            lowered in str(getattr(r, "tg_username", None) or "").lower()
            or lowered in str(getattr(r, "display_name", None) or "").lower()
        )
    ]


def _candidate_view(row: Any) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "display_name": row.display_name,
        "username": row.tg_username,
        "phone": mask_phone(row.phone),
        "status": row.status,
    }


async def _load_account_rows(ctx: ToolContext) -> list[Any]:
    rows = await ctx.db.execute(
        select(Account.id, Account.display_name, Account.tg_username, Account.phone, Account.status).order_by(Account.id.asc())
    )
    return list(rows.all())


async def resolve_account(ctx: ToolContext, args: dict[str, Any]) -> tuple[int | None, dict[str, Any] | None]:
    """解析目标账号。返回 ``(account_id, error)``；Bot 渠道强制当前绑定账号。"""

    raw_id = args.get("account_id")
    query = str(args.get("account") or args.get("query") or "").strip()
    explicit_id: int | None = None
    if raw_id not in (None, ""):
        try:
            explicit_id = int(raw_id)
        except (TypeError, ValueError):
            query = query or str(raw_id)
    if ctx.channel == "bot":
        bound = ctx.account_id
        if bound is None:
            return None, {"error": "account_id_required", "message": "Bot 渠道没有绑定账号上下文。"}
        if explicit_id is not None and explicit_id != bound:
            return None, {"error": "forbidden", "message": "管理 Bot 只能体检当前绑定账号。"}
        if query:
            matched = match_accounts(await _load_account_rows(ctx), query)
            if matched and all(int(r.id) != bound for r in matched):
                return None, {"error": "forbidden", "message": "管理 Bot 只能体检当前绑定账号。"}
        return bound, None
    if explicit_id is not None:
        return explicit_id, None
    rows = await _load_account_rows(ctx)
    if query:
        matched = match_accounts(rows, query)
        if len(matched) == 1:
            return int(matched[0].id), None
        if not matched:
            return None, {
                "error": "not_found",
                "message": f"没有找到与「{query[:40]}」匹配的账号。",
                "candidates": [_candidate_view(r) for r in rows[:_MAX_LISTED_ACCOUNTS]],
            }
        return None, {
            "error": "ambiguous",
            "message": f"「{query[:40]}」匹配到 {len(matched)} 个账号，请指定 account_id。",
            "candidates": [_candidate_view(r) for r in matched[:_MAX_LISTED_ACCOUNTS]],
        }
    if ctx.account_id is not None:
        return int(ctx.account_id), None
    if len(rows) == 1:
        return int(rows[0].id), None
    return None, {
        "error": "account_required",
        "message": "请指定要体检的账号（ID、@用户名、名称或手机号）。",
        "candidates": [_candidate_view(r) for r in rows[:_MAX_LISTED_ACCOUNTS]],
    }


def _in_active_window(start: Any, end: Any, now_local: datetime) -> bool | None:
    if start is None or end is None:
        return None
    current = now_local.time().replace(tzinfo=None)
    if start == end:
        return True
    if start < end:
        return start <= current < end
    return current >= start or current < end


async def _gather_account_facts(ctx: ToolContext, account_id: int, opts: CheckOptions) -> dict[str, Any]:
    """逐段收集账号事实；每段独立降级，失败记入 ``unavailable``。"""

    facts: dict[str, Any] = {"unavailable": {}}

    async def guarded(name: str, coro_fn: Callable[[], Awaitable[Any]]) -> Any:
        try:
            async with _savepoint(ctx.db):
                return await asyncio.wait_for(coro_fn(), timeout=SECTION_TIMEOUT_SECONDS)
        except TimeoutError:
            facts["unavailable"][name] = "timeout"
        except Exception as exc:  # noqa: BLE001
            facts["unavailable"][name] = _safe_reason(exc)
        return None

    facts["account"] = await guarded("account", lambda: _account_basics(ctx, account_id))
    facts["kill_switch"] = await guarded("kill_switch", lambda: _kill_switch_enabled(ctx, opts))
    facts["worker"] = await guarded("worker", lambda: _account_worker(account_id))
    facts["features"] = await guarded("features", lambda: _account_features(ctx, account_id))
    facts["rules"] = await guarded("rules", lambda: _account_rules(ctx, account_id))
    facts["providers"] = await guarded("providers", lambda: _account_providers(ctx, account_id, opts))
    facts["activity"] = await guarded("activity", lambda: _account_activity(ctx, account_id, opts))
    facts["humanize"] = await guarded("humanize", lambda: _account_humanize(ctx, account_id, opts))
    return facts


async def _account_basics(ctx: ToolContext, account_id: int) -> dict[str, Any] | None:
    from ....services import account_service

    row = await ctx.db.get(Account, account_id)
    if row is None:
        return None
    decryptable: bool | None
    try:
        account_service.ensure_account_secrets_decryptable(row)
        decryptable = True
    except Exception:  # noqa: BLE001
        decryptable = False
    return {
        "id": int(row.id),
        "display_name": row.display_name,
        "username": row.tg_username,
        "phone": mask_phone(row.phone),
        "status": row.status,
        "paused": str(row.status or "").lower() == ACCOUNT_STATUS_PAUSED,
        "session_decryptable": decryptable,
        "proxy_id": row.proxy_id,
        "cold_start_until": row.cold_start_until.isoformat() if getattr(row, "cold_start_until", None) else None,
        "updated_at": _iso(row.updated_at),
    }


async def _account_worker(account_id: int) -> dict[str, Any]:
    snapshot = _worker_snapshot()
    worker = snapshot.get(int(account_id))
    if worker is None:
        return {"observed": False, "alive": False, "supervisor_workers": len(snapshot)}
    return {
        "observed": True,
        "alive": bool(worker.get("alive")),
        "pid": worker.get("pid"),
        "desired": worker.get("desired"),
        "fail_count": int(worker.get("fail_count") or 0),
        "queued": bool(worker.get("queued")),
        "starting": bool(worker.get("starting")),
    }


async def _account_features(ctx: ToolContext, account_id: int) -> dict[str, Any]:
    from . import features as feature_tools

    status = await feature_tools.get_account_status(
        ToolContext(db=ctx.db, channel="web", role=ctx.role, account_id=account_id),
        {"account_id": account_id},
    )
    items = status.get("features") or []
    enabled = [i for i in items if i.get("enabled")]
    return {
        "enabled_count": len(enabled),
        "total": len(items),
        "enabled": [
            {
                "feature_key": i.get("feature_key"),
                "kind": i.get("kind"),
                "package_enabled": i.get("installed_package_enabled"),
                "last_error": _external(i.get("last_error"), 200),
            }
            for i in enabled
        ][:40],
        "enabled_but_package_disabled": [
            i.get("feature_key") for i in enabled if i.get("installed_package_enabled") is False
        ],
        "failing": [
            {"feature_key": i.get("feature_key"), "last_error": _external(i.get("last_error"), 200)}
            for i in enabled
            if i.get("last_error")
        ],
    }


async def _account_rules(ctx: ToolContext, account_id: int) -> dict[str, Any]:
    from ....db.models.command import AccountCommandLink, CommandTemplate
    from ....db.models.rule import Rule
    from ....services import platform_capabilities
    from . import interaction as interaction_tools

    by_feature: dict[str, dict[str, int]] = {}
    rows = (
        await ctx.db.execute(
            select(Rule.feature_key, Rule.enabled, func.count(Rule.id))
            .where(Rule.account_id == account_id)
            .group_by(Rule.feature_key, Rule.enabled)
        )
    ).all()
    for key, enabled, count in rows:
        bucket = by_feature.setdefault(str(key), {"enabled": 0, "disabled": 0})
        bucket["enabled" if enabled else "disabled"] += int(count or 0)
    interaction_rules = await interaction_tools._load_rules(ctx, account_id)
    interaction_enabled = sum(1 for r in interaction_rules if r.get("enabled", True))
    interaction_module = await platform_capabilities.is_module_enabled(ctx.db, "interaction_bot")
    commands = (
        await ctx.db.execute(
            select(CommandTemplate.id, CommandTemplate.name, CommandTemplate.type, CommandTemplate.config)
            .join(AccountCommandLink, AccountCommandLink.template_id == CommandTemplate.id)
            .where(AccountCommandLink.account_id == account_id, AccountCommandLink.enabled.is_(True))
            .order_by(CommandTemplate.id.asc())
        )
    ).all()
    ai_commands = []
    other_commands = 0
    for cid, name, ctype, cfg in commands:
        if str(ctype) != "ai":
            other_commands += 1
            continue
        cfg = cfg if isinstance(cfg, dict) else {}
        ai_commands.append(
            {
                "id": cid,
                "name": name,
                "routing_mode": cfg.get("routing_mode", "fixed"),
                "provider_id": cfg.get("provider_id"),
                "fallback_provider_id": cfg.get("routing_fallback_provider_id"),
                "model": cfg.get("model"),
            }
        )
    return {
        "generic_rules": by_feature,
        "interaction_rules": len(interaction_rules),
        "interaction_rules_enabled": interaction_enabled,
        "interaction_module_enabled": bool(interaction_module),
        "ai_commands": ai_commands[:20],
        "other_commands_enabled": other_commands,
    }


def _collect_provider_ids(value: Any, out: set[int], depth: int = 0) -> None:
    if depth > 4:
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key) in {"provider_id", "routing_fallback_provider_id", "fallback_provider_id"}:
                try:
                    if item not in (None, ""):
                        out.add(int(item))
                except (TypeError, ValueError):
                    pass
            else:
                _collect_provider_ids(item, out, depth + 1)
    elif isinstance(value, list):
        for item in value[:50]:
            _collect_provider_ids(item, out, depth + 1)


async def _account_providers(ctx: ToolContext, account_id: int, opts: CheckOptions) -> dict[str, Any]:
    from ....db.models.command import AccountCommandLink, CommandTemplate, LLMProvider
    from ....db.models.feature import AccountFeature

    referenced: dict[int, set[str]] = {}

    def note(pid: int, origin: str) -> None:
        referenced.setdefault(int(pid), set()).add(origin)

    cmd_rows = (
        await ctx.db.execute(
            select(CommandTemplate.name, CommandTemplate.config)
            .join(AccountCommandLink, AccountCommandLink.template_id == CommandTemplate.id)
            .where(
                AccountCommandLink.account_id == account_id,
                AccountCommandLink.enabled.is_(True),
                CommandTemplate.type == "ai",
            )
        )
    ).all()
    for name, cfg in cmd_rows:
        ids: set[int] = set()
        _collect_provider_ids(cfg if isinstance(cfg, dict) else {}, ids)
        for pid in ids:
            note(pid, f"command:{name}")
    feature_rows = (
        await ctx.db.execute(
            select(AccountFeature.feature_key, AccountFeature.config).where(
                AccountFeature.account_id == account_id, AccountFeature.enabled.is_(True)
            )
        )
    ).all()
    for key, cfg in feature_rows:
        ids = set()
        _collect_provider_ids(cfg if isinstance(cfg, dict) else {}, ids)
        for pid in ids:
            note(pid, f"feature:{key}")
    usage = await _usage_failures(ctx, since=opts.since, account_id=account_id)
    for pid in usage:
        if pid is not None:
            note(pid, "recent_usage")
    if not referenced:
        return {"providers": [], "missing_provider_ids": [], "account_usage": usage.get(None)}
    rows = list(
        (await ctx.db.execute(select(LLMProvider).where(LLMProvider.id.in_(sorted(referenced))))).scalars().all()
    )
    cooling = _cooling_models()
    found = {int(r.id) for r in rows}
    items = []
    for row in rows:
        info = _provider_readiness(row, usage.get(int(row.id)), cooling.get(int(row.id)))
        info["used_by"] = sorted(referenced.get(int(row.id), set()))[:10]
        items.append(info)
    return {
        "providers": items,
        "missing_provider_ids": sorted(pid for pid in referenced if pid not in found),
        "window_hours": opts.hours,
    }


async def _account_activity(ctx: ToolContext, account_id: int, opts: CheckOptions) -> dict[str, Any]:
    from ....db.models.account_bot import AccountBot
    from ....db.models.action_event import ACTION_EVENT_STATUS_FAILED, ActionEvent
    from ....db.models.llm_usage import LLMUsage
    from ....db.models.log import RuntimeLog
    from ....db.models.payout_compensation import (
        PAYOUT_COMPENSATION_STATUS_PENDING,
        PayoutCompensation,
    )

    stats = await _error_log_stats(ctx, since=opts.since, account_id=account_id)
    last_log = (
        await ctx.db.execute(select(func.max(RuntimeLog.ts)).where(RuntimeLog.account_id == account_id))
    ).scalar_one_or_none()
    last_llm = (
        await ctx.db.execute(select(func.max(LLMUsage.created_at)).where(LLMUsage.account_id == account_id))
    ).scalar_one_or_none()
    events = (
        await ctx.db.execute(
            select(ActionEvent.status, func.count(ActionEvent.id), func.max(ActionEvent.created_at))
            .where(ActionEvent.account_id == account_id, ActionEvent.created_at >= opts.since)
            .group_by(ActionEvent.status)
        )
    ).all()
    event_counts = {str(status): int(count or 0) for status, count, _ in events}
    last_event = max((_iso(ts) for *_, ts in events if ts is not None), default=None)
    last_failed = None
    if event_counts.get(ACTION_EVENT_STATUS_FAILED):
        row = (
            await ctx.db.execute(
                select(ActionEvent.id, ActionEvent.action_type, ActionEvent.error_code, ActionEvent.error_summary, ActionEvent.created_at)
                .where(ActionEvent.account_id == account_id, ActionEvent.status == ACTION_EVENT_STATUS_FAILED)
                .order_by(desc(ActionEvent.created_at))
                .limit(1)
            )
        ).first()
        if row is not None:
            last_failed = {
                "id": row.id,
                "action_type": row.action_type,
                "error_code": row.error_code,
                "error_summary": _external(row.error_summary, 200),
                "created_at": _iso(row.created_at),
            }
    pending_comp = (
        await ctx.db.execute(
            select(func.count(PayoutCompensation.id)).where(
                PayoutCompensation.account_id == account_id,
                PayoutCompensation.status == PAYOUT_COMPENSATION_STATUS_PENDING,
            )
        )
    ).scalar_one_or_none()
    bot = (
        await ctx.db.execute(
            select(AccountBot.enabled, AccountBot.status, AccountBot.last_error).where(AccountBot.account_id == account_id)
        )
    ).first()
    candidates = [v for v in (_iso(last_log), _iso(last_llm), last_event) if v]
    return {
        "window_hours": opts.hours,
        "errors": stats,
        "last_log_at": _iso(last_log),
        "last_llm_call_at": _iso(last_llm),
        "last_action_event_at": last_event,
        "last_activity_at": max(candidates) if candidates else None,
        "action_events": event_counts,
        "last_failed_action": last_failed,
        "pending_payout_compensations": int(pending_comp or 0),
        "management_bot": (
            {"enabled": bool(bot.enabled), "status": bot.status, "last_error": _external(bot.last_error, 200)}
            if bot is not None
            else None
        ),
    }


async def _account_humanize(ctx: ToolContext, account_id: int, opts: CheckOptions) -> dict[str, Any] | None:
    from zoneinfo import ZoneInfo

    from ....db.models.account import HumanizeConfig
    from ._helpers import get_timezone_name

    row = await ctx.db.get(HumanizeConfig, account_id)
    if row is None:
        return None
    tz_name = await get_timezone_name(ctx.db)
    try:
        tz = ZoneInfo(tz_name)
    except Exception:  # noqa: BLE001
        tz = ZoneInfo("UTC")
    start, end = row.active_window_start, row.active_window_end
    return {
        "timezone": tz_name,
        "active_window_start": start.isoformat() if start else None,
        "active_window_end": end.isoformat() if end else None,
        "in_active_window": _in_active_window(start, end, opts.now.astimezone(tz)),
        "typing_simulate": bool(row.typing_simulate),
    }


def _cause(
    code: str,
    title: str,
    confidence: str,
    evidence: list[str],
    *,
    fix: str,
    tool: str | None = None,
    requires_confirmation: bool = False,
) -> dict[str, Any]:
    return {
        "code": code,
        "title": title,
        "confidence": confidence,
        "evidence": evidence,
        "fix": fix,
        "tool": tool,
        "requires_confirmation": requires_confirmation,
    }


_CONFIDENCE_RANK = {"high": 0, "medium": 1, "low": 2}


def diagnose_account_causes(facts: dict[str, Any], *, focus_feature: str | None = None) -> list[dict[str, Any]]:
    """根据账号事实给出“为什么不回复/不工作”的可能原因（按可信度排序）。"""

    causes: list[dict[str, Any]] = []
    account = facts.get("account") or {}
    worker = facts.get("worker") or {}
    features = facts.get("features") or {}
    rules = facts.get("rules") or {}
    providers = facts.get("providers") or {}
    activity = facts.get("activity") or {}
    humanize = facts.get("humanize") or {}
    status = str(account.get("status") or "")
    kill = facts.get("kill_switch")

    if kill:
        causes.append(_cause("kill_switch", "系统总闸已开启，所有 Worker 与 Bot 都已停止", "high", ["kill_switch.enabled=true"], fix="若非有意，关闭总闸。", tool="safety.set_kill_switch", requires_confirmation=True))
    if status == ACCOUNT_STATUS_PAUSED:
        causes.append(_cause("paused", "账号已暂停（暂停不会自动恢复）", "high", [f"account.status={status}"], fix="确认无需暂停后恢复账号。", tool="accounts.set_paused", requires_confirmation=True))
    elif status == ACCOUNT_STATUS_DEAD:
        causes.append(_cause("session_dead", "账号会话已失效（可能被登出或封禁）", "high", [f"account.status={status}"], fix="在 Web 账号页重新登录该账号（Agent 不接管验证码/2FA）。"))
    elif status == ACCOUNT_STATUS_LOGIN_REQUIRED:
        causes.append(_cause("login_required", "账号需要重新登录", "high", [f"account.status={status}"], fix="在 Web 账号页完成登录（Agent 不接管验证码/2FA）。"))
    elif status == ACCOUNT_STATUS_FLOODWAIT:
        causes.append(_cause("floodwait", "账号处于 Telegram FloodWait 限制期", "high", [f"account.status={status}"], fix="等待限制结束，并检查发送频率与风控事件。", tool="rate_limits.get_events"))
    if account.get("session_decryptable") is False:
        causes.append(_cause("secrets_undecryptable", "账号登录凭据无法解密（MASTER_KEY 变更或数据损坏），Worker 启动即失败", "high", ["ensure_account_secrets_decryptable 失败"], fix="恢复原 MASTER_KEY，或在 Web 重新登录该账号。"))
    if status == ACCOUNT_STATUS_ACTIVE and not kill and worker:
        if not worker.get("alive"):
            if worker.get("queued") or worker.get("starting"):
                causes.append(_cause("worker_starting", "Worker 正在排队/启动中", "medium", [f"worker.queued={worker.get('queued')}", f"worker.starting={worker.get('starting')}"], fix="稍等片刻后重新体检；若长时间未就绪再重启 Worker。"))
            else:
                evidence = ["worker.alive=false"]
                if not worker.get("observed"):
                    evidence.append("supervisor 未持有该账号的 Worker 句柄")
                if worker.get("fail_count"):
                    evidence.append(f"worker.fail_count={worker.get('fail_count')}")
                causes.append(_cause("worker_down", "账号状态正常但 Worker 进程未运行", "high", evidence, fix="结合错误日志确认原因后重启 Worker。", tool="accounts.restart_worker", requires_confirmation=True))
        elif int(worker.get("fail_count") or 0) > 0:
            causes.append(_cause("worker_flapping", "Worker 近期异常退出并被自动拉起", "medium", [f"worker.fail_count={worker.get('fail_count')}"], fix="查看该账号错误日志定位崩溃原因。", tool="logs.search_errors"))

    enabled_features = features.get("enabled") or []
    if features and not enabled_features and not (rules.get("ai_commands") or rules.get("other_commands_enabled")):
        causes.append(_cause("nothing_enabled", "账号没有启用任何功能、插件或指令，收到消息也不会处理", "high", ["features.enabled_count=0", "无启用指令"], fix="为账号启用需要的功能/插件。", tool="features.set_enabled", requires_confirmation=True))
    if focus_feature and features:
        focus = str(focus_feature).strip().lower()
        hit = [f for f in enabled_features if focus in str(f.get("feature_key") or "").lower()]
        if not hit:
            causes.append(_cause("focus_not_enabled", f"账号未启用与「{focus_feature}」相关的功能/插件", "high", [f"enabled features 中没有包含 {focus} 的 feature_key"], fix="确认功能 key 后为账号启用。", tool="features.set_enabled", requires_confirmation=True))
    for key in features.get("enabled_but_package_disabled") or []:
        causes.append(_cause("package_disabled", f"插件 {key} 在账号上启用，但安装包已全局停用", "high", [f"{key}: installed_package_enabled=false"], fix="重新启用插件安装包。", tool="plugins.set_package_enabled", requires_confirmation=True))
    for item in features.get("failing") or []:
        causes.append(_cause("feature_error", f"功能/插件 {item.get('feature_key')} 最近报错", "high", [f"last_error={item.get('last_error')}"], fix="按插件名查看错误日志与源码定位，必要时更新插件。", tool="logs.search_errors"))

    if rules.get("interaction_rules_enabled") and rules.get("interaction_module_enabled") is False:
        causes.append(_cause("interaction_module_paused", "账号有启用的交互规则，但 Interaction Bot 平台模块已暂停", "high", [f"interaction_rules_enabled={rules.get('interaction_rules_enabled')}", "interaction_bot 模块关闭"], fix="重新启用 Interaction Bot 平台模块。", tool="capabilities.set", requires_confirmation=True))

    for item in providers.get("providers") or []:
        if item.get("status") == STATUS_ERROR or (item.get("status") == STATUS_WARN and item.get("recent_failed")):
            confidence = "high" if not item.get("has_api_key") or item.get("status") == STATUS_ERROR else "medium"
            evidence = list(item.get("issues") or [])
            if item.get("recent_error_types"):
                evidence.append(f"近期失败类型：{item['recent_error_types']}")
            causes.append(_cause("provider_unhealthy", f"该账号使用的 Provider「{item.get('name')}」不可用或频繁失败", confidence, evidence + [f"used_by={item.get('used_by')}"], fix="复测该 Provider，必要时切换路由或补齐 Key。", tool="providers.verify", requires_confirmation=True))
    if providers.get("missing_provider_ids"):
        causes.append(_cause("provider_missing", "指令或插件配置引用了已不存在的 Provider", "high", [f"missing_provider_ids={providers['missing_provider_ids']}"], fix="为相关 AI 指令/插件重新选择 Provider。", tool="routing.list_ai_commands"))

    if humanize.get("in_active_window") is False:
        causes.append(_cause("outside_active_window", "当前不在账号拟人化“活跃时段”内，回复可能被推迟或跳过", "medium", [f"active_window={humanize.get('active_window_start')}~{humanize.get('active_window_end')} ({humanize.get('timezone')})"], fix="确认活跃时段设置是否符合预期。", tool="humanize.get"))

    errors = activity.get("errors") or {}
    if errors.get("error_count"):
        top = (errors.get("top") or [{}])[0]
        causes.append(_cause("recent_errors", f"该账号最近 {activity.get('window_hours')} 小时有 {errors.get('error_count')} 条错误日志", "medium", [f"最多的一类出现 {top.get('count')} 次：{top.get('signature')}"] if top else [], fix="按错误签名查看日志明细并定位源码。", tool="logs.search_errors"))
    if errors.get("floodwait_mentions") and status != ACCOUNT_STATUS_FLOODWAIT:
        causes.append(_cause("floodwait_logs", "日志中出现 FloodWait，发送可能被 Telegram 限速", "medium", [f"floodwait_mentions={errors.get('floodwait_mentions')}"], fix="检查风控事件并降低发送频率。", tool="rate_limits.get_events"))
    failed_action = activity.get("last_failed_action")
    if failed_action:
        causes.append(_cause("action_failed", "最近有发送/动作执行失败", "medium", [f"{failed_action.get('action_type')} {failed_action.get('error_code') or ''}".strip(), f"{failed_action.get('error_summary') or ''}"], fix="查看 ActionEvent 详情。", tool="logs.get_event_detail"))

    worker_ok = status == ACCOUNT_STATUS_ACTIVE and bool(worker.get("alive")) and not kill
    if worker_ok and not causes and activity and not activity.get("last_activity_at"):
        causes.append(_cause("no_activity", "Worker 在线但没有任何活动记录，可能没收到触发消息或规则条件不匹配", "low", ["日志、LLM 调用与动作事件均为空"], fix="用一条真实消息做命中模拟，确认规则/指令是否命中。", tool="dispatch.simulate"))
    causes.sort(key=lambda item: _CONFIDENCE_RANK.get(item["confidence"], 3))
    return causes


def _account_section_status(facts: dict[str, Any], causes: list[dict[str, Any]]) -> str:
    if not causes:
        return STATUS_OK
    if any(c["confidence"] == "high" for c in causes):
        return STATUS_ERROR
    return STATUS_WARN


async def account_checkup(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    account_id, error = await resolve_account(ctx, args)
    if error is not None:
        return {**error, "business_changed": False}
    assert account_id is not None
    hours = _parse_hours(args.get("hours"))
    now = datetime.now(UTC)
    opts = CheckOptions(now=now, hours=hours, since=now - timedelta(hours=hours), scope_account_id=account_id)
    started = time.monotonic()
    facts = await _gather_account_facts(ctx, account_id, opts)
    if facts.get("account") is None and "account" not in facts["unavailable"]:
        return {"error": "not_found", "message": f"账号 {account_id} 不存在", "business_changed": False}
    focus = str(args.get("feature_key") or "").strip() or None
    causes = diagnose_account_causes(facts, focus_feature=focus)
    status = _account_section_status(facts, causes)
    if causes:
        verdict_text = f"发现 {len(causes)} 个可能原因，最可能：{causes[0]['title']}"
    elif facts["unavailable"]:
        verdict_text = "未发现明显故障，但部分检查未能完成"
    else:
        verdict_text = "未发现明显故障：账号、Worker、功能与 Provider 均正常"
    next_steps = [
        {
            "order": index,
            "text": f"{cause['title']} → {cause['fix']}",
            "tool": cause.get("tool"),
            "requires_confirmation": cause.get("requires_confirmation", False),
        }
        for index, cause in enumerate(causes[:_MAX_NEXT_STEPS], start=1)
    ]
    if not causes:
        next_steps.append(
            {
                "order": 1,
                "text": "用一条真实消息做命中模拟，确认规则/指令是否命中并查看投递链路。",
                "tool": "dispatch.simulate",
                "requires_confirmation": False,
            }
        )
    return {
        "kind": "account_checkup",
        "checked_at": now.isoformat(),
        "account_id": account_id,
        "window_hours": hours,
        "verdict": status,
        "verdict_text": verdict_text,
        "likely_causes": causes,
        "next_steps": next_steps,
        "facts": facts,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "business_changed": False,
        "note": "只读体检，未修改任何配置、未调用上游模型。建议中的写操作均需用户确认后执行。",
    }


def register(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="diagnostics.system_checkup",
            description=(
                "一键体检（只读）：一次调用汇总整个系统的健康快照——数据库/Redis/运行时组件、版本、"
                "总闸与平台模块、账号状态与 Worker 存活、最近 N 小时错误日志统计与高频错误、"
                "模型 Provider 就绪度（凭据/模型/冷却/近期失败类型，不调用上游）、系统助手配置、"
                "定时任务失败/逾期、插件加载失败/可更新、通知 Bot 与 APNs。每项给出 ok/warn/error/unknown、"
                "一句话结论与关键事实，并输出总体结论与按优先级排列的“建议下一步”（引用具体工具）。"
                "用户说“体检”“一键体检”“健康检查”“全面检查”“系统有没有问题”时优先调用；"
                "本工具已覆盖多个领域，无需再逐个调用其它查询工具。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "hours": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": MAX_WINDOW_HOURS,
                        "description": "错误日志与调用记录的统计窗口（小时），默认 24。",
                    }
                },
                "additionalProperties": False,
            },
            read_only=True,
            min_role="viewer",
            channels=("web", "bot"),
            read_handler=system_checkup,
        )
    )
    registry.register(
        ToolSpec(
            name="diagnostics.account_checkup",
            description=(
                "单账号体检（只读），回答“账号 X 为什么不回复/没反应/不工作了”：一次调用检查账号连接与会话状态、"
                "Worker 进程、暂停/总闸、功能与插件矩阵、通用 Rule/交互规则/AI 指令路由、该账号所用 Provider 的就绪度、"
                "最近错误与动作事件、补付积压、拟人化活跃时段与最近活动时间，并给出按可信度排序的可能原因（含证据）"
                "与建议下一步。账号可用 account_id，或 account（ID、@用户名、名称、手机号片段，模糊匹配）；"
                "可用 feature_key 聚焦某个功能（如 auto_reply）。管理 Bot 渠道只能体检当前绑定账号。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "account_id": {"type": "integer"},
                    "account": {
                        "type": "string",
                        "maxLength": 100,
                        "description": "账号 ID、@用户名、显示名或手机号片段（模糊匹配；多个命中时返回候选）。",
                    },
                    "feature_key": {
                        "type": "string",
                        "maxLength": 128,
                        "description": "可选：重点检查的功能/插件 key（如 auto_reply），用于判断是否启用。",
                    },
                    "hours": {"type": "integer", "minimum": 1, "maximum": MAX_WINDOW_HOURS},
                },
                "additionalProperties": False,
            },
            read_only=True,
            min_role="viewer",
            channels=("web", "bot"),
            read_handler=account_checkup,
        )
    )


__all__ = [
    "SYSTEM_PLAN",
    "CheckOptions",
    "account_checkup",
    "diagnose_account_causes",
    "error_signature",
    "mask_phone",
    "match_accounts",
    "register",
    "resolve_account",
    "run_sections",
    "summarize_sections",
    "system_checkup",
]
