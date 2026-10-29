"""插件声明式 UI：控件契约与原生卡片（Plugin API 1.3）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ..deps import CurrentUser, DBSession
from ..schemas.plugin_ui import (
    PluginUICardActionResponse,
    PluginUICardData,
    PluginUICardDataRequest,
    PluginUICardsResponse,
)
from ..services import plugin_ui_cards
from ..worker.plugins import plugin_ui

router = APIRouter(tags=["plugins"])


def _bad(code: str, message: str, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


@router.get("/api/plugins/ui-contract", response_model=dict[str, Any])
async def get_plugin_ui_contract(_user: CurrentUser) -> dict[str, Any]:
    """控件 / 卡片契约（schemas/plugin-ui-widgets.json）。客户端遇到不认识的控件时据此提示需要的 App 版本。"""

    return plugin_ui.load_contract()


@router.get("/api/plugin-ui/cards", response_model=PluginUICardsResponse)
async def list_plugin_ui_cards(
    db: DBSession,
    _user: CurrentUser,
    slot: str = Query(..., description="plugin.overview / account.detail / dashboard"),
    plugin_key: str | None = None,
    account_id: int | None = None,
) -> PluginUICardsResponse:
    """列出某个位置的插件卡片声明（不含数据，数据逐卡读取，互不影响）。"""

    try:
        cards, warnings = await plugin_ui_cards.list_cards(db, slot=slot, plugin_key=plugin_key, account_id=account_id)
    except plugin_ui_cards.PluginCardError as exc:
        raise _bad("PLUGIN_CARD_INVALID", str(exc)) from exc
    return PluginUICardsResponse(slot=slot, cards=cards, warnings=warnings)


@router.post("/api/plugin-ui/cards/{plugin_key}/{card_id}/data", response_model=PluginUICardData)
async def read_plugin_ui_card(
    plugin_key: str,
    card_id: str,
    payload: PluginUICardDataRequest,
    db: DBSession,
    _user: CurrentUser,
) -> PluginUICardData:
    """读取一张卡的数据。失败（插件未加载、超时 3 秒、抛异常）返回 status=error，客户端只在这张卡上显示「已停止工作 · 重试」。"""

    result = await plugin_ui_cards.card_data(
        db, plugin_key=plugin_key, card_id=card_id, account_id=payload.account_id, slot=payload.slot
    )
    return PluginUICardData(**result)


@router.post(
    "/api/plugin-ui/cards/{plugin_key}/{card_id}/actions/{action_id}",
    response_model=PluginUICardActionResponse,
)
async def run_plugin_ui_card_action(
    plugin_key: str,
    card_id: str,
    action_id: str,
    payload: PluginUICardDataRequest,
    db: DBSession,
    user: CurrentUser,
) -> PluginUICardActionResponse:
    """执行卡片 buttons 布局里的按钮（插件 on_ui_action，10 秒；宿主卡片由宿主处理）。不自动重试。"""

    from ..services import audit

    try:
        result = await plugin_ui_cards.card_action(
            db,
            plugin_key=plugin_key,
            card_id=card_id,
            action_id=action_id,
            account_id=payload.account_id,
            slot=payload.slot,
        )
    except plugin_ui_cards.PluginCardError as exc:
        raise _bad("PLUGIN_CARD_ACTION_UNAVAILABLE", str(exc), 404) from exc
    except TimeoutError as exc:
        raise _bad("PLUGIN_CARD_ACTION_TIMEOUT", "插件处理超时（10 秒），结果未知，请刷新卡片确认", 504) from exc
    except Exception as exc:  # noqa: BLE001
        raise _bad("PLUGIN_CARD_ACTION_FAILED", f"插件处理失败：{type(exc).__name__}", 400) from exc
    await audit.write(
        db,
        user.id,
        "plugin.ui.action",
        target=f"plugin:{plugin_key}/card:{card_id}",
        detail={"action_id": action_id, "account_id": payload.account_id},
    )
    await db.commit()
    return PluginUICardActionResponse(**result)
