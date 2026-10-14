"""WP-B13：at-least-once 语义文档要跟真实消费者保持一致。

`docs/API-CONTRACT.md` 声明 runtime_log / rate_limit_event 是 at-least-once 投递。
这里断言的两件事都是字面可核对的：文档真的写了这个语义，且 `RuntimeLog` /
`RateLimitEvent` 仍然没有 `event_id` 之类的去重列——一旦有人给模型加上去重键，
文档的 at-least-once 说明就变成错的，需要同步改口径。
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import inspect as sa_inspect

from app.db.models.log import RuntimeLog
from app.db.models.rate_limit import RateLimitEvent

REPO_ROOT = Path(__file__).resolve().parents[3]
API_CONTRACT = REPO_ROOT / "docs" / "API-CONTRACT.md"

def test_api_contract_documents_at_least_once_delivery() -> None:
    doc = API_CONTRACT.read_text(encoding="utf-8")
    assert "at-least-once" in doc
    # 应对齐真实链路的关键词：inflight 重试 + ack 在提交之后 + 不加 event_id
    assert "inflight" in doc
    assert "event_id" in doc
    assert "runtime_log" in doc
    assert "rate_limit_event" in doc


def test_api_contract_does_not_claim_exactly_once() -> None:
    doc = API_CONTRACT.read_text(encoding="utf-8")
    # 文档不能把自己描述成 exactly-once，否则和消费者实现直接矛盾
    assert "exactly-once（恰好一次）" not in doc


def test_event_tables_have_no_dedup_column() -> None:
    """两张观测表仍未加去重键：这决定了文档必须维持 at-least-once 口径。"""
    for model in (RuntimeLog, RateLimitEvent):
        columns = {column.name for column in sa_inspect(model).columns}
        assert not columns & {"event_id", "dedup_key"}, (
            f"{model.__tablename__} 出现了去重列，需要同步改写 API-CONTRACT 的投递语义"
        )
