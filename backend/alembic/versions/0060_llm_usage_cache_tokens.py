"""Persist prompt cache token usage.

Revision ID: 0060
Revises: 0059
Create Date: 2026-10-16
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("llm_usage", sa.Column("cache_read_tokens", sa.Integer(), nullable=True))
    op.add_column("llm_usage", sa.Column("cache_write_tokens", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("llm_usage", "cache_write_tokens")
    op.drop_column("llm_usage", "cache_read_tokens")
