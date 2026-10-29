"""插件声明式 UI 接口 schema（卡片 DSL、控件契约）。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class PluginUICard(BaseModel):
    plugin_key: str
    plugin_name: str
    card_id: str
    slot: str
    title: str
    subtitle: str | None = None
    source_kind: Literal["read", "action"]
    # 块声明原样下发（stats / table / kv / list / buttons），结构见 schemas/plugin-ui-widgets.json。
    blocks: list[dict[str, Any]] = Field(default_factory=list)
    refresh_seconds: int | None = None
    # 宿主内置卡片（如插件详情的「运行统计」）
    host: bool = False


class PluginUICardsResponse(BaseModel):
    slot: str
    cards: list[PluginUICard] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class PluginUICardDataRequest(BaseModel):
    account_id: int | None = None
    slot: str | None = None


class PluginUICardData(BaseModel):
    plugin_key: str
    card_id: str
    status: Literal["ok", "error"]
    data: dict[str, Any] | None = None
    message: str | None = None
    updated_at: int
    duration_ms: int = 0


class PluginUICardActionResponse(BaseModel):
    ok: bool = True
    message: str | None = None
    refresh: bool = True
