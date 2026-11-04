"""AI 回复「inline 代发」的 Bot 侧中继（0.119.0）。

非 Premium 的 UserBot 发不了 Rich Message，但交互 Bot 可以。inline 代发让 UserBot 借本账号
绑定的交互 Bot 的 inline 模式发出一条「用户名 via @交互Bot」的消息，再由交互 Bot 通过
``editMessageText(inline_message_id, rich_message)`` 把它编辑成富文本答案：

1. UserBot（worker）调用 :func:`issue_token` 写一次性令牌（Redis，短 TTL，只认账号本人
   user_id），然后 ``GetInlineBotResults(query="tpai:<令牌>")`` + ``SendInlineBotResult``；
2. 交互 Bot 收到 ``inline_query``：:func:`handle_interaction_update` 校验令牌与发起人，
   ``answerInlineQuery`` 返回一条带按钮的占位（没有 inline keyboard 时
   ``chosen_inline_result`` 不带 ``inline_message_id``），``cache_time=0``、``is_personal``；
3. 交互 Bot 收到 ``chosen_inline_result``：把 ``inline_message_id`` 推到该令牌的队列并作废令牌；
   worker 端 :func:`wait_inline_message_id` 取到后开始编辑。

需要在 BotFather 为交互 Bot 打开 inline 模式（``/setinline``）与 inline 反馈
（``/setinlinefeedback``）。inline 反馈无法通过 getMe 读取，这里按最近一次实际结果记录
（:func:`read_feedback_status`），供 Web / App 诊断提示。
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from typing import Any

from ..redis_client import get_redis

log = logging.getLogger(__name__)

QUERY_PREFIX = "tpai:"
NOOP_CALLBACK_DATA = "tpai:noop"
TOKEN_TTL_SECONDS = 120
CHOSEN_TTL_SECONDS = 120
FEEDBACK_TTL_SECONDS = 30 * 24 * 3600
DEFAULT_PLACEHOLDER = "🤔 思考中…"
PLACEHOLDER_BUTTON_TEXT = "⏳ AI 生成中…"
REPLY_STYLE_NORMAL = "normal"
REPLY_STYLE_INLINE = "inline"
REPLY_STYLES = (REPLY_STYLE_NORMAL, REPLY_STYLE_INLINE)

FEEDBACK_OK = "ok"
FEEDBACK_MISSING = "missing"

_TOKEN_KEY = "tp:inline_ai:tok:{token}"
_CHOSEN_KEY = "tp:inline_ai:chosen:{token}"
_FEEDBACK_KEY = "tp:inline_ai:feedback:{aid}"


def _token_key(token: str) -> str:
    return _TOKEN_KEY.format(token=token)


def _chosen_key(token: str) -> str:
    return _CHOSEN_KEY.format(token=token)


def _feedback_key(aid: int) -> str:
    return _FEEDBACK_KEY.format(aid=int(aid))


def normalize_reply_style(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("value")
    text = str(value or "").strip().lower()
    return text if text in REPLY_STYLES else REPLY_STYLE_NORMAL


def normalize_delete_command(value: Any) -> bool:
    if isinstance(value, dict):
        value = value.get("enabled", value.get("value", True))
    if value is None:
        return True
    return bool(value)


def parse_query_token(query: Any) -> str | None:
    """``tpai:<令牌>`` → 令牌；不是本功能的查询返回 ``None``。"""

    text = str(query or "").strip()
    if not text.startswith(QUERY_PREFIX):
        return None
    token = text[len(QUERY_PREFIX):].strip()
    if not token or len(token) > 64 or not all(ch.isalnum() or ch in "-_" for ch in token):
        return None
    return token


def result_id_for(token: str) -> str:
    # InlineQueryResult.id 最长 64 字节。
    return f"tpai-{token}"[:64]


async def issue_token(
    *,
    account_id: int,
    owner_user_id: int,
    chat_id: int | None,
    placeholder: str = DEFAULT_PLACEHOLDER,
    parse_mode: str | None = None,
    redis: Any = None,
) -> str:
    """生成一次性令牌（只接受账号本人 ``owner_user_id`` 发起的 inline 查询）。"""

    token = secrets.token_urlsafe(18)
    payload = {
        "account_id": int(account_id),
        "owner_user_id": int(owner_user_id),
        "chat_id": int(chat_id) if chat_id is not None else None,
        "placeholder": str(placeholder or DEFAULT_PLACEHOLDER)[:512],
        "parse_mode": "HTML" if str(parse_mode or "").lower() == "html" else None,
        "created_at": int(time.time()),
    }
    client = redis or get_redis()
    await client.set(_token_key(token), json.dumps(payload, ensure_ascii=False), ex=TOKEN_TTL_SECONDS)
    return token


async def revoke_token(token: str, *, redis: Any = None) -> None:
    client = redis or get_redis()
    try:
        await client.delete(_token_key(token), _chosen_key(token))
    except Exception:  # noqa: BLE001
        log.debug("inline ai token revoke failed", exc_info=True)


async def _read_token(client: Any, token: str) -> dict[str, Any] | None:
    raw = await client.get(_token_key(token))
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="ignore")
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _token_matches(data: dict[str, Any] | None, *, aid: int, user_id: int | None) -> bool:
    if not data or user_id is None:
        return False
    try:
        return int(data.get("account_id")) == int(aid) and int(data.get("owner_user_id")) == int(user_id)
    except (TypeError, ValueError):
        return False


def placeholder_result(token: str, data: dict[str, Any]) -> dict[str, Any]:
    """占位结果：必须带 inline keyboard，否则 chosen_inline_result 不给 inline_message_id。"""

    content: dict[str, Any] = {
        "message_text": str(data.get("placeholder") or DEFAULT_PLACEHOLDER),
        "link_preview_options": {"is_disabled": True},
    }
    if data.get("parse_mode") == "HTML":
        content["parse_mode"] = "HTML"
    return {
        "type": "article",
        "id": result_id_for(token),
        "title": "TelePilot AI",
        "description": "AI 回答生成中",
        "input_message_content": content,
        "reply_markup": {"inline_keyboard": [[{"text": PLACEHOLDER_BUTTON_TEXT, "callback_data": NOOP_CALLBACK_DATA}]]},
    }


def _chosen_inline_message_id(native_raw: Any) -> str:
    raw = native_raw if isinstance(native_raw, dict) else {}
    chosen = raw.get("chosen_inline_result") if isinstance(raw.get("chosen_inline_result"), dict) else {}
    return str(chosen.get("inline_message_id") or "").strip()


async def handle_interaction_update(incoming: Any, *, redis: Any = None) -> bool:
    """交互 Bot 更新入口：属于 inline 代发的更新在这里处理完并返回 ``True``（不再进插件 / 规则）。"""

    from . import account_bot_service

    aid = int(getattr(incoming, "account_id", 0) or 0)
    bot_token = str(getattr(incoming, "token", "") or "")
    user_id = getattr(incoming, "user_id", None)

    callback_id = getattr(incoming, "callback_id", None)
    if callback_id:
        if str(getattr(incoming, "callback_data", "") or "") != NOOP_CALLBACK_DATA:
            return False
        try:
            await account_bot_service.answer_callback(bot_token, str(callback_id), text="AI 正在生成回答，请稍候…")
        except Exception:  # noqa: BLE001
            log.debug("inline ai noop callback answer failed aid=%s", aid, exc_info=True)
        return True

    inline_query_id = getattr(incoming, "inline_query_id", None)
    chosen_id = getattr(incoming, "chosen_inline_result_id", None)
    if not inline_query_id and not chosen_id:
        return False
    token = parse_query_token(getattr(incoming, "inline_query_text", None) or getattr(incoming, "text", None))
    if token is None:
        return False
    client = redis or get_redis()
    try:
        data = await _read_token(client, token)
    except Exception:  # noqa: BLE001
        log.warning("inline ai token read failed aid=%s", aid, exc_info=True)
        data = None
    valid = _token_matches(data, aid=aid, user_id=user_id)

    if inline_query_id:
        # 令牌无效 / 不是账号本人：回空结果（发起方拿不到结果自然退回普通回复），也不交给插件。
        results = [placeholder_result(token, data or {})] if valid else []
        try:
            await account_bot_service.answer_inline_query(
                bot_token,
                str(inline_query_id),
                results=results,
                cache_time=0,
                is_personal=True,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "inline ai answerInlineQuery failed aid=%s: %s",
                aid,
                account_bot_service.sanitize_bot_error(exc, token=bot_token),
            )
        if not valid:
            log.info("inline ai inline_query rejected aid=%s (token invalid or not owner)", aid)
        return True

    # chosen_inline_result
    if not valid or str(chosen_id) != result_id_for(token):
        log.info("inline ai chosen_inline_result rejected aid=%s", aid)
        return True
    inline_message_id = _chosen_inline_message_id(getattr(incoming, "native_raw", None))
    try:
        await client.delete(_token_key(token))  # 一次性：用过即作废
        if inline_message_id:
            await client.rpush(_chosen_key(token), inline_message_id)
            await client.expire(_chosen_key(token), CHOSEN_TTL_SECONDS)
        await client.set(_feedback_key(aid), FEEDBACK_OK, ex=FEEDBACK_TTL_SECONDS)
    except Exception:  # noqa: BLE001
        log.warning("inline ai chosen result store failed aid=%s", aid, exc_info=True)
    return True


async def wait_inline_message_id(token: str, *, timeout: float = 8.0, redis: Any = None) -> str | None:
    """等交互 Bot 收到 chosen_inline_result；超时返回 ``None``。"""

    client = redis or get_redis()
    try:
        item = await client.blpop([_chosen_key(token)], timeout=max(1, int(round(timeout))))
    except Exception:  # noqa: BLE001
        log.warning("inline ai wait chosen result failed", exc_info=True)
        return None
    if not item:
        return None
    value = item[1] if isinstance(item, (list, tuple)) and len(item) >= 2 else item
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="ignore")
    value = str(value or "").strip()
    return value or None


async def mark_feedback_missing(aid: int, *, redis: Any = None) -> None:
    client = redis or get_redis()
    try:
        await client.set(_feedback_key(aid), FEEDBACK_MISSING, ex=FEEDBACK_TTL_SECONDS)
    except Exception:  # noqa: BLE001
        log.debug("inline ai feedback mark failed aid=%s", aid, exc_info=True)


async def read_feedback_status(aid: int, *, redis: Any = None) -> str | None:
    """最近一次 inline 代发的反馈结果：``ok`` / ``missing``；从未用过返回 ``None``。"""

    client = redis or get_redis()
    try:
        raw = await client.get(_feedback_key(aid))
    except Exception:  # noqa: BLE001
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="ignore")
    value = str(raw or "").strip()
    return value if value in {FEEDBACK_OK, FEEDBACK_MISSING} else None


async def read_reply_preference() -> dict[str, Any]:
    """读取全局「AI 回复方式」设置（每次调用读库，Web/App 改完立即生效）。"""

    style = REPLY_STYLE_NORMAL
    delete_command = True
    try:
        from ..db.base import AsyncSessionLocal
        from ..db.models.system import SystemSetting

        async with AsyncSessionLocal() as db:
            style_row = await db.get(SystemSetting, "ai_reply_style")
            delete_row = await db.get(SystemSetting, "ai_inline_delete_command")
        style = normalize_reply_style(style_row.value if style_row is not None else None)
        delete_command = normalize_delete_command(delete_row.value if delete_row is not None else None)
    except Exception:  # noqa: BLE001
        log.debug("read ai reply preference failed; using normal", exc_info=True)
    return {"style": style, "delete_command": delete_command}


__all__ = [
    "DEFAULT_PLACEHOLDER",
    "FEEDBACK_MISSING",
    "FEEDBACK_OK",
    "NOOP_CALLBACK_DATA",
    "QUERY_PREFIX",
    "REPLY_STYLE_INLINE",
    "REPLY_STYLE_NORMAL",
    "handle_interaction_update",
    "issue_token",
    "mark_feedback_missing",
    "normalize_delete_command",
    "normalize_reply_style",
    "parse_query_token",
    "placeholder_result",
    "read_feedback_status",
    "read_reply_preference",
    "result_id_for",
    "revoke_token",
    "wait_inline_message_id",
]
