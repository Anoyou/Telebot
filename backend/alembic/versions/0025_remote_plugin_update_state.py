"""远程模块更新检查状态。

Revision ID: 0025
Revises: 0024
Create Date: 2026-05-21
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("remote_plugin", sa.Column("latest_version", sa.String(length=64), nullable=True))
    op.add_column(
        "remote_plugin",
        sa.Column("update_available", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("remote_plugin", sa.Column("last_update_check_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("remote_plugin", sa.Column("last_update_check_error", sa.Text(), nullable=True))
    op.add_column(
        "remote_plugin",
        sa.Column("lint_warnings", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )


def downgrade() -> None:
    op.drop_column("remote_plugin", "lint_warnings")
    op.drop_column("remote_plugin", "last_update_check_error")
    op.drop_column("remote_plugin", "last_update_check_at")
    op.drop_column("remote_plugin", "update_available")
    op.drop_column("remote_plugin", "latest_version")
