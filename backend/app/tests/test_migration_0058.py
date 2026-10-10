from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_migration_module():
    path = Path(__file__).resolve().parents[2] / "alembic/versions/0058_llm_provider_key_pool.py"
    spec = importlib.util.spec_from_file_location("telepilot_migration_0058", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_0058_upgrade_and_downgrade(monkeypatch) -> None:
    migration = _load_migration_module()
    created_tables: list[tuple[str, list[str]]] = []
    dropped_tables: list[str] = []
    added: list[tuple[str, str, bool]] = []
    dropped: list[tuple[str, str]] = []
    constraints: list[tuple[str, str, str]] = []
    dropped_constraints: list[str] = []
    indexes: list[tuple[str, str]] = []
    dropped_indexes: list[str] = []

    monkeypatch.setattr(
        migration.op,
        "create_table",
        lambda name, *columns: created_tables.append(
            (name, [c.name for c in columns if hasattr(c, "name") and getattr(c, "type", None) is not None])
        ),
    )
    monkeypatch.setattr(migration.op, "drop_table", lambda name: dropped_tables.append(name))
    monkeypatch.setattr(
        migration.op,
        "add_column",
        lambda table, column: added.append((table, column.name, column.nullable)),
    )
    monkeypatch.setattr(migration.op, "drop_column", lambda table, column: dropped.append((table, column)))
    monkeypatch.setattr(
        migration.op,
        "create_check_constraint",
        lambda name, table, condition: constraints.append((name, table, condition)),
    )
    monkeypatch.setattr(
        migration.op,
        "drop_constraint",
        lambda name, _table, **_kwargs: dropped_constraints.append(name),
    )
    monkeypatch.setattr(
        migration.op,
        "create_index",
        lambda name, table, _columns, **_kwargs: indexes.append((name, table)),
    )
    monkeypatch.setattr(
        migration.op,
        "drop_index",
        lambda name, **_kwargs: dropped_indexes.append(name),
    )

    migration.upgrade()
    migration.downgrade()

    assert migration.revision == "0058"
    assert migration.down_revision == "0057"
    assert len(created_tables) == 1
    table_name, columns = created_tables[0]
    assert table_name == "llm_provider_key"
    assert {"id", "provider_id", "label", "api_key_enc", "key_hint", "enabled", "priority", "proxy_id"} <= set(
        columns
    )
    assert indexes == [("ix_llm_provider_key_provider_id", "llm_provider_key")]
    assert added == [
        ("llm_provider", "key_strategy", False),
        ("llm_usage", "provider_key_id", True),
        ("llm_usage", "provider_key_label", True),
    ]
    assert constraints == [
        ("ck_llm_provider_key_strategy", "llm_provider", "key_strategy IN ('order', 'rotate', 'least_used')")
    ]
    assert dropped_constraints == ["ck_llm_provider_key_strategy"]
    assert dropped == [
        ("llm_usage", "provider_key_label"),
        ("llm_usage", "provider_key_id"),
        ("llm_provider", "key_strategy"),
    ]
    assert dropped_indexes == ["ix_llm_provider_key_provider_id"]
    assert dropped_tables == ["llm_provider_key"]
