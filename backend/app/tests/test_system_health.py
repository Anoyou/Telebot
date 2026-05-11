"""系统健康探测 (`api/system_health.py`) 单元测试。

不连真 PG / Redis；mock 各依赖验证组装逻辑。
覆盖：
- DB / Redis 探测的成功 / 失败分支
- alembic 探测的"in_sync / 待跑列表 / 错误兜底"三种状态
- providers / proxies / workers 统计的字段聚合正确性
- 顶层 GET endpoint 的 timeout 兜底（任一子探测卡住不应让整个接口失败）
- ``auto_migrate_on_startup`` settings 字段默认 False
"""
from __future__ import annotations

import sys
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api import system_health as sh
from app.api.system_health import (
    AlembicStatus,
    DbStatus,
    ProvidersStatus,
    ProxiesStatus,
    RedisStatus,
    WorkersStatus,
    _probe_alembic,
    _probe_db,
    _probe_providers,
    _probe_proxies,
    _probe_redis,
    _probe_workers,
)

# ════════════════════════════════════════════════════════════
# 1) settings 默认值
# ════════════════════════════════════════════════════════════


def test_auto_migrate_default_false() -> None:
    """默认关闭启动期自动迁移，避免与部署脚本重复执行迁移。"""
    from app.settings import settings

    assert settings.auto_migrate_on_startup is False


# ════════════════════════════════════════════════════════════
# 2) DB 探测
# ════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_probe_db_ok() -> None:
    """SELECT version() 成功 → ok=True 且带版本字符串。"""

    fake_session = AsyncMock()
    fake_result = MagicMock()
    fake_result.scalar = MagicMock(return_value="PostgreSQL 16.1 on x86_64")
    fake_session.execute = AsyncMock(return_value=fake_result)

    fake_ctx = AsyncMock()
    fake_ctx.__aenter__.return_value = fake_session
    with patch("app.api.system_health.AsyncSessionLocal", return_value=fake_ctx):
        out = await _probe_db()
    assert out.ok is True
    assert out.version and "PostgreSQL" in out.version


@pytest.mark.asyncio
async def test_probe_db_failure_returns_error() -> None:
    """连不上 DB 时不能抛，要返 ok=False + error。"""
    with patch(
        "app.api.system_health.AsyncSessionLocal",
        side_effect=ConnectionError("nope"),
    ):
        out = await _probe_db()
    assert out.ok is False
    assert out.error and "ConnectionError" in out.error


@pytest.mark.asyncio
async def test_probe_db_truncates_long_version() -> None:
    """PG 的 version() 输出可能很长；要截断到 ~80 char。"""
    fake_session = AsyncMock()
    fake_result = MagicMock()
    fake_result.scalar = MagicMock(return_value="PostgreSQL " + "x" * 200)
    fake_session.execute = AsyncMock(return_value=fake_result)

    fake_ctx = AsyncMock()
    fake_ctx.__aenter__.return_value = fake_session
    with patch("app.api.system_health.AsyncSessionLocal", return_value=fake_ctx):
        out = await _probe_db()
    assert out.version and len(out.version) <= 90  # 80 + "..."
    assert out.version.endswith("...")


# ════════════════════════════════════════════════════════════
# 3) Redis 探测
# ════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_probe_redis_ok() -> None:
    fake = AsyncMock()
    fake.ping = AsyncMock(return_value=True)
    with patch("app.api.system_health.get_redis", return_value=fake):
        out = await _probe_redis()
    assert out.ok is True


@pytest.mark.asyncio
async def test_probe_redis_falsy_pong() -> None:
    """ping 返 False / 0 时也算失败。"""
    fake = AsyncMock()
    fake.ping = AsyncMock(return_value=False)
    with patch("app.api.system_health.get_redis", return_value=fake):
        out = await _probe_redis()
    assert out.ok is False


@pytest.mark.asyncio
async def test_probe_redis_exception() -> None:
    with patch(
        "app.api.system_health.get_redis", side_effect=ConnectionError("conn")
    ):
        out = await _probe_redis()
    assert out.ok is False
    assert out.error and "ConnectionError" in out.error


# ════════════════════════════════════════════════════════════
# 4) Providers 统计
# ════════════════════════════════════════════════════════════


def _make_provider(
    pid: int,
    *,
    provider: str = "openai",
    has_key: bool = True,
    proxy_id: int | None = None,
    modality: str = "text",
    cost_tier: int = 2,
):
    from app.db.models.command import LLMProvider

    return LLMProvider(
        id=pid,
        name=f"p{pid}",
        provider=provider,
        api_key_enc="fernet-fake" if has_key else None,
        base_url=None,
        default_model="m",
        modality=modality,
        tags=[],
        cost_tier=cost_tier,
        notes=None,
        proxy_id=proxy_id,
        created_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_probe_providers_aggregates_correctly() -> None:
    rows = [
        _make_provider(1, modality="text", cost_tier=1),
        _make_provider(2, modality="vision", cost_tier=3, proxy_id=1),
        _make_provider(3, modality="text", cost_tier=2, proxy_id=1, has_key=False),
        _make_provider(4, provider="ollama", modality="text", has_key=False),  # ollama 算"有 key"
    ]
    fake_session = AsyncMock()
    fake_result = MagicMock()
    fake_result.scalars.return_value.all = MagicMock(return_value=rows)
    fake_session.execute = AsyncMock(return_value=fake_result)

    fake_ctx = AsyncMock()
    fake_ctx.__aenter__.return_value = fake_session
    with patch("app.api.system_health.AsyncSessionLocal", return_value=fake_ctx):
        out = await _probe_providers()

    assert out.total == 4
    # has_key: #1 yes, #2 yes, #3 no, #4 ollama 视为 yes → 共 3
    assert out.with_api_key == 3
    # proxy_id 非空：#2 #3 → 2 条
    assert out.with_proxy == 2
    assert out.by_modality == {"text": 3, "vision": 1}
    assert out.by_cost_tier == {"1": 1, "2": 2, "3": 1}


@pytest.mark.asyncio
async def test_probe_providers_failure_returns_empty() -> None:
    """DB 异常时不能抛，要返空统计（保持其它子探测能继续展示）。"""
    with patch(
        "app.api.system_health.AsyncSessionLocal",
        side_effect=ConnectionError("nope"),
    ):
        out = await _probe_providers()
    assert isinstance(out, ProvidersStatus)
    assert out.total == 0


# ════════════════════════════════════════════════════════════
# 5) Proxies 统计
# ════════════════════════════════════════════════════════════


def _make_proxy(pid: int, ptype: str):
    from app.db.models.account import Proxy

    return Proxy(id=pid, type=ptype, host="x", port=1, username=None, password_enc=None)


@pytest.mark.asyncio
async def test_probe_proxies_aggregates_by_type_and_used() -> None:
    rows = [
        _make_proxy(1, "socks5"),
        _make_proxy(2, "socks5"),
        _make_proxy(3, "http"),
        _make_proxy(4, "mtproxy"),
    ]
    used_ids = [1, 1, 3, None]  # 模拟 LLMProvider.proxy_id 列：#1 被两次引用 + #3 + None

    fake_session = AsyncMock()

    # 两次 execute 调用：第一次列代理，第二次列 used_ids
    call_count = {"i": 0}

    async def _exec(*_a, **_kw):
        call_count["i"] += 1
        result = MagicMock()
        if call_count["i"] == 1:
            result.scalars.return_value.all = MagicMock(return_value=rows)
        else:
            result.scalars.return_value.all = MagicMock(return_value=used_ids)
        return result

    fake_session.execute = AsyncMock(side_effect=_exec)
    fake_ctx = AsyncMock()
    fake_ctx.__aenter__.return_value = fake_session
    with patch("app.api.system_health.AsyncSessionLocal", return_value=fake_ctx):
        out = await _probe_proxies()

    assert out.total == 4
    assert out.by_type == {"socks5": 2, "http": 1, "mtproxy": 1}
    # 去重：#1 + #3 = 2 条独立代理被引用
    assert out.used_by_llm == 2


# ════════════════════════════════════════════════════════════
# 6) Workers 统计
# ════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_probe_workers_aggregates_by_status() -> None:
    fake_session = AsyncMock()
    fake_result = MagicMock()
    # group_by 返回 [(status, count), ...]
    fake_result.all = MagicMock(
        return_value=[("active", 3), ("paused", 1), ("login_required", 1)]
    )
    fake_session.execute = AsyncMock(return_value=fake_result)

    fake_ctx = AsyncMock()
    fake_ctx.__aenter__.return_value = fake_session
    with patch("app.api.system_health.AsyncSessionLocal", return_value=fake_ctx):
        out = await _probe_workers()

    assert out.total == 5
    assert out.by_status == {"active": 3, "paused": 1, "login_required": 1}


# ════════════════════════════════════════════════════════════
# 7) Alembic 探测兜底
# ════════════════════════════════════════════════════════════


def test_probe_alembic_missing_ini_returns_error() -> None:
    """alembic.ini 不存在时返回 error 而不是抛。"""
    from pathlib import Path

    with patch("app.api.system_health.__file__", str(Path("/nonexistent/system_health.py"))):
        out = _probe_alembic()
    assert out.ok is False
    assert out.error


def test_probe_alembic_db_connect_failure() -> None:
    """连不上 DB 时（同步引擎层报错）返 ok=False。"""
    # _probe_alembic 内部用 ``from sqlalchemy import create_engine``，所以要 patch
    # 库本身那条符号
    with patch(
        "sqlalchemy.create_engine",
        side_effect=ConnectionError("nope"),
    ):
        out = _probe_alembic()
    assert isinstance(out, AlembicStatus)
    assert out.ok is False


def test_read_process_stats_prefers_psutil(monkeypatch) -> None:
    """资源面板优先用 psutil 读取进程 CPU/RSS，避免 Linux/Oracle 上 ps 输出差异。

    首次调用会初始化 ``cpu_percent`` 采样窗口，按 psutil 语义返回 None；
    第二次调用复用缓存的 Process 实例，给出真实差分 CPU%。这样跨 Dashboard
    轮询既能拿到准确值，又不需要旧实现里的 ``time.sleep(0.05)``。
    """

    class _FakeProcess:
        pid = 123

        def cpu_percent(self, interval=None):  # noqa: ANN001
            return 7.5

        def memory_info(self):
            return SimpleNamespace(rss=128 * 1024 * 1024)

        def is_running(self):
            return True

        def create_time(self):
            return 1.0

    fake_psutil = SimpleNamespace(Process=lambda pid: _FakeProcess())
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)
    monkeypatch.setattr(sh, "_read_process_stats_with_ps", lambda _pids: {123: (0.0, 0.0)})
    # 避免本测试受其他用例的进程缓存污染
    sh._PROC_CACHE.clear()

    first = sh._read_process_stats([123])
    second = sh._read_process_stats([123])

    assert first == {123: (None, 128.0)}
    assert second == {123: (7.5, 128.0)}
    sh._PROC_CACHE.clear()


def test_read_process_stats_falls_back_to_ps(monkeypatch) -> None:
    """psutil 不可用时仍保留原 ps fallback。"""

    monkeypatch.setattr(sh, "_read_process_stats_with_psutil", lambda _pids: None)
    monkeypatch.setattr(sh, "_read_process_stats_with_ps", lambda _pids: {456: (1.25, 64.0)})

    out = sh._read_process_stats([456])

    assert out == {456: (1.25, 64.0)}


# ════════════════════════════════════════════════════════════
# 8) 顶层 endpoint：超时与并行
# ════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_get_health_overview_resilient_to_one_probe_hanging() -> None:
    """如果 _probe_db 卡住超过 2s，超时降级为 ok=False，但其它子探测仍要返回。"""
    import asyncio

    async def _hanging_db() -> DbStatus:  # > 2s 超时，永不返回
        await asyncio.sleep(5)
        return DbStatus(ok=True)

    async def _ok_redis() -> RedisStatus:
        return RedisStatus(ok=True)

    async def _empty_providers() -> ProvidersStatus:
        return ProvidersStatus(total=0)

    async def _empty_proxies() -> ProxiesStatus:
        return ProxiesStatus(total=0)

    async def _empty_workers() -> WorkersStatus:
        return WorkersStatus(total=0)

    with (
        patch("app.api.system_health._probe_db", new=_hanging_db),
        patch("app.api.system_health._probe_redis", new=_ok_redis),
        patch("app.api.system_health._probe_providers", new=_empty_providers),
        patch("app.api.system_health._probe_proxies", new=_empty_proxies),
        patch("app.api.system_health._probe_workers", new=_empty_workers),
        patch(
            "app.api.system_health._probe_alembic",
            return_value=AlembicStatus(ok=True, current="0007", head="0007"),
        ),
    ):
        out = await sh.get_health_overview(_user=None)  # type: ignore[arg-type]
    # _probe_db 卡死被超时兜底
    assert out.db.ok is False
    # 其它项仍返回
    assert out.redis.ok is True
    assert out.providers.total == 0
    assert out.alembic.ok is True
