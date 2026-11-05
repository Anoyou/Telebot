"""交互 Bot inline 提问：「@Bot 问题」在 inline 模式下也能让 AI 回答（0.119.1）。

交互 Bot 在 BotFather 用 ``/setinline`` 打开 inline 模式后，群里有人输入「@Bot 问题」，Telegram
客户端会把它当成 inline 查询发给 Bot，而不是访客消息（``guest_message``）。没人回答
``answerInlineQuery`` 时客户端一直转圈、消息发不出去。这里补上这条路：

1. ``inline_query``：照常先交给订阅了 inline 的插件；处理完仍没人回答时由平台回一条
   「发送这个问题」（:func:`ask_result`，描述是问题预览，带 ⏳ 按钮——没有 inline keyboard 时
   ``chosen_inline_result`` 不带 ``inline_message_id``）。查询为空给一条用法提示
   （:func:`help_result`）；访客提问不可用（运行设置关闭访客消息、没有插件处理访客消息、
   触发访客限流）时回空结果并设短 ``cache_time``。
2. ``chosen_inline_result``（结果 id 以 ``tpask:`` 开头）：记下 ``inline_message_id`` → 问题，
   合成一条 ``guest_message``（``guest_query_id = "tpask:<inline_message_id>"``）交给原来的访客
   处理链路——同一个 AI 答疑插件、同一套白名单 / 每日限额 / 访客限流。
3. 插件对这条“访客消息”的回答（``answerGuestQuery``）在 :func:`answer_guest_as_edit` 改成
   ``editMessageText(inline_message_id)``；之后对这条 inline 消息的编辑都自动在顶部带上问题引用
   （:func:`decorate_text` / :func:`decorate_rich_message`），并默认去掉 ⏳ 按钮。
4. 处理结束仍没人动过这条消息（不在许可范围、被限流等）时编辑成「没有拿到回答」，不留下转圈占位。

群白名单：inline 查询和选中结果都不带群 id，插件设了会话白名单时按现有规则（会话未知 = 不在
白名单）不回答。需要 BotFather ``/setinlinefeedback`` 设为 100%（Enabled），否则 Bot 收不到
``chosen_inline_result``。
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import re
import secrets
import time
from typing import Any

from ..redis_client import get_redis

log = logging.getLogger(__name__)

ASK_PREFIX = "tpask:"
HELP_RESULT_ID = "tphelp"
NOOP_CALLBACK_DATA = "tpai:noop"  # 与 inline_ai_relay 的占位按钮共用（点了只提示“生成中”）
ASK_TTL_SECONDS = 48 * 3600
QUERY_PREVIEW_LIMIT = 96
QUESTION_DISPLAY_LIMIT = 600
UNAVAILABLE_CACHE_SECONDS = 10
ASK_CACHE_SECONDS = 2
HELP_CACHE_SECONDS = 60
THINKING_TEXT = "🤔 AI 思考中…"
PENDING_BUTTON_TEXT = "⏳ AI 生成中…"
NO_ANSWER_TEXT = "⚠️ 这次没有拿到 AI 回答（可能不在 AI 答疑的许可范围内，或提问太频繁），可以稍后再试。"
REMOVE_KEYBOARD: dict[str, Any] = {"inline_keyboard": []}

REASON_GUEST_DISABLED = "guest_updates_disabled"
REASON_NO_HANDLER = "no_guest_handler"
REASON_RATE_LIMITED = "guest_rate_limited"
REASON_SELF = "bot_self"

# inline_query.chat_type → 结果 id 里的单字符（chosen_inline_result 不带会话类型）。
_CHAT_TYPE_CODES = {"sender": "p", "private": "p", "group": "g", "supergroup": "s", "channel": "c"}
_CODE_CHAT_TYPES = {"p": "private", "g": "group", "s": "supergroup", "c": "channel"}
_KEY = "tp:inline_ask:msg:{digest}"
_MD_PUNCT_RE = re.compile(r"([\\`*_\[\]#|~=$>])")


def is_ask_result_id(result_id: Any) -> bool:
    return str(result_id or "").startswith(ASK_PREFIX)


def is_help_result_id(result_id: Any) -> bool:
    return str(result_id or "") == HELP_RESULT_ID


def is_ask_guest_query_id(guest_query_id: Any) -> bool:
    return str(guest_query_id or "").startswith(ASK_PREFIX)


def guest_query_id_for(inline_message_id: str) -> str:
    return f"{ASK_PREFIX}{inline_message_id}"


def inline_message_id_from_guest_query(guest_query_id: Any) -> str:
    text = str(guest_query_id or "")
    return text[len(ASK_PREFIX):] if text.startswith(ASK_PREFIX) else ""


def chat_type_from_result_id(result_id: Any) -> str | None:
    parts = str(result_id or "").split(":")
    if len(parts) >= 2 and parts[0] + ":" == ASK_PREFIX:
        return _CODE_CHAT_TYPES.get(parts[1])
    return None


def normalize_question(query: Any) -> str:
    return " ".join(str(query or "").split())


def _preview(text: str, limit: int = QUERY_PREVIEW_LIMIT) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def ask_result(query: str, *, chat_type: str | None, show_question: bool = True) -> dict[str, Any]:
    """「发送这个问题」：选中后以 inline 消息（via @Bot）发出问题 + 思考中，答案再编辑进来。"""

    question = normalize_question(query)
    code = _CHAT_TYPE_CODES.get(str(chat_type or "").lower(), "u")
    return {
        "type": "article",
        "id": f"{ASK_PREFIX}{code}:{secrets.token_hex(8)}",
        "title": "发送这个问题",
        "description": _preview(question),
        "input_message_content": {
            "message_text": decorate_text(THINKING_TEXT, question, parse_mode="HTML") if show_question else THINKING_TEXT,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        },
        "reply_markup": {"inline_keyboard": [[{"text": PENDING_BUTTON_TEXT, "callback_data": NOOP_CALLBACK_DATA}]]},
    }


def help_result() -> dict[str, Any]:
    return {
        "type": "article",
        "id": HELP_RESULT_ID,
        "title": "向 AI 提问",
        "description": "在 @Bot 后面输入问题，再点「发送这个问题」",
        "input_message_content": {
            "message_text": "💡 在输入框里输入「@Bot 名 + 你的问题」，选择「发送这个问题」，AI 会把回答写进这条消息。",
        },
    }


def query_answer(
    query: Any,
    *,
    chat_type: str | None,
    unavailable_reason: str | None,
    show_question: bool = True,
) -> dict[str, Any]:
    """``answerInlineQuery`` 参数（results / cache_time / is_personal）。"""

    if unavailable_reason:
        return {"results": [], "cache_time": UNAVAILABLE_CACHE_SECONDS, "is_personal": True}
    question = normalize_question(query)
    if not question:
        return {"results": [help_result()], "cache_time": HELP_CACHE_SECONDS, "is_personal": False}
    return {
        "results": [ask_result(question, chat_type=chat_type, show_question=show_question)],
        "cache_time": ASK_CACHE_SECONDS,
        "is_personal": True,
    }


def synthetic_guest_update(
    *,
    update_id: int,
    from_user: dict[str, Any],
    question: str,
    inline_message_id: str,
    chat_type: str | None,
) -> dict[str, Any]:
    """把选中的 inline 提问合成为 ``guest_message`` 更新，交给原访客处理链路。

    没有 chat id（inline 结果不告诉 Bot 发到了哪个会话）；``via_inline_ask`` 只是标记。
    """

    chat: dict[str, Any] = {}
    if chat_type:
        chat["type"] = chat_type
    return {
        "update_id": int(update_id or 0),
        "guest_message": {
            "guest_query_id": guest_query_id_for(inline_message_id),
            "from": dict(from_user or {}),
            "chat": chat,
            "date": int(time.time()),
            "text": normalize_question(question),
            "via_inline_ask": True,
        },
    }


# ---------------------------------------------------------------------------
# inline_message_id → 问题（Redis，跨进程：插件动作可能在 worker 里实时执行）
# ---------------------------------------------------------------------------


def _key(inline_message_id: str) -> str:
    digest = hashlib.sha256(str(inline_message_id).encode("utf-8")).hexdigest()[:40]
    return _KEY.format(digest=digest)


async def register(
    inline_message_id: str,
    question: str,
    *,
    show_question: bool = True,
    divider: bool = True,
    redis: Any = None,
) -> None:
    """登记 inline 提问消息。``show_question`` / ``divider`` 来自服务器参数（登记时定下，后续编辑沿用）。"""

    client = redis or get_redis()
    payload = {
        "q": normalize_question(question)[:QUESTION_DISPLAY_LIMIT],
        "touched": 0,
        "show": bool(show_question),
        "divider": bool(divider),
    }
    try:
        await client.set(_key(inline_message_id), json.dumps(payload, ensure_ascii=False), ex=ASK_TTL_SECONDS)
    except Exception:  # noqa: BLE001
        log.warning("inline ask register failed", exc_info=True)


async def lookup(inline_message_id: str | None, *, redis: Any = None) -> dict[str, Any] | None:
    if not inline_message_id:
        return None
    client = redis or get_redis()
    try:
        raw = await client.get(_key(inline_message_id))
    except Exception:  # noqa: BLE001
        log.debug("inline ask lookup failed", exc_info=True)
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="ignore")
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


async def mark_touched(inline_message_id: str, meta: dict[str, Any], *, redis: Any = None) -> None:
    if meta.get("touched"):
        return
    client = redis or get_redis()
    payload = dict(meta)
    payload["touched"] = 1
    try:
        await client.set(_key(inline_message_id), json.dumps(payload, ensure_ascii=False), ex=ASK_TTL_SECONDS)
    except Exception:  # noqa: BLE001
        log.debug("inline ask mark touched failed", exc_info=True)


# ---------------------------------------------------------------------------
# 编辑时在顶部带上问题
# ---------------------------------------------------------------------------


def decorate_text(text: str, question: str, *, parse_mode: str | None) -> str:
    question = normalize_question(question)
    if not question:
        return text
    body = str(text or "")
    if str(parse_mode or "").upper() == "HTML":
        return f"<blockquote>❓ {html.escape(question, quote=False)}</blockquote>\n{body}"
    return f"❓ {question}\n\n{body}"


def decorate_rich_message(rich_message: dict[str, Any], question: str, *, divider: bool = True) -> dict[str, Any]:
    question = normalize_question(question)
    if not question or not isinstance(rich_message, dict):
        return rich_message
    out = dict(rich_message)
    escaped = html.escape(question, quote=False)
    if isinstance(out.get("markdown"), str):
        quoted = _MD_PUNCT_RE.sub(r"\\\1", escaped)
        body = str(out["markdown"]).lstrip("\n")
        # 排版：问题引用 → 原生分割线 → 回答（回答后面的分割线 + 署名由回答方自己排）。
        if body.startswith("---"):
            body = body[3:].lstrip("\n")
        sep = "\n\n---\n\n" if divider else "\n\n"
        out["markdown"] = f"> ❓ {quoted}{sep}{body}" if body.strip() else f"> ❓ {quoted}"
    elif isinstance(out.get("html"), str):
        out["html"] = f"<blockquote>❓ {escaped}</blockquote>{'<hr/>' if divider else ''}{out['html']}"
    # blocks：不改动（插件 / 平台的 AI 回答都用 markdown，blocks 结构由调用方自己负责）。
    return out


async def prepare_inline_edit(
    inline_message_id: str,
    *,
    text: str | None = None,
    parse_mode: str | None = None,
    rich_message: dict[str, Any] | None = None,
    reply_markup: dict[str, Any] | None = None,
    redis: Any = None,
) -> tuple[str | None, dict[str, Any] | None, dict[str, Any] | None]:
    """编辑 inline 提问消息前调用：带上问题、默认去掉 ⏳ 按钮。不是 inline 提问的消息原样返回。"""

    meta = await lookup(inline_message_id, redis=redis)
    if meta is None:
        return text, rich_message, reply_markup
    question = str(meta.get("q") or "") if meta.get("show", True) else ""
    if rich_message is not None:
        rich_message = decorate_rich_message(rich_message, question, divider=bool(meta.get("divider", True)))
    if text is not None:
        text = decorate_text(text, question, parse_mode=parse_mode)
    if reply_markup is None:
        reply_markup = REMOVE_KEYBOARD
    await mark_touched(inline_message_id, meta, redis=redis)
    return text, rich_message, reply_markup


async def answer_guest_as_edit(token: str, guest_query_id: str, result: dict[str, Any]) -> dict[str, Any]:
    """合成访客消息的 ``answerGuestQuery`` → ``editMessageText(inline_message_id)``，返回同样的 ``inline_message_id``。"""

    from . import account_bot_service  # noqa: PLC0415

    inline_message_id = inline_message_id_from_guest_query(guest_query_id)
    if not inline_message_id:
        raise RuntimeError("inline ask guest_query_id missing inline_message_id")
    content = result.get("input_message_content") if isinstance(result.get("input_message_content"), dict) else {}
    markup = result.get("reply_markup") if isinstance(result.get("reply_markup"), dict) else None
    try:
        if isinstance(content.get("rich_message"), dict):
            await account_bot_service.edit_inline_rich_message(
                token, inline_message_id, content["rich_message"], reply_markup=markup
            )
        else:
            await account_bot_service.edit_inline_message(
                token,
                inline_message_id,
                str(content.get("message_text") or ""),
                reply_markup=markup,
                parse_mode=content.get("parse_mode"),
            )
    except Exception as exc:  # noqa: BLE001
        if not account_bot_service.is_bot_message_not_modified_error(exc):
            raise
    return {"inline_message_id": inline_message_id}


async def finish_if_untouched(token: str, inline_message_id: str, *, redis: Any = None) -> bool:
    """处理结束仍没人编辑过（插件没回答）时改成「没有拿到回答」并去掉按钮；返回是否改写了。"""

    from . import account_bot_service  # noqa: PLC0415

    meta = await lookup(inline_message_id, redis=redis)
    if meta is None or meta.get("touched"):
        return False
    try:
        await account_bot_service.edit_inline_message(token, inline_message_id, NO_ANSWER_TEXT, parse_mode=None)
    except Exception as exc:  # noqa: BLE001
        log.info(
            "inline ask fallback edit failed: %s",
            account_bot_service.sanitize_bot_error(exc, token=token),
        )
        return False
    return True


__all__ = [
    "ASK_PREFIX",
    "HELP_RESULT_ID",
    "NO_ANSWER_TEXT",
    "REASON_GUEST_DISABLED",
    "REASON_NO_HANDLER",
    "REASON_RATE_LIMITED",
    "REASON_SELF",
    "answer_guest_as_edit",
    "ask_result",
    "chat_type_from_result_id",
    "decorate_rich_message",
    "decorate_text",
    "finish_if_untouched",
    "guest_query_id_for",
    "help_result",
    "inline_message_id_from_guest_query",
    "is_ask_guest_query_id",
    "is_ask_result_id",
    "is_help_result_id",
    "lookup",
    "prepare_inline_edit",
    "query_answer",
    "register",
    "synthetic_guest_update",
]
