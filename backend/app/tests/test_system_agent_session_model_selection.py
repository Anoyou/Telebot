"""第 22 轮：Agent 会话记住上次选择的本轮模型。

- 会话新增 model_selection（可空 JSON）；PATCH 会话可设置 / 显式 null 清除。
- 只改模型选择不刷新 updated_at（不改变会话列表排序）。
- 新建会话缺省为 null（客户端按「自动」处理），也可在新建时带上初始选择。
- 库里存了不合法的旧值时读取为 null，不报错。
- /api/system/capabilities 的 features.agent_session_model_selection 供客户端探测。
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import system_agent as api
from app.db.models.system import SystemSetting
from app.db.models.system_agent import SystemAgentMessage, SystemAgentRunEvent, SystemAgentSession
from app.schemas.platform_capabilities import PLATFORM_FEATURE_FLAGS, PlatformCapabilitiesOut
from app.schemas.system_agent import (
    SystemAgentSessionCreate,
    SystemAgentSessionOut,
    SystemAgentSessionUpdate,
)
from app.services.system_agent.service import SystemAgentService

PINNED = {"mode": "pinned", "provider_id": 3, "model": "gpt-5.1"}
USER = SimpleNamespace(id=1)


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


async def _create(factory, payload: SystemAgentSessionCreate | None = None) -> SystemAgentSessionOut:
    async with factory() as db:
        return await api.create_session(payload or SystemAgentSessionCreate(), db, USER)


async def _patch(factory, session_id: str, payload: SystemAgentSessionUpdate) -> SystemAgentSessionOut:
    async with factory() as db:
        return await api.update_session(session_id, payload, db, USER)


async def _get(factory, session_id: str) -> SystemAgentSessionOut:
    async with factory() as db:
        return await api.get_session(session_id, db, USER)


async def _set_updated_at(factory, session_id: str, value: datetime) -> None:
    async with factory() as db:
        await db.execute(
            update(SystemAgentSession)
            .where(SystemAgentSession.id == session_id)
            .values(updated_at=value)
        )
        await db.commit()


def _naive(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=None) if value is not None else None


@pytest.mark.asyncio
async def test_new_session_defaults_to_no_selection(agent_db, real_svc) -> None:
    out = await _create(agent_db)
    assert out.model_selection is None


@pytest.mark.asyncio
async def test_patch_remembers_selection_and_survives_reload(agent_db, real_svc) -> None:
    out = await _create(agent_db)
    patched = await _patch(agent_db, out.id, SystemAgentSessionUpdate(model_selection=PINNED))
    assert patched.model_selection is not None
    assert patched.model_selection.mode == "pinned"
    assert patched.model_selection.provider_id == 3
    assert patched.model_selection.model == "gpt-5.1"
    again = await _get(agent_db, out.id)
    assert again.model_selection is not None and again.model_selection.model == "gpt-5.1"
    async with agent_db() as db:
        row = await db.get(SystemAgentSession, out.id)
        assert row is not None
        # 空字段不落库
        assert row.model_selection == {**PINNED, "mode": "pinned", "execution_backend": "provider"}


@pytest.mark.asyncio
async def test_patch_keeps_execution_backend_and_identity(agent_db, real_svc) -> None:
    out = await _create(agent_db)
    selection = {"mode": "auto", "execution_backend": "direct", "client_identity_profile": "codex_tui"}
    patched = await _patch(agent_db, out.id, SystemAgentSessionUpdate(model_selection=selection))
    assert patched.model_selection is not None
    assert patched.model_selection.execution_backend == "direct"
    assert patched.model_selection.client_identity_profile == "codex_tui"


@pytest.mark.asyncio
async def test_patch_null_clears_selection(agent_db, real_svc) -> None:
    out = await _create(agent_db)
    await _patch(agent_db, out.id, SystemAgentSessionUpdate(model_selection=PINNED))
    cleared = await _patch(agent_db, out.id, SystemAgentSessionUpdate(model_selection=None))
    assert cleared.model_selection is None


@pytest.mark.asyncio
async def test_patch_without_field_leaves_selection(agent_db, real_svc) -> None:
    out = await _create(agent_db)
    await _patch(agent_db, out.id, SystemAgentSessionUpdate(model_selection=PINNED))
    renamed = await _patch(agent_db, out.id, SystemAgentSessionUpdate(title="改名"))
    assert renamed.title == "改名"
    assert renamed.model_selection is not None and renamed.model_selection.model == "gpt-5.1"


@pytest.mark.asyncio
async def test_selection_only_patch_does_not_bump_updated_at(agent_db, real_svc) -> None:
    out = await _create(agent_db)
    old = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    await _set_updated_at(agent_db, out.id, old)
    patched = await _patch(agent_db, out.id, SystemAgentSessionUpdate(model_selection=PINNED))
    assert _naive(patched.updated_at) == _naive(old)
    # 再选一次同一个模型：不写库
    patched = await _patch(agent_db, out.id, SystemAgentSessionUpdate(model_selection=PINNED))
    assert _naive(patched.updated_at) == _naive(old)


@pytest.mark.asyncio
async def test_create_with_initial_selection(agent_db, real_svc) -> None:
    out = await _create(agent_db, SystemAgentSessionCreate(model_selection=PINNED))
    assert out.model_selection is not None and out.model_selection.provider_id == 3


@pytest.mark.asyncio
async def test_remember_on_run_start_via_service(agent_db, real_svc) -> None:
    """发送消息时 API 调 remember_model_selection：之后读取会话就能拿到。"""

    out = await _create(agent_db)
    async with agent_db() as db:
        row = await db.get(SystemAgentSession, out.id)
        assert row is not None
        await real_svc.remember_model_selection(db, row, PINNED)
        await db.commit()
    again = await _get(agent_db, out.id)
    assert again.model_selection is not None and again.model_selection.model == "gpt-5.1"


def test_invalid_stored_selection_reads_as_none() -> None:
    row = SimpleNamespace(
        id="s1",
        web_user_id=1,
        bot_tg_user_id=None,
        account_id=None,
        channel="web",
        title=None,
        origin="interactive",
        status="active",
        memory_summary="",
        memory_state={},
        created_at=None,
        updated_at=None,
        model_selection={"mode": "pinned"},  # 缺 provider_id / model
    )
    assert SystemAgentSessionOut.model_validate(row).model_selection is None
    row.model_selection = "garbage"
    assert SystemAgentSessionOut.model_validate(row).model_selection is None


def test_feature_flag_advertised() -> None:
    assert PLATFORM_FEATURE_FLAGS["agent_session_model_selection"] is True
    out = PlatformCapabilitiesOut(modules=[], channels=[])
    assert out.features["agent_session_model_selection"] is True
