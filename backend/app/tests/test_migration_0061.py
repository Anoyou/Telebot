from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def _load_migration_module():
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic/versions/0061_account_dedupe_and_usage_indexes.py"
    )
    spec = importlib.util.spec_from_file_location("telepilot_migration_0061", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_0061_preserves_history_and_roundtrips(monkeypatch) -> None:
    migration = _load_migration_module()
    assert migration.revision == "0061"
    assert migration.down_revision == "0060"
    assert migration.branch_labels is None
    assert migration.depends_on is None
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "CREATE TABLE account (id INTEGER PRIMARY KEY, phone TEXT NOT NULL, "
                    "tg_user_id INTEGER)"
                )
            )
            conn.execute(
                sa.text(
                    "CREATE TABLE audit_log "
                    "(id INTEGER PRIMARY KEY, ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
                )
            )
            conn.execute(
                sa.text("CREATE TABLE llm_usage (id INTEGER PRIMARY KEY, source TEXT)")
            )
            monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(conn)))
            migration.upgrade()
            indexes = {
                table: {item["name"] for item in sa.inspect(conn).get_indexes(table)}
                for table in ("account", "audit_log", "llm_usage")
            }
            assert indexes["account"] == {"ix_account_phone", "ix_account_tg_user_id"}
            assert indexes["audit_log"] == {"ix_audit_log_ts"}
            assert indexes["llm_usage"] == {"ix_llm_usage_source"}
            # 索引不影响既有数据
            conn.execute(sa.text("INSERT INTO account VALUES (1, '+8613800000000', 12345)"))
            assert conn.execute(sa.text("SELECT * FROM account")).one() == (1, "+8613800000000", 12345)
            migration.downgrade()
            for table, expected in (
                ("account", set()),
                ("audit_log", set()),
                ("llm_usage", set()),
            ):
                assert {item["name"] for item in sa.inspect(conn).get_indexes(table)} == expected
    finally:
        engine.dispose()
