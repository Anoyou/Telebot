"""Transactional push outbox, also installed in worker processes.

Only identifiers enter this queue. Row insertion shares the business transaction,
so rollback cannot leak an approval or account alert to a device.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import event, func, inspect, select
from sqlalchemy.engine import Connection

OUTBOX_PREFIX = "apns:outbox:"


def enqueue(connection: Connection, *, kind: str, event_id: str | None = None, user_id: int | None,
            account_id: int | None = None, session_id: str | None = None) -> None:
    from .models.system import SystemSetting

    # Partial SQLite schemas are used by isolated service tests/local tools.
    if connection.dialect.name == "sqlite" and not inspect(connection).has_table("system_setting"):
        return
    config = connection.execute(
        select(SystemSetting.value).where(SystemSetting.key == "apns:config")
    ).scalar_one_or_none()
    if not isinstance(config, dict) or not config.get("enabled"):
        return
    queued = select(SystemSetting.key).where(SystemSetting.key.like(f"{OUTBOX_PREFIX}%"))
    if connection.scalar(select(func.count()).select_from(queued.subquery())) >= 1000:
        return
    connection.execute(SystemSetting.__table__.insert().values(
        key=OUTBOX_PREFIX + uuid.uuid4().hex,
        value={"kind": kind, "event_id": event_id or uuid.uuid4().hex, "user_id": user_id,
               "account_id": account_id, "created_at": datetime.now(UTC).isoformat(),
               "session_id": session_id,
               "attempts": 0, "next_attempt_at": 0},
    ))


def _action_created(mapper, connection, target) -> None:
    if target.status == "pending" and target.actor_user_id is not None:
        enqueue(connection, kind="assistant_approval", event_id=target.id,
                user_id=target.actor_user_id, account_id=target.account_id)


def _run_changed(mapper, connection, target) -> None:
    if (inspect(target).attrs.status.history.has_changes()
            and target.status in {"succeeded", "failed", "cancelled"}
            and target.web_user_id is not None):
        enqueue(connection, kind="assistant_result", event_id=target.id,
                user_id=target.web_user_id, session_id=target.session_id)


def _account_changed(mapper, connection, target) -> None:
    if (inspect(target).attrs.status.history.has_changes()
            and target.status in {"dead", "login_required", "floodwait"}):
        enqueue(connection, kind="account_alert", event_id=uuid.uuid4().hex,
                user_id=None, account_id=target.id)


def install_listeners() -> None:
    from .models.account import Account
    from .models.system_agent import SystemAgentAction, SystemAgentRun

    for model, name, listener in (
        (SystemAgentAction, "after_insert", _action_created),
        (SystemAgentRun, "after_update", _run_changed),
        (Account, "after_update", _account_changed),
    ):
        if not event.contains(model, name, listener):
            event.listen(model, name, listener)
