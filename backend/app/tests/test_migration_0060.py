from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def _load_migration_module():
    path = Path(__file__).resolve().parents[2] / "alembic/versions/0060_llm_usage_cache_tokens.py"
    spec = importlib.util.spec_from_file_location("telepilot_migration_0060", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_0060_preserves_history_and_roundtrips(monkeypatch) -> None:
    migration = _load_migration_module()
    assert migration.revision == "0060"
    assert migration.down_revision == "0059"
    assert migration.branch_labels is None
    assert migration.depends_on is None
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as conn:
            conn.execute(sa.text("CREATE TABLE llm_usage (id INTEGER PRIMARY KEY, input_tokens INTEGER NOT NULL)"))
            conn.execute(sa.text("INSERT INTO llm_usage VALUES (1, 123)"))
            monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(conn)))
            migration.upgrade()
            columns = {column["name"]: column for column in sa.inspect(conn).get_columns("llm_usage")}
            for name in ("cache_read_tokens", "cache_write_tokens"):
                assert columns[name]["nullable"] is True
                assert isinstance(columns[name]["type"], sa.Integer)
            row = conn.execute(sa.text("SELECT * FROM llm_usage")).one()
            assert tuple(row) == (1, 123, None, None)
            conn.execute(sa.text("UPDATE llm_usage SET cache_read_tokens=80, cache_write_tokens=20"))
            assert conn.execute(sa.text("SELECT cache_read_tokens, cache_write_tokens FROM llm_usage")).one() == (80, 20)
            migration.downgrade()
            assert conn.execute(sa.text("SELECT * FROM llm_usage")).one() == (1, 123)
            assert {column["name"] for column in sa.inspect(conn).get_columns("llm_usage")} == {"id", "input_tokens"}
    finally:
        engine.dispose()
