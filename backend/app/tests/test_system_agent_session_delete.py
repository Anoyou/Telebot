"""单会话删除必须在服务端阻止活动任务和待处理队列丢失。"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import system_agent as api
from app.db.models.system_agent import (
    AGENT_RUN_OPEN_STATUSES,
    AGENT_RUN_TERMINAL_STATUSES,
    CHANNEL_BOT,
    CHANNEL_WEB,
    PENDING_TURN_CANCELLED,
    PENDING_TURN_DISPATCHED,
    PENDING_TURN_DISPATCHING,
    PENDING_TURN_PAUSED,
    PENDING_TURN_PENDING,
    SystemAgentPendingTurn,
    SystemAgentRun,
    SystemAgentSession,
)


@pytest.fixture
async def deletion_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        for model in (SystemAgentSession, SystemAgentPendingTurn, SystemAgentRun):
            await connection.run_sync(model.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add(SystemAgentSession(id="delete-session", web_user_id=7, channel=CHANNEL_WEB))
        await db.commit()
        yield db
    await engine.dispose()


def run_row(run_id: str, status: str) -> SystemAgentRun:
    return SystemAgentRun(
        id=run_id,
        session_id="delete-session",
        web_user_id=7,
        channel=CHANNEL_WEB,
        client_request_id=run_id,
        request_hash="test-request",
        kind="message",
        status=status,
    )


def turn_row(status: str) -> SystemAgentPendingTurn:
    return SystemAgentPendingTurn(
        id="turn-1",
        session_id="delete-session",
        web_user_id=7,
        channel=CHANNEL_WEB,
        kind="message",
        position=1,
        status=status,
        client_request_id="turn-request",
        request_hash="test-request",
        content_enc="not-used-in-this-test",
    )


@pytest.mark.parametrize("status", sorted(AGENT_RUN_OPEN_STATUSES))
async def test_single_session_delete_rejects_any_open_run(deletion_db, status):
    db = deletion_db
    # 旧客户端只检查最近 200 条运行，服务端必须同样拦住更早的活动任务。
    active = run_row("open-run", status)
    active.created_at = datetime(2020, 1, 1, tzinfo=UTC)
    db.add(active)
    db.add_all([run_row(f"later-run-{i}", "succeeded") for i in range(205)])
    await db.commit()
    execute = AsyncMock(wraps=db.execute)
    db.execute = execute

    with pytest.raises(HTTPException) as error:
        await api.delete_session("delete-session", db, SimpleNamespace(id=7))

    assert error.value.status_code == 409
    assert error.value.detail["code"] == "SESSION_BUSY"
    assert await db.get(SystemAgentSession, "delete-session") is not None
    assert await db.get(SystemAgentRun, "open-run") is not None
    statement = execute.call_args_list[0].args[0]
    assert "FOR UPDATE" in str(statement.compile(dialect=postgresql.dialect()))


@pytest.mark.parametrize("status", [PENDING_TURN_PENDING, PENDING_TURN_DISPATCHING, PENDING_TURN_PAUSED])
async def test_single_session_delete_rejects_pending_queue_without_active_run(deletion_db, status):
    db = deletion_db
    db.add(turn_row(status))
    await db.commit()

    with pytest.raises(HTTPException) as error:
        await api.delete_session("delete-session", db, SimpleNamespace(id=7))

    assert error.value.status_code == 409
    assert error.value.detail["code"] == "SESSION_BUSY"
    assert await db.get(SystemAgentSession, "delete-session") is not None
    assert await db.get(SystemAgentPendingTurn, "turn-1") is not None


@pytest.mark.parametrize("status", [None, *sorted(AGENT_RUN_TERMINAL_STATUSES)])
async def test_single_session_delete_allows_empty_or_finished_runs(deletion_db, status):
    db = deletion_db
    if status is not None:
        db.add(run_row("finished-run", status))
        db.add(turn_row(PENDING_TURN_CANCELLED if status == "cancelled" else PENDING_TURN_DISPATCHED))
    await db.commit()

    result = await api.delete_session("delete-session", db, SimpleNamespace(id=7))

    assert result == {"ok": True, "deleted": "delete-session"}
    assert await db.get(SystemAgentSession, "delete-session") is None


@pytest.mark.parametrize("session_id, user_id", [("delete-session", 8), ("bot-session", 7), ("missing", 7)])
async def test_single_session_delete_preserves_owner_and_bot_boundaries(deletion_db, session_id, user_id):
    db = deletion_db
    db.add(SystemAgentSession(id="bot-session", bot_tg_user_id=42, channel=CHANNEL_BOT))
    await db.commit()

    with pytest.raises(HTTPException) as error:
        await api.delete_session(session_id, db, SimpleNamespace(id=user_id))

    assert error.value.status_code == 404
    assert error.value.detail["code"] == "SESSION_NOT_FOUND"
    assert await db.get(SystemAgentSession, "delete-session") is not None
    assert await db.get(SystemAgentSession, "bot-session") is not None
