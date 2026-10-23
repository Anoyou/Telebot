from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models.account import Account  # noqa: F401 - registers FK target table metadata
from app.db.models.action_event import (
    ACTION_EVENT_STATUS_COMPENSATED,
    ACTION_EVENT_STATUS_DRY_RUN,
    ACTION_EVENT_STATUS_FAILED,
    ACTION_EVENT_STATUS_OK,
    ActionEvent,
)
from app.db.models.log import EventTrace
from app.db.models.payout_compensation import (
    PAYOUT_COMPENSATION_STATUS_COMPENSATED,
    PAYOUT_COMPENSATION_STATUS_PENDING,
    PayoutCompensation,
)
from app.services import ledger_service, platform_capabilities
from app.services.action_tap import derive_ledger_columns


@pytest.fixture(autouse=True)
def _enable_ledger_actions_for_ledger_write_tests() -> None:
    platform_capabilities._reset_for_tests()
    platform_capabilities._CACHE_READY = True
    platform_capabilities._DESIRED["ledger"] = True
    platform_capabilities._RUNTIME["ledger"] = "ready"
    yield
    platform_capabilities._reset_for_tests()


@pytest.fixture
async def ledger_session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE account (id BIGINT PRIMARY KEY)"))
        await conn.execute(text("INSERT INTO account (id) VALUES (7), (8)"))
        await conn.run_sync(ActionEvent.__table__.create)
        await conn.run_sync(PayoutCompensation.__table__.create)
        await conn.run_sync(EventTrace.__table__.create)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield session_factory
    finally:
        await engine.dispose()


async def _insert_action_event(session_factory, **overrides) -> ActionEvent:
    params_summary = overrides.pop("params_summary")
    payout_key = overrides.pop("payout_key", None)
    if payout_key is None and isinstance(params_summary, dict):
        payout_key = params_summary.get("payout_key")
    action_type = overrides.pop("action_type")
    # 模拟 0062 迁移后的行：写入时按台账口径派生三个提取列（JSON 缺字段则为 NULL）。
    ledger_direction, ledger_amount, ledger_chat_id = derive_ledger_columns(action_type, params_summary)
    row = ActionEvent(
        account_id=overrides.pop("account_id", 7),
        channel=overrides.pop("channel", "interaction_bot"),
        session_key=overrides.pop("session_key", "session_1"),
        plugin_key=overrides.pop("plugin_key", "game"),
        entry_key=overrides.pop("entry_key", "main"),
        action_type=action_type,
        params_summary=params_summary,
        status=overrides.pop("status", ACTION_EVENT_STATUS_OK),
        error_code=overrides.pop("error_code", None),
        error_summary=overrides.pop("error_summary", None),
        payout_key=payout_key,
        direction=ledger_direction,
        amount=ledger_amount,
        chat_id=ledger_chat_id,
        created_at=overrides.pop("created_at"),
    )
    async with session_factory() as db:
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return row


async def _insert_compensation(session_factory, **overrides) -> PayoutCompensation:
    now = overrides.pop("now", datetime.now(UTC))
    row = PayoutCompensation(
        payout_key=overrides.pop("payout_key", "pay_manual"),
        account_id=overrides.pop("account_id", 7),
        trace_id=overrides.pop("trace_id", "evt_manual"),
        plugin_key=overrides.pop("plugin_key", "game"),
        entry_key=overrides.pop("entry_key", "main"),
        origin=overrides.pop("origin", "delivery"),
        chat_id=overrides.pop("chat_id", -100123),
        amount=overrides.pop("amount", 88),
        payload=overrides.pop(
            "payload",
            {"action_type": "payout", "payout_key": "pay_manual", "chat_id": -100123, "amount": 88},
        ),
        status=overrides.pop("status", PAYOUT_COMPENSATION_STATUS_PENDING),
        error_code_first=overrides.pop("error_code_first", "telegram_api_error"),
        error_code_last=overrides.pop("error_code_last", "telegram_api_error"),
        error_last=overrides.pop("error_last", "ConnectionError: network down"),
        ambiguous=overrides.pop("ambiguous", False),
        retry_count=overrides.pop("retry_count", 1),
        next_attempt_at=overrides.pop("next_attempt_at", now - timedelta(seconds=1)),
        created_at=overrides.pop("created_at", now - timedelta(minutes=10)),
        updated_at=overrides.pop("updated_at", now - timedelta(minutes=10)),
        **overrides,
    )
    async with session_factory() as db:
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return row


async def _insert_event_trace(session_factory, **overrides) -> EventTrace:
    row = EventTrace(
        id=overrides.pop("id", 1),
        trace_id=overrides.pop("trace_id", "trace_1"),
        account_id=overrides.pop("account_id", 7),
        source_channel=overrides.pop("source_channel", "interaction_bot"),
        event_type=overrides.pop("event_type", "message"),
        chat_id=overrides.pop("chat_id", -100123),
        sender_user_id=overrides.pop("sender_user_id", 9001),
        sender_name=overrides.pop("sender_name", "历史收款人"),
        status=overrides.pop("status", "ok"),
        payload_snapshot=overrides.pop("payload_snapshot", {"chat": {"title": "历史测试群"}}),
        started_at=overrides.pop("started_at", datetime.now(UTC)),
        **overrides,
    )
    async with session_factory() as db:
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return row


@pytest.mark.asyncio
async def test_ledger_summary_matches_filtered_entries_sum(ledger_session_factory) -> None:
    base = datetime.now(UTC) - timedelta(days=1)
    await _insert_action_event(
        ledger_session_factory,
        action_type="payment_confirmed",
        params_summary={
            "event_type": "payment_confirmed",
            "amount": "100.50",
            "chat_id": -100123,
            "chat_title": "测试群",
            "payer_user_id": 8001,
            "payer_name": "付款人",
            "receiver_user_id": 9001,
            "receiver_name": "收款人甲",
        },
        created_at=base,
    )
    await _insert_action_event(
        ledger_session_factory,
        action_type="payout",
        params_summary={
            "type": "payout",
            "amount": "30.25",
            "chat_id": -100123,
            "chat_title": "测试群",
            "payout_key": "pay_1",
            "receiver_user_id": 9002,
            "receiver_name": "收款人乙",
        },
        created_at=base + timedelta(minutes=5),
    )
    await _insert_action_event(
        ledger_session_factory,
        action_type="payout",
        params_summary={"type": "payout", "amount": "10", "chat_id": -100456, "payout_key": "pay_2"},
        status=ACTION_EVENT_STATUS_FAILED,
        error_code="telegram_api_error",
        created_at=base + timedelta(days=1),
    )
    await _insert_action_event(
        ledger_session_factory,
        action_type="payout",
        params_summary={"type": "payout", "amount": "99", "chat_id": -100456, "payout_key": "pay_dry"},
        status=ACTION_EVENT_STATUS_DRY_RUN,
        created_at=base + timedelta(days=1, minutes=1),
    )
    await _insert_action_event(
        ledger_session_factory,
        account_id=8,
        action_type="payment_confirmed",
        params_summary={"event_type": "payment_confirmed", "amount": "999", "chat_id": -100123},
        created_at=base,
    )
    await _insert_action_event(
        ledger_session_factory,
        action_type="send_message",
        params_summary={"type": "send_message", "amount": "999", "chat_id": -100123},
        created_at=base,
    )

    filters = ledger_service.LedgerFilters(account_id=7, plugin_key="game", limit=None)
    async with ledger_session_factory() as db:
        entries = await ledger_service.list_ledger_entries(db, filters)
        empty_status_entries = await ledger_service.list_ledger_entries(
            db,
            ledger_service.LedgerFilters(account_id=7, plugin_key="game", status="", limit=None),
        )
        failed_entries = await ledger_service.list_ledger_entries(
            db,
            ledger_service.LedgerFilters(
                account_id=7,
                plugin_key="game",
                status=ACTION_EVENT_STATUS_FAILED,
                limit=None,
            ),
        )
        dry_run_entries = await ledger_service.list_ledger_entries(
            db,
            ledger_service.LedgerFilters(
                account_id=7,
                plugin_key="game",
                status=ACTION_EVENT_STATUS_DRY_RUN,
                limit=None,
            ),
        )
        summary = await ledger_service.summarize_ledger(db, filters)

    real_entries = {item.id: item for item in entries}
    income = sum((Decimal(item.amount) for item in real_entries.values() if item.direction == "in"), Decimal("0"))
    payout = sum((Decimal(item.amount) for item in real_entries.values() if item.direction == "out"), Decimal("0"))

    assert len(entries) == 2
    assert [item.id for item in empty_status_entries] == [item.id for item in entries]
    assert {item.status for item in entries} == {ACTION_EVENT_STATUS_OK}
    assert len(failed_entries) == 1
    assert failed_entries[0].error_code == "telegram_api_error"
    assert len(dry_run_entries) == 1
    assert dry_run_entries[0].status == ACTION_EVENT_STATUS_DRY_RUN
    assert summary.count == len(real_entries) == 2
    assert Decimal(summary.income) == income == Decimal("100.50")
    assert Decimal(summary.payout) == payout == Decimal("30.25")
    assert Decimal(summary.net) == income - payout == Decimal("70.25")
    assert {item.key for item in summary.by_day} == {
        base.date().isoformat(),
        (base + timedelta(minutes=5)).date().isoformat(),
    }
    assert {item.key for item in summary.by_chat} == {"-100123"}
    assert summary.by_chat[0].label == "测试群"
    assert {item.user_id for item in summary.by_recipient} == {9001, 9002}
    recipient_amounts = {item.user_id: Decimal(item.received) for item in summary.by_recipient}
    assert recipient_amounts == {9001: Decimal("100.50"), 9002: Decimal("30.25")}
    income_entry = next(item for item in entries if item.direction == "in")
    assert income_entry.chat_title == "测试群"
    assert income_entry.payer_user_id == 8001
    assert income_entry.payer_name == "付款人"
    assert income_entry.receiver_user_id == 9001
    assert income_entry.receiver_name == "收款人甲"


@pytest.mark.asyncio
async def test_ledger_defaults_to_single_success_after_failed_retries(ledger_session_factory) -> None:
    base = datetime.now(UTC) - timedelta(days=1)
    for index in range(6):
        await _insert_action_event(
            ledger_session_factory,
            action_type="payout",
            params_summary={"type": "payout", "amount": "10", "chat_id": -100456, "payout_key": "pay_retry"},
            status=ACTION_EVENT_STATUS_FAILED,
            error_code="reply_anchor_missing",
            created_at=base + timedelta(seconds=index),
        )
    await _insert_action_event(
        ledger_session_factory,
        action_type="payout",
        params_summary={"type": "payout", "amount": "10", "chat_id": -100456, "payout_key": "pay_retry"},
        status=ACTION_EVENT_STATUS_OK,
        created_at=base + timedelta(minutes=1),
    )

    async with ledger_session_factory() as db:
        entries = await ledger_service.list_ledger_entries(
            db,
            ledger_service.LedgerFilters(account_id=7, plugin_key="game", limit=None),
        )
        failed_entries = await ledger_service.list_ledger_entries(
            db,
            ledger_service.LedgerFilters(
                account_id=7,
                plugin_key="game",
                status=ACTION_EVENT_STATUS_FAILED,
                limit=None,
            ),
        )
        summary = await ledger_service.summarize_ledger(
            db,
            ledger_service.LedgerFilters(account_id=7, plugin_key="game", limit=None),
        )

    assert len(entries) == 1
    assert entries[0].status == ACTION_EVENT_STATUS_OK
    assert entries[0].payout_key == "pay_retry"
    assert len(failed_entries) == 6
    assert {item.error_code for item in failed_entries} == {"reply_anchor_missing"}
    assert summary.count == 1
    assert Decimal(summary.payout) == Decimal("10")
    assert Decimal(summary.net) == Decimal("-10")


@pytest.mark.asyncio
async def test_ledger_summary_includes_compensated_but_rejects_failed_status_filter(ledger_session_factory) -> None:
    base = datetime.now(UTC) - timedelta(days=1)
    await _insert_action_event(
        ledger_session_factory,
        action_type="payout",
        params_summary={"type": "payout", "amount": "12", "chat_id": -100456, "payout_key": "pay_manual"},
        status=ACTION_EVENT_STATUS_COMPENSATED,
        created_at=base,
    )
    await _insert_action_event(
        ledger_session_factory,
        action_type="payout",
        params_summary={"type": "payout", "amount": "7", "chat_id": -100456, "payout_key": "pay_failed"},
        status=ACTION_EVENT_STATUS_FAILED,
        created_at=base + timedelta(minutes=1),
    )

    async with ledger_session_factory() as db:
        summary = await ledger_service.summarize_ledger(db, ledger_service.LedgerFilters(account_id=7, limit=None))
        failed_summary = await ledger_service.summarize_ledger(
            db,
            ledger_service.LedgerFilters(account_id=7, status=ACTION_EVENT_STATUS_FAILED, limit=None),
        )

    assert summary.count == 1
    assert Decimal(summary.payout) == Decimal("12")
    assert Decimal(summary.net) == Decimal("-12")
    assert failed_summary.count == 0
    assert Decimal(failed_summary.payout) == Decimal("0")


@pytest.mark.asyncio
async def test_ledger_summary_applies_default_time_window(ledger_session_factory) -> None:
    now = datetime.now(UTC)
    await _insert_action_event(
        ledger_session_factory,
        action_type="payment_confirmed",
        params_summary={"event_type": "payment_confirmed", "amount": "100", "chat_id": -100123},
        created_at=now - timedelta(days=ledger_service.DEFAULT_LEDGER_SUMMARY_WINDOW_DAYS + 1),
    )
    await _insert_action_event(
        ledger_session_factory,
        action_type="payment_confirmed",
        params_summary={"event_type": "payment_confirmed", "amount": "9", "chat_id": -100123},
        created_at=now - timedelta(days=1),
    )

    async with ledger_session_factory() as db:
        summary = await ledger_service.summarize_ledger(db, ledger_service.LedgerFilters(account_id=7, limit=None))

    assert summary.count == 1
    assert Decimal(summary.income) == Decimal("9")


@pytest.mark.asyncio
async def test_ledger_list_scans_past_sparse_non_financial_rows(ledger_session_factory) -> None:
    now = datetime.now(UTC)
    async with ledger_session_factory() as db:
        db.add_all(
            [
                ActionEvent(
                    account_id=7,
                    channel="interaction_bot",
                    action_type="send_message",
                    params_summary={"type": "send_message", "text": f"noise-{index}"},
                    status=ACTION_EVENT_STATUS_OK,
                    created_at=now - timedelta(seconds=index),
                )
                for index in range(1201)
            ]
        )
        db.add(
            ActionEvent(
                account_id=7,
                channel="external_payment_notice",
                action_type="payment_confirmed",
                params_summary={"event_type": "payment_confirmed", "amount": "16", "chat_id": -100123},
                status=ACTION_EVENT_STATUS_OK,
                direction="in",
                amount=Decimal("16"),
                chat_id=-100123,
                created_at=now - timedelta(seconds=1300),
            )
        )
        await db.commit()

        entries = await ledger_service.list_ledger_entries(
            db,
            ledger_service.LedgerFilters(account_id=7, limit=1),
        )

    assert len(entries) == 1
    assert entries[0].direction == ledger_service.LEDGER_DIRECTION_IN
    assert entries[0].amount == "16"


@pytest.mark.asyncio
async def test_ledger_hydrates_historical_chat_and_recipient_labels(ledger_session_factory) -> None:
    now = datetime.now(UTC)
    await _insert_event_trace(ledger_session_factory, started_at=now)
    await _insert_action_event(
        ledger_session_factory,
        action_type="payout",
        params_summary={
            "type": "payout",
            "amount": "66",
            "chat_id": -100123,
            "reply_to_user_id": 9001,
            "payout_key": "pay_historical",
        },
        created_at=now,
    )

    async with ledger_session_factory() as db:
        entries = await ledger_service.list_ledger_entries(db)
        summary = await ledger_service.summarize_ledger(db)

    assert entries[0].chat_title == "历史测试群"
    assert entries[0].receiver_user_id == 9001
    assert entries[0].receiver_name == "历史收款人"
    assert summary.by_chat[0].label == "历史测试群"
    assert summary.by_recipient[0].label == "历史收款人"


@pytest.mark.asyncio
async def test_manual_paid_compensation_writes_audit_and_closes_row(
    ledger_session_factory,
    monkeypatch,
) -> None:
    row = await _insert_compensation(ledger_session_factory)
    audit_write = AsyncMock()
    monkeypatch.setattr(ledger_service.audit, "write", audit_write)

    async with ledger_session_factory() as db:
        paid = await ledger_service.mark_compensation_manual_paid(
            db,
            row.id,
            user_id=42,
            note="线下已补",
        )
        await db.commit()

    assert paid.status == PAYOUT_COMPENSATION_STATUS_COMPENSATED
    assert paid.sent_at is not None
    audit_write.assert_awaited_once()
    assert audit_write.await_args.args[1] == 42
    assert audit_write.await_args.args[2] == "ledger.compensation.manual_paid"
    assert audit_write.await_args.kwargs["target"] == f"payout_compensation:{row.id}"
    assert audit_write.await_args.kwargs["detail"] == {
        "previous_status": PAYOUT_COMPENSATION_STATUS_PENDING,
        "status": PAYOUT_COMPENSATION_STATUS_COMPENSATED,
        "payout_key": "pay_manual",
        "account_id": 7,
        "chat_id": -100123,
        "amount": "88",
        "plugin_key": "game",
        "entry_key": "main",
        "note": "线下已补",
    }

    async with ledger_session_factory() as db:
        current = await db.get(PayoutCompensation, row.id)
        assert current is not None
        assert current.status == PAYOUT_COMPENSATION_STATUS_COMPENSATED
        assert current.sent_at is not None
        open_rows = await ledger_service.list_compensations(db)
        all_rows = (await db.execute(select(PayoutCompensation))).scalars().all()

    assert open_rows == []
    assert len(all_rows) == 1

    async with ledger_session_factory() as db:
        events = (await db.execute(select(ActionEvent))).scalars().all()
    assert len(events) == 1
    assert events[0].status == ACTION_EVENT_STATUS_COMPENSATED
    assert events[0].action_type == "payout"
    assert events[0].params_summary.get("payout_key") == "pay_manual"


@pytest.mark.asyncio
async def test_manual_paid_compensation_fails_closed_without_mutating_row(
    ledger_session_factory,
    monkeypatch,
) -> None:
    from fastapi import HTTPException

    row = await _insert_compensation(ledger_session_factory, payout_key="pay_manual_fuse")
    audit_write = AsyncMock()
    monkeypatch.setattr(ledger_service.audit, "write", audit_write)
    platform_capabilities._RUNTIME["ledger"] = "quiescing"

    async with ledger_session_factory() as db:
        with pytest.raises(HTTPException) as exc_info:
            await ledger_service.mark_compensation_manual_paid(
                db,
                row.id,
                user_id=42,
                note="线下已补",
            )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == {
        "code": "ledger_actions_failed_closed",
        "message": "资金动作被保险丝拒绝",
        "audit_status": "FAILED_CLOSED",
        "deny_reasons": ["ledger_runtime_quiescing"],
    }
    audit_write.assert_not_awaited()
    async with ledger_session_factory() as db:
        current = await db.get(PayoutCompensation, row.id)
        assert current is not None
        assert current.status == PAYOUT_COMPENSATION_STATUS_PENDING
        events = list((await db.execute(select(ActionEvent))).scalars().all())
    assert events == []


@pytest.mark.asyncio
async def test_manual_paid_writes_compensated_action_event_once(
    ledger_session_factory,
    monkeypatch,
) -> None:
    row = await _insert_compensation(ledger_session_factory, payout_key="pay_manual_ledger")
    monkeypatch.setattr(ledger_service.audit, "write", AsyncMock())

    async with ledger_session_factory() as db:
        await ledger_service.mark_compensation_manual_paid(db, row.id, user_id=1, note="ok")
        await db.commit()

    from fastapi import HTTPException

    async with ledger_session_factory() as db:
        with pytest.raises(HTTPException) as exc_info:
            await ledger_service.mark_compensation_manual_paid(db, row.id, user_id=2)
        assert exc_info.value.status_code == 409

    async with ledger_session_factory() as db:
        events = list((await db.execute(select(ActionEvent))).scalars().all())
    assert len(events) == 1
    assert events[0].status == ACTION_EVENT_STATUS_COMPENSATED
    assert events[0].params_summary.get("payout_key") == "pay_manual_ledger"

    async with ledger_session_factory() as db:
        summary = await ledger_service.summarize_ledger(db)
    assert Decimal(summary.payout) == Decimal("88")
    assert Decimal(summary.net) == Decimal("-88")


@pytest.mark.asyncio
async def test_manual_paid_compensation_rejects_second_conditional_update(
    ledger_session_factory,
    monkeypatch,
) -> None:
    row = await _insert_compensation(ledger_session_factory, payout_key="pay_manual_race")
    monkeypatch.setattr(ledger_service.audit, "write", AsyncMock())

    async with ledger_session_factory() as first_db:
        await ledger_service.mark_compensation_manual_paid(first_db, row.id, user_id=1)
        await first_db.commit()

    async with ledger_session_factory() as second_db:
        with pytest.raises(Exception) as exc_info:
            await ledger_service.mark_compensation_manual_paid(second_db, row.id, user_id=2)

    assert getattr(exc_info.value, "status_code", None) == 409
    assert "不能重复核销" in str(getattr(exc_info.value, "detail", ""))


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_ledger_module_disabled_blocks_api_but_action_events_still_write(
    ledger_session_factory,
    monkeypatch,
) -> None:
    """台账模块关闭只冻查询面；ActionEvent 主账写入不受影响。"""

    from types import SimpleNamespace

    from fastapi import HTTPException

    from app.api import ledger as ledger_api
    from app.services import platform_capabilities as platform_caps

    platform_caps._reset_for_tests()
    platform_caps._DESIRED["ledger"] = False
    platform_caps._CACHE_READY = True
    platform_caps._GENERATIONS["ledger"] = 1

    async def _disabled(db, module_key, **kwargs):  # noqa: ANN001
        raise HTTPException(
            status_code=409,
            detail={"code": "PLATFORM_MODULE_DISABLED", "module": module_key},
        )

    monkeypatch.setattr(platform_caps, "require_module_enabled", _disabled)

    async with ledger_session_factory() as db:
        with pytest.raises(HTTPException) as exc:
            await ledger_api.list_ledger_entries(db, SimpleNamespace(id=1))  # type: ignore[arg-type]
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "PLATFORM_MODULE_DISABLED"

    # 主账写入路径（service / ORM）不经 API 门禁
    now = datetime.now(UTC)
    await _insert_action_event(
        ledger_session_factory,
        action_type="payout",
        params_summary={"type": "payout", "amount": "12", "chat_id": -1001},
        created_at=now,
    )
    async with ledger_session_factory() as db:
        rows = (await db.execute(select(ActionEvent))).scalars().all()
    assert len(rows) == 1
    assert rows[0].action_type == "payout"
    platform_caps._reset_for_tests()


@pytest.mark.asyncio
async def test_reset_ledger_data_removes_financial_and_operational_rows_only(
    ledger_session_factory,
    monkeypatch,
) -> None:
    now = datetime.now(UTC)
    for action_type in ("payment_confirmed", "payout", "start_session", "send_message"):
        await _insert_action_event(
            ledger_session_factory,
            action_type=action_type,
            params_summary={"type": action_type, "amount": "10", "chat_id": -100123},
            created_at=now,
        )
    await _insert_compensation(ledger_session_factory, payout_key="pay_reset")
    audit_write = AsyncMock()
    monkeypatch.setattr(ledger_service.audit, "write", audit_write)

    async with ledger_session_factory() as db:
        result = await ledger_service.reset_ledger_data(db, user_id=42)
        await db.commit()

    assert result.deleted_action_events == 3
    assert result.deleted_compensations == 1
    audit_write.assert_awaited_once()
    assert audit_write.await_args.args[2] == "ledger.reset"
    async with ledger_session_factory() as db:
        action_rows = (await db.execute(select(ActionEvent))).scalars().all()
        compensation_rows = (await db.execute(select(PayoutCompensation))).scalars().all()
    assert [row.action_type for row in action_rows] == ["send_message"]
    assert compensation_rows == []


def _legacy_list_entry_ids(rows: list[ActionEvent], filters: ledger_service.LedgerFilters) -> list[int]:
    """旧 Python 过滤口径的对照实现：全量行 + `_entry_from_action_event`/`_entry_matches`。"""

    def _naive(value: datetime) -> datetime:
        # SQLite 驱动返回 naive datetime；过滤值统一按 UTC 时钟转 naive 再比较。
        return value.replace(tzinfo=None) if value.tzinfo is not None else value

    out: list[int] = []
    for row in rows:
        row_created_at = _naive(row.created_at)
        if filters.since is not None and row_created_at < _naive(filters.since):
            continue
        if filters.until is not None and row_created_at > _naive(filters.until):
            continue
        if filters.account_id is not None and int(row.account_id) != int(filters.account_id):
            continue
        if filters.plugin_key and row.plugin_key != filters.plugin_key:
            continue
        if filters.status:
            if row.status != filters.status:
                continue
        elif filters.statuses and row.status not in filters.statuses:
            continue
        entry = ledger_service._entry_from_action_event(row)
        if entry is None or not ledger_service._entry_matches(entry, filters):
            continue
        out.append(entry.id)
        if filters.limit is not None and len(out) >= filters.limit:
            break
    return out


@pytest.mark.asyncio
async def test_ledger_sql_filters_match_legacy_python_behavior(ledger_session_factory) -> None:
    """金额/群/方向过滤下推 SQL 后，结果与旧 Python 全量过滤口径逐条一致。"""

    base = datetime.now(UTC) - timedelta(days=1)
    # 覆盖：常规 in/out、result.amount 嵌套金额、source.chat_id 嵌套群、负数金额（按绝对值过滤）、
    # JSON 缺 amount / 缺 chat_id、非资金动作、不可解析金额、失败状态、其他账号。
    await _insert_action_event(
        ledger_session_factory,
        action_type="payment_confirmed",
        params_summary={"event_type": "payment_confirmed", "amount": "100.50", "chat_id": -100123},
        created_at=base,
    )
    await _insert_action_event(
        ledger_session_factory,
        action_type="payout",
        params_summary={"type": "payout", "amount": "30.25", "chat_id": -100123, "payout_key": "pay_a"},
        created_at=base + timedelta(minutes=1),
    )
    await _insert_action_event(
        ledger_session_factory,
        action_type="payout",
        params_summary={"type": "payout", "amount": "10", "chat_id": -100456, "payout_key": "pay_b"},
        created_at=base + timedelta(minutes=2),
    )
    await _insert_action_event(
        ledger_session_factory,
        action_type="payout",
        params_summary={"type": "payout", "chat_id": -100456, "result": {"amount": "5"}, "payout_key": "pay_c"},
        created_at=base + timedelta(minutes=3),
    )
    await _insert_action_event(
        ledger_session_factory,
        action_type="payout",
        params_summary={"type": "payout", "amount": "7", "source": {"chat_id": -100789}, "payout_key": "pay_d"},
        created_at=base + timedelta(minutes=4),
    )
    await _insert_action_event(
        ledger_session_factory,
        action_type="payout",
        params_summary={"type": "payout", "amount": "-8", "chat_id": -100456, "payout_key": "pay_e"},
        created_at=base + timedelta(minutes=5),
    )
    await _insert_action_event(
        ledger_session_factory,
        action_type="payout",
        params_summary={"type": "payout", "chat_id": -100456, "payout_key": "pay_no_amount"},
        created_at=base + timedelta(minutes=6),
    )
    await _insert_action_event(
        ledger_session_factory,
        action_type="payment_confirmed",
        params_summary={"event_type": "payment_confirmed", "amount": "40"},
        created_at=base + timedelta(minutes=7),
    )
    await _insert_action_event(
        ledger_session_factory,
        action_type="send_message",
        params_summary={"type": "send_message", "amount": "999", "chat_id": -100123},
        created_at=base + timedelta(minutes=8),
    )
    await _insert_action_event(
        ledger_session_factory,
        action_type="payout",
        params_summary={"type": "payout", "amount": "abc", "chat_id": -100456, "payout_key": "pay_bad"},
        created_at=base + timedelta(minutes=9),
    )
    await _insert_action_event(
        ledger_session_factory,
        action_type="payout",
        params_summary={"type": "payout", "amount": "11", "chat_id": -100456, "payout_key": "pay_failed"},
        status=ACTION_EVENT_STATUS_FAILED,
        error_code="telegram_api_error",
        created_at=base + timedelta(minutes=10),
    )
    await _insert_action_event(
        ledger_session_factory,
        account_id=8,
        action_type="payment_confirmed",
        params_summary={"event_type": "payment_confirmed", "amount": "999", "chat_id": -100123},
        created_at=base + timedelta(minutes=11),
    )

    filter_cases: list[ledger_service.LedgerFilters] = [
        ledger_service.LedgerFilters(limit=None),
        ledger_service.LedgerFilters(direction="in", limit=None),
        ledger_service.LedgerFilters(direction="out", limit=None),
        ledger_service.LedgerFilters(chat_id=-100123, limit=None),
        ledger_service.LedgerFilters(chat_id=-100456, limit=None),
        ledger_service.LedgerFilters(chat_id=-100789, limit=None),
        ledger_service.LedgerFilters(amount=Decimal("30.25"), limit=None),
        ledger_service.LedgerFilters(amount=Decimal("8"), limit=None),
        ledger_service.LedgerFilters(amount=Decimal("12.50"), limit=None),
        ledger_service.LedgerFilters(amount_min=Decimal("7"), amount_max=Decimal("30.25"), limit=None),
        ledger_service.LedgerFilters(direction="out", amount_min=Decimal("10"), limit=None),
        ledger_service.LedgerFilters(chat_id=-100456, direction="out", amount_max=Decimal("10"), limit=None),
        ledger_service.LedgerFilters(account_id=7, plugin_key="game", limit=2),
        ledger_service.LedgerFilters(direction="out", limit=1),
        ledger_service.LedgerFilters(
            since=base + timedelta(minutes=3),
            until=base + timedelta(minutes=7),
            limit=None,
        ),
    ]

    async with ledger_session_factory() as db:
        all_rows = list(
            (
                await db.execute(
                    select(ActionEvent).order_by(ActionEvent.created_at.desc(), ActionEvent.id.desc())
                )
            )
            .scalars()
            .all()
        )
        for filters in filter_cases:
            # 与生产路径一致地补默认状态集合，供对照实现使用。
            expected_filters = ledger_service._normalize_filters(replace(filters))
            if expected_filters.status is None and not expected_filters.statuses:
                expected_filters.statuses = ledger_service.LEDGER_SUMMARY_STATUSES
            expected = _legacy_list_entry_ids(all_rows, expected_filters)
            entries = await ledger_service.list_ledger_entries(db, replace(filters))
            assert [item.id for item in entries] == expected, (
                f"filters={filters!r} expected={expected} got={[item.id for item in entries]}"
            )


@pytest.mark.asyncio
async def test_ledger_null_columns_keep_legacy_missing_field_semantics(ledger_session_factory) -> None:
    """JSON 缺字段（列为 NULL）的行沿用旧口径：不产生台账条目；缺 chat 仅在群过滤时排除。"""

    base = datetime.now(UTC) - timedelta(days=1)
    complete = await _insert_action_event(
        ledger_session_factory,
        action_type="payout",
        params_summary={"type": "payout", "amount": "20", "chat_id": -100123, "payout_key": "pay_ok"},
        created_at=base,
    )
    missing_amount = await _insert_action_event(
        ledger_session_factory,
        action_type="payout",
        params_summary={"type": "payout", "chat_id": -100123, "payout_key": "pay_no_amount"},
        created_at=base + timedelta(minutes=1),
    )
    missing_chat = await _insert_action_event(
        ledger_session_factory,
        action_type="payout",
        params_summary={"type": "payout", "amount": "15", "payout_key": "pay_no_chat"},
        created_at=base + timedelta(minutes=2),
    )

    # 列派生口径：JSON 缺字段 → NULL（等价旧行为里解析失败跳过该字段）。
    assert missing_amount.amount is None and missing_amount.direction == "out"
    assert missing_chat.chat_id is None and missing_chat.amount == Decimal("15")

    async with ledger_session_factory() as db:
        entries = await ledger_service.list_ledger_entries(
            db, ledger_service.LedgerFilters(account_id=7, limit=None)
        )
        chat_entries = await ledger_service.list_ledger_entries(
            db, ledger_service.LedgerFilters(account_id=7, chat_id=-100123, limit=None)
        )
        summary = await ledger_service.summarize_ledger(db, ledger_service.LedgerFilters(account_id=7))
        chat_summary = await ledger_service.summarize_ledger(
            db, ledger_service.LedgerFilters(account_id=7, chat_id=-100123)
        )

    # 旧行为：缺 amount 的行无台账条目；缺 chat_id 的行不受影响、但群过滤时被排除。
    assert [item.id for item in entries] == [missing_chat.id, complete.id]
    assert [item.id for item in chat_entries] == [complete.id]
    assert summary.count == 2
    assert Decimal(summary.payout) == Decimal("35")
    assert chat_summary.count == 1
    assert Decimal(chat_summary.payout) == Decimal("20")

    # 未回填旧行（JSON 完整但列为 NULL，例如跳过 0062 的库）不参与台账：
    # 这是列提升后明确的新边界，回填迁移保证存量行不会停留在此状态。
    async with ledger_session_factory() as db:
        db.add(
            ActionEvent(
                account_id=7,
                channel="interaction_bot",
                action_type="payout",
                params_summary={"type": "payout", "amount": "50", "chat_id": -100123, "payout_key": "pay_stale"},
                status=ACTION_EVENT_STATUS_OK,
                created_at=base + timedelta(minutes=3),
            )
        )
        await db.commit()
        entries_after = await ledger_service.list_ledger_entries(
            db, ledger_service.LedgerFilters(account_id=7, limit=None)
        )
        summary_after = await ledger_service.summarize_ledger(
            db, ledger_service.LedgerFilters(account_id=7)
        )
    assert [item.id for item in entries_after] == [item.id for item in entries]
    assert summary_after.count == summary.count
    assert Decimal(summary_after.payout) == Decimal("35")


@pytest.mark.asyncio
async def test_ledger_summary_sql_totals_match_filtered_entries(ledger_session_factory) -> None:
    """SQL SUM/COUNT 总额与明细条目聚合逐项一致（含金额区间、方向、群过滤组合）。"""

    base = datetime.now(UTC) - timedelta(days=1)
    amounts_by_direction = {
        "in": ["100.50", "9", "40"],
        "out": ["30.25", "10", "5"],
    }
    index = 0
    for direction, amounts in amounts_by_direction.items():
        for amount in amounts:
            chat_id = -100123 if index % 2 == 0 else -100456
            await _insert_action_event(
                ledger_session_factory,
                action_type="payment_confirmed" if direction == "in" else "payout",
                params_summary=(
                    {"event_type": "payment_confirmed", "amount": amount, "chat_id": chat_id}
                    if direction == "in"
                    else {"type": "payout", "amount": amount, "chat_id": chat_id, "payout_key": f"pay_{index}"}
                ),
                created_at=base + timedelta(minutes=index),
            )
            index += 1

    filter_cases: list[ledger_service.LedgerFilters] = [
        ledger_service.LedgerFilters(account_id=7, limit=None),
        ledger_service.LedgerFilters(account_id=7, direction="out", limit=None),
        ledger_service.LedgerFilters(account_id=7, chat_id=-100456, limit=None),
        ledger_service.LedgerFilters(account_id=7, amount_min=Decimal("10"), amount_max=Decimal("40"), limit=None),
    ]

    async with ledger_session_factory() as db:
        for filters in filter_cases:
            entries = await ledger_service.list_ledger_entries(db, replace(filters))
            summary = await ledger_service.summarize_ledger(db, replace(filters))
            income = sum(
                (Decimal(item.amount) for item in entries if item.direction == "in"), Decimal("0")
            )
            payout = sum(
                (Decimal(item.amount) for item in entries if item.direction == "out"), Decimal("0")
            )
            assert summary.count == len(entries), f"filters={filters!r}"
            assert Decimal(summary.income) == income, f"filters={filters!r}"
            assert Decimal(summary.payout) == payout, f"filters={filters!r}"
            assert Decimal(summary.net) == income - payout, f"filters={filters!r}"
