from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def _load_migration_module():
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic/versions/0062_action_event_ledger_columns.py"
    )
    spec = importlib.util.spec_from_file_location("telepilot_migration_0062", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_action_event_table(conn) -> None:
    conn.execute(
        sa.text(
            "CREATE TABLE action_event ("
            "id INTEGER PRIMARY KEY, "
            "action_type VARCHAR(80) NOT NULL, "
            "params_summary TEXT NOT NULL, "
            "status VARCHAR(32) NOT NULL DEFAULT 'OK')"
        )
    )


def _insert(conn, row_id: int, action_type: str, summary: dict) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO action_event (id, action_type, params_summary) "
            "VALUES (:id, :action_type, :summary)"
        ),
        {"id": row_id, "action_type": action_type, "summary": json.dumps(summary, ensure_ascii=False)},
    )


def _fetch_columns(conn) -> dict[int, tuple]:
    rows = conn.execute(
        sa.text("SELECT id, amount, chat_id, direction FROM action_event ORDER BY id")
    ).all()
    return {int(row[0]): (row[1], row[2], row[3]) for row in rows}


def test_migration_0062_backfills_ledger_columns_and_roundtrips(monkeypatch) -> None:
    migration = _load_migration_module()
    assert migration.revision == "0062"
    assert migration.down_revision == "0061"
    assert migration.branch_labels is None
    assert migration.depends_on is None
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as conn:
            _create_action_event_table(conn)
            # 覆盖台账派生口径的各分支（金额/群/方向的多路径回退与缺失）。
            _insert(conn, 1, "payout", {"type": "payout", "amount": "12.50", "chat_id": -100123})
            _insert(conn, 2, "payment_confirmed", {"event_type": "payment_confirmed", "amount": 100, "chat_id": -100123})
            _insert(conn, 3, "payout", {"type": "payout", "chat_id": -100456, "result": {"amount": "5"}})
            _insert(conn, 4, "send_message", {"type": "send_message", "amount": "999", "chat_id": -100123})
            _insert(conn, 5, "reward_payout", {"type": "reward_payout", "amount": "-8", "chat_id": -100456})
            _insert(conn, 6, "payout", {"type": "payout", "chat_id": -100456})  # 缺 amount
            _insert(conn, 7, "payout", {"type": "payout", "amount": "abc", "chat_id": -100456})  # 不可解析金额
            _insert(conn, 8, "Payout", {"action_type": "Payout", "amount": "7"})  # 大小写不敏感 + 缺 chat
            _insert(conn, 9, "payment_confirmed", {"event_type": "payment_confirmed", "amount": "9", "source": {"chat_id": -100789}})
            _insert(conn, 10, "start_session", {"type": "start_session", "chat_id": -100123})

            monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(conn)))
            migration.upgrade()

            columns = {col["name"] for col in sa.inspect(conn).get_columns("action_event")}
            assert {"amount", "chat_id", "direction"} <= columns
            data = _fetch_columns(conn)
            # PG 分支（->> + ::numeric/::bigint）与 SQLite 分支（json_extract + CAST）同口径。
            assert data[1] == (12.5, -100123, "out")
            assert data[2] == (100, -100123, "in")
            assert data[3] == (5, -100456, "out")
            # 非资金动作：direction 为 NULL（amount/chat_id 仍按 JSON 提取，读取侧要求
            # direction 与 amount 同时非空才计台账，口径不变）。
            assert data[4] == (999, -100123, None)
            # *_payout 后缀识别为出账；负金额按原值回填（过滤/汇总时取 abs）。
            assert data[5] == (-8, -100456, "out")
            # JSON 缺 amount / 金额不可解析：amount 保持 NULL。
            assert data[6] == (None, -100456, "out")
            assert data[7] == (None, -100456, "out")
            # 大小写不敏感；缺 chat_id 的行 chat 列为 NULL。
            assert data[8] == (7, None, "out")
            # source.chat_id 回退路径。
            assert data[9] == (9, -100789, "in")
            assert data[10] == (None, -100123, None)

            migration.downgrade()
            columns_after = {col["name"] for col in sa.inspect(conn).get_columns("action_event")}
            assert not {"amount", "chat_id", "direction"} & columns_after
            # 原有列与数据不受迁移影响。
            assert conn.execute(sa.text("SELECT count(*) FROM action_event")).scalar_one() == 10
    finally:
        engine.dispose()
