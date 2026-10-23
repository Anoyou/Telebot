"""action_event 资金台账提取列（amount / chat_id / direction）+ 存量回填。

Revision ID: 0062
Revises: 0061
Create Date: 2026-10-24

背景：ledger/stats 旧行为把窗口内全部 ``action_event`` 行拉到 Python，再从
``params_summary`` JSON 里过滤金额/群/方向（O(窗口事件数) 的内存+CPU）。
本迁移把台账口径的三个字段提升为可空列，写入路径同步维护（见
``app/services/action_tap.py``），让过滤与聚合下推到 SQL。

存量回填口径（与 ``ledger_service`` 旧 Python 读取口径一致）：
- ``direction``：payment_confirmed 类事件为 ``'in'``；payout 类动作为 ``'out'``；
  其余（非资金动作）保持 NULL。
- ``amount``：依次取 ``amount`` / ``payment.amount`` / ``parsed.amount`` /
  ``raw.parsed.amount`` / ``result.amount``，取第一个可解析为数字的值；均缺失为 NULL。
- ``chat_id``：依次取 ``chat_id`` / ``source.chat_id`` / ``chat.id`` / ``payment.chat_id``。

``params_summary`` JSON 原样保留（冗余，兼容旧读路径）；downgrade 直接删列，
数据可按同一口径由 JSON 重建。查询索引：现有 ``ix_action_event_status_created``
与 ``ix_action_event_account_created`` 已覆盖"时间窗口 + 状态/账号"的新查询
形态，无需新增索引。
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import text

from alembic import op

revision = "0062"
down_revision = "0061"
branch_labels = None
depends_on = None

# PG：JSON（非 JSONB）用 ->> 取文本；与 ledger_service._extract_amount 取值优先级一致。
_PG_AMOUNT_PATHS = (
    "params_summary->>'amount'",
    "params_summary->'payment'->>'amount'",
    "params_summary->'parsed'->>'amount'",
    "params_summary->'raw'->'parsed'->>'amount'",
    "params_summary->'result'->>'amount'",
)
# 与 ledger_service._entry_from_action_event 的 chat_id 取值优先级一致。
_PG_CHAT_PATHS = (
    "params_summary->>'chat_id'",
    "params_summary->'source'->>'chat_id'",
    "params_summary->'chat'->>'id'",
    "params_summary->'payment'->>'chat_id'",
)
# 数字外形守卫：Python Decimal(str(x)) 可接受的常规十进制/科学计数法文本；
# 非数字文本（旧 Python 侧同样解析失败）不回填、保持 NULL。
_PG_NUMERIC_TEXT_RE = r"^[+-]?([0-9]+(\.[0-9]*)?|\.[0-9]+)([eE][+-]?[0-9]+)?$"
_PG_INT_TEXT_RE = r"^-?[0-9]+$"

_SQLITE_AMOUNT_PATHS = (
    "$.amount",
    "$.payment.amount",
    "$.parsed.amount",
    "$.raw.parsed.amount",
    "$.result.amount",
)
_SQLITE_CHAT_PATHS = ("$.chat_id", "$.source.chat_id", "$.chat.id", "$.payment.chat_id")

# 与 ledger_service._direction_from_action 等价的 SQL 口径：
# in = payment_confirmed 类；out = payout 类（含 *_payout / payout_* 前后缀）。
_PG_DIRECTION_SQL = """
UPDATE action_event
SET direction = CASE
    WHEN lower(btrim(action_type)) = 'payment_confirmed'
        OR lower(btrim(params_summary->>'type')) = 'payment_confirmed'
        OR lower(btrim(params_summary->>'action_type')) = 'payment_confirmed'
        OR lower(btrim(params_summary->>'event_type')) = 'payment_confirmed'
        OR lower(btrim(params_summary->'source'->>'type')) = 'payment_confirmed'
        OR lower(btrim(params_summary->'raw'->>'event_type')) = 'payment_confirmed'
    THEN 'in'
    WHEN lower(btrim(action_type)) = 'payout'
        OR lower(btrim(params_summary->>'type')) = 'payout'
        OR lower(btrim(params_summary->>'action_type')) = 'payout'
        OR lower(btrim(action_type)) LIKE '%\\_payout' ESCAPE '\\'
        OR lower(btrim(action_type)) LIKE 'payout\\_%' ESCAPE '\\'
    THEN 'out'
    ELSE NULL
END
"""

_SQLITE_DIRECTION_SQL = """
UPDATE action_event
SET direction = CASE
    WHEN lower(trim(cast(action_type AS TEXT))) = 'payment_confirmed'
        OR lower(trim(cast(json_extract(params_summary, '$.type') AS TEXT))) = 'payment_confirmed'
        OR lower(trim(cast(json_extract(params_summary, '$.action_type') AS TEXT))) = 'payment_confirmed'
        OR lower(trim(cast(json_extract(params_summary, '$.event_type') AS TEXT))) = 'payment_confirmed'
        OR lower(trim(cast(json_extract(params_summary, '$.source.type') AS TEXT))) = 'payment_confirmed'
        OR lower(trim(cast(json_extract(params_summary, '$.raw.event_type') AS TEXT))) = 'payment_confirmed'
    THEN 'in'
    WHEN lower(trim(cast(action_type AS TEXT))) = 'payout'
        OR lower(trim(cast(json_extract(params_summary, '$.type') AS TEXT))) = 'payout'
        OR lower(trim(cast(json_extract(params_summary, '$.action_type') AS TEXT))) = 'payout'
        OR lower(trim(cast(action_type AS TEXT))) LIKE '%\\_payout' ESCAPE '\\'
        OR lower(trim(cast(action_type AS TEXT))) LIKE 'payout\\_%' ESCAPE '\\'
    THEN 'out'
    ELSE NULL
END
"""


def _pg_amount_case(path: str) -> str:
    return (
        f"CASE WHEN btrim({path}) ~ '{_PG_NUMERIC_TEXT_RE}' "
        f"THEN btrim({path})::numeric END"
    )


def _pg_chat_case(path: str) -> str:
    return (
        f"CASE WHEN btrim({path}) ~ '{_PG_INT_TEXT_RE}' "
        f"THEN btrim({path})::bigint END"
    )


def _sqlite_amount_case(path: str) -> str:
    value = f"trim(cast(json_extract(params_summary, '{path}') AS TEXT))"
    # GLOB 区分大小写：只放行数字外形文本，避免 SQLite CAST 对垃圾文本静默取 0。
    return (
        f"CASE WHEN {value} GLOB '*[0-9]*' "
        f"AND {value} NOT GLOB '*[^0-9.eE+-]*' "
        f"THEN cast({value} AS NUMERIC) END"
    )


def _sqlite_chat_case(path: str) -> str:
    value = f"trim(cast(json_extract(params_summary, '{path}') AS TEXT))"
    # 允许可选前导负号 + 纯数字；拒绝内部负号/非数字字符。
    return (
        f"CASE WHEN ({value} GLOB '-[0-9]*' OR {value} GLOB '[0-9]*') "
        f"AND {value} NOT GLOB '*[0-9]-*' "
        f"AND {value} NOT GLOB '*[^0-9-]*' "
        f"THEN cast({value} AS INTEGER) END"
    )


def upgrade() -> None:
    op.add_column("action_event", sa.Column("amount", sa.Numeric(), nullable=True))
    op.add_column("action_event", sa.Column("chat_id", sa.BigInteger(), nullable=True))
    op.add_column("action_event", sa.Column("direction", sa.String(length=16), nullable=True))

    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        op.execute(text(_PG_DIRECTION_SQL))
        op.execute(
            text(
                "UPDATE action_event SET amount = COALESCE({cases})".format(
                    cases=", ".join(_pg_amount_case(path) for path in _PG_AMOUNT_PATHS)
                )
            )
        )
        op.execute(
            text(
                "UPDATE action_event SET chat_id = COALESCE({cases})".format(
                    cases=", ".join(_pg_chat_case(path) for path in _PG_CHAT_PATHS)
                )
            )
        )
    else:
        # SQLite：开发/测试库同样回填，口径与 PG 一致（json_extract 版本）。
        op.execute(text(_SQLITE_DIRECTION_SQL))
        op.execute(
            text(
                "UPDATE action_event SET amount = COALESCE({cases})".format(
                    cases=", ".join(_sqlite_amount_case(path) for path in _SQLITE_AMOUNT_PATHS)
                )
            )
        )
        op.execute(
            text(
                "UPDATE action_event SET chat_id = COALESCE({cases})".format(
                    cases=", ".join(_sqlite_chat_case(path) for path in _SQLITE_CHAT_PATHS)
                )
            )
        )


def downgrade() -> None:
    # 三列均由 params_summary 派生（写入路径持续维护 JSON），删列不丢数据。
    op.drop_column("action_event", "direction")
    op.drop_column("action_event", "chat_id")
    op.drop_column("action_event", "amount")
