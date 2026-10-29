"""插件动作的通用扩展：回调 token 路由（E1）与 Bot API 字段受控透传（E3）。

两条执行链路（主进程交互 Bot 执行器、worker UserBot / 实时 apply 执行器）都经
:func:`app.services.interaction.action_core.run_action_batch` 分发动作；本模块在分发前
对每个动作做一次与渠道无关的预处理：

**回调 token（``messages.callback_routing``）**

插件在任意动作的 ``reply_markup`` 里写::

    {"text": "重试", "callback": {"data": "retry:42"}}

宿主签发 ``cb1.<token>`` 作为真实 ``callback_data``（≤64 字节），Redis 记下
``token → {plugin_key, account_id, data, origin}``（带 TTL）。点击后按 token 路由回发出按钮的
插件（订阅 ``scope: "own_buttons"``），与会话范围无关，payload 统一带
``callback: {data, origin, clicker, token}``。Redis 不可用时签发失败的按钮会被去掉（消息照常发出），
点击时查不到 token 一律拒绝（fail-closed）。直接写 ``callback_data`` 的旧写法原样透传并记入弃用登记。

**Bot API 字段透传（``messages.bot_api_extra``）**

动作可带 ``bot_api_extra: {...}``：按动作类型的白名单校验，不得覆盖平台字段，未知字段整条动作拒绝
（``error_code=bot_api_extra_rejected``）；通过校验的值只在执行该动作时、并且只合并进该动作对应的
Bot API 方法（交互 Bot 渠道），Trace 与日志只记字段名。
"""

from __future__ import annotations

import contextvars
import json
import logging
import secrets
import time
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────
# 回调 token
# ─────────────────────────────────────────────────────
CALLBACK_TOKEN_PREFIX = "cb1."
CALLBACK_TOKEN_KEY_PREFIX = "telepilot:cbtok:v1:"
CALLBACK_TOKEN_DEFAULT_TTL_SECONDS = 7 * 24 * 3600
CALLBACK_TOKEN_MIN_TTL_SECONDS = 60
CALLBACK_TOKEN_MAX_TTL_SECONDS = 30 * 24 * 3600
CALLBACK_DATA_MAX_BYTES = 4096
# 一个动作里最多签发的按钮数（Telegram 单条消息按钮上限 100）。
CALLBACK_TOKENS_PER_ACTION_LIMIT = 100

CALLBACK_REASON_EXPIRED = "callback_token_expired"
CALLBACK_REASON_UNAVAILABLE = "callback_token_unavailable"
CALLBACK_REASON_NOT_OWNED = "callback_not_owned"

ORIGIN_CHAT = "chat"
ORIGIN_INLINE = "inline"
ORIGIN_GUEST = "guest"
ORIGIN_USERBOT_TEXT = "userbot_text"


class CallbackTokenUnavailable(RuntimeError):
    """Redis 不可用：签发或解析回调 token 失败（调用方按 fail-closed 处理）。"""


def is_callback_token(data: Any) -> bool:
    return isinstance(data, str) and data.startswith(CALLBACK_TOKEN_PREFIX) and len(data) > len(CALLBACK_TOKEN_PREFIX)


def _token_key(account_id: int, token: str) -> str:
    return f"{CALLBACK_TOKEN_KEY_PREFIX}{int(account_id)}:{token}"


def _new_token() -> str:
    # 16 字节随机数 → 22 字符 urlsafe；加前缀共 26 字节，远低于 callback_data 的 64 字节上限。
    return CALLBACK_TOKEN_PREFIX + secrets.token_urlsafe(16)


def _clamp_ttl(value: Any) -> int:
    try:
        ttl = int(value)
    except (TypeError, ValueError):
        return CALLBACK_TOKEN_DEFAULT_TTL_SECONDS
    return max(CALLBACK_TOKEN_MIN_TTL_SECONDS, min(CALLBACK_TOKEN_MAX_TTL_SECONDS, ttl))


async def mint_callback_token(
    redis: Any,
    *,
    account_id: int,
    plugin_key: str,
    data: str,
    origin: dict[str, Any] | None = None,
    ttl_seconds: int | None = None,
) -> str:
    """签发一个回调 token；Redis 不可用时抛 :class:`CallbackTokenUnavailable`。"""

    if redis is None:
        raise CallbackTokenUnavailable("redis unavailable")
    token = _new_token()
    record = {
        "plugin_key": str(plugin_key),
        "account_id": int(account_id),
        "data": str(data),
        "origin": dict(origin or {}),
        "created_at": int(time.time()),
    }
    try:
        await redis.set(
            _token_key(account_id, token),
            json.dumps(record, ensure_ascii=False, separators=(",", ":")),
            ex=_clamp_ttl(ttl_seconds if ttl_seconds is not None else CALLBACK_TOKEN_DEFAULT_TTL_SECONDS),
        )
    except Exception as exc:  # noqa: BLE001
        raise CallbackTokenUnavailable(f"{type(exc).__name__}: {exc}") from exc
    return token


async def resolve_callback_token(redis: Any, *, account_id: int, callback_data: str) -> dict[str, Any] | None:
    """解析回调 token；不存在 / 已过期返回 None，Redis 不可用抛 :class:`CallbackTokenUnavailable`。"""

    if not is_callback_token(callback_data):
        return None
    if redis is None:
        raise CallbackTokenUnavailable("redis unavailable")
    try:
        raw = await redis.get(_token_key(account_id, callback_data))
    except Exception as exc:  # noqa: BLE001
        raise CallbackTokenUnavailable(f"{type(exc).__name__}: {exc}") from exc
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="ignore")
    try:
        record = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(record, dict) or int(record.get("account_id") or 0) != int(account_id):
        return None
    record["token"] = callback_data
    return record


def callback_origin_for_action(action: dict[str, Any]) -> dict[str, Any]:
    """按动作推断按钮来源（chat / inline / guest / userbot_text）。message_id 在签发时还未知。"""

    action_type = str(action.get("type") or "").strip()
    send_via = str(action.get("send_via") or "").strip()
    if action_type == "answer_guest_query":
        return {"kind": ORIGIN_GUEST, "guest_query_id": action.get("guest_query_id")}
    if action_type == "answer_inline_query":
        return {"kind": ORIGIN_INLINE, "inline_query_id": action.get("inline_query_id")}
    inline_message_id = str(action.get("inline_message_id") or "").strip()
    if inline_message_id:
        return {"kind": ORIGIN_INLINE, "inline_message_id": inline_message_id}
    origin: dict[str, Any] = {"kind": ORIGIN_USERBOT_TEXT if send_via.startswith("userbot") else ORIGIN_CHAT}
    if action.get("chat_id") is not None:
        origin["chat_id"] = action.get("chat_id")
    if action.get("message_id") is not None:
        origin["message_id"] = action.get("message_id")
    return origin


def markup_has_token_buttons(markup: Any) -> bool:
    rows = markup.get("inline_keyboard") if isinstance(markup, dict) else None
    if not isinstance(rows, list):
        return False
    return any(
        isinstance(button, dict) and isinstance(button.get("callback"), dict)
        for row in rows
        if isinstance(row, list)
        for button in row
    )


def markup_has_raw_callback_data(markup: Any) -> bool:
    rows = markup.get("inline_keyboard") if isinstance(markup, dict) else None
    if not isinstance(rows, list):
        return False
    return any(
        isinstance(button, dict)
        and str(button.get("callback_data") or "")
        and not is_callback_token(button.get("callback_data"))
        for row in rows
        if isinstance(row, list)
        for button in row
    )


async def rewrite_reply_markup(
    markup: Any,
    *,
    redis: Any,
    account_id: int,
    plugin_key: str,
    origin: dict[str, Any],
    budget: list[int] | None = None,
) -> tuple[Any, list[str]]:
    """把 ``{"callback": {"data": ...}}`` 按钮改写为 ``callback_data = cb1.<token>``。

    返回 ``(新 markup, 警告码列表)``。签发失败（Redis 不可用 / 超出数量 / data 过大）的按钮被去掉，
    其它按钮保留；行变空时去掉该行，全部为空时返回 None。
    """

    if not markup_has_token_buttons(markup):
        return markup, []
    warnings: list[str] = []
    counter = budget if budget is not None else [CALLBACK_TOKENS_PER_ACTION_LIMIT]
    new_rows: list[list[dict[str, Any]]] = []
    for row in markup.get("inline_keyboard") or []:
        if not isinstance(row, list):
            continue
        new_row: list[dict[str, Any]] = []
        for button in row:
            if not isinstance(button, dict):
                continue
            spec = button.get("callback")
            if not isinstance(spec, dict):
                new_row.append(dict(button))
                continue
            data = spec.get("data")
            data_text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, separators=(",", ":"))
            if len(data_text.encode("utf-8")) > CALLBACK_DATA_MAX_BYTES:
                warnings.append("callback_data_too_large")
                continue
            if counter[0] <= 0:
                warnings.append("callback_token_limit")
                continue
            try:
                token = await mint_callback_token(
                    redis,
                    account_id=account_id,
                    plugin_key=plugin_key,
                    data=data_text,
                    origin=origin,
                    ttl_seconds=spec.get("ttl_seconds"),
                )
            except CallbackTokenUnavailable:
                warnings.append(CALLBACK_REASON_UNAVAILABLE)
                continue
            counter[0] -= 1
            converted = {key: value for key, value in button.items() if key not in {"callback", "callback_data"}}
            converted["callback_data"] = token
            new_row.append(converted)
        if new_row:
            new_rows.append(new_row)
    rewritten = dict(markup)
    rewritten["inline_keyboard"] = new_rows
    if not new_rows:
        return None, sorted(set(warnings))
    return rewritten, sorted(set(warnings))


# ─────────────────────────────────────────────────────
# Bot API 字段透传
# ─────────────────────────────────────────────────────
BOT_API_EXTRA_REJECTED = "bot_api_extra_rejected"
BOT_API_EXTRA_MAX_BYTES = 2048

# 动作类型 → 允许透传的 Bot API 字段（Bot API 10.x）。只放与投递呈现相关、不涉及资金和身份的字段。
_SEND_COMMON = frozenset(
    {
        "disable_notification",
        "protect_content",
        "message_effect_id",
        "message_thread_id",
        "direct_messages_topic_id",
    }
)
BOT_API_EXTRA_FIELDS: dict[str, frozenset[str]] = {
    "send_message": _SEND_COMMON | {"link_preview_options"},
    "send_rich_message": _SEND_COMMON | {"link_preview_options"},
    "send_photo": _SEND_COMMON | {"has_spoiler", "show_caption_above_media"},
    "send_file": _SEND_COMMON | {"disable_content_type_detection"},
    "edit_message": frozenset({"link_preview_options"}),
    "edit_caption": frozenset({"show_caption_above_media"}),
    "pin_message": frozenset({"disable_notification"}),
    "answer_callback": frozenset({"url", "cache_time"}),
}
# 动作类型 → 执行时会调用的 Bot API 方法；透传只合并进这些方法的请求。
BOT_API_EXTRA_METHODS: dict[str, frozenset[str]] = {
    "send_message": frozenset({"sendMessage"}),
    "send_rich_message": frozenset({"sendRichMessage", "sendMessage"}),
    "send_photo": frozenset({"sendPhoto"}),
    "send_file": frozenset({"sendDocument"}),
    "edit_message": frozenset({"editMessageText"}),
    "edit_caption": frozenset({"editMessageCaption"}),
    "pin_message": frozenset({"pinChatMessage"}),
    "answer_callback": frozenset({"answerCallbackQuery"}),
}
# 无论哪种动作都不能经透传改写的平台字段（明确列出，报错时给出更准确的原因）。
BOT_API_PLATFORM_FIELDS = frozenset(
    {
        "chat_id",
        "message_id",
        "inline_message_id",
        "text",
        "caption",
        "parse_mode",
        "entities",
        "caption_entities",
        "reply_markup",
        "reply_parameters",
        "reply_to_message_id",
        "rich_message",
        "photo",
        "document",
        "callback_query_id",
        "inline_query_id",
        "guest_query_id",
        "business_connection_id",
        "allow_paid_broadcast",
        "suggested_post_parameters",
    }
)

_ACTIVE_BOT_API_EXTRA: contextvars.ContextVar[tuple[frozenset[str], dict[str, Any]] | None] = contextvars.ContextVar(
    "telepilot_bot_api_extra",
    default=None,
)


def validate_bot_api_extra(action_type: str, extra: Any) -> tuple[dict[str, Any] | None, str | None]:
    """校验 ``bot_api_extra``；返回 ``(通过的字段, 错误说明)``，错误说明非空时整条动作拒绝。"""

    if extra in (None, {}):
        return None, None
    if not isinstance(extra, dict):
        return None, "bot_api_extra 必须是对象"
    allowed = BOT_API_EXTRA_FIELDS.get(action_type)
    if allowed is None:
        return None, f"动作 {action_type or '?'} 不支持 bot_api_extra"
    platform = sorted(key for key in extra if str(key) in BOT_API_PLATFORM_FIELDS)
    if platform:
        return None, f"bot_api_extra 不能覆盖平台字段：{', '.join(platform)}"
    unknown = sorted(str(key) for key in extra if str(key) not in allowed)
    if unknown:
        return None, f"bot_api_extra 字段不在 {action_type} 白名单：{', '.join(unknown)}（允许：{', '.join(sorted(allowed))}）"
    try:
        encoded = json.dumps(extra, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return None, "bot_api_extra 只能包含 JSON 值"
    if len(encoded.encode("utf-8")) > BOT_API_EXTRA_MAX_BYTES:
        return None, f"bot_api_extra 超过 {BOT_API_EXTRA_MAX_BYTES} 字节"
    return {str(key): value for key, value in extra.items()}, None


def activate_bot_api_extra(action: dict[str, Any]) -> contextvars.Token[Any] | None:
    """执行动作前调用：把通过校验的字段放进当前上下文，供 Bot API 请求合并。"""

    values = action.get("_tp_bot_api_extra")
    if not isinstance(values, dict) or not values:
        return None
    methods = BOT_API_EXTRA_METHODS.get(str(action.get("type") or "").strip())
    if not methods:
        return None
    return _ACTIVE_BOT_API_EXTRA.set((methods, dict(values)))


def reset_bot_api_extra(token: contextvars.Token[Any] | None) -> None:
    if token is not None:
        _ACTIVE_BOT_API_EXTRA.reset(token)


def bot_api_extra_for_method(method: str) -> dict[str, Any] | None:
    """当前动作给 ``method`` 的透传字段（不是该动作对应的方法时返回 None）。"""

    active = _ACTIVE_BOT_API_EXTRA.get()
    if not active:
        return None
    methods, values = active
    if method not in methods:
        return None
    return dict(values)


def merge_bot_api_extra_json(method: str, payload: dict[str, Any] | None) -> dict[str, Any] | None:
    extra = bot_api_extra_for_method(method)
    if not extra:
        return payload
    merged = dict(payload or {})
    for key, value in extra.items():
        # 平台已经给出的字段优先（包括平台合成的同名字段），透传只补缺。
        merged.setdefault(key, value)
    return merged


def merge_bot_api_extra_form(method: str, form_data: dict[str, Any] | None) -> dict[str, Any] | None:
    extra = bot_api_extra_for_method(method)
    if not extra:
        return form_data
    merged = dict(form_data or {})
    for key, value in extra.items():
        if key in merged:
            continue
        if isinstance(value, (dict, list)):
            merged[key] = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        elif isinstance(value, bool):
            merged[key] = "true" if value else "false"
        else:
            merged[key] = str(value)
    return merged


# ─────────────────────────────────────────────────────
# 动作预处理（run_action_batch 调用）
# ─────────────────────────────────────────────────────
def _note_deprecated(dep_id: str, *, plugin_key: str | None, account_id: int | None, detail: str) -> None:
    if not plugin_key:
        return
    try:
        from ...worker.plugins.deprecations import note_deprecated

        note_deprecated(dep_id, plugin_key=plugin_key, account_id=account_id, detail=detail)
    except Exception:  # noqa: BLE001 - 统计不能影响投递
        log.debug("记录弃用失败 %s", dep_id, exc_info=True)


def _action_plugin_key(action: dict[str, Any], default: str | None) -> str | None:
    context = action.get("context") if isinstance(action.get("context"), dict) else {}
    value = str(context.get("plugin_key") or default or "").strip()
    return value or None


def _reject(action: dict[str, Any], error_code: str, error: str) -> dict[str, Any]:
    action["_tp_reject"] = {"error_code": error_code, "error": error}
    return action


async def preprocess_plugin_action(
    action: dict[str, Any],
    *,
    account_id: int | None,
    redis_factory: Callable[[], Any] | None = None,
    plugin_key: str | None = None,
) -> dict[str, Any]:
    """分发前的通用预处理：回调 token 签发 + ``bot_api_extra`` 校验。幂等。"""

    if not isinstance(action, dict) or action.get("_tp_preprocessed"):
        return action
    action["_tp_preprocessed"] = True
    action_type = str(action.get("type") or "").strip()
    owner = _action_plugin_key(action, plugin_key)

    # Bot API 字段透传
    if "bot_api_extra" in action:
        values, error = validate_bot_api_extra(action_type, action.get("bot_api_extra"))
        if error:
            action["bot_api_extra"] = sorted(str(key) for key in (action.get("bot_api_extra") or {})) if isinstance(action.get("bot_api_extra"), dict) else []
            return _reject(action, BOT_API_EXTRA_REJECTED, error)
        action.pop("bot_api_extra", None)
        if values:
            action["_tp_bot_api_extra"] = values
            # Trace / 日志只记字段名
            action["bot_api_extra_fields"] = sorted(values)

    # 回调 token
    markups: list[tuple[dict[str, Any], str]] = []
    if isinstance(action.get("reply_markup"), dict):
        markups.append((action, "reply_markup"))
    result = action.get("result")
    if action_type == "answer_guest_query" and isinstance(result, dict) and isinstance(result.get("reply_markup"), dict):
        markups.append((result, "reply_markup"))
    if action_type == "answer_inline_query" and isinstance(action.get("results"), list):
        for item in action["results"]:
            if isinstance(item, dict) and isinstance(item.get("reply_markup"), dict):
                markups.append((item, "reply_markup"))
    if not markups:
        return action
    needs_token = any(markup_has_token_buttons(holder[key]) for holder, key in markups)
    if any(markup_has_raw_callback_data(holder[key]) for holder, key in markups):
        _note_deprecated("markup.raw_callback_data", plugin_key=owner, account_id=account_id, detail=action_type)
    if not needs_token:
        return action
    if not owner:
        return _reject(action, "callback_owner_unknown", "callback 按钮需要在插件动作中使用（缺少 plugin_key）")
    if account_id is None:
        return _reject(action, "callback_owner_unknown", "callback 按钮缺少账号上下文")
    try:
        redis = redis_factory() if redis_factory is not None else None
    except Exception:  # noqa: BLE001
        redis = None
    origin = callback_origin_for_action(action)
    budget = [CALLBACK_TOKENS_PER_ACTION_LIMIT]
    all_warnings: set[str] = set()
    minted = 0
    for holder, key in markups:
        before = holder[key]
        rewritten, warnings = await rewrite_reply_markup(
            before,
            redis=redis,
            account_id=int(account_id),
            plugin_key=owner,
            origin=origin,
            budget=budget,
        )
        all_warnings.update(warnings)
        if rewritten is None:
            holder.pop(key, None)
        else:
            holder[key] = rewritten
    minted = CALLBACK_TOKENS_PER_ACTION_LIMIT - budget[0]
    action["callback_tokens"] = minted
    if all_warnings:
        action["callback_token_warnings"] = sorted(all_warnings)
        log.warning(
            "插件回调按钮签发不完整 plugin=%s account=%s action=%s warnings=%s",
            owner,
            account_id,
            action_type,
            ",".join(sorted(all_warnings)),
        )
    return action


def action_rejection(action: dict[str, Any]) -> dict[str, str] | None:
    value = action.get("_tp_reject") if isinstance(action, dict) else None
    return value if isinstance(value, dict) else None


__all__ = [
    "BOT_API_EXTRA_FIELDS",
    "BOT_API_EXTRA_METHODS",
    "BOT_API_EXTRA_REJECTED",
    "BOT_API_PLATFORM_FIELDS",
    "CALLBACK_REASON_EXPIRED",
    "CALLBACK_REASON_NOT_OWNED",
    "CALLBACK_REASON_UNAVAILABLE",
    "CALLBACK_TOKEN_PREFIX",
    "CallbackTokenUnavailable",
    "action_rejection",
    "activate_bot_api_extra",
    "bot_api_extra_for_method",
    "callback_origin_for_action",
    "is_callback_token",
    "merge_bot_api_extra_form",
    "merge_bot_api_extra_json",
    "mint_callback_token",
    "preprocess_plugin_action",
    "reset_bot_api_extra",
    "resolve_callback_token",
    "rewrite_reply_markup",
    "validate_bot_api_extra",
]
