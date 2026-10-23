"""0063 孤儿对象清障迁移的 SQLite smoke 测试。

覆盖：
  - upgrade 后 plugin_available 表与 plugin_install.repo_id 列均不存在，
    且 plugin_install 其余列 / 数据不受影响
  - 重建一次 0012 之后的真实形态：plugin_repo 已删、plugin_install 的
    repo_id 外键悬空（FK DDL 仍残留），迁移仍可完成
  - 新库（create_all 口径，plugin_install 本就无 repo_id）重复执行 upgrade 幂等
  - downgrade 为 no-op：不抛错、不恢复对象
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def _load_migration_module():
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic/versions/0063_drop_orphan_plugin_objects.py"
    )
    spec = importlib.util.spec_from_file_location("telepilot_migration_0063", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_legacy_tables(conn, *, with_repo_id: bool) -> None:
    """按 0001/0005 的原始口径建表；with_repo_id=False 模拟 create_all 新库。"""

    conn.execute(
        sa.text(
            "CREATE TABLE plugin_repo ("
            "id INTEGER PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL, "
            "enabled BOOLEAN DEFAULT 1, last_synced_at TEXT)"
        )
    )
    if with_repo_id:
        conn.execute(
            sa.text(
                "CREATE TABLE plugin_available ("
                "repo_id INTEGER NOT NULL REFERENCES plugin_repo(id) ON DELETE CASCADE, "
                "key TEXT NOT NULL, name TEXT NOT NULL, version TEXT NOT NULL, "
                "author TEXT, description TEXT, manifest JSON, "
                "PRIMARY KEY (repo_id, key))"
            )
        )
        repo_id_ddl = "repo_id INTEGER REFERENCES plugin_repo(id) ON DELETE SET NULL, "
    else:
        repo_id_ddl = ""
    conn.execute(
        sa.text(
            "CREATE TABLE plugin_install ("
            "key TEXT PRIMARY KEY, "
            "source TEXT NOT NULL, "
            "version TEXT NOT NULL DEFAULT '0.0.0', "
            f"{repo_id_ddl}"
            "manifest_json JSON, "
            "signature_ok BOOLEAN, "
            "installed_path TEXT NOT NULL, "
            "enabled BOOLEAN NOT NULL DEFAULT 0, "
            "installed_at TEXT DEFAULT (datetime('now')), "
            "updated_at TEXT DEFAULT (datetime('now')))"
        )
    )
    conn.execute(
        sa.text(
            "INSERT INTO plugin_install (key, source, version, installed_path, enabled) "
            "VALUES ('legacy-zip', 'zip', '1.0.0', '/data/plugins/installed/legacy-zip', 1)"
        )
    )
    # 模拟 0012 在 SQLite 上的效果：plugin_repo 被删除（外键 PRAGMA 默认关闭，
    # plugin_install 的 repo_id 外键从此悬空，与生产 PG 库的残留形态一致）。
    conn.execute(sa.text("DROP TABLE plugin_repo"))


def _plugin_install_columns(conn) -> set[str]:
    return {column["name"] for column in sa.inspect(conn).get_columns("plugin_install")}


def test_migration_0063_drops_orphan_table_and_column_then_noop_downgrade(monkeypatch) -> None:
    migration = _load_migration_module()
    assert migration.revision == "0063"
    assert migration.down_revision == "0062"
    assert migration.branch_labels is None
    assert migration.depends_on is None
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as conn:
            _create_legacy_tables(conn, with_repo_id=True)
            assert "plugin_available" in sa.inspect(conn).get_table_names()
            assert "repo_id" in _plugin_install_columns(conn)

            monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(conn)))
            migration.upgrade()

            # 孤儿表 / 孤儿列消失；plugin_install 本体与其余列、存量数据保留。
            assert "plugin_available" not in sa.inspect(conn).get_table_names()
            columns = _plugin_install_columns(conn)
            assert "repo_id" not in columns
            assert {"key", "source", "version", "installed_path", "enabled"} <= columns
            rows = conn.execute(
                sa.text("SELECT key, source, installed_path FROM plugin_install")
            ).all()
            assert rows == [("legacy-zip", "zip", "/data/plugins/installed/legacy-zip")]

            # downgrade 是文档化 no-op：不抛错，也不恢复被删对象。
            migration.downgrade()
            assert "plugin_available" not in sa.inspect(conn).get_table_names()
            assert "repo_id" not in _plugin_install_columns(conn)
    finally:
        engine.dispose()


def test_migration_0063_upgrade_is_idempotent_on_fresh_schema(monkeypatch) -> None:
    """create_all 口径的新库（无 plugin_available、无 repo_id）重复 upgrade 不报错。"""

    migration = _load_migration_module()
    engine = sa.create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as conn:
            _create_legacy_tables(conn, with_repo_id=False)
            conn.execute(sa.text("DROP TABLE IF EXISTS plugin_available"))

            monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(conn)))
            migration.upgrade()

            assert "plugin_available" not in sa.inspect(conn).get_table_names()
            assert "repo_id" not in _plugin_install_columns(conn)
            assert (
                conn.execute(sa.text("SELECT count(*) FROM plugin_install")).scalar_one() == 1
            )
    finally:
        engine.dispose()
