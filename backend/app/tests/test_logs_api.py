from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.api.logs import (
    EventTraceSummary,
    list_command_traces,
    list_event_actions,
    list_event_traces,
    trace_overview,
)


def _trace_row(**overrides):
    data = {
        "id": 1,
        "trace_id": "evt_test",
        "account_id": 1,
        "source_channel": "interaction_bot",
        "event_type": "message",
        "chat_id": None,
        "message_id": None,
        "update_id": 11,
        "callback_query_id": None,
        "sender_user_id": 1001,
        "sender_name": "Alice",
        "text_preview": None,
        "status": "ok",
        "started_at": datetime(2026, 6, 29, tzinfo=UTC),
        "ended_at": None,
        "duration_ms": None,
        "native_raw_meta": None,
        "raw_summary": None,
        "payload_snapshot": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def test_trace_summary_projects_inline_query_fields() -> None:
    row = _trace_row(
        event_type="inline_query",
        text_preview="fallback query",
        raw_summary={"query": "raw query"},
        payload_snapshot={"inline_query": {"query": "payload query"}},
    )

    summary = EventTraceSummary.from_row(row)

    assert summary.inline_query == "payload query"
    assert summary.chosen_inline_result_id is None
    assert summary.chosen_inline_query is None


def test_trace_summary_projects_chosen_inline_result_fields() -> None:
    row = _trace_row(
        event_type="chosen_inline_result",
        text_preview="fallback chosen query",
        raw_summary={"query": "raw chosen query"},
        payload_snapshot={"chosen_inline_result": {"result_id": "result-1", "query": "payload chosen query"}},
    )

    summary = EventTraceSummary.from_row(row)

    assert summary.inline_query is None
    assert summary.chosen_inline_result_id == "result-1"
    assert summary.chosen_inline_query == "payload chosen query"


class _EmptyScalarResult:
    def scalar_one(self):
        return 0

    def scalars(self):
        return self

    def all(self):
        return []


class _CaptureDB:
    def __init__(self) -> None:
        self.statements: list[str] = []

    async def execute(self, stmt):
        self.statements.append(str(stmt))
        return _EmptyScalarResult()


@pytest.mark.asyncio
async def test_list_event_traces_filters_by_trace_and_reason_code() -> None:
    db = _CaptureDB()

    rows = await list_event_traces(
        db=db,
        _user=object(),
        trace_id="evt_test",
        reason_code="send_channel_deprecated",
        limit=100,
    )

    assert rows == []
    sql = "\n".join(db.statements)
    assert "event_trace.trace_id" in sql
    assert "event_span.reason_code" in sql
    assert "event_action.error_code" in sql


@pytest.mark.asyncio
async def test_list_event_actions_filters_by_reason_or_error_code() -> None:
    db = _CaptureDB()

    rows = await list_event_actions(
        db=db,
        _user=object(),
        account_id=1,
        trace_id="evt_test",
        reason_code="telegram_api_error",
        since=datetime(2026, 6, 29, tzinfo=UTC),
        until=datetime(2026, 6, 30, tzinfo=UTC),
        limit=100,
    )

    assert rows == []
    sql = "\n".join(db.statements)
    assert "event_action.trace_id" in sql
    assert "event_action.error_code" in sql
    assert "event_action.created_at >=" in sql
    assert "event_action.created_at <=" in sql
    assert "event_trace.account_id" in sql


@pytest.mark.asyncio
async def test_trace_overview_filters_failed_actions_by_account_id() -> None:
    db = _CaptureDB()

    overview = await trace_overview(db=db, _user=object(), account_id=1)

    assert overview.recent_failed_actions == []
    action_sql = "\n".join(stmt for stmt in db.statements if "event_action" in stmt)
    assert "event_action.status" in action_sql
    assert "event_trace.account_id" in action_sql


@pytest.mark.asyncio
async def test_list_command_traces_filters_by_time_and_reason_code() -> None:
    db = _CaptureDB()

    rows = await list_command_traces(
        db=db,
        _user=object(),
        since=datetime(2026, 6, 29, tzinfo=UTC),
        until=datetime(2026, 6, 30, tzinfo=UTC),
        reason_code="command_matched",
        limit=100,
    )

    assert rows == []
    sql = "\n".join(db.statements)
    assert "event_trace.started_at >=" in sql
    assert "event_trace.started_at <=" in sql
    assert "event_span.reason_code" in sql
    assert "event_action.error_code" in sql
