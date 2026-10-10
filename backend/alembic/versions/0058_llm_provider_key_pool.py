"""增加 LLM Provider 密钥池。

Revision ID: 0058
Revises: 0057
Create Date: 2026-10-10
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_provider_key",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("provider_id", sa.BigInteger(), nullable=False),
        sa.Column("label", sa.String(length=64), nullable=False),
        sa.Column("api_key_enc", sa.Text(), nullable=False),
        sa.Column("key_hint", sa.String(length=8), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("priority", sa.Integer(), server_default=sa.text("100"), nullable=False),
        sa.Column("proxy_id", sa.BigInteger(), nullable=True),
        sa.Column("daily_request_limit", sa.Integer(), nullable=True),
        sa.Column("daily_token_limit", sa.Integer(), nullable=True),
        sa.Column("notes", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["provider_id"], ["llm_provider.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["proxy_id"], ["proxy.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_llm_provider_key_provider_id", "llm_provider_key", ["provider_id"])

    # 既有 Provider 一律 order，行为与单密钥时期完全一致。
    op.add_column(
        "llm_provider",
        sa.Column(
            "key_strategy",
            sa.String(length=16),
            server_default=sa.text("'order'"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_llm_provider_key_strategy",
        "llm_provider",
        "key_strategy IN ('order', 'rotate', 'least_used')",
    )

    # 历史记录保持 NULL：无法事后判定当时用的是哪把密钥。
    op.add_column("llm_usage", sa.Column("provider_key_id", sa.BigInteger(), nullable=True))
    op.add_column("llm_usage", sa.Column("provider_key_label", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("llm_usage", "provider_key_label")
    op.drop_column("llm_usage", "provider_key_id")
    op.drop_constraint("ck_llm_provider_key_strategy", "llm_provider", type_="check")
    op.drop_column("llm_provider", "key_strategy")
    op.drop_index("ix_llm_provider_key_provider_id", table_name="llm_provider_key")
    op.drop_table("llm_provider_key")
