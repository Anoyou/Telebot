"""消息模板实验室 API schemas。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class MessageTemplateCatalogItem(BaseModel):
    id: str
    group: str
    feature_key: str | None = None
    field_key: str
    title: str
    description: str = ""
    template: str = ""
    sample_data: dict[str, Any] = Field(default_factory=dict)
    parse_mode: str | None = "HTML"


class MessageTemplateCatalogGroup(BaseModel):
    group: str
    title: str
    items: list[MessageTemplateCatalogItem] = Field(default_factory=list)


class MessageTemplateCatalogResponse(BaseModel):
    account_id: int
    groups: list[MessageTemplateCatalogGroup] = Field(default_factory=list)
    items: list[MessageTemplateCatalogItem] = Field(default_factory=list)


class MessageTemplateRenderRequest(BaseModel):
    template: str = Field(default="", max_length=10000)
    sample_data: dict[str, Any] = Field(default_factory=dict)
    parse_mode: str | None = "HTML"


class MessageTemplateEntitySummary(BaseModel):
    type: str
    raw_type: str
    offset: int
    length: int
    language: str | None = None
    collapsed: bool | None = None


class MessageTemplateValidationResult(BaseModel):
    ok: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    plain_text: str = ""


class MessageTemplateRenderResponse(BaseModel):
    text: str
    parse_mode: str | None = "HTML"
    plain_text: str = ""
    entities: list[MessageTemplateEntitySummary] = Field(default_factory=list)
    validation: MessageTemplateValidationResult


class MessageTemplateTestSendRequest(BaseModel):
    account_id: int = Field(ge=1)
    target_chat_id: int
    text: str = Field(min_length=1, max_length=4000)
    parse_mode: str | None = "HTML"


class MessageTemplateTestSendResponse(BaseModel):
    ok: bool
    target_chat_id: int
    parse_mode: str | None = "HTML"
    message_id: int | None = None
    message: str
