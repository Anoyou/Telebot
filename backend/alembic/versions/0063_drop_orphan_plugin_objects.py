"""drop 孤儿对象：plugin_available 表与 plugin_install.repo_id 列

Revision ID: 0063
Revises: 0062
Create Date: 2026-10-24

清理两个自早期版本残留、当前代码已完全不引用的孤儿对象：

- ``plugin_available`` 表：0001 与 ``plugin_repo`` 一起创建，0012 删除
  ``plugin_repo`` 时（PG CASCADE 只连带删依赖约束）本表残留为孤儿；全仓库
  （backend/、scripts/、docs/ 的代码引用）无任何运行时引用，模型亦未映射。
- ``plugin_install.repo_id`` 列：0005 作为指向 ``plugin_repo.id`` 的外键
  加入，0012 删 ``plugin_repo`` 后列残留在库中；``PluginInstall`` 模型
  （0029 起只读兼容表）未映射该列。

downgrade 为 no-op：孤儿对象本就无引用、不承载任何可恢复业务数据
（plugin_repo 本体已在 0012 删除，残留列值全部悬空），重建空表 / 空列
没有意义（对照 0029 的 no-op downgrade 先例）。
"""

from __future__ import annotations

from sqlalchemy import inspect

from alembic import op

revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 两方言都支持 DROP TABLE IF EXISTS；plugin_available 无被依赖对象。
    op.execute("DROP TABLE IF EXISTS plugin_available;")

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE plugin_install DROP COLUMN IF EXISTS repo_id;")
        return
    # SQLite（开发/测试）：create_all 建出的库本就没有 repo_id（模型未映射），
    # 反射确认存在才删，保持幂等。删除本身优先用 3.35+ 的原生 DROP COLUMN
    # （不做表反射，悬空外键场景也能工作），老版本再回落 batch 整表重建
    # （对照 0039/0052 的 SQLite 兼容先例）。
    columns = {column["name"] for column in inspect(bind).get_columns("plugin_install")}
    if "repo_id" not in columns:
        return
    if getattr(bind.dialect.dbapi, "sqlite_version_info", (0,)) >= (3, 35, 0):
        op.execute("ALTER TABLE plugin_install DROP COLUMN repo_id;")
    else:
        with op.batch_alter_table("plugin_install") as batch:
            batch.drop_column("repo_id")


def downgrade() -> None:
    # no-op：孤儿表 / 孤儿列删除前已无任何代码引用，数据不可恢复也无需恢复。
    pass
