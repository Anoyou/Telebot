"""规则 schema。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RuleCreate(BaseModel):
    name: str
    enabled: bool = True
    priority: int = 100
    config: dict[str, Any] = Field(default_factory=dict)


class RuleUpdate(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    priority: int | None = None
    config: dict[str, Any] | None = None


class RuleOut(BaseModel):
    id: int
    account_id: int
    feature_key: str
    name: str
    enabled: bool
    priority: int
    config: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RuleCopyRequest(BaseModel):
    rule_ids: list[int]
    target_account_ids: list[int]


class RuleDryRunRequest(BaseModel):
    """试运行：把模拟消息喂给规则，返回是否命中 + 渲染结果。"""
    sample_message: str
    sample_chat_type: str | None = "private"  # private | group | channel
    sample_chat_id: int | None = None         # group/channel 类型可选，用于 group_specific 命中


class RuleDryRunResponse(BaseModel):
    matched: bool
    output: str | None = None
    detail: dict[str, Any] | None = None
