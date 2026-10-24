"""Provider 全量模型巡检（测活）工具。

与 Web「全部 Provider 巡检」（FullLivenessPanel）共用同一套后端实现：
直接调用 ``app.api.commands`` 中的 ``full_liveness_preview`` / ``full_liveness_run`` /
``full_liveness_status`` / ``full_liveness_cancel``，不走 HTTP、不复制调度与诊断逻辑。

- ``providers.patrol``：待确认写操作。预览只读数据库（不调用上游、不消耗额度）；
  用户确认后才创建异步测活 Job，返回 ``run_id``。失败不会自动重试。
- ``providers.patrol_status``：只读，按 Provider 分组返回逐模型结果（已脱敏）。
- ``providers.patrol_cancel``：待确认操作，停止发起新任务并中断在途请求。

测活 Job 只保存在当前进程内存，完成后保留约 30 分钟供查询。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from sqlalchemy import select

from ....db.models.system_agent import ACTION_STATUS_EXECUTED, SystemAgentAction
from ...redactor import redact_text
from ..context import ToolContext
from ..registry import PreparedAction, ToolRegistry, ToolSpec

PATROL_TOOL = "providers.patrol"
PATROL_STATUS_TOOL = "providers.patrol_status"
PATROL_CANCEL_TOOL = "providers.patrol_cancel"

_DEFAULT_RESULT_LIMIT = 200
_MAX_RESULT_LIMIT = 500
_ERROR_SUMMARY_CHARS = 240
_FINAL_STATUSES = {"completed", "cancelled"}


def _actor_user(ctx: ToolContext) -> Any:
    return SimpleNamespace(id=ctx.web_user_id or 0)


def _http_error_to_value_error(exc: Exception) -> ValueError:
    """把复用路由抛出的 HTTPException 转成工具可读的错误（不含凭据）。"""

    detail = getattr(exc, "detail", None)
    if isinstance(detail, dict):
        message = str(detail.get("message") or detail.get("code") or "模型巡检请求被拒绝")
        code = str(detail.get("code") or "").strip()
        text = f"{message}（{code}）" if code and code not in message else message
    else:
        text = str(detail or exc)
    return ValueError(redact_text(text))


def _clean_int_list(value: Any, *, field: str, maximum: int = 200) -> list[int] | None:
    if value in (None, "", []):
        return None
    if not isinstance(value, list):
        value = [value]
    out: list[int] = []
    for item in value:
        try:
            number = int(item)
        except (TypeError, ValueError):
            raise ValueError(f"{field} 必须是整数 ID 列表") from None
        if number <= 0:
            raise ValueError(f"{field} 必须是正整数 ID")
        if number not in out:
            out.append(number)
    if len(out) > maximum:
        raise ValueError(f"{field} 最多 {maximum} 项")
    return out


def _clean_models(value: Any) -> list[str] | None:
    if value in (None, "", []):
        return None
    if not isinstance(value, list):
        value = [value]
    out: list[str] = []
    for item in value:
        model = str(item or "").strip()
        if model and len(model) <= 256 and model not in out:
            out.append(model)
    if len(out) > 500:
        raise ValueError("models 最多 500 项")
    return out or None


def _normalize_scope(args: dict[str, Any]) -> dict[str, Any]:
    provider_ids = _clean_int_list(
        args.get("provider_ids")
        if args.get("provider_ids") not in (None, "", [])
        else args.get("provider_id"),
        field="provider_ids",
    )
    models = _clean_models(args.get("models"))
    timeout = args.get("timeout_seconds")
    scope: dict[str, Any] = {"provider_ids": provider_ids, "models": models}
    if timeout not in (None, ""):
        try:
            timeout_value = int(timeout)
        except (TypeError, ValueError):
            raise ValueError("timeout_seconds 必须是整数") from None
        scope["timeout_seconds"] = max(10, min(600, timeout_value))
    return scope


async def _load_plan(ctx: ToolContext, scope: dict[str, Any]) -> dict[str, Any]:
    """复用 Web 预览接口生成权威执行计划（只读数据库，不调用上游）。"""

    from fastapi import HTTPException

    from ....api import commands as commands_api
    from ....schemas.command import FullLivenessPreviewRequest

    provider_ids = scope.get("provider_ids")
    models = scope.get("models")
    try:
        base = await commands_api.full_liveness_preview(
            FullLivenessPreviewRequest(only_provider_ids=provider_ids),
            ctx.db,
            _actor_user(ctx),  # type: ignore[arg-type]
        )
        models_by_provider: dict[int, list[str]] | None = None
        if models:
            wanted = set(models)
            models_by_provider = {
                plan.provider_id: [m for m in plan.enabled_models if m in wanted]
                for plan in base.providers
            }
            base = await commands_api.full_liveness_preview(
                FullLivenessPreviewRequest(
                    only_provider_ids=provider_ids,
                    models_by_provider=models_by_provider,
                ),
                ctx.db,
                _actor_user(ctx),  # type: ignore[arg-type]
            )
    except HTTPException as exc:
        raise _http_error_to_value_error(exc) from None

    if provider_ids:
        found = {plan.provider_id for plan in base.providers}
        missing = [pid for pid in provider_ids if pid not in found]
        if missing:
            raise ValueError(f"Provider 不存在：{', '.join(f'#{pid}' for pid in missing)}")
    return {"preview": base, "models_by_provider": models_by_provider}


def _plan_view(preview: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for plan in preview.providers:
        item: dict[str, Any] = {
            "provider_id": plan.provider_id,
            "provider_name": plan.provider_name,
            "model_count": len(plan.enabled_models),
            "models": list(plan.enabled_models)[:50],
            "executable": bool(plan.executable),
        }
        if len(plan.enabled_models) > 50:
            item["models_truncated"] = len(plan.enabled_models) - 50
        if plan.skipped_reason:
            item["skipped_reason"] = plan.skipped_reason
        out.append(item)
    return out


async def patrol_preview(ctx: ToolContext, args: dict[str, Any]) -> PreparedAction:
    scope = _normalize_scope(args)
    plan = await _load_plan(ctx, scope)
    preview = plan["preview"]
    if int(preview.task_total) <= 0:
        raise ValueError(
            "当前范围内没有可巡检的已启用模型（Provider 缺少凭据或没有启用模型）；未创建待确认操作。"
        )
    scope_label = (
        f"{len(scope['provider_ids'])} 个指定 Provider" if scope.get("provider_ids") else "全部 Provider"
    )
    summary = (
        f"模型巡检：{scope_label}，{preview.executable_provider_total} 个可执行 Provider / "
        f"{preview.task_total} 个已启用模型"
    )
    arguments = {
        **{k: v for k, v in scope.items() if v is not None},
        "approved_task_total": int(preview.task_total),
    }
    return PreparedAction(
        arguments=arguments,
        preview={
            "summary": summary,
            "scope": scope_label,
            "provider_total": preview.provider_total,
            "executable_provider_total": preview.executable_provider_total,
            "task_total": preview.task_total,
            "max_output_tokens": preview.max_output_tokens,
            "global_concurrency": preview.global_concurrency,
            "provider_concurrency": preview.provider_concurrency,
            "large_run": bool(preview.needs_confirmation),
            "providers": _plan_view(preview),
            "warning": (
                f"确认后将向上游真实发起 {preview.task_total} 次模型调用，消耗对应 Provider 额度；"
                "仅返回诊断结果，不修改 Provider 配置、不写入生产冷却。失败不会自动重试。"
            ),
        },
    )


async def patrol_execute(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    from fastapi import HTTPException

    from ....api import commands as commands_api
    from ....schemas.command import FullLivenessRunRequest

    scope = _normalize_scope(args)
    plan = await _load_plan(ctx, scope)
    preview = plan["preview"]
    task_total = int(preview.task_total)
    approved = int(args.get("approved_task_total") or 0)
    if task_total <= 0:
        raise ValueError("确认时范围内已没有可巡检的已启用模型，未发起任何上游请求。")
    if approved and task_total > approved:
        raise ValueError(
            f"确认后巡检范围已扩大（预览 {approved} 个模型，当前 {task_total} 个），"
            "为避免超出已确认的额度消耗，未发起请求；请重新发起巡检。"
        )

    run_kwargs: dict[str, Any] = {
        # Action 卡片已向用户展示模型数与额度消耗；确认即为大批量运行的二次确认。
        "confirm_large_run": True,
        "only_provider_ids": scope.get("provider_ids"),
        "models_by_provider": plan.get("models_by_provider"),
    }
    if scope.get("timeout_seconds"):
        run_kwargs["timeout_seconds"] = scope["timeout_seconds"]
    try:
        started = await commands_api.full_liveness_run(
            FullLivenessRunRequest(**run_kwargs),
            ctx.db,
            _actor_user(ctx),  # type: ignore[arg-type]
        )
    except HTTPException as exc:
        raise _http_error_to_value_error(exc) from None
    return {
        "run_id": started.run_id,
        "status": started.status,
        "task_total": started.task_total,
        "provider_count": preview.executable_provider_total,
        "business_changed": False,
        "note": (
            "巡检已在后台启动。用 providers.patrol_status（可传 run_id 与 wait_seconds）读取按 Provider 分组的结果；"
            "不要重复发起巡检。"
        ),
    }


def _error_summary(item: dict[str, Any]) -> str | None:
    parts: list[str] = []
    for key in ("error", "upstream_error_message"):
        text = str(item.get(key) or "").strip()
        if text and text not in parts and not any(text in p for p in parts):
            parts.append(text)
    if not parts:
        return None
    summary = redact_text(" | ".join(parts))
    if len(summary) > _ERROR_SUMMARY_CHARS:
        summary = summary[: _ERROR_SUMMARY_CHARS - 1] + "…"
    return summary


def _model_entry(item: dict[str, Any]) -> dict[str, Any]:
    status = str(item.get("status") or "")
    ok = status == "healthy"
    http_status = item.get("upstream_status_code") or item.get("status_code")
    entry: dict[str, Any] = {
        "model": item.get("model_id"),
        "ok": ok,
        "status": status,
        "latency_ms": int(item.get("latency_ms") or 0),
        "http_status": http_status if isinstance(http_status, int) else None,
    }
    if item.get("skipped"):
        entry["skipped"] = True
    if not ok:
        entry["error"] = _error_summary(item)
        if item.get("error_category"):
            entry["error_category"] = item.get("error_category")
        if item.get("suggestion"):
            entry["suggestion"] = redact_text(str(item.get("suggestion"))[:200])
    return entry


def group_results(
    snapshot: dict[str, Any],
    *,
    only_failed: bool = False,
    limit: int = _DEFAULT_RESULT_LIMIT,
) -> dict[str, Any]:
    """把测活快照按 Provider 分组；失败项优先列出，单次最多返回 ``limit`` 个模型。"""

    groups: dict[int, dict[str, Any]] = {}
    order: list[int] = []
    for raw in snapshot.get("results") or []:
        item = dict(raw)
        pid = int(item.get("provider_id") or 0)
        group = groups.get(pid)
        if group is None:
            group = {
                "provider_id": pid,
                "provider_name": item.get("provider_name"),
                "tested": 0,
                "healthy": 0,
                "failed": 0,
                "skipped": 0,
                "models": [],
            }
            groups[pid] = group
            order.append(pid)
        entry = _model_entry(item)
        group["tested"] += 1
        if entry["ok"]:
            group["healthy"] += 1
        elif entry.get("skipped") or entry["status"] == "cancelled":
            group["skipped"] += 1
        else:
            group["failed"] += 1
        group["models"].append(entry)

    remaining = max(1, int(limit))
    omitted = 0
    providers: list[dict[str, Any]] = []
    for pid in sorted(order, key=lambda p: (-groups[p]["failed"], p)):
        group = groups[pid]
        models = group["models"]
        if only_failed:
            models = [m for m in models if not m["ok"]]
        models.sort(key=lambda m: (m["ok"], str(m.get("model") or "")))
        shown = models[:remaining]
        omitted += len(models) - len(shown)
        remaining -= len(shown)
        if only_failed and not models:
            continue
        group["models"] = shown
        providers.append(group)

    status = str(snapshot.get("status") or "")
    out = {
        "run_id": snapshot.get("run_id"),
        "status": status,
        "finished": status in _FINAL_STATUSES,
        "task_total": int(snapshot.get("task_total") or 0),
        "completed": int(snapshot.get("completed") or 0),
        "healthy": int(snapshot.get("healthy") or 0),
        "failed": int(snapshot.get("failed") or 0),
        "skipped": int(snapshot.get("skipped") or 0),
        "cancelled": int(snapshot.get("cancelled") or 0),
        "providers": providers,
    }
    if omitted:
        out["models_omitted"] = omitted
    if snapshot.get("error"):
        out["error"] = redact_text(str(snapshot.get("error"))[:_ERROR_SUMMARY_CHARS])
    return out


async def _latest_run_id(ctx: ToolContext) -> str | None:
    """未指定 run_id 时取最近一次已确认执行的巡检（优先当前会话）。"""

    base = (
        select(SystemAgentAction)
        .where(SystemAgentAction.tool_name == PATROL_TOOL)
        .where(SystemAgentAction.status == ACTION_STATUS_EXECUTED)
        .order_by(SystemAgentAction.updated_at.desc())
        .limit(1)
    )
    queries = []
    session_id = getattr(ctx.session, "id", None)
    if session_id:
        queries.append(base.where(SystemAgentAction.session_id == session_id))
    queries.append(base)
    for query in queries:
        row = (await ctx.db.execute(query)).scalars().first()
        result = getattr(row, "result", None) if row is not None else None
        if isinstance(result, dict):
            run_id = str(result.get("run_id") or "").strip()
            if run_id:
                return run_id
    return None


def _clean_run_id(value: Any) -> str | None:
    run_id = str(value or "").strip()
    if not run_id:
        return None
    if len(run_id) > 64:
        raise ValueError("run_id 无效")
    return run_id


async def _snapshot(run_id: str) -> dict[str, Any]:
    from fastapi import HTTPException

    from ....api import commands as commands_api

    try:
        resp = await commands_api.full_liveness_status(run_id, SimpleNamespace(id=0))  # type: ignore[arg-type]
    except HTTPException as exc:
        raise _http_error_to_value_error(exc) from None
    return resp.model_dump(mode="json")


async def patrol_status(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    import asyncio

    run_id = _clean_run_id(args.get("run_id")) or await _latest_run_id(ctx)
    if not run_id:
        return {
            "found": False,
            "note": "没有找到已确认执行的模型巡检。需要巡检时先调用 providers.patrol 生成待确认操作。",
        }
    try:
        wait_seconds = max(0, min(30, int(args.get("wait_seconds") or 0)))
    except (TypeError, ValueError):
        wait_seconds = 0
    limit = _DEFAULT_RESULT_LIMIT
    if args.get("limit") not in (None, ""):
        try:
            limit = max(1, min(_MAX_RESULT_LIMIT, int(args.get("limit"))))
        except (TypeError, ValueError):
            limit = _DEFAULT_RESULT_LIMIT

    try:
        snapshot = await _snapshot(run_id)
    except ValueError:
        return {
            "found": False,
            "run_id": run_id,
            "note": "巡检运行不存在或已过期（结果仅在当前进程保留约 30 分钟）。如需最新结果请重新发起 providers.patrol 并确认。",
        }
    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait_seconds
    while snapshot.get("status") not in _FINAL_STATUSES and loop.time() < deadline:
        await asyncio.sleep(min(1.0, max(0.0, deadline - loop.time())))
        snapshot = await _snapshot(run_id)

    out = group_results(snapshot, only_failed=bool(args.get("only_failed")), limit=limit)
    out["found"] = True
    if not out["finished"]:
        out["note"] = "巡检仍在进行，可稍后再次调用 providers.patrol_status 读取进度；不要重新发起巡检。"
    return out


async def cancel_preview(ctx: ToolContext, args: dict[str, Any]) -> PreparedAction:
    run_id = _clean_run_id(args.get("run_id")) or await _latest_run_id(ctx)
    if not run_id:
        raise ValueError("没有找到可停止的模型巡检")
    snapshot = await _snapshot(run_id)
    if snapshot.get("status") in _FINAL_STATUSES:
        raise ValueError(f"巡检已结束（{snapshot.get('status')}），无需停止")
    return PreparedAction(
        arguments={"run_id": run_id},
        preview={
            "summary": f"停止模型巡检（已完成 {snapshot.get('completed', 0)}/{snapshot.get('task_total', 0)}）",
            "run_id": run_id,
            "note": "停止发起新的测活请求并中断在途请求；已完成的结果保留。",
        },
    )


async def cancel_execute(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    from fastapi import HTTPException

    from ....api import commands as commands_api

    run_id = _clean_run_id(args.get("run_id"))
    if not run_id:
        raise ValueError("缺少 run_id")
    try:
        resp = await commands_api.full_liveness_cancel(run_id, SimpleNamespace(id=0))  # type: ignore[arg-type]
    except HTTPException as exc:
        raise _http_error_to_value_error(exc) from None
    snap = resp.model_dump(mode="json")
    return {
        "run_id": run_id,
        "cancel_requested": True,
        "status": snap.get("status"),
        "completed": snap.get("completed"),
        "task_total": snap.get("task_total"),
        "business_changed": False,
    }


def register(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name=PATROL_TOOL,
            channels=("web", "bot"),
            description=(
                "模型巡检/批量测活：对全部（或指定 provider_ids / models）Provider 的已启用模型逐个真实调用，"
                "与 Web「全部 Provider 巡检」同一实现。会消耗上游额度，只生成待确认操作；"
                "确认后后台运行并返回 run_id，再用 providers.patrol_status 读取结果。失败不自动重试。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "provider_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "只巡检这些 Provider；省略表示全部 Provider。",
                    },
                    "models": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "只巡检这些已启用模型 ID；省略表示范围内全部已启用模型。",
                    },
                    "timeout_seconds": {"type": "integer", "minimum": 10, "maximum": 600},
                },
                "additionalProperties": False,
            },
            read_only=False,
            min_role="admin",
            risk="normal",
            diagnostic_safe=True,
            runtime_retryable=False,
            preview_handler=patrol_preview,
            execute_handler=patrol_execute,
        )
    )
    registry.register(
        ToolSpec(
            name=PATROL_STATUS_TOOL,
            channels=("web", "bot"),
            description=(
                "读取模型巡检进度与结果（只读）：按 Provider 分组列出模型、ok/失败、延迟、HTTP 状态和脱敏错误摘要。"
                "省略 run_id 时读取最近一次已确认的巡检；wait_seconds 最多等待 30 秒。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "run_id": {"type": "string"},
                    "wait_seconds": {"type": "integer", "minimum": 0, "maximum": 30},
                    "only_failed": {"type": "boolean"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": _MAX_RESULT_LIMIT},
                },
                "additionalProperties": False,
            },
            read_only=True,
            min_role="viewer",
            read_handler=patrol_status,
        )
    )
    registry.register(
        ToolSpec(
            name=PATROL_CANCEL_TOOL,
            channels=("web", "bot"),
            description="停止进行中的模型巡检（停止新请求并中断在途请求，保留已完成结果）。只生成待确认操作。",
            input_schema={
                "type": "object",
                "properties": {"run_id": {"type": "string"}},
                "additionalProperties": False,
            },
            read_only=False,
            min_role="admin",
            risk="normal",
            diagnostic_safe=True,
            runtime_retryable=False,
            preview_handler=cancel_preview,
            execute_handler=cancel_execute,
        )
    )


__all__ = [
    "PATROL_CANCEL_TOOL",
    "PATROL_STATUS_TOOL",
    "PATROL_TOOL",
    "group_results",
    "register",
]
