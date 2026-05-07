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
import subprocess
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import JSONResponse
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


# ════════════════════════════════════════════════════════════
# 检查更新 / 拉取更新 / 重启（分步确认式）
# ════════════════════════════════════════════════════════════


def _git_root() -> Path | None:
    """返回项目 git 仓库根目录（含 .git），找不到返回 None。"""
    try:
        p = Path(__file__).resolve()
        for parent in (p, *p.parents):
            if (parent / ".git").exists():
                return parent
    except Exception:
        pass
    return None


def _run_git(*args: str, timeout: int = 30) -> tuple[str, str, int]:
    """同步执行 git 命令，返回 (stdout, stderr, returncode)。"""
    root = _git_root()
    if not root:
        return "", "git root not found", 1
    try:
        result = subprocess.run(
            ["git"] + list(args),
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.stdout.strip(), result.stderr.strip(), result.returncode
    except subprocess.TimeoutExpired:
        return "", "git command timed out", 1
    except Exception as e:
        return "", str(e), 1


class CheckUpdateResponse(BaseModel):
    has_update: bool = False
    current_commit: str | None = None
    remote_commit: str | None = None
    ahead: int = 0
    error: str | None = None


class PullUpdateResponse(BaseModel):
    success: bool = False
    new_commit: str | None = None
    summary: str | None = None
    error: str | None = None


class RestartResponse(BaseModel):
    success: bool = False
    error: str | None = None


@router.post("/check-update", response_model=CheckUpdateResponse)
async def check_update(_user: CurrentUser) -> CheckUpdateResponse:
    """仅 git fetch + 对比本地/远程 commit，不拉取代码。"""
    try:
        fetch_out, fetch_err, fetch_rc = await asyncio.to_thread(
            _run_git, "fetch", "origin", timeout=30
        )
        if fetch_rc != 0:
            return CheckUpdateResponse(error=f"git fetch 失败: {fetch_err or fetch_out}")

        head_out, _, head_rc = await asyncio.to_thread(
            _run_git, "rev-parse", "HEAD", timeout=10
        )
        if head_rc != 0:
            return CheckUpdateResponse(error="无法获取当前 commit")

        remote_out, _, remote_rc = await asyncio.to_thread(
            _run_git, "rev-parse", "origin/main", timeout=10
        )
        if remote_rc != 0:
            # 尝试 origin/master 作为 fallback
            remote_out, _, remote_rc = await asyncio.to_thread(
                _run_git, "rev-parse", "origin/master", timeout=10
            )
        if remote_rc != 0:
            return CheckUpdateResponse(error="无法获取远程 commit（origin/main 或 origin/master）")

        current = head_out[:12]
        remote = remote_out[:12]
        has_update = head_out != remote_out

        # 计算 ahead（本地落后远程多少个 commit）
        ahead_out, _, ahead_rc = await asyncio.to_thread(
            _run_git, "rev-list", "--count", f"{remote_out}..{head_out}", timeout=10
        )
        behind_out, _, _ = await asyncio.to_thread(
            _run_git, "rev-list", "--count", f"{head_out}..{remote_out}", timeout=10
        )
        behind = int(behind_out) if not _ else 0

        return CheckUpdateResponse(
            has_update=has_update and behind > 0,
            current_commit=current,
            remote_commit=remote,
            ahead=behind,
        )
    except Exception as e:  # noqa: BLE001
        return CheckUpdateResponse(error=f"{type(e).__name__}: {str(e)[:200]}")


@router.post("/pull-update", response_model=PullUpdateResponse)
async def pull_update(_user: CurrentUser) -> PullUpdateResponse:
    """仅执行 git pull，不重启。"""
    try:
        out, err, rc = await asyncio.to_thread(
            _run_git, "pull", "origin", "main", timeout=60
        )
        if rc != 0:
            # 尝试 master
            out, err, rc = await asyncio.to_thread(
                _run_git, "pull", "origin", "master", timeout=60
            )
        if rc != 0:
            return PullUpdateResponse(error=f"git pull 失败: {err or out}")

        # 获取最新 commit
        head_out, _, _ = await asyncio.to_thread(
            _run_git, "rev-parse", "HEAD", timeout=10
        )
        # 获取简短 summary
        summary_out, _, _ = await asyncio.to_thread(
            _run_git, "log", "-1", "--oneline", timeout=10
        )

        return PullUpdateResponse(
            success=True,
            new_commit=head_out[:12] if head_out else None,
            summary=summary_out or None,
        )
    except Exception as e:  # noqa: BLE001
        return PullUpdateResponse(error=f"{type(e).__name__}: {str(e)[:200]}")


@router.post("/restart", response_model=RestartResponse)
async def restart_app(_user: CurrentUser) -> RestartResponse:
    """触发应用重启。使用 subprocess detach 避免阻塞当前进程。"""
    try:
        root = _git_root()
        if not root:
            return RestartResponse(error="git root not found")

        # 检测运行方式：有 docker-compose.yml 用 docker，否则用 make
        compose = root / "docker-compose.yml"
        makefile = root / "Makefile"

        if compose.exists():
            cmd = ["docker", "compose", "restart"]
        elif makefile.exists():
            cmd = ["make", "restart"]
        else:
            # 直接杀进程，依赖 systemd/docker 等外部重启机制
            import os
            import signal

            os.kill(os.getpid(), signal.SIGTERM)
            return RestartResponse(success=True)

        subprocess.Popen(
            cmd,
            cwd=str(root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return RestartResponse(success=True)
    except Exception as e:  # noqa: BLE001
        return RestartResponse(error=f"{type(e).__name__}: {str(e)[:200]}")


# ════════════════════════════════════════════════════════════
# 配置备份与导出 / 导入
# ════════════════════════════════════════════════════════════

# 每个类别定义：ORM 模型、序列化时排除的敏感字段、标识字段（用于去重）
_EXPORT_DEFS: dict[str, dict[str, Any]] = {
    "system_settings": {
        "model": "SystemSetting",
        "exclude_fields": set(),
        "id_fields": {"key"},
    },
    "command_templates": {
        "model": "CommandTemplate",
        "exclude_fields": set(),
        "id_fields": {"name"},
    },
    "account_commands": {
        "model": "AccountCommandLink",
        "exclude_fields": set(),
        "id_fields": {"account_id", "template_id"},
    },
    "llm_providers": {
        "model": "LLMProvider",
        "exclude_fields": {"api_key_enc"},
        "id_fields": {"name"},
    },
    "forward_rules": {
        "model": "Rule",
        "exclude_fields": set(),
        "filter": {"feature_key": "forward"},
        "id_fields": {"account_id", "feature_key", "name"},
    },
    "auto_reply_rules": {
        "model": "Rule",
        "exclude_fields": set(),
        "filter": {"feature_key": "auto_reply"},
        "id_fields": {"account_id", "feature_key", "name"},
    },
    "rate_limit_templates": {
        "model": "RateLimitTemplate",
        "exclude_fields": set(),
        "id_fields": {"name"},
    },
    "rate_limit_rules": {
        "model": "RateLimitRule",
        "exclude_fields": set(),
        "id_fields": {"scope", "scope_id", "action"},
    },
    "feature_config": {
        "model": "AccountFeature",
        "exclude_fields": set(),
        "id_fields": {"account_id", "feature_key"},
    },
    "account_settings": {
        "model": "Account",
        "exclude_fields": {"session_enc", "api_id_enc", "api_hash_enc", "phone"},
        "id_fields": {"id"},
    },
    "ignored_peers": {
        "model": "IgnoredPeer",
        "exclude_fields": set(),
        "id_fields": {"account_id", "peer_id"},
    },
    "notify_bots": {
        "model": "NotifyBot",
        "exclude_fields": {"bot_token_enc"},
        "id_fields": {"name"},
    },
}

# 敏感字段的完整集合（include_sensitive=true 时不排除）
_ALL_SENSITIVE = {"session_enc", "api_key_enc", "api_id_enc", "api_hash_enc", "phone", "bot_token_enc", "password_enc"}


class ExportConfigRequest(BaseModel):
    categories: list[str] = Field(default_factory=list)
    include_sensitive: bool = False


def _row_to_dict(row: Any, exclude: set[str], include_sensitive: bool) -> dict[str, Any]:
    """将 ORM 行转为 dict，排除指定字段。"""
    data = {}
    for col in row.__table__.columns:
        name = col.name
        if include_sensitive or name not in exclude:
            val = getattr(row, name)
            # 处理不可序列化的类型
            if hasattr(val, "isoformat"):
                val = val.isoformat()
            elif isinstance(val, (bytes, bytearray)):
                val = val.hex() if include_sensitive else None
            data[name] = val
    return {k: v for k, v in data.items() if v is not None}


@router.post("/export-config")
async def export_config(
    _user: CurrentUser,
    body: ExportConfigRequest,
) -> JSONResponse:
    """导出配置为 JSON 文件下载。"""
    from .. import __version__
    from ..db.models import (
        Account,
        AccountCommandLink,
        AccountFeature,
        CommandTemplate,
        IgnoredPeer,
        LLMProvider,
        NotifyBot,
        RateLimitRule,
        RateLimitTemplate,
        Rule,
    )
    from ..db.models.system import SystemSetting

    model_map = {
        "SystemSetting": SystemSetting,
        "CommandTemplate": CommandTemplate,
        "AccountCommandLink": AccountCommandLink,
        "LLMProvider": LLMProvider,
        "Rule": Rule,
        "RateLimitTemplate": RateLimitTemplate,
        "RateLimitRule": RateLimitRule,
        "AccountFeature": AccountFeature,
        "Account": Account,
        "IgnoredPeer": IgnoredPeer,
        "NotifyBot": NotifyBot,
    }

    result: dict[str, Any] = {
        "_meta": {
            "version": __version__,
            "exported_at": datetime.now().isoformat(),
            "include_sensitive": body.include_sensitive,
        },
    }

    for cat in body.categories:
        defn = _EXPORT_DEFS.get(cat)
        if not defn:
            continue
        model_cls = model_map.get(defn["model"])
        if not model_cls:
            continue

        exclude = defn["exclude_fields"]
        if body.include_sensitive:
            exclude = set()

        try:
            async with AsyncSessionLocal() as db:
                query = select(model_cls)
                filt = defn.get("filter")
                if filt:
                    for k, v in filt.items():
                        query = query.where(getattr(model_cls, k) == v)
                rows = (await db.execute(query)).scalars().all()
                result[cat] = [_row_to_dict(r, exclude, body.include_sensitive) for r in rows]
        except Exception as e:  # noqa: BLE001
            result[cat] = {"_error": f"{type(e).__name__}: {str(e)[:200]}"}

    filename = f"telebot-config-{datetime.now().strftime('%Y-%m-%d')}.json"
    return JSONResponse(
        content=result,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


class ImportConfigResponse(BaseModel):
    imported: int = 0
    skipped: int = 0
    warnings: list[str] = Field(default_factory=list)


@router.post("/import-config", response_model=ImportConfigResponse)
async def import_config(
    _user: CurrentUser,
    file: UploadFile = File(...),
) -> ImportConfigResponse:
    """从上传的 JSON 文件导入配置。冲突策略：同名/同 ID 跳过并记录。"""
    import json as _json

    from ..db.models import (
        Account,
        AccountCommandLink,
        AccountFeature,
        CommandTemplate,
        IgnoredPeer,
        LLMProvider,
        NotifyBot,
        RateLimitRule,
        RateLimitTemplate,
        Rule,
    )
    from ..db.models.system import SystemSetting

    model_map = {
        "SystemSetting": SystemSetting,
        "CommandTemplate": CommandTemplate,
        "AccountCommandLink": AccountCommandLink,
        "LLMProvider": LLMProvider,
        "Rule": Rule,
        "RateLimitTemplate": RateLimitTemplate,
        "RateLimitRule": RateLimitRule,
        "AccountFeature": AccountFeature,
        "Account": Account,
        "IgnoredPeer": IgnoredPeer,
        "NotifyBot": NotifyBot,
    }

    content = await file.read()
    try:
        data = _json.loads(content)
    except Exception:
        return ImportConfigResponse(warnings=["上传的文件不是合法的 JSON"])

    meta = data.pop("_meta", {})
    imported = 0
    skipped = 0
    warnings: list[str] = []

    for cat, rows in data.items():
        defn = _EXPORT_DEFS.get(cat)
        if not defn or not isinstance(rows, list):
            if isinstance(rows, dict) and "_error" in rows:
                warnings.append(f"[{cat}] 导出时出错: {rows['_error']}")
            continue

        model_cls = model_map.get(defn["model"])
        if not model_cls:
            continue

        id_fields = defn["id_fields"]
        exclude = defn["exclude_fields"]
        include_sensitive = meta.get("include_sensitive", False)
        if include_sensitive:
            exclude = set()

        try:
            async with AsyncSessionLocal() as db:
                for row_data in rows:
                    if not isinstance(row_data, dict):
                        continue
                    # 检查是否已存在（按 id_fields 判断冲突）
                    exists_query = select(model_cls)
                    for f in id_fields:
                        if f in row_data:
                            exists_query = exists_query.where(
                                getattr(model_cls, f) == row_data[f]
                            )
                    existing = (await db.execute(exists_query.limit(1))).scalar_one_or_none()
                    if existing is not None:
                        skipped += 1
                        continue

                    # 过滤排除字段 + 不允许覆盖 id 等自动生成字段
                    auto_fields = {"id", "created_at", "updated_at"}
                    filtered = {
                        k: v for k, v in row_data.items()
                        if k not in exclude and k not in auto_fields
                    }

                    # hex 字符串转回 bytes（session_enc）
                    for k, v in list(filtered.items()):
                        col_type = getattr(model_cls.__table__.c, k, None)
                        if col_type and hasattr(col_type.type, "python_type"):
                            try:
                                py_type = col_type.type.python_type
                                if py_type is bytes and isinstance(v, str):
                                    filtered[k] = bytes.fromhex(v)
                            except Exception:
                                pass

                    try:
                        new_row = model_cls(**filtered)
                        db.add(new_row)
                        imported += 1
                    except Exception as exc:  # noqa: BLE001
                        warnings.append(f"[{cat}] 插入失败: {str(exc)[:100]}")

                await db.commit()
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"[{cat}] 批量导入失败: {str(exc)[:200]}")

    return ImportConfigResponse(imported=imported, skipped=skipped, warnings=warnings)


__all__ = ["router"]
