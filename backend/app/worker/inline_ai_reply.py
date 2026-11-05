"""UserBot 侧的 AI 回复「inline 代发」（0.119.0）。

账号本人发 ``,ai 问题`` 时，若服务器参数「AI 回复方式」= inline 代发：

1. 读取本账号绑定的交互 Bot（getMe 需 ``supports_inline_queries``），写一次性令牌；
2. ``client.inline_query(bot, "tpai:<令牌>", entity=chat)``（GetInlineBotResultsRequest）拿到占位结果，
   ``result.click(chat, reply_to=…, hide_via=False)``（SendInlineBotResultRequest）发出
   「用户名 via @交互Bot」消息；
3. 等交互 Bot 的 ``chosen_inline_result`` 带回 ``inline_message_id``，之后所有编辑都由交互 Bot
   ``editMessageText(inline_message_id, …)`` 完成，最终答案优先 Rich Message（表格走 core
   紧凑 / 边框 / 斑马纹逻辑），失败退 HTML，再退纯文本。

任一步失败（群禁止 inline bot、Bot 没开 inline、拿不到结果、等不到 inline_message_id）都返回
``None`` 并写明原因，由调用方走原来的普通回复路径；已经发出的占位会先删掉，保证不重复发送。
"""

from __future__ import annotations

import logging
from typing import Any

from ..services import account_bot_service, inline_ai_relay

log = logging.getLogger(__name__)

RICH_MARKDOWN_LIMIT = 32000
CHOSEN_WAIT_SECONDS = 8.0
_REMOVE_KEYBOARD: dict[str, Any] = {"inline_keyboard": []}

# Telethon 的错误类名 → 诊断原因（不依赖 import，便于旧版本 / 测试桩）。
_FALLBACK_ERRORS = {
    "ChatSendInlineForbiddenError": "chat_send_inline_forbidden",
    "BotInlineDisabledError": "bot_inline_disabled",
    "InlineBotRequiredError": "inline_bot_required",
    "BotResponseTimeoutError": "bot_response_timeout",
    "ChatWriteForbiddenError": "chat_write_forbidden",
    "ChatRestrictedError": "chat_restricted",
    "UserBannedInChannelError": "user_banned_in_channel",
    "InlineResultExpiredError": "inline_result_expired",
    "SlowModeWaitError": "slow_mode",
}


def _is_not_modified(exc: BaseException) -> bool:
    return "message is not modified" in str(exc).lower()


def _message_id_of(sent: Any) -> int | None:
    for value in (getattr(sent, "id", None), getattr(getattr(sent, "message", None), "id", None)):
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            continue
    return None


class InlineReplyHandle:
    """一条已经发出的 inline 代发消息；编辑全部经交互 Bot 完成。"""

    def __init__(
        self,
        *,
        client: Any,
        bot_token: str,
        inline_message_id: str,
        chat_id: Any,
        message_id: int | None,
        account_id: int,
    ) -> None:
        self.client = client
        self.bot_token = bot_token
        self.inline_message_id = inline_message_id
        self.chat_id = chat_id
        self.message_id = message_id
        self.account_id = account_id
        self.finalized = False

    async def edit_text(self, text: str, *, parse_mode: str | None = None, final: bool = True) -> bool:
        """普通文本编辑（错误提示等）；``final`` 时去掉占位按钮。HTML 被拒退纯文本。"""

        body = str(text or "").strip() or "（空）"
        mode = "HTML" if str(parse_mode or "").lower() in {"html", "htm"} else None
        markup = _REMOVE_KEYBOARD if final else None
        for current_mode in ([mode, None] if mode else [None]):
            try:
                await account_bot_service.edit_inline_message(
                    self.bot_token,
                    self.inline_message_id,
                    body,
                    reply_markup=markup,
                    parse_mode=current_mode,
                )
                if final:
                    self.finalized = True
                return True
            except Exception as exc:  # noqa: BLE001
                if _is_not_modified(exc):
                    if final:
                        self.finalized = True
                    return True
                log.info(
                    "[ai-inline] edit inline message failed account=%s mode=%s: %s",
                    self.account_id,
                    current_mode or "plain",
                    account_bot_service.sanitize_bot_error(exc, token=self.bot_token),
                )
        return False

    async def finish(self, *, markdown: str | None, html: str | None, plain: str) -> str:
        """最终答案：Rich Message → HTML → 纯文本；返回实际使用的格式（``rich`` / ``html`` / ``plain`` / ``failed``）。"""

        if markdown and markdown.strip():
            from ..services import render_profile

            rich = {"markdown": markdown[:RICH_MARKDOWN_LIMIT]}
            action = render_profile.apply_outbound_core(
                {"rich_message": rich, "compact_tables": True},
                account_id=self.account_id,
            )
            try:
                await account_bot_service.edit_inline_rich_message(
                    self.bot_token,
                    self.inline_message_id,
                    action.get("rich_message") or rich,
                    reply_markup=_REMOVE_KEYBOARD,
                )
                self.finalized = True
                return "rich"
            except Exception as exc:  # noqa: BLE001
                if _is_not_modified(exc):
                    self.finalized = True
                    return "rich"
                log.info(
                    "[ai-inline] rich edit failed, falling back to HTML account=%s: %s",
                    self.account_id,
                    account_bot_service.sanitize_bot_error(exc, token=self.bot_token),
                )
        if html and await self.edit_text(html, parse_mode="html"):
            return "html"
        if await self.edit_text(plain):
            return "plain"
        return "failed"

    async def discard(self) -> None:
        """删掉代发消息（UserBot 删除自己发出的消息）。"""

        if self.message_id is None:
            return
        try:
            await self.client.delete_messages(self.chat_id, [self.message_id])
        except Exception:  # noqa: BLE001
            log.debug("[ai-inline] discard inline message failed", exc_info=True)

    def proxy(self, event: Any) -> InlineProxyEvent:
        return InlineProxyEvent(event, self)


class InlineProxyEvent:
    """把 ``event.edit`` 改为编辑 inline 代发消息；``delete`` 变成空操作，其余属性透传原事件。

    AI 流程里的错误提示（``event.edit("✗ …")``）因此自动落到 inline 消息上。
    """

    def __init__(self, event: Any, handle: InlineReplyHandle) -> None:
        object.__setattr__(self, "_event", event)
        object.__setattr__(self, "_inline_handle", handle)

    def __getattr__(self, name: str) -> Any:
        return getattr(object.__getattribute__(self, "_event"), name)

    async def edit(self, text: Any = None, *args: Any, parse_mode: Any = None, **kwargs: Any) -> Any:
        handle: InlineReplyHandle = object.__getattribute__(self, "_inline_handle")
        await handle.edit_text(str(text or ""), parse_mode=parse_mode if isinstance(parse_mode, str) else None)
        return None

    async def delete(self, *args: Any, **kwargs: Any) -> None:
        return None


async def _bot_username(bot_token: str) -> tuple[str | None, str]:
    try:
        caps = await account_bot_service.get_bot_capabilities(bot_token)
    except Exception as exc:  # noqa: BLE001
        return None, f"bot_get_me_failed:{type(exc).__name__}"
    if caps.get("supports_inline_queries") is False:
        return None, "bot_inline_disabled"
    username = str(caps.get("username") or "").strip().lstrip("@")
    if not username:
        return None, "bot_username_missing"
    return username, ""


async def _load_bot_token(account_id: int) -> str | None:
    try:
        from ..db.base import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            return await account_bot_service.get_interaction_bot_token(db, account_id)
    except Exception:  # noqa: BLE001
        log.debug("[ai-inline] load interaction bot token failed account=%s", account_id, exc_info=True)
        return None


async def start_inline_reply(
    client: Any,
    *,
    account_id: int,
    owner_user_id: int | None,
    chat_id: Any,
    reply_to_message_id: int | None = None,
    placeholder: str = inline_ai_relay.DEFAULT_PLACEHOLDER,
    parse_mode: str | None = None,
    wait_timeout: float = CHOSEN_WAIT_SECONDS,
    bot_token: str | None = None,
) -> tuple[InlineReplyHandle | None, str]:
    """发出 inline 代发占位；成功返回 ``(handle, "")``，失败返回 ``(None, 原因)`` 且不留下任何消息。"""

    if owner_user_id is None:
        return None, "owner_unknown"
    if chat_id is None:
        return None, "chat_unknown"
    token_value = bot_token or await _load_bot_token(account_id)
    if not token_value:
        return None, "no_interaction_bot"
    username, reason = await _bot_username(token_value)
    if username is None:
        return None, reason

    from .command import acquire_direct_userbot_action_rate_limit

    allowed, _ = await acquire_direct_userbot_action_rate_limit(client, account_id, "send_message", chat_id)
    if not allowed:
        return None, "rate_limited"

    relay_token = await inline_ai_relay.issue_token(
        account_id=account_id,
        owner_user_id=int(owner_user_id),
        chat_id=chat_id if isinstance(chat_id, int) else None,
        placeholder=placeholder,
        parse_mode=parse_mode,
    )
    sent: Any = None
    try:
        results = await client.inline_query(username, f"{inline_ai_relay.QUERY_PREFIX}{relay_token}", entity=chat_id)
        if not results:
            await inline_ai_relay.revoke_token(relay_token)
            return None, "inline_no_results"
        sent = await results[0].click(chat_id, reply_to=reply_to_message_id, hide_via=False)
    except Exception as exc:  # noqa: BLE001
        await inline_ai_relay.revoke_token(relay_token)
        reason = _FALLBACK_ERRORS.get(type(exc).__name__, f"inline_send_failed:{type(exc).__name__}")
        log.info("[ai-inline] inline send failed account=%s reason=%s", account_id, reason)
        return None, reason

    message_id = _message_id_of(sent)
    inline_message_id = await inline_ai_relay.wait_inline_message_id(relay_token, timeout=wait_timeout)
    if not inline_message_id:
        # 没开 inline 反馈（/setinlinefeedback）或交互 Bot 没在运行：删掉占位，交还普通回复路径。
        await inline_ai_relay.revoke_token(relay_token)
        await inline_ai_relay.mark_feedback_missing(account_id)
        handle = InlineReplyHandle(
            client=client,
            bot_token=token_value,
            inline_message_id="",
            chat_id=chat_id,
            message_id=message_id,
            account_id=account_id,
        )
        await handle.discard()
        return None, "chosen_inline_result_timeout"
    return (
        InlineReplyHandle(
            client=client,
            bot_token=token_value,
            inline_message_id=inline_message_id,
            chat_id=chat_id,
            message_id=message_id,
            account_id=account_id,
        ),
        "",
    )


__all__ = ["InlineProxyEvent", "InlineReplyHandle", "plugin_inline_via_bot", "start_inline_reply"]


_SUPERGROUP_MIN_ID = -(10**12)


async def _bot_in_chat(account_id: int, chat_id: int) -> bool:
    """本账号的交互 Bot 是否在这个会话里（getChatMember，结果有短缓存）；查不到一律当作不在。"""

    if chat_id > 0:
        return False
    token = await _load_bot_token(account_id)
    if not token:
        return False
    try:
        caps = await account_bot_service.get_bot_capabilities(token)
        bot_id = caps.get("bot_id")
        if not bot_id:
            return False
        rows = await account_bot_service.inspect_bot_chat_memberships(token, int(bot_id), [int(chat_id)])
    except Exception:  # noqa: BLE001
        log.debug("[ai-inline] bot membership probe failed account=%s", account_id, exc_info=True)
        return False
    return bool(rows) and rows[0].get("status") == "joined"


async def plugin_inline_via_bot(
    client: Any,
    *,
    account_id: int,
    chat_id: Any,
    requester_user_id: Any = None,
    reply_to_message_id: Any = None,
    delete_message_id: Any = None,
    placeholder: str | None = None,
    parse_mode: str | None = "html",
    respect_setting: bool = True,
    same_message_ids: bool | None = None,
) -> dict[str, Any]:
    """``ctx.messages.inline_via_bot`` 的实现（能力 ``messages.inline_via_bot``，0.119.0）。

    - 遵循服务器参数「AI 回复方式」、「插件触发由 Bot 直接回复」（默认开，开着就返回 ``bot_reply_preferred``）
      与「inline 代发后删除指令消息」（``respect_setting=False`` 全部跳过）；
    - 只接受账号本人（``requester_user_id`` = UserBot 自己的 user_id）触发，防止群友借机以账号名义发消息；
    - 交互 Bot 事件里的 message_id 只在超级群 / 频道（-100…）与 UserBot 一致；普通群 / 私聊默认不引用、
      不删除（``same_message_ids=True`` 可显式声明 ID 一致，例如 UserBot 来源的事件）。
    返回 ``{"ok", "inline_message_id", "message_id", "deleted_message", "reason"}``；``ok=False`` 时没有发出任何消息。
    """

    from .command import get_command_context

    def _fail(reason: str) -> dict[str, Any]:
        return {"ok": False, "reason": reason, "inline_message_id": None, "message_id": None, "deleted_message": False}

    if client is None:
        return _fail("userbot_unavailable")
    try:
        chat = int(chat_id)
    except (TypeError, ValueError):
        return _fail("chat_unknown")
    delete_command = True
    prefer_bot = False
    if respect_setting:
        pref = await inline_ai_relay.read_reply_preference()
        if pref.get("style") != inline_ai_relay.REPLY_STYLE_INLINE:
            return _fail("style_normal")
        prefer_bot = bool(pref.get("ai_plugin_reply_via_bot", True))
        delete_command = bool(pref.get("delete_command", True))
    ctx = get_command_context()
    self_id = getattr(ctx, "self_tg_user_id", None) if ctx is not None else None
    if self_id is None:
        try:
            me = await client.get_me()
            self_id = getattr(me, "id", None)
        except Exception:  # noqa: BLE001
            self_id = None
    if self_id is None:
        return _fail("owner_unknown")
    try:
        if requester_user_id is None or int(requester_user_id) != int(self_id):
            return _fail("not_owner")
    except (TypeError, ValueError):
        return _fail("not_owner")
    # 0.120.1：交互 Bot 就在这个群里时默认由 Bot 直接回复，不用账号代发（Bot 不在群里才代发）。
    if prefer_bot and await _bot_in_chat(account_id, chat):
        return _fail("bot_reply_preferred")
    ids_trusted = same_message_ids if same_message_ids is not None else chat <= _SUPERGROUP_MIN_ID

    def _id(value: Any) -> int | None:
        try:
            return int(value) if value is not None and ids_trusted else None
        except (TypeError, ValueError):
            return None

    handle, reason = await start_inline_reply(
        client,
        account_id=account_id,
        owner_user_id=int(self_id),
        chat_id=chat,
        reply_to_message_id=_id(reply_to_message_id),
        placeholder=str(placeholder or inline_ai_relay.DEFAULT_PLACEHOLDER),
        parse_mode=parse_mode,
    )
    if handle is None:
        return _fail(reason)
    deleted = False
    target = _id(delete_message_id)
    if delete_command and target is not None:
        try:
            await client.delete_messages(chat, [target])
            deleted = True
        except Exception:  # noqa: BLE001
            log.debug("[ai-inline] plugin delete trigger message failed", exc_info=True)
    return {
        "ok": True,
        "reason": "",
        "inline_message_id": handle.inline_message_id,
        "message_id": handle.message_id,
        "deleted_message": deleted,
    }
