"""add aliases to command_template

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-06
"""

from __future__ import annotations

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE command_template
            ADD COLUMN aliases JSONB NOT NULL DEFAULT '[]'::jsonb;
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE command_template DROP COLUMN IF EXISTS aliases;")
