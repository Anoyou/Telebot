"""系统级只读工具。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select, text

from .... import __version__
from ....db.models.account import Account
from ....db.models.command import LLMProvider
from ....redis_client import get_redis
from ..config import load_config, load_system_context_flags
from ..context import ToolContext
from ..registry import ToolRegistry, ToolSpec
from ._helpers import get_timezone_name


async def get_context(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    flags = await load_system_context_flags(ctx.db)
    cfg = flags["agent_config"]
    return {
        "version": __version__,
        "timezone": flags["timezone"],
        "command_prefix": flags["command_prefix"],
        "ai_enabled": flags["ai_enabled"],
        "system_agent": {
            "enabled": cfg.get("enabled"),
            "provider_id": cfg.get("provider_id"),
            "model": cfg.get("model"),
            "max_steps": cfg.get("max_steps"),
            "max_tool_calls": cfg.get("max_tool_calls"),
            "session_token_limit": cfg.get("session_token_limit"),
        },
        "channel": ctx.channel,
        "role": ctx.role,
        "account_id": ctx.account_id,
    }


async def get_health(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    # DB
    try:
        await ctx.db.execute(text("SELECT 1"))
        db_check: dict[str, Any] = {"ok": True, "migration_revision": None}
        try:
            # SAVEPOINT 隔离缺表等错误，避免诊断查询把当前只读事务整体置为 failed。
            async with ctx.db.begin_nested():
                result = await ctx.db.execute(
                    text("SELECT version_num FROM alembic_version LIMIT 1")
                )
                db_check["migration_revision"] = result.scalar_one_or_none()
            if db_check["migration_revision"] is None:
                db_check["migration_warning"] = "未找到数据库迁移版本记录"
        except Exception as exc:  # noqa: BLE001
            db_check["migration_warning"] = f"无法读取数据库迁移版本：{str(exc)[:160]}"
        checks["db"] = db_check
    except Exception as exc:  # noqa: BLE001
        checks["db"] = {"ok": False, "migration_revision": None, "error": str(exc)[:200]}
    # Redis
    try:
        r = get_redis()
        pong = await r.ping()
        checks["redis"] = {"ok": bool(pong)}
    except Exception as exc:  # noqa: BLE001
        checks["redis"] = {"ok": False, "error": str(exc)[:200]}
    # 账号统计
    try:
        result = await ctx.db.execute(select(Account.id, Account.status))
        rows = result.all()
        by_status: dict[str, int] = {}
        for _id, status in rows:
            key = str(status or "unknown")
            by_status[key] = by_status.get(key, 0) + 1
        checks["accounts"] = {"ok": True, "total": len(rows), "by_status": by_status}
    except Exception as exc:  # noqa: BLE001
        checks["accounts"] = {"ok": False, "error": str(exc)[:200]}
    # Provider 数量
    try:
        result = await ctx.db.execute(select(LLMProvider.id))
        checks["llm_providers"] = {"ok": True, "count": len(result.scalars().all())}
    except Exception as exc:  # noqa: BLE001
        checks["llm_providers"] = {"ok": False, "error": str(exc)[:200]}

    overall = all(bool(v.get("ok")) for v in checks.values() if isinstance(v, dict))
    tz = await get_timezone_name(ctx.db)
    agent_cfg = await load_config(ctx.db)
    return {
        "ok": overall,
        "timezone": tz,
        "system_agent_enabled": bool(agent_cfg.get("enabled")),
        "checks": checks,
    }


async def get_resources(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    """复用资源面板 collector，避免维护第二套口径更弱的采样逻辑。"""

    del ctx, args
    try:
        from ....api.system_health import get_resource_dashboard

        snapshot = await get_resource_dashboard(None)  # type: ignore[arg-type]
        return {**snapshot.model_dump(mode="json"), "business_changed": False}
    except Exception as exc:  # noqa: BLE001
        return {
            "error": "resource_probe_failed",
            "message": f"资源探测失败：{type(exc).__name__}",
            "business_changed": False,
        }


async def get_versions(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    """只读汇总运行时与依赖版本；不返回环境变量、连接串或任何密钥。"""

    from ...runtime_versions import collect_runtime_versions

    package = args.get("package")
    package_name = str(package).strip() if isinstance(package, str) else ""
    probe_gateway = args.get("probe_gateway")
    try:
        result = await collect_runtime_versions(
            ctx.db,
            package=package_name or None,
            probe_gateway=True if probe_gateway is None else bool(probe_gateway),
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "error": "version_probe_failed",
            "message": f"版本信息采集失败：{type(exc).__name__}",
            "business_changed": False,
        }
    result["business_changed"] = False
    return result


def register(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="system.get_versions",
            channels=("web", "bot"),
            description=(
                "查询当前部署的运行时与依赖版本：TelePilot 版本与部署 Git commit、Python 版本、"
                "内置 Telethon 版本（含 MTProto Layer 与 cryptg）、内置 telepilot-gateway"
                "（Codex Gateway）的实际运行版本/协议版本/构建 commit、FastAPI/SQLAlchemy/"
                "Pydantic/httpx/redis 等核心依赖版本、Worker 进程运行环境、PostgreSQL/SQLite "
                "与 Redis 服务端版本、操作系统/容器信息。用户问“telethon 版本”“gateway 版本”"
                "“依赖/库版本”“Python 版本”“数据库/Redis 版本”“部署的是哪个 commit”时调用。"
                "可用 package 参数查询任意已安装 Python 包的版本。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "package": {
                        "type": "string",
                        "description": "可选：要额外查询的 Python 包名（如 telethon、openai、orjson），大小写和 -/_ 不敏感。",
                        "maxLength": 100,
                    },
                    "probe_gateway": {
                        "type": "boolean",
                        "description": "Gateway 未运行时是否短暂探测内置二进制的 /version，默认 true。",
                    },
                },
                "additionalProperties": False,
            },
            read_only=True,
            min_role="viewer",
            read_handler=get_versions,
        )
    )
    registry.register(
        ToolSpec(
            name="system.get_resources",
            channels=("web",),
            description="获取主机 CPU/内存/磁盘与 TelePilot 主进程、Worker 进程资源快照。",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            read_only=True,
            min_role="viewer",
            read_handler=get_resources,
        )
    )
    registry.register(
        ToolSpec(
            name="system.get_context",
            channels=("web",),
            description="获取系统上下文：时区、指令前缀、AI/Agent 开关、版本与当前会话上下文。",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            read_only=True,
            min_role="viewer",
            read_handler=get_context,
        )
    )
    registry.register(
        ToolSpec(
            name="system.get_health",
            channels=("web",),
            description="获取系统健康状态：数据库及迁移版本、Redis、账号分布、Provider 数量等组件就绪信息。",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            read_only=True,
            min_role="viewer",
            read_handler=get_health,
        )
    )
