from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.logs import list_audit_logs


@pytest.mark.asyncio
async def test_list_audit_logs_applies_action_filter() -> None:
    fake_db = AsyncMock()
    fake_result = MagicMock()
    fake_result.scalars.return_value.all = MagicMock(return_value=[])
    fake_db.execute = AsyncMock(return_value=fake_result)

    await list_audit_logs(
        db=fake_db,
        _user=None,  # type: ignore[arg-type]
        action="account_bot.test",
        limit=20,
    )

    stmt = fake_db.execute.await_args.args[0]
    sql = str(stmt)
    assert "audit_log.action" in sql
    assert "LIMIT" in sql


@pytest.mark.asyncio
async def test_list_audit_logs_applies_keyword_filter() -> None:
    fake_db = AsyncMock()
    fake_result = MagicMock()
    fake_result.scalars.return_value.all = MagicMock(return_value=[])
    fake_db.execute = AsyncMock(return_value=fake_result)

    await list_audit_logs(
        db=fake_db,
        _user=None,  # type: ignore[arg-type]
        keyword="restart",
        limit=20,
    )

    stmt = fake_db.execute.await_args.args[0]
    sql = str(stmt)
    assert "audit_log.action" in sql
    assert "audit_log.target" in sql
