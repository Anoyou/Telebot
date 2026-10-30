"""第 20 轮：新建 Agent 会话时只有 1 个账号则默认关联（#111 后续）。

- POST /api/system-agent/sessions 新增可选 default_account；未传 account_id 且服务器只有
  1 个账号时自动关联，响应 account_auto_linked=true；0 个或多个账号保持系统级。
- 显式 account_id 优先；不传 default_account（旧客户端）行为不变。
- /api/system/capabilities 的 features.agent_session_default_account 供客户端探测。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import system_agent as api
from app.db.models.system import SystemSetting
from app.db.models.system_agent import SystemAgentMessage, SystemAgentRunEvent, SystemAgentSession
from app.schemas.platform_capabilities import PLATFORM_FEATURE_FLAGS, PlatformCapabilitiesOut
from app.schemas.system_agent import SystemAgentSessionCreate
from app.services.system_agent.service import SystemAgentService


@pytest.fixture
async def agent_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE web_user (id INTEGER PRIMARY KEY)"))
        await conn.execute(text("CREATE TABLE account (id INTEGER PRIMARY KEY)"))
        await conn.run_sync(SystemSetting.__table__.create)
        await conn.run_sync(SystemAgentSession.__table__.create)
        await conn.run_sync(SystemAgentMessage.__table__.create)
        await conn.run_sync(SystemAgentRunEvent.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.fixture
def real_svc(monkeypatch):
    svc = SystemAgentService()
    monkeypatch.setattr(api, "get_system_agent_service", lambda: svc)
    return svc


async def _add_accounts(factory, *ids: int) -> None:
    async with factory() as db:
        for account_id in ids:
            await db.execute(text("INSERT INTO account (id) VALUES (:id)"), {"id": account_id})
        await db.commit()


async def _create(factory, payload: SystemAgentSessionCreate):
    async with factory() as db:
        return await api.create_session(payload, db, SimpleNamespace(id=1))


@pytest.mark.asyncio
async def test_sole_account_id_counts_accounts(agent_db) -> None:
    svc = SystemAgentService()
    async with agent_db() as db:
        assert await svc.sole_account_id(db) is None
    await _add_accounts(agent_db, 5)
    async with agent_db() as db:
        assert await svc.sole_account_id(db) == 5
    await _add_accounts(agent_db, 9)
    async with agent_db() as db:
        assert await svc.sole_account_id(db) is None


@pytest.mark.asyncio
async def test_default_account_links_sole_account(agent_db, real_svc) -> None:
    await _add_accounts(agent_db, 7)
    out = await _create(agent_db, SystemAgentSessionCreate(default_account=True))
    assert out.account_id == 7
    assert out.account_auto_linked is True
    async with agent_db() as db:
        row = await db.get(SystemAgentSession, out.id)
        assert row is not None and row.account_id == 7


@pytest.mark.asyncio
async def test_default_account_keeps_global_with_multiple_accounts(agent_db, real_svc) -> None:
    await _add_accounts(agent_db, 7, 8)
    out = await _create(agent_db, SystemAgentSessionCreate(default_account=True))
    assert out.account_id is None
    assert out.account_auto_linked is False


@pytest.mark.asyncio
async def test_default_account_without_accounts_stays_global(agent_db, real_svc) -> None:
    out = await _create(agent_db, SystemAgentSessionCreate(default_account=True))
    assert out.account_id is None
    assert out.account_auto_linked is False


@pytest.mark.asyncio
async def test_old_client_without_flag_stays_global(agent_db, real_svc) -> None:
    """旧客户端不传 default_account：单账号也保持系统级（行为不变）。"""

    await _add_accounts(agent_db, 7)
    out = await _create(agent_db, SystemAgentSessionCreate())
    assert out.account_id is None
    assert out.account_auto_linked is False


@pytest.mark.asyncio
async def test_explicit_account_wins_over_default(agent_db, real_svc) -> None:
    await _add_accounts(agent_db, 7, 8)
    out = await _create(agent_db, SystemAgentSessionCreate(account_id=8, default_account=True))
    assert out.account_id == 8
    assert out.account_auto_linked is False


@pytest.mark.asyncio
async def test_explicit_missing_account_still_404(agent_db, real_svc) -> None:
    await _add_accounts(agent_db, 7)
    with pytest.raises(HTTPException) as exc:
        await _create(agent_db, SystemAgentSessionCreate(account_id=99, default_account=True))
    assert exc.value.status_code == 404
    assert exc.value.detail["code"] == "ACCOUNT_NOT_FOUND"


@pytest.mark.asyncio
async def test_patch_can_clear_auto_linked_account(agent_db, real_svc) -> None:
    """自动关联后用户可在会话设置改回全局（PATCH account_id=null）。"""

    await _add_accounts(agent_db, 7)
    out = await _create(agent_db, SystemAgentSessionCreate(default_account=True))
    assert out.account_id == 7
    async with agent_db() as db:
        patched = await api.update_session(
            out.id,
            api.SystemAgentSessionUpdate(account_id=None),
            db,
            SimpleNamespace(id=1),
        )
    assert patched.account_id is None
    assert patched.account_auto_linked is False


def test_unknown_fields_ignored_by_old_schema_shape() -> None:
    """客户端总是可以带 default_account：字段可选，缺省 False。"""

    assert SystemAgentSessionCreate().default_account is False
    assert SystemAgentSessionCreate.model_validate({"default_account": True}).default_account is True


def test_feature_flag_advertised() -> None:
    assert PLATFORM_FEATURE_FLAGS["agent_session_default_account"] is True
    out = PlatformCapabilitiesOut(modules=[], channels=[])
    assert out.features["agent_session_default_account"] is True
