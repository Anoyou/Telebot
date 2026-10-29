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


# ── 插件页面与菜单（ui.pages / ui.actions，Plugin API 1.4）──


class PluginUIPageEntry(BaseModel):
    plugin_key: str
    plugin_name: str
    page_id: str
    title: str
    subtitle: str | None = None
    # 页面声明的 Bridge 权限（config.read / cards.read / actions.invoke），只用于展示；服务器逐次校验。
    bridge: list[str] = Field(default_factory=list)


class PluginUIMenuAction(BaseModel):
    plugin_key: str
    plugin_name: str
    action_id: str
    menu: str
    title: str
    confirm: str | None = None
    destructive: bool = False
    # 有值时点击打开该页面，而不是调用 on_ui_action
    page_id: str | None = None


class PluginUIEntriesResponse(BaseModel):
    menu: str
    pages: list[PluginUIPageEntry] = Field(default_factory=list)
    actions: list[PluginUIMenuAction] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class PluginUIPageSessionRequest(BaseModel):
    account_id: int | None = None


class PluginUIPageSession(BaseModel):
    plugin_key: str
    page_id: str
    title: str
    token: str
    # 相对服务器根的页面地址（/api/plugin-ui/page-assets/{token}/{entry}），不需要登录态
    url: str
    entry: str
    expires_at: int
    bridge: list[str] = Field(default_factory=list)
    protocol: int = 1
    csp: str


class PluginUIBridgeRequest(BaseModel):
    method: str = Field(..., max_length=64)
    params: dict[str, Any] = Field(default_factory=dict)
    account_id: int | None = None


class PluginUIBridgeResponse(BaseModel):
    ok: bool = True
    result: Any = None
    error: dict[str, str] | None = None


class PluginUIMenuActionRequest(BaseModel):
    account_id: int | None = None
    menu: str | None = None


class PluginUIMenuActionResponse(BaseModel):
    ok: bool = True
    message: str | None = None
    refresh: bool = True
    data: Any = None
