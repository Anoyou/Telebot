"""插件声明式 UI：控件契约与原生卡片（Plugin API 1.3），插件页面与菜单（1.4）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from ..deps import CurrentUser, DBSession
from ..schemas.plugin_ui import (
    PaymentSourceStatusResponse,
    PluginUIBridgeRequest,
    PluginUIBridgeResponse,
    PluginUICardActionResponse,
    PluginUICardData,
    PluginUICardDataRequest,
    PluginUICardsResponse,
    PluginUIEntriesResponse,
    PluginUIMenuActionRequest,
    PluginUIMenuActionResponse,
    PluginUIPageSession,
    PluginUIPageSessionRequest,
)
from ..services import plugin_funds_cards, plugin_ui_cards, plugin_ui_pages
from ..worker.plugins import plugin_ui

router = APIRouter(tags=["plugins"])


def _bad(code: str, message: str, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


@router.get("/api/plugins/ui-contract", response_model=dict[str, Any])
async def get_plugin_ui_contract(_user: CurrentUser) -> dict[str, Any]:
    """控件 / 卡片契约（schemas/plugin-ui-widgets.json）。客户端遇到不认识的控件时据此提示需要的 App 版本。"""

    return plugin_ui.load_contract()


@router.get("/api/payment-sources/status", response_model=PaymentSourceStatusResponse)
async def get_payment_source_status(db: DBSession, _user: CurrentUser) -> PaymentSourceStatusResponse:
    """「转账通知以插件结果为准」开关当前状态 + 已安装的转账通知插件（只读）。

    生效与否完全以服务器当下配置为准；修改仍走 ``PATCH /api/system/settings`` 的
    ``log_retention.payment_source_plugin_authoritative``（30 秒内生效）。旧服务器没有这个接口（404），
    客户端据此不显示插件详情里的开关区。
    """

    return PaymentSourceStatusResponse(**(await plugin_funds_cards.status(db)))


@router.get("/api/plugin-ui/cards", response_model=PluginUICardsResponse)
async def list_plugin_ui_cards(
    db: DBSession,
    _user: CurrentUser,
    slot: str = Query(..., description="plugin.overview / account.detail / dashboard / interaction.rule_editor"),
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


# ── 插件页面与菜单（ui.pages / ui.actions，Plugin API 1.4）──


def _page_error(exc: plugin_ui_pages.PluginPageError) -> HTTPException:
    return _bad(exc.code, str(exc), exc.status)


@router.get("/api/plugin-ui/entries", response_model=PluginUIEntriesResponse)
async def list_plugin_ui_entries(
    db: DBSession,
    _user: CurrentUser,
    menu: str = Query(..., description="plugin.detail / account.detail"),
    plugin_key: str | None = None,
    account_id: int | None = None,
) -> PluginUIEntriesResponse:
    """插件详情 / 账号详情的「插件页面与操作」入口。插件没有声明 ui.pages / ui.actions 时返回空列表（不显示任何入口）。"""

    try:
        pages, actions, warnings = await plugin_ui_pages.list_entries(
            db, menu=menu, plugin_key=plugin_key, account_id=account_id
        )
    except plugin_ui_pages.PluginPageError as exc:
        raise _page_error(exc) from exc
    return PluginUIEntriesResponse(menu=menu, pages=pages, actions=actions, warnings=warnings)


@router.post("/api/plugin-ui/pages/{plugin_key}/{page_id}/session", response_model=PluginUIPageSession)
async def open_plugin_ui_page(
    plugin_key: str,
    page_id: str,
    payload: PluginUIPageSessionRequest,
    db: DBSession,
    _user: CurrentUser,
) -> PluginUIPageSession:
    """打开插件页面：换一个 1 小时有效的页面地址（签名 token，不含登录凭据）。"""

    try:
        result = await plugin_ui_pages.open_page_session(
            db, plugin_key=plugin_key, page_id=page_id, account_id=payload.account_id
        )
    except plugin_ui_pages.PluginPageError as exc:
        raise _page_error(exc) from exc
    return PluginUIPageSession(**result)


@router.get("/api/plugin-ui/page-assets/{token}/{path:path}", include_in_schema=False)
async def plugin_ui_page_asset(token: str, path: str, db: DBSession) -> Response:
    """插件页面文件（只读插件目录，严格 CSP，禁止联网）。凭 token 访问，不读取登录态。"""

    headers = plugin_ui_pages.asset_headers()
    try:
        data, content_type = await plugin_ui_pages.read_asset(db, token=token, path=path)
    except plugin_ui_pages.PluginPageError as exc:
        return Response(content=str(exc), status_code=exc.status, media_type="text/plain; charset=utf-8", headers=headers)
    return Response(content=data, media_type=content_type, headers=headers)


@router.post("/api/plugin-ui/pages/{plugin_key}/{page_id}/bridge", response_model=PluginUIBridgeResponse)
async def plugin_ui_page_bridge(
    plugin_key: str,
    page_id: str,
    payload: PluginUIBridgeRequest,
    db: DBSession,
    user: CurrentUser,
) -> PluginUIBridgeResponse:
    """页面 Bridge 请求（宿主客户端转发）。按页面声明的 bridge 权限逐次校验；失败以 ok=false 返回给页面。"""

    from ..services import audit

    try:
        result = await plugin_ui_pages.bridge_call(
            db,
            plugin_key=plugin_key,
            page_id=page_id,
            method=payload.method,
            params=payload.params,
            account_id=payload.account_id,
        )
    except plugin_ui_pages.PluginPageError as exc:
        if exc.code == "PLUGIN_PAGE_NOT_FOUND":
            raise _page_error(exc) from exc
        return PluginUIBridgeResponse(ok=False, error={"code": exc.code, "message": str(exc)})
    except TimeoutError:
        return PluginUIBridgeResponse(
            ok=False, error={"code": "PLUGIN_ACTION_TIMEOUT", "message": "插件处理超时（10 秒），结果未知"}
        )
    except Exception as exc:  # noqa: BLE001
        return PluginUIBridgeResponse(
            ok=False, error={"code": "PLUGIN_ACTION_FAILED", "message": f"插件处理失败：{type(exc).__name__}"}
        )
    if payload.method == "action.invoke":
        await audit.write(
            db,
            user.id,
            "plugin.ui.page_action",
            target=f"plugin:{plugin_key}/page:{page_id}",
            detail={"action_id": str(payload.params.get("id") or ""), "account_id": payload.account_id},
        )
        await db.commit()
    return PluginUIBridgeResponse(ok=True, result=result)


@router.post("/api/plugin-ui/actions/{plugin_key}/{action_id}", response_model=PluginUIMenuActionResponse)
async def run_plugin_ui_menu_action(
    plugin_key: str,
    action_id: str,
    payload: PluginUIMenuActionRequest,
    db: DBSession,
    user: CurrentUser,
) -> PluginUIMenuActionResponse:
    """执行菜单项（插件 on_ui_action，10 秒，context.menu 为菜单位置）。不自动重试。"""

    from ..services import audit

    try:
        action = await plugin_ui_pages.find_menu_action(db, plugin_key, action_id, payload.menu)
        if action.get("page"):
            raise plugin_ui_pages.PluginPageError("这个菜单项用于打开页面", "PLUGIN_ACTION_IS_PAGE", 400)
        result = await plugin_ui_pages.invoke_ui_action(
            db,
            plugin_key=plugin_key,
            action_id=action_id,
            account_id=payload.account_id,
            context={"menu": action["menu"], "page_id": None, "payload": {}},
        )
    except plugin_ui_pages.PluginPageError as exc:
        raise _page_error(exc) from exc
    except TimeoutError as exc:
        raise _bad("PLUGIN_ACTION_TIMEOUT", "插件处理超时（10 秒），结果未知，请刷新确认", 504) from exc
    except Exception as exc:  # noqa: BLE001
        raise _bad("PLUGIN_ACTION_FAILED", f"插件处理失败：{type(exc).__name__}", 400) from exc
    await audit.write(
        db,
        user.id,
        "plugin.ui.menu_action",
        target=f"plugin:{plugin_key}/action:{action_id}",
        detail={"menu": action["menu"], "account_id": payload.account_id},
    )
    await db.commit()
    return PluginUIMenuActionResponse(**result)
