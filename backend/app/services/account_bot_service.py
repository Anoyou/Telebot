"""账号绑定 Bot 的配置、授权用户与 Bot API 轻量封装。"""

from __future__ import annotations

import logging
import re
from html import escape
from typing import Any

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..crypto import decrypt_str, encrypt_str
from ..db.models.account import Account
from ..db.models.account_bot import (
    ACCOUNT_BOT_ROLE_ADMIN,
    ACCOUNT_BOT_ROLE_OPERATOR,
    ACCOUNT_BOT_ROLE_VIEWER,
    ACCOUNT_BOT_ROLES,
    ACCOUNT_BOT_STATUS_DISABLED,
    ACCOUNT_BOT_STATUS_ERROR,
    AccountBot,
    AccountBotUser,
)
from ..schemas.account_bot import (
    AccountBotConfigResponse,
    AccountBotConfigUpdate,
    AccountBotRemotePluginPolicy,
    AccountBotUserCreate,
    AccountBotUserUpdate,
)

log = logging.getLogger(__name__)

BOT_API_BASE = "https://api.telegram.org"
BOT_API_TIMEOUT = httpx.Timeout(connect=5.0, read=35.0, write=10.0, pool=5.0)

ROLE_RANK = {
    ACCOUNT_BOT_ROLE_VIEWER: 0,
    ACCOUNT_BOT_ROLE_OPERATOR: 1,
    ACCOUNT_BOT_ROLE_ADMIN: 2,
}


def role_allows(role: str, required: str) -> bool:
    """角色权限比较：viewer < operator < admin。"""

    return ROLE_RANK.get(role, -1) >= ROLE_RANK.get(required, 99)


def _bad(code: str, message: str, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def sanitize_bot_error(exc: BaseException | str, *, token: str | None = None) -> str:
    """把 Bot API 错误脱敏，避免把 token、URL、路径打到 DB 或前端。"""

    text = str(exc)
    if token:
        text = text.replace(token, "***")
    text = text.replace("https://api.telegram.org", "Bot API")
    text = re.sub(r"Bearer\s+[A-Za-z0-9._\-]+", "Bearer ***", text)
    text = re.sub(r"sk-[A-Za-z0-9_\-]{12,}", "sk-***", text)
    text = re.sub(r"(?<!\w)/(?:Users|private|var|tmp|opt|home)/[^\s\"']+", "[path]", text)
    if len(text) > 500:
        text = text[:500] + "..."
    return text or type(exc).__name__


def default_remote_plugin_policy() -> dict[str, bool]:
    return {
        "enabled": False,
        "install": False,
        "update": False,
        "uninstall": False,
        "enable_disable": False,
    }


def normalize_remote_plugin_policy(raw: Any) -> dict[str, bool]:
    base = default_remote_plugin_policy()
    if not isinstance(raw, dict):
        return base
    for key in base:
        if key in raw:
            base[key] = bool(raw[key])
    return base


def config_to_response(row: AccountBot, *, account_id: int | None = None) -> AccountBotConfigResponse:
    remote_plugin_policy = normalize_remote_plugin_policy(row.remote_plugin_policy)
    return AccountBotConfigResponse(
        account_id=int(account_id or row.account_id),
        enabled=bool(row.enabled),
        status=row.status or ACCOUNT_BOT_STATUS_DISABLED,
        has_token=bool(row.bot_token_enc),
        username=row.username,
        remote_plugin_policy=AccountBotRemotePluginPolicy(**remote_plugin_policy),
        last_update_id=row.last_update_id,
        last_error=row.last_error,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def ensure_account(db: AsyncSession, aid: int) -> Account:
    acc = await db.get(Account, aid)
    if acc is None:
        raise _bad("ACCOUNT_NOT_FOUND", "账号不存在", 404)
    return acc


async def get_bot_config(db: AsyncSession, aid: int, *, create: bool = True) -> AccountBot:
    await ensure_account(db, aid)
    row = (
        await db.execute(select(AccountBot).where(AccountBot.account_id == aid))
    ).scalar_one_or_none()
    if row is None:
        if not create:
            raise _bad("ACCOUNT_BOT_NOT_FOUND", "该账号尚未配置 Bot", 404)
        row = AccountBot(
            account_id=aid,
            enabled=False,
            status=ACCOUNT_BOT_STATUS_DISABLED,
            remote_plugin_policy=default_remote_plugin_policy(),
        )
        db.add(row)
        await db.flush()
    elif not row.remote_plugin_policy:
        row.remote_plugin_policy = default_remote_plugin_policy()
    return row


async def update_bot_config(
    db: AsyncSession,
    aid: int,
    payload: AccountBotConfigUpdate,
) -> AccountBot:
    row = await get_bot_config(db, aid, create=True)
    if payload.clear_token:
        row.bot_token_enc = None
        row.username = None
    if payload.bot_token:
        row.bot_token_enc = encrypt_str(payload.bot_token)
        row.last_error = None
        try:
            me = await get_me(payload.bot_token)
            username = me.get("username")
            if isinstance(username, str):
                row.username = username
        except Exception as exc:  # noqa: BLE001
            row.status = ACCOUNT_BOT_STATUS_ERROR
            row.last_error = sanitize_bot_error(exc, token=payload.bot_token)
    if payload.enabled is not None:
        row.enabled = bool(payload.enabled)
        if not row.enabled:
            row.status = ACCOUNT_BOT_STATUS_DISABLED
    if payload.remote_plugin_policy is not None:
        current = normalize_remote_plugin_policy(row.remote_plugin_policy)
        patch = payload.remote_plugin_policy.model_dump(exclude_unset=True)
        merged = {**current, **{k: bool(v) for k, v in patch.items()}}
        row.remote_plugin_policy = normalize_remote_plugin_policy(merged)
    if row.enabled and not row.bot_token_enc:
        raise _bad("ACCOUNT_BOT_TOKEN_REQUIRED", "启用 Bot 前必须填写 Bot Token", 422)
    if row.enabled:
        try:
            decrypt_bot_token(row)
        except HTTPException:
            row.enabled = False
            row.status = ACCOUNT_BOT_STATUS_ERROR
            row.last_error = "Bot Token 解密失败，请重新保存"
            raise
    await db.flush()
    return row


async def list_bot_users(db: AsyncSession, aid: int) -> list[AccountBotUser]:
    await ensure_account(db, aid)
    rows = (
        await db.execute(
            select(AccountBotUser)
            .where(AccountBotUser.account_id == aid)
            .order_by(AccountBotUser.id.asc())
        )
    ).scalars().all()
    return list(rows)


async def get_bot_user(db: AsyncSession, aid: int, uid: int) -> AccountBotUser:
    row = await db.get(AccountBotUser, uid)
    if row is None or row.account_id != aid:
        raise _bad("ACCOUNT_BOT_USER_NOT_FOUND", "授权用户不存在", 404)
    return row


async def find_bot_user(
    db: AsyncSession, aid: int, tg_user_id: int
) -> AccountBotUser | None:
    return (
        await db.execute(
            select(AccountBotUser).where(
                AccountBotUser.account_id == aid,
                AccountBotUser.tg_user_id == tg_user_id,
            )
        )
    ).scalar_one_or_none()


async def create_bot_user(
    db: AsyncSession,
    aid: int,
    payload: AccountBotUserCreate,
) -> AccountBotUser:
    await ensure_account(db, aid)
    if payload.role not in ACCOUNT_BOT_ROLES:
        raise _bad("ACCOUNT_BOT_ROLE_INVALID", "role 只能是 viewer / operator / admin", 422)
    row = AccountBotUser(
        account_id=aid,
        tg_user_id=payload.tg_user_id,
        display_name=payload.display_name,
        role=payload.role,
        notify_enabled=payload.notify_enabled,
        enabled=payload.enabled,
    )
    db.add(row)
    try:
        await db.flush()
    except IntegrityError as exc:
        raise _bad("ACCOUNT_BOT_USER_DUPLICATE", "该 Telegram 用户已授权", 409) from exc
    return row


async def update_bot_user(
    db: AsyncSession,
    aid: int,
    uid: int,
    payload: AccountBotUserUpdate,
) -> AccountBotUser:
    row = await get_bot_user(db, aid, uid)
    data = payload.model_dump(exclude_unset=True)
    if "role" in data and data["role"] not in ACCOUNT_BOT_ROLES:
        raise _bad("ACCOUNT_BOT_ROLE_INVALID", "role 只能是 viewer / operator / admin", 422)
    for key, value in data.items():
        setattr(row, key, value)
    await db.flush()
    return row


async def delete_bot_user(db: AsyncSession, aid: int, uid: int) -> None:
    row = await get_bot_user(db, aid, uid)
    await db.delete(row)
    await db.flush()


def decrypt_bot_token(row: AccountBot) -> str:
    if not row.bot_token_enc:
        raise _bad("ACCOUNT_BOT_TOKEN_REQUIRED", "该账号未配置 Bot Token", 422)
    try:
        return decrypt_str(row.bot_token_enc)
    except ValueError as exc:
        raise _bad("ACCOUNT_BOT_TOKEN_DECRYPT_FAILED", "Bot Token 解密失败，请重新保存", 422) from exc


async def call_bot_api(
    token: str,
    method: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: httpx.Timeout | None = None,
) -> dict[str, Any]:
    """调用 Bot API；只在这里拼接 token URL。"""

    url = f"{BOT_API_BASE}/bot{token}/{method}"
    async with httpx.AsyncClient(timeout=timeout or BOT_API_TIMEOUT) as client:
        resp = await client.post(url, json=payload or {})
    data = resp.json() if resp.content else {}
    if resp.status_code >= 400 or not data.get("ok", False):
        desc = data.get("description") or f"HTTP {resp.status_code}"
        raise RuntimeError(desc)
    result = data.get("result")
    return result if isinstance(result, dict) else {"result": result}


async def get_me(token: str) -> dict[str, Any]:
    return await call_bot_api(token, "getMe", {}, timeout=httpx.Timeout(10.0))


async def send_message(
    token: str,
    chat_id: int,
    text: str,
    *,
    reply_markup: dict[str, Any] | None = None,
    parse_mode: str | None = "HTML",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text[:4000],
        "disable_web_page_preview": True,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    return await call_bot_api(token, "sendMessage", payload)


async def edit_message(
    token: str,
    chat_id: int,
    message_id: int,
    text: str,
    *,
    reply_markup: dict[str, Any] | None = None,
    parse_mode: str | None = "HTML",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text[:4000],
        "disable_web_page_preview": True,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    return await call_bot_api(token, "editMessageText", payload)


async def answer_callback(
    token: str,
    callback_query_id: str,
    *,
    text: str | None = None,
    show_alert: bool = False,
) -> None:
    payload: dict[str, Any] = {"callback_query_id": callback_query_id, "show_alert": show_alert}
    if text:
        payload["text"] = text[:200]
    await call_bot_api(token, "answerCallbackQuery", payload, timeout=httpx.Timeout(10.0))


def html_text(value: Any) -> str:
    """Bot HTML 消息统一转义。"""

    return escape("" if value is None else str(value), quote=False)
