"""add ignored_peer table

Revision ID: 0004
Revises: 0003b
Create Date: 2026-05-03

新增表：``ignored_peer``
- 每账号一份的"被忽略 peer"名单
- worker 收到来自这些 peer 的 incoming 消息时直接短路插件分发
- 详见 ``app/db/models/ignored_peer.py``

注意：
- ``peer_id`` 用 ``BIGINT`` 而非 ``INTEGER``，因为 supergroup 的 chat_id 形如
  ``-1001234567890``，超过 32 位整型的范围。
- ``UNIQUE (account_id, peer_id)`` 保证同账号下幂等加入。

链顺序（汇总后）：``0001 → 0002 → 0003 (command) → 0003b (device) → 0004 (本) → 0005 (plugin)``。
"""

from __future__ import annotations

from alembic import op

revision = "0004"
down_revision = "0003b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE ignored_peer (
            id          BIGSERIAL PRIMARY KEY,
            account_id  BIGINT NOT NULL REFERENCES account(id) ON DELETE CASCADE,
            peer_id     BIGINT NOT NULL,
            peer_kind   VARCHAR(16) NOT NULL,
            peer_label  VARCHAR(128),
            added_at    TIMESTAMPTZ DEFAULT now(),
            CONSTRAINT uq_ignored_peer_account_peer UNIQUE (account_id, peer_id)
        );
        CREATE INDEX ix_ignored_peer_account_id ON ignored_peer (account_id);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS ignored_peer CASCADE;
        """
    )
