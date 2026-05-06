"""系统健康概览 API。

提供：
  - ``GET /api/system/health-overview``  一次性返回所有运维向状态：
    DB 连通 + alembic 版本同步 / Redis / LLM provider 池 / 代理 / 账号 worker 状态分布

设计目标：
- 所有探测 ≤ 2s 超时；任一项失败不影响其他项；前端能在 Dashboard 一眼看清"系统健不健康"
- 不返回敏感字段（不含明文 api_key、不含 proxy 密码、不含 session_str）
- 老数据兼容：getattr 兜底，避免历史迁移没跑齐时直接 500
"""

from __future__ import annotations

import asyncio
from collections import Counter
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text

from ..db.base import AsyncSessionLocal
from ..db.models.account import Account, Proxy
from ..db.models.command import LLMProvider
from ..deps import CurrentUser
from ..redis_client import get_redis

router = APIRouter(prefix="/api/system", tags=["system"])


# ════════════════════════════════════════════════════════════
# 0.4.2 版本号端点（public — 前端启动 / 未登录页都要能调）
# ════════════════════════════════════════════════════════════


class VersionInfo(BaseModel):
    """``GET /api/system/version`` 响应。

    前端启动时拉一次 + 每 60s 轮询，对比前端 ``APP_VERSION`` 检测前后端版本是否一致；
    不一致时 sidebar 顶部弹红条提示用户 `make restart` + 硬刷浏览器。

    public 接口（**无鉴权**）：未登录页也要能调，否则发现"前端是新版后端是旧版"
    会一直登不上去（旧 schema 拒新登录字段之类）。返回的字段都是公开的版本元数据。
    """

    version: str
    """SemVer 形式，如 ``0.4.2``"""
    stage: str | None = None
    """非正式标签，如 ``Sprint 4``；达到 1.0.0 后通常 None"""


@router.get("/version", response_model=VersionInfo)
async def get_version() -> VersionInfo:
    """返回后端版本号（无鉴权）。"""
    from .. import APP_STAGE, __version__

    return VersionInfo(version=__version__, stage=APP_STAGE)


# ════════════════════════════════════════════════════════════
# Schemas
# ════════════════════════════════════════════════════════════


class DbStatus(BaseModel):
    ok: bool
    version: str | None = None
    """形如 ``"PostgreSQL 16.1"``。失败时为 None；error 字段含原因。"""
    error: str | None = None


class AlembicStatus(BaseModel):
    ok: bool
    """``True`` 表示 DB 当前版本 == 代码 head；``False`` 表示需要跑 ``alembic upgrade head``。"""
    current: str | None = None
    """DB 里 ``alembic_version`` 表的版本字符串。"""
    head: str | None = None
    """代码仓库里 alembic 链的最新版本。"""
    pending: list[str] = Field(default_factory=list)
    """已经写在文件里、但还没 apply 到 DB 的迁移版本号列表（按时间序）。"""
    error: str | None = None


class RedisStatus(BaseModel):
    ok: bool
    error: str | None = None


class ProvidersStatus(BaseModel):
    total: int = 0
    with_api_key: int = 0
    """配齐了 api_key（或 ollama 本地）能直接被调的数量。"""
    with_proxy: int = 0
    """指定了出口代理的 provider 数量；其余走 DIRECT。"""
    by_modality: dict[str, int] = Field(default_factory=dict)
    """按 modality 计数，如 ``{"text":2,"vision":1,"multimodal":1}``。"""
    by_cost_tier: dict[str, int] = Field(default_factory=dict)
    """按 cost_tier 计数，如 ``{"1":1,"2":2,"3":1}``。key 是 str 是因为 JSON 不支持 int 键。"""


class ProxiesStatus(BaseModel):
    total: int = 0
    by_type: dict[str, int] = Field(default_factory=dict)
    """如 ``{"socks5":2,"http":1}``。mtproxy 也算在内；前端展示"可用于 LLM 的"由前端过滤。"""
    used_by_llm: int = 0
    """被某个 LLMProvider.proxy_id 引用的代理数量（去重）。"""


class WorkersStatus(BaseModel):
    total: int = 0
    by_status: dict[str, int] = Field(default_factory=dict)
    """如 ``{"active":3,"paused":1,"login_required":1,"dead":0,"floodwait":0}``。"""


class HealthOverview(BaseModel):
    """前端 Dashboard 用的一次性聚合状态。"""

    db: DbStatus
    alembic: AlembicStatus
    redis: RedisStatus
    providers: ProvidersStatus
    proxies: ProxiesStatus
    workers: WorkersStatus


# ════════════════════════════════════════════════════════════
# 各子探测
# ════════════════════════════════════════════════════════════


async def _probe_db() -> DbStatus:
    """``SELECT version()`` 顺手把 DB 版本号也带回来。"""
    try:
        async with AsyncSessionLocal() as db:
            row = (await db.execute(text("SELECT version()"))).scalar()
            ver_str = str(row or "").strip()
            # 把超长字符串截断；PostgreSQL 16.1 (Debian 16.1-1.pgdg120+1) on x86_64...
            if len(ver_str) > 80:
                ver_str = ver_str[:80].rstrip() + "..."
            return DbStatus(ok=True, version=ver_str)
    except Exception as e:  # noqa: BLE001
        return DbStatus(ok=False, error=f"{type(e).__name__}: {str(e)[:200]}")


async def _probe_redis() -> RedisStatus:
    try:
        r = get_redis()
        pong = await r.ping()
        if not pong:
            return RedisStatus(ok=False, error="PING returned falsy")
        return RedisStatus(ok=True)
    except Exception as e:  # noqa: BLE001
        return RedisStatus(ok=False, error=f"{type(e).__name__}: {str(e)[:200]}")


def _probe_alembic() -> AlembicStatus:
    """对比 DB 里 alembic_version 与代码仓库里的 head。

    同步实现（alembic API 都是同步）；调用方应在 ``asyncio.to_thread`` 里跑。
    """
    try:
        from pathlib import Path

        from alembic.config import Config
        from alembic.runtime.migration import MigrationContext
        from alembic.script import ScriptDirectory
        from sqlalchemy import create_engine

        from ..settings import settings

        ini_path = Path(__file__).resolve().parents[2] / "alembic.ini"
        if not ini_path.exists():
            return AlembicStatus(ok=False, error=f"alembic.ini 不存在：{ini_path}")

        cfg = Config(str(ini_path))
        script = ScriptDirectory.from_config(cfg)
        head_rev = script.get_current_head() or ""

        # 同步引擎读 alembic_version
        sync_engine = create_engine(settings.database_url_sync)
        try:
            with sync_engine.connect() as conn:
                ctx = MigrationContext.configure(conn)
                current = ctx.get_current_revision() or ""
        finally:
            sync_engine.dispose()

        in_sync = bool(head_rev) and current == head_rev
        pending: list[str] = []
        if not in_sync and head_rev:
            # 列出从 current 到 head 之间还差哪几个迁移
            try:
                for rev in script.walk_revisions(base="base", head=head_rev):
                    if rev.revision == current:
                        break
                    pending.append(rev.revision)
                pending.reverse()  # walk_revisions 默认 head→base，反过来变 base→head
            except Exception:
                pending = []
        return AlembicStatus(
            ok=in_sync, current=current or None, head=head_rev or None, pending=pending
        )
    except Exception as e:  # noqa: BLE001
        return AlembicStatus(ok=False, error=f"{type(e).__name__}: {str(e)[:200]}")


async def _probe_providers() -> ProvidersStatus:
    try:
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(select(LLMProvider))).scalars().all()
        total = len(rows)
        with_key = sum(
            1 for r in rows
            if r.api_key_enc or (r.provider or "").lower() == "ollama"
        )
        with_proxy = sum(1 for r in rows if r.proxy_id is not None)
        by_modality: Counter[str] = Counter(
            (getattr(r, "modality", None) or "text") for r in rows
        )
        by_cost_tier: Counter[str] = Counter(
            str(int(getattr(r, "cost_tier", None) or 2)) for r in rows
        )
        return ProvidersStatus(
            total=total,
            with_api_key=with_key,
            with_proxy=with_proxy,
            by_modality=dict(by_modality),
            by_cost_tier=dict(by_cost_tier),
        )
    except Exception:  # noqa: BLE001
        # 失败时返空统计而不是抛——alembic 不同步时 SELECT * 会爆，但 alembic 探测自己会标 ok=False
        return ProvidersStatus()


async def _probe_proxies() -> ProxiesStatus:
    try:
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(select(Proxy))).scalars().all()
            # 被 LLMProvider 引用的 proxy id 集合
            used_ids = (
                await db.execute(
                    select(LLMProvider.proxy_id).where(LLMProvider.proxy_id.is_not(None))
                )
            ).scalars().all()
        used_set = {x for x in used_ids if x is not None}
        by_type: Counter[str] = Counter((p.type or "?").lower() for p in rows)
        return ProxiesStatus(
            total=len(rows),
            by_type=dict(by_type),
            used_by_llm=len(used_set),
        )
    except Exception:  # noqa: BLE001
        return ProxiesStatus()


async def _probe_workers() -> WorkersStatus:
    """按 ``account.status`` 统计；不区分"是否真的 worker 子进程在跑"——那是 supervisor 的事。"""
    try:
        async with AsyncSessionLocal() as db:
            rows = (
                await db.execute(
                    select(Account.status, func.count(Account.id)).group_by(Account.status)
                )
            ).all()
        total = sum(int(c) for _, c in rows)
        by_status = {str(s): int(c) for s, c in rows}
        return WorkersStatus(total=total, by_status=by_status)
    except Exception:  # noqa: BLE001
        return WorkersStatus()


# ════════════════════════════════════════════════════════════
# 路由
# ════════════════════════════════════════════════════════════


@router.get("/health-overview", response_model=HealthOverview)
async def get_health_overview(_user: CurrentUser) -> HealthOverview:
    """聚合一次性返所有运维状态。各子探测并行 + 各自带 2s 超时。"""

    async def _safe(coro: Any, fallback: Any) -> Any:
        try:
            return await asyncio.wait_for(coro, timeout=2.0)
        except (TimeoutError, Exception):
            return fallback

    db_t = _safe(_probe_db(), DbStatus(ok=False, error="timeout/exception"))
    redis_t = _safe(_probe_redis(), RedisStatus(ok=False, error="timeout/exception"))
    providers_t = _safe(_probe_providers(), ProvidersStatus())
    proxies_t = _safe(_probe_proxies(), ProxiesStatus())
    workers_t = _safe(_probe_workers(), WorkersStatus())
    # alembic 探测是同步阻塞，扔到线程池跑
    alembic_t = _safe(asyncio.to_thread(_probe_alembic), AlembicStatus(ok=False, error="timeout"))

    db, alembic, redis_, providers, proxies, workers = await asyncio.gather(
        db_t, alembic_t, redis_t, providers_t, proxies_t, workers_t
    )
    return HealthOverview(
        db=db,
        alembic=alembic,
        redis=redis_,
        providers=providers,
        proxies=proxies,
        workers=workers,
    )


__all__ = ["router"]
