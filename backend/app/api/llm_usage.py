"""LLM 调用记录查询 API。"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from ..db.models.llm_usage import LLMUsage
from ..deps import CurrentUser, DBSession

router = APIRouter(prefix="/api/llm/usage", tags=["llm-usage"])


class LLMUsageItem(BaseModel):
    """最近一次 LLM 调用记录。"""

    id: int
    account_id: int | None
    provider_id: int | None
    provider_name: str | None
    model: str | None
    source: str | None
    input_tokens: int
    output_tokens: int
    latency_ms: int
    success: bool
    error_type: str | None
    used_fallback: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class LLMUsageRecentResponse(BaseModel):
    """最近 LLM 调用记录列表。"""

    items: list[LLMUsageItem]


@router.get("/recent", response_model=LLMUsageRecentResponse)
async def list_recent_llm_usage(
    db: DBSession,
    _user: CurrentUser,
    limit: int = Query(20, ge=1, le=100),
) -> LLMUsageRecentResponse:
    """返回最近 LLM 调用记录，供 AI 中心最小可用 Usage 页展示。"""
    rows = (
        await db.execute(
            select(LLMUsage)
            .order_by(LLMUsage.created_at.desc(), LLMUsage.id.desc())
            .limit(limit)
        )
    ).scalars().all()
    return LLMUsageRecentResponse(
        items=[LLMUsageItem.model_validate(row) for row in rows]
    )
