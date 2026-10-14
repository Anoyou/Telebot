from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_migration_module():
    path = Path(__file__).resolve().parents[2] / "alembic/versions/0059_system_agent_secret_map.py"
    spec = importlib.util.spec_from_file_location("telepilot_migration_0059", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_0059_chain_matches_0058() -> None:
    migration = _load_migration_module()
    assert migration.revision == "0059"
    assert migration.down_revision == "0058"
    assert migration.branch_labels is None
    assert migration.depends_on is None


def test_migration_0059_upgrade_and_downgrade(monkeypatch) -> None:
    migration = _load_migration_module()
    added: list[tuple[str, str, bool]] = []
    dropped: list[tuple[str, str]] = []

    monkeypatch.setattr(
        migration.op,
        "add_column",
        lambda table, column: added.append((table, column.name, column.nullable)),
    )
    monkeypatch.setattr(migration.op, "drop_column", lambda table, column: dropped.append((table, column)))

    migration.upgrade()
    assert added == [("system_agent_session", "secret_map_enc", True)]

    migration.downgrade()
    assert dropped == [("system_agent_session", "secret_map_enc")]


def test_migration_0059_column_is_encrypted_text() -> None:
    """列类型必须是 Text，且允许为 NULL（老会话没有映射）。"""
    migration = _load_migration_module()
    import sqlalchemy as sa

    column = sa.Column("secret_map_enc", sa.Text(), nullable=True)
    assert isinstance(column.type, sa.Text)
    assert column.nullable is True
    assert migration.revision
