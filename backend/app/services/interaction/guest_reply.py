"""Bot API 10.0 Guest Mode（访客模式）回复缓冲。

Guest Mode 下 Bot 会在**未加入**的群或私聊里被 ``@提及``（或被回复），收到
``guest_message`` 更新；它不能向该会话 ``sendMessage``，只能用
``answerGuestQuery(guest_query_id, result)`` 回复**一次**（官方文档：
https://core.telegram.org/bots/features#guest-bots ，"is able to issue one reply"）。

TelePilot 的命令、AI 助手、交互规则和插件都会按普通消息的方式多次调用
``send/edit/delete``。为了让这些能力在访客场景下零改动复用，这里提供一个按
``Incoming`` 绑定的缓冲区：

- 处理期间的发送会被记录为缓冲条目，返回一个仅在本次处理内有效的合成
  ``message_id``；对它的编辑会原地替换内容，删除会移除条目；
- 处理结束后由 runtime 调用 :func:`flush_guest_reply`，把缓冲内容合成一条
  ``InlineQueryResultArticle`` 并只调用一次 ``answerGuestQuery``；
- 插件也可以显式返回 ``answer_guest_query`` 动作立即回答，之后缓冲不再发送。

这样 AI 等耗时流程只会在最终结果出来后发送一次，不会被“思考中…”占位消息
占掉唯一的回复机会。
"""

from __future__ import annotations

import html
import logging
import re
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

# InputTextMessageContent.message_text：1-4096 characters（解析实体后）。
GUEST_MESSAGE_TEXT_LIMIT = 4096
GUEST_ARTICLE_TITLE_LIMIT = 64
GUEST_REPLY_JOINER = "\n\n"

# 每个调用者 / 每个 Bot 的访客消息限流（进程内滑动窗口）。
GUEST_RATE_LIMIT_PER_USER = 6
GUEST_RATE_LIMIT_PER_BOT = 60
GUEST_RATE_LIMIT_WINDOW_SECONDS = 60.0

GUEST_REASON_REPLY_ALREADY_USED = "guest_reply_already_used"
GUEST_REASON_ACTION_UNSUPPORTED = "guest_action_unsupported"
GUEST_REASON_MEDIA_UNSUPPORTED = "guest_media_unsupported"
GUEST_REASON_NO_REPLY = "guest_no_reply"
GUEST_REASON_RATE_LIMITED = "guest_rate_limited"
GUEST_REASON_DISABLED = "guest_updates_disabled"
GUEST_REASON_ANSWER_FAILED = "guest_answer_failed"
GUEST_REASON_QUERY_ID_MISSING = "guest_query_id_missing"
GUEST_REASON_CAPTURED = "guest_reply_buffered"

GUEST_REASON_CODES = frozenset(
    {
        GUEST_REASON_REPLY_ALREADY_USED,
        GUEST_REASON_ACTION_UNSUPPORTED,
        GUEST_REASON_MEDIA_UNSUPPORTED,
        GUEST_REASON_NO_REPLY,
        GUEST_REASON_RATE_LIMITED,
        GUEST_REASON_DISABLED,
        GUEST_REASON_ANSWER_FAILED,
        GUEST_REASON_QUERY_ID_MISSING,
        GUEST_REASON_CAPTURED,
    }
)

# 访客回复是 inline message，回调按钮会以 inline_message_id 回到 Bot（没有 chat/message）。
# 平台自己合成的访客回复（命令、AI 助手、规则）依赖 chat/message 处理回调，所以缓冲合成时
# 只保留不需要回调的按钮；插件显式 ``answer_guest_query`` 给的 callback_data 按钮从
# 0.106.0-beta.17 起原样透传，点击后以 inline callback 投递给订阅了 ``callback_query``
# （scope ``guest_all``）的插件。
_GUEST_CALLBACK_BUTTON_KEYS = ("callback_data",)
_GUEST_SAFE_BUTTON_KEYS = (
    "url",
    "copy_text",
    "switch_inline_query",
    "switch_inline_query_current_chat",
    "switch_inline_query_chosen_chat",
)

_LEADING_BOT_MENTIONS = re.compile(r"^(?:\s*@[A-Za-z0-9_]{1,29}[Bb][Oo][Tt]\b[\s,:，：]*)+")
_TRAILING_BOT_MENTIONS = re.compile(r"(?:\s*@[A-Za-z0-9_]{1,29}[Bb][Oo][Tt])+\s*$")
_HTML_TAG = re.compile(r"<[^>]+>")
_HTML_BR = re.compile(r"<br\s*/?>", re.IGNORECASE)


def strip_guest_bot_mentions(text: str) -> str:
    """去掉召唤访客 Bot 时开头/结尾的 ``@xxx_bot`` 提及（最多可同时提及 3 个）。

    中间位置的提及保留，避免误删正文里引用的 Bot 名。
    """

    raw = str(text or "")
    stripped = _LEADING_BOT_MENTIONS.sub("", raw, count=1)
    stripped = _TRAILING_BOT_MENTIONS.sub("", stripped, count=1)
    return stripped.strip()


def html_to_plain_text(value: str) -> str:
    text = _HTML_BR.sub("\n", str(value or ""))
    text = _HTML_TAG.sub("", text)
    return html.unescape(text).strip()


def sanitize_guest_reply_markup(markup: Any, *, allow_callback: bool = False) -> dict[str, Any] | None:
    """只保留访客回复里可用的按钮（URL / 复制 / 切换 inline）。

    ``allow_callback=True``（插件显式 ``answer_guest_query``）时也保留 ``callback_data`` 按钮。
    """

    keys = _GUEST_SAFE_BUTTON_KEYS + (_GUEST_CALLBACK_BUTTON_KEYS if allow_callback else ())

    if not isinstance(markup, dict):
        return None
    rows = markup.get("inline_keyboard")
    if not isinstance(rows, list):
        return None
    safe_rows: list[list[dict[str, Any]]] = []
    for row in rows:
        if not isinstance(row, list):
            continue
        safe_row = [
            dict(button)
            for button in row
            if isinstance(button, dict)
            and str(button.get("text") or "").strip()
            and any(button.get(key) not in (None, "") for key in keys)
        ]
        if safe_row:
            safe_rows.append(safe_row)
    return {"inline_keyboard": safe_rows} if safe_rows else None


@dataclass(slots=True)
class GuestReplyEntry:
    entry_id: int
    kind: str  # "html" | "plain" | "rich"
    content: str
    rich_message: dict[str, Any] | None = None
    reply_markup: dict[str, Any] | None = None
    source: str = "send"


@dataclass(slots=True)
class GuestReplyBuffer:
    """一次 ``guest_message`` 处理期间的回复缓冲。"""

    query_id: str
    entries: list[GuestReplyEntry] = field(default_factory=list)
    answered: bool = False
    answer_result: dict[str, Any] | None = None
    answer_error: str | None = None
    skipped_actions: list[str] = field(default_factory=list)
    _next_id: int = 1

    def _new_id(self) -> int:
        value = self._next_id
        self._next_id += 1
        return value

    def add(
        self,
        content: str,
        *,
        kind: str = "html",
        rich_message: dict[str, Any] | None = None,
        reply_markup: dict[str, Any] | None = None,
        source: str = "send",
    ) -> int:
        entry = GuestReplyEntry(
            entry_id=self._new_id(),
            kind=kind,
            content=str(content or ""),
            rich_message=dict(rich_message) if isinstance(rich_message, dict) else None,
            reply_markup=sanitize_guest_reply_markup(reply_markup),
            source=source,
        )
        self.entries.append(entry)
        return entry.entry_id

    def replace(
        self,
        entry_id: int | None,
        content: str,
        *,
        kind: str = "html",
        rich_message: dict[str, Any] | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> int:
        """编辑缓冲中的消息；目标未知时替换最后一条（占位消息 → 最终结果）。"""

        target = None
        if entry_id is not None:
            target = next((item for item in self.entries if item.entry_id == entry_id), None)
        if target is None and self.entries:
            target = self.entries[-1]
        if target is None:
            return self.add(content, kind=kind, rich_message=rich_message, reply_markup=reply_markup, source="edit")
        target.kind = kind
        target.content = str(content or "")
        target.rich_message = dict(rich_message) if isinstance(rich_message, dict) else None
        if reply_markup is not None:
            target.reply_markup = sanitize_guest_reply_markup(reply_markup)
        return target.entry_id

    def remove(self, entry_id: int | None) -> bool:
        if entry_id is None:
            return False
        before = len(self.entries)
        self.entries = [item for item in self.entries if item.entry_id != entry_id]
        return len(self.entries) != before

    def has_content(self) -> bool:
        return any(item.content.strip() or item.rich_message for item in self.entries)

    def result_message(self, entry_id: int) -> dict[str, Any]:
        """返回给调用方的伪 Telegram 结果，供后续 edit/delete 引用。"""

        return {"message_id": entry_id, "guest_buffered": True, "guest_query_id": self.query_id}


def _entry_html(entry: GuestReplyEntry) -> str:
    if entry.kind == "html":
        return entry.content.strip()
    if entry.kind == "rich":
        rich = entry.rich_message or {}
        if isinstance(rich.get("html"), str):
            return html.escape(html_to_plain_text(rich["html"]), quote=False)
        if isinstance(rich.get("markdown"), str):
            return html.escape(str(rich["markdown"]).strip(), quote=False)
        return html.escape(entry.content.strip(), quote=False)
    return html.escape(entry.content.strip(), quote=False)


def _entry_plain(entry: GuestReplyEntry) -> str:
    if entry.kind == "html":
        return html_to_plain_text(entry.content)
    if entry.kind == "rich":
        rich = entry.rich_message or {}
        if isinstance(rich.get("html"), str):
            return html_to_plain_text(rich["html"])
        if isinstance(rich.get("markdown"), str):
            return str(rich["markdown"]).strip()
    return entry.content.strip()


def _merged_reply_markup(entries: list[GuestReplyEntry]) -> dict[str, Any] | None:
    for entry in reversed(entries):
        if entry.reply_markup:
            return entry.reply_markup
    return None


def _truncate_plain(text: str, limit: int = GUEST_MESSAGE_TEXT_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _article_title(plain: str) -> str:
    first_line = next((line.strip() for line in plain.splitlines() if line.strip()), "") or "TelePilot"
    return _truncate_plain(first_line, GUEST_ARTICLE_TITLE_LIMIT)


def build_guest_article(
    *,
    message_text: str,
    parse_mode: str | None = "HTML",
    title: str | None = None,
    reply_markup: dict[str, Any] | None = None,
    rich_message: dict[str, Any] | None = None,
    allow_callback: bool = False,
) -> dict[str, Any]:
    """组装 ``InlineQueryResultArticle``。``allow_callback`` 见 :func:`sanitize_guest_reply_markup`。"""

    content: dict[str, Any]
    if rich_message:
        content = {"rich_message": dict(rich_message)}
    else:
        content = {"message_text": message_text}
        if parse_mode:
            content["parse_mode"] = parse_mode
    result: dict[str, Any] = {
        "type": "article",
        "id": uuid.uuid4().hex,
        "title": title or _article_title(html_to_plain_text(message_text) if parse_mode else message_text),
        "input_message_content": content,
    }
    markup = sanitize_guest_reply_markup(reply_markup, allow_callback=allow_callback)
    if markup:
        result["reply_markup"] = markup
    return result


def compose_guest_results(buffer: GuestReplyBuffer) -> list[dict[str, Any]]:
    """把缓冲合成为按优先级排列的候选结果（首选 → 纯文本兜底）。"""

    entries = [item for item in buffer.entries if item.content.strip() or item.rich_message]
    if not entries:
        return []
    markup = _merged_reply_markup(entries)
    plain = GUEST_REPLY_JOINER.join(text for text in (_entry_plain(item) for item in entries) if text)
    plain = _truncate_plain(plain or "（空回复）")
    candidates: list[dict[str, Any]] = []
    rich_entries = [item for item in entries if item.kind == "rich" and item.rich_message]
    if len(entries) == 1 and rich_entries:
        rich = {k: v for k, v in (rich_entries[0].rich_message or {}).items() if k in {"html", "markdown", "blocks", "is_rtl", "skip_entity_detection"}}
        if rich:
            candidates.append(
                build_guest_article(
                    message_text=plain,
                    title=_article_title(plain),
                    reply_markup=markup,
                    rich_message=rich,
                )
            )
    if not rich_entries:
        html_text = GUEST_REPLY_JOINER.join(text for text in (_entry_html(item) for item in entries) if text)
        if html_text and len(html_text) <= GUEST_MESSAGE_TEXT_LIMIT:
            candidates.append(
                build_guest_article(
                    message_text=html_text,
                    parse_mode="HTML",
                    title=_article_title(plain),
                    reply_markup=markup,
                )
            )
        elif html_text:
            from ..account_bot_service import _truncate_bot_text  # noqa: PLC0415

            truncated = _truncate_bot_text(html_text, parse_mode="HTML", limit=GUEST_MESSAGE_TEXT_LIMIT - 1) + "…"
            if len(truncated) <= GUEST_MESSAGE_TEXT_LIMIT:
                candidates.append(
                    build_guest_article(
                        message_text=truncated,
                        parse_mode="HTML",
                        title=_article_title(plain),
                        reply_markup=markup,
                    )
                )
    candidates.append(
        build_guest_article(
            message_text=plain,
            parse_mode=None,
            title=_article_title(plain),
            reply_markup=markup,
        )
    )
    return candidates


def guest_result_from_action(action: dict[str, Any]) -> dict[str, Any] | None:
    """把插件的 ``answer_guest_query`` 动作转成 ``InlineQueryResult``。

    - 传 ``result``（完整 InlineQueryResult 对象）时原样使用，缺 ``id`` 自动补；
    - 传 ``rich_message``（``{"markdown"|"html"|"blocks": ...}``）时用
      ``InputRichMessageContent``；
    - 否则使用 ``text`` + ``parse_mode``（``html`` / 省略为纯文本）。

    ``reply_markup``（或 ``result.reply_markup``）里的 ``callback_data`` 按钮原样保留
    （0.106.0-beta.17+）：点击产生只带 ``inline_message_id`` 的 callback_query。
    """

    raw_result = action.get("result")
    if isinstance(raw_result, dict) and raw_result.get("type"):
        result = dict(raw_result)
        result.setdefault("id", uuid.uuid4().hex)
        if "reply_markup" in result:
            markup = sanitize_guest_reply_markup(result.get("reply_markup"), allow_callback=True)
            if markup:
                result["reply_markup"] = markup
            else:
                result.pop("reply_markup", None)
        return result
    reply_markup = action.get("reply_markup") if isinstance(action.get("reply_markup"), dict) else None
    title = str(action.get("title") or "").strip() or None
    rich_message = action.get("rich_message")
    if isinstance(rich_message, dict) and any(rich_message.get(key) for key in ("markdown", "html", "blocks")):
        text = str(action.get("text") or "").strip()
        fallback = text or html_to_plain_text(str(rich_message.get("html") or rich_message.get("markdown") or ""))
        return build_guest_article(
            message_text=_truncate_plain(fallback or "TelePilot"),
            title=title or _article_title(fallback),
            reply_markup=reply_markup,
            rich_message=dict(rich_message),
            allow_callback=True,
        )
    text = str(action.get("text") or "").strip()
    if not text:
        return None
    parse_mode = "HTML" if str(action.get("parse_mode") or "").strip().lower() == "html" else None
    if parse_mode and len(text) > GUEST_MESSAGE_TEXT_LIMIT:
        text = html_to_plain_text(text)
        parse_mode = None
    message_text = _truncate_plain(text) if not parse_mode else text
    return build_guest_article(
        message_text=message_text,
        parse_mode=parse_mode,
        title=title,
        reply_markup=reply_markup,
        allow_callback=True,
    )


async def flush_guest_reply(token: str, buffer: GuestReplyBuffer | None) -> dict[str, Any]:
    """处理结束后只调用一次 ``answerGuestQuery``。

    返回 ``{"status": "answered"|"skipped"|"failed", "reason_code": ..., ...}``，
    由 runtime 写入 Trace / 运行日志。首选格式被 Telegram 拒绝时（例如 HTML 解析
    错误，请求未生效）依次退回更保守的格式重试；任一次成功即停止。
    """

    from .. import account_bot_service  # noqa: PLC0415

    if buffer is None:
        return {"status": "skipped", "reason_code": GUEST_REASON_QUERY_ID_MISSING}
    if buffer.answered:
        return {"status": "skipped", "reason_code": GUEST_REASON_REPLY_ALREADY_USED, "result": buffer.answer_result}
    if not str(buffer.query_id or "").strip():
        return {"status": "skipped", "reason_code": GUEST_REASON_QUERY_ID_MISSING}
    candidates = compose_guest_results(buffer)
    if not candidates:
        return {"status": "skipped", "reason_code": GUEST_REASON_NO_REPLY}
    last_error: str | None = None
    for index, result in enumerate(candidates):
        try:
            sent = await account_bot_service.answer_guest_query(token, buffer.query_id, result=result)
        except Exception as exc:  # noqa: BLE001
            last_error = account_bot_service.sanitize_bot_error(exc, token=token)
            log.info("answerGuestQuery attempt %s failed: %s", index + 1, last_error)
            continue
        buffer.answered = True
        buffer.answer_result = sent if isinstance(sent, dict) else {"result": sent}
        content = result.get("input_message_content") or {}
        return {
            "status": "answered",
            "reason_code": None,
            "attempt": index + 1,
            "format": "rich" if "rich_message" in content else ("html" if content.get("parse_mode") else "plain"),
            "entries": len(buffer.entries),
            "result": buffer.answer_result,
        }
    buffer.answer_error = last_error
    return {"status": "failed", "reason_code": GUEST_REASON_ANSWER_FAILED, "error": last_error}


# ---------------------------------------------------------------------------
# 访客回复来源（guest_query_id / inline_message_id → 原访客会话）
# ---------------------------------------------------------------------------
# 访客回复上的 callback 按钮被点击时，Telegram 只给 inline_message_id 与点击者，没有
# chat/message。平台在收到 guest_message 时按 guest_query_id 记下原会话（只含 id 与类型），
# answerGuestQuery 成功后转记到返回的 inline_message_id，之后 inline callback 投递给插件时
# 一并带上（``payload["inline_callback"]["origin"]``）。只是附加信息：Redis 不可用时静默跳过。
GUEST_ORIGIN_QUERY_TTL_SECONDS = 15 * 60
GUEST_ORIGIN_INLINE_TTL_SECONDS = 48 * 3600
_GUEST_ORIGIN_REDIS_TIMEOUT_SECONDS = 1.0
_GUEST_ORIGIN_FIELDS = ("guest_query_id", "chat_id", "chat_type", "caller_user_id", "message_id")


def _guest_origin_scope(token: str) -> str:
    import hashlib  # noqa: PLC0415

    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()[:16]


def _guest_origin_key(token: str, kind: str, value: str) -> str:
    return f"tp:guest_origin:{kind}:{_guest_origin_scope(token)}:{value}"


def _clean_guest_origin(origin: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in _GUEST_ORIGIN_FIELDS:
        value = origin.get(key)
        if value in (None, ""):
            continue
        if key in {"chat_id", "caller_user_id", "message_id"}:
            try:
                out[key] = int(value)
            except (TypeError, ValueError):
                continue
        else:
            out[key] = str(value)[:128]
    return out


async def _guest_origin_redis_call(method: str, *args: Any, **kwargs: Any) -> Any:
    import asyncio  # noqa: PLC0415

    from ...redis_client import get_redis  # noqa: PLC0415

    redis = get_redis()
    return await asyncio.wait_for(getattr(redis, method)(*args, **kwargs), timeout=_GUEST_ORIGIN_REDIS_TIMEOUT_SECONDS)


async def remember_guest_query_origin(token: str, guest_query_id: str | None, origin: dict[str, Any]) -> None:
    """收到 guest_message 时按 guest_query_id 记下原访客会话（best-effort）。"""

    query_id = str(guest_query_id or "").strip()
    if not token or not query_id:
        return
    payload = _clean_guest_origin({**origin, "guest_query_id": query_id})
    try:
        import json  # noqa: PLC0415

        await _guest_origin_redis_call(
            "set",
            _guest_origin_key(token, "q", query_id),
            json.dumps(payload, ensure_ascii=False),
            ex=GUEST_ORIGIN_QUERY_TTL_SECONDS,
        )
    except Exception:  # noqa: BLE001
        log.debug("remember guest query origin failed", exc_info=True)


async def link_guest_inline_origin(token: str, guest_query_id: str | None, inline_message_id: str | None) -> None:
    """answerGuestQuery 成功后，把 guest_query_id 的来源转记到 inline_message_id（best-effort）。"""

    query_id = str(guest_query_id or "").strip()
    inline_id = str(inline_message_id or "").strip()
    if not token or not query_id or not inline_id:
        return
    try:
        raw = await _guest_origin_redis_call("get", _guest_origin_key(token, "q", query_id))
        if raw is None:
            return
        await _guest_origin_redis_call(
            "set",
            _guest_origin_key(token, "i", inline_id),
            raw,
            ex=GUEST_ORIGIN_INLINE_TTL_SECONDS,
        )
    except Exception:  # noqa: BLE001
        log.debug("link guest inline origin failed", exc_info=True)


async def guest_inline_origin(token: str, inline_message_id: str | None) -> dict[str, Any] | None:
    """按 inline_message_id 读回原访客会话；没有记录或读取失败返回 None。"""

    inline_id = str(inline_message_id or "").strip()
    if not token or not inline_id:
        return None
    try:
        raw = await _guest_origin_redis_call("get", _guest_origin_key(token, "i", inline_id))
    except Exception:  # noqa: BLE001
        log.debug("read guest inline origin failed", exc_info=True)
        return None
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="ignore")
    try:
        import json  # noqa: PLC0415

        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return _clean_guest_origin(data) if isinstance(data, dict) else None


class GuestRateLimiter:
    """进程内滑动窗口限流：每个调用者 + 每个 Bot 各一个窗口。"""

    def __init__(
        self,
        *,
        per_user: int = GUEST_RATE_LIMIT_PER_USER,
        per_bot: int = GUEST_RATE_LIMIT_PER_BOT,
        window_seconds: float = GUEST_RATE_LIMIT_WINDOW_SECONDS,
    ) -> None:
        self.per_user = per_user
        self.per_bot = per_bot
        self.window_seconds = window_seconds
        self._buckets: dict[str, deque[float]] = {}

    def _allow(self, key: str, limit: int, now: float) -> bool:
        bucket = self._buckets.setdefault(key, deque())
        while bucket and now - bucket[0] >= self.window_seconds:
            bucket.popleft()
        return len(bucket) < limit

    def allow(self, *, account_id: int, role: str, user_id: int | None, now: float | None = None) -> bool:
        current = time.monotonic() if now is None else now
        bot_key = f"{account_id}:{role}"
        user_key = f"{bot_key}:{user_id if user_id is not None else 'anon'}"
        if not self._allow(bot_key, self.per_bot, current) or not self._allow(user_key, self.per_user, current):
            return False
        self._buckets[bot_key].append(current)
        self._buckets[user_key].append(current)
        if len(self._buckets) > 4096:
            stale = [key for key, bucket in self._buckets.items() if not bucket or current - bucket[-1] >= self.window_seconds]
            for key in stale:
                self._buckets.pop(key, None)
        return True

    def reset(self) -> None:
        self._buckets.clear()


guest_rate_limiter = GuestRateLimiter()


__all__ = [
    "GUEST_MESSAGE_TEXT_LIMIT",
    "GUEST_REASON_ACTION_UNSUPPORTED",
    "GUEST_REASON_ANSWER_FAILED",
    "GUEST_REASON_CAPTURED",
    "GUEST_REASON_CODES",
    "GUEST_REASON_DISABLED",
    "GUEST_REASON_MEDIA_UNSUPPORTED",
    "GUEST_REASON_NO_REPLY",
    "GUEST_REASON_QUERY_ID_MISSING",
    "GUEST_REASON_RATE_LIMITED",
    "GUEST_REASON_REPLY_ALREADY_USED",
    "GuestRateLimiter",
    "GuestReplyBuffer",
    "GuestReplyEntry",
    "build_guest_article",
    "compose_guest_results",
    "flush_guest_reply",
    "guest_inline_origin",
    "guest_result_from_action",
    "guest_rate_limiter",
    "html_to_plain_text",
    "link_guest_inline_origin",
    "remember_guest_query_origin",
    "sanitize_guest_reply_markup",
    "strip_guest_bot_mentions",
]
