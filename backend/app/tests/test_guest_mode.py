"""Bot API 10.0 Guest Mode（访客模式）回归测试。

覆盖：guest_message 解析、allowed_updates 开关、Event Bus 信封与 guest_all 范围、
交付执行器把消息类动作写进一次性回复缓冲、answer_guest_query 动作、
answerGuestQuery 只调用一次与纯文本兜底、交互 Bot / 管理 Bot 端到端路由、限流、
以及 /bot/capabilities 的 supports_guest_queries。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.services import account_bot_runtime, account_bot_service
from app.services.event_bus import match_subscriptions, normalize_bot_update, normalize_event_subscription
from app.services.event_trace import TRACE_STATUS_OK, TRACE_STATUS_SKIPPED
from app.services.interaction import guest_reply
from app.services.interaction.delivery import InteractionDeliveryExecutor
from app.services.interaction.guest_reply import (
    GuestRateLimiter,
    GuestReplyBuffer,
    compose_guest_results,
    flush_guest_reply,
    guest_result_from_action,
    sanitize_guest_reply_markup,
    strip_guest_bot_mentions,
)
from app.worker.plugins.message_ops import BufferedMessageOps

GUEST_CHAT = -100555


def _guest_update(text: str = "@telepilot_demo_bot 规则", *, update_id: int = 9001, user_id: int = 4242) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "guest_message": {
            "message_id": 77,
            "guest_query_id": "gq-123",
            "date": 1_778_000_000,
            "text": text,
            "from": {"id": user_id, "first_name": "Guest", "username": "guest_user"},
            "chat": {"id": GUEST_CHAT, "type": "supergroup", "title": "外部群"},
            "reply_to_message": {
                "message_id": 76,
                "text": "被回复的原文",
                "from": {"id": 5151, "first_name": "Other"},
                "chat": {"id": GUEST_CHAT, "type": "supergroup"},
            },
        },
    }


@pytest.fixture(autouse=True)
def _reset_guest_rate_limiter() -> None:
    guest_reply.guest_rate_limiter.reset()
    yield
    guest_reply.guest_rate_limiter.reset()


class _DB:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def commit(self):
        return None


# ---------------------------------------------------------------------------
# 解析与订阅
# ---------------------------------------------------------------------------
def test_extract_incoming_parses_guest_message() -> None:
    incoming = account_bot_runtime._extract_incoming(1, "tok", _guest_update())

    assert incoming is not None
    assert incoming.event_type == "guest_message"
    assert incoming.guest_query_id == "gq-123"
    assert incoming.chat_id == GUEST_CHAT
    assert incoming.user_id == 4242
    assert incoming.username == "guest_user"
    assert incoming.text == "规则"
    assert incoming.guest_raw_text == "@telepilot_demo_bot 规则"
    assert incoming.reply_to_message_id == 76
    assert incoming.reply_to_text == "被回复的原文"
    assert account_bot_runtime._incoming_event_type(incoming) == "guest_message"


def test_normal_message_has_no_guest_query_id() -> None:
    incoming = account_bot_runtime._extract_incoming(
        1,
        "tok",
        {
            "update_id": 1,
            "message": {
                "message_id": 1,
                "text": "@telepilot_demo_bot hi",
                "from": {"id": 1},
                "chat": {"id": -100, "type": "group"},
            },
        },
    )
    assert incoming is not None
    assert incoming.guest_query_id is None
    assert incoming.text == "@telepilot_demo_bot hi"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("@MyHelperBot 翻译一下", "翻译一下"),
        ("@a_bot @b_bot, /status", "/status"),
        ("帮我总结 @summary_bot", "帮我总结"),
        ("问问 @other_bot 怎么看", "问问 @other_bot 怎么看"),
        ("纯文本", "纯文本"),
    ],
)
def test_strip_guest_bot_mentions(raw: str, expected: str) -> None:
    assert strip_guest_bot_mentions(raw) == expected


def test_allowed_updates_include_guest_message_by_default_and_respect_flag() -> None:
    base = ["message", "edited_message", "callback_query"]
    assert "guest_message" in account_bot_runtime._inline_update_allowed_updates({}, base)
    off = account_bot_runtime._inline_update_allowed_updates(
        {"guest_updates_enabled": False, "inline_updates_enabled": False},
        base,
    )
    assert off == base


def test_normalize_bot_update_guest_envelope_and_guest_all_scope() -> None:
    event = normalize_bot_update(1, _guest_update())

    assert event["event_type"] == "guest_message"
    assert event["source"]["guest_query_id"] == "gq-123"
    assert event["guest"]["query_id"] == "gq-123"
    assert event["guest"]["reply_once"] is True
    assert event["chat"]["id"] == GUEST_CHAT

    guest_all = normalize_event_subscription({"events": ["guest_message"], "scope": "guest_all"}, plugin_key="ai_guest")
    message_only = normalize_event_subscription({"events": ["message"]}, plugin_key="legacy_msg")
    all_events = normalize_event_subscription({"events": ["all_events"]}, plugin_key="wide")
    allowed_only = normalize_event_subscription({"events": ["guest_message"]}, plugin_key="scoped")
    decisions = match_subscriptions(
        event,
        [guest_all, message_only, all_events, allowed_only],
        {"allowed_chat_ids": [-100999], "owner_user_ids": [], "known_user_ids": []},
    )
    by_key = {item.plugin_key: item for item in decisions}
    assert by_key["ai_guest"].matched is True
    # 旧插件只订阅 message / all_events 不会被访客消息意外唤起。
    assert by_key["legacy_msg"].reason_code == "event_type_not_subscribed"
    assert by_key["wide"].reason_code == "event_type_not_subscribed"
    # all_allowed_chats 仍按会话收窄：访客会话不在列表中则跳过。
    assert by_key["scoped"].reason_code == "scope_not_matched"

    guest_all_inline = normalize_event_subscription({"events": ["message"], "scope": "guest_all"}, plugin_key="x")
    plain_event = normalize_bot_update(
        1,
        {"update_id": 2, "message": {"message_id": 1, "text": "hi", "chat": {"id": -1, "type": "group"}, "from": {"id": 1}}},
    )
    assert match_subscriptions(plain_event, [guest_all_inline], {"allowed_chat_ids": "*"})[0].matched is False


def test_incoming_trace_payload_carries_guest_envelope() -> None:
    incoming = account_bot_runtime._extract_incoming(1, "tok", _guest_update())
    assert incoming is not None
    payload = account_bot_runtime._incoming_trace_payload(incoming)

    assert payload["event_type"] == "guest_message"
    assert payload["source"]["guest_query_id"] == "gq-123"
    assert payload["guest"]["reply_via"] == "answer_guest_query"
    assert payload["message"]["text"] == "规则"
    assert payload["chat"]["title"] == "外部群"


def test_classify_routes_guest_message_skips_money_routes_and_keeps_keyword_scope() -> None:
    cfg = {
        "enabled": True,
        "interaction_bot_id": 999,
        "query_commands": ["规则"],
        "rules": [{"id": "r1", "name": "全局", "enabled": True}],
        "transfer_test_chat_ids": [GUEST_CHAT],
    }
    index = account_bot_runtime._build_interaction_routing_index(cfg, subscriptions=[], active_session_chat_ids=set())
    incoming = account_bot_runtime._extract_incoming(1, "tok", _guest_update("@demo_bot +5"))
    assert incoming is not None
    routes = account_bot_runtime._classify_interaction_routes(incoming, index, event_bus_enabled=True)
    assert account_bot_runtime._ROUTE_TRANSFER_COMMAND not in routes

    incoming = account_bot_runtime._extract_incoming(1, "tok", _guest_update("@demo_bot 规则"))
    assert incoming is not None
    routes = account_bot_runtime._classify_interaction_routes(incoming, index, event_bus_enabled=True)
    assert account_bot_runtime._ROUTE_KEYWORD in routes

    scoped_cfg = {**cfg, "rules": [{"id": "r1", "enabled": True, "chat_ids": [-100999]}]}
    scoped_index = account_bot_runtime._build_interaction_routing_index(
        scoped_cfg,
        subscriptions=[],
        active_session_chat_ids=set(),
    )
    routes = account_bot_runtime._classify_interaction_routes(incoming, scoped_index, event_bus_enabled=True)
    assert account_bot_runtime._ROUTE_KEYWORD not in routes

    guest_sub = normalize_event_subscription({"events": ["guest_message"], "scope": "guest_all"}, plugin_key="ai")
    bus_index = account_bot_runtime._build_interaction_routing_index(
        scoped_cfg,
        subscriptions=[guest_sub],
        active_session_chat_ids=set(),
    )
    routes = account_bot_runtime._classify_interaction_routes(incoming, bus_index, event_bus_enabled=True)
    assert account_bot_runtime._ROUTE_EVENT_BUS in routes


# ---------------------------------------------------------------------------
# 回复缓冲与 answerGuestQuery
# ---------------------------------------------------------------------------
def test_guest_buffer_send_edit_delete_compose() -> None:
    buffer = GuestReplyBuffer(query_id="gq")
    placeholder = buffer.add("⏳ 正在思考…")
    buffer.replace(placeholder, "<b>答案</b>：42")
    extra = buffer.add("附加说明 <code>x</code>", reply_markup={
        "inline_keyboard": [[
            {"text": "打开", "url": "https://example.com"},
            {"text": "回调", "callback_data": "cb"},
        ]]
    })
    temp = buffer.add("临时")
    buffer.remove(temp)

    results = compose_guest_results(buffer)
    assert results[0]["type"] == "article"
    content = results[0]["input_message_content"]
    assert content["parse_mode"] == "HTML"
    assert content["message_text"] == "<b>答案</b>：42\n\n附加说明 <code>x</code>"
    assert results[0]["reply_markup"] == {"inline_keyboard": [[{"text": "打开", "url": "https://example.com"}]]}
    assert results[-1]["input_message_content"] == {"message_text": "答案：42\n\n附加说明 x"}
    assert extra == 2


def test_guest_compose_truncates_long_html_to_limit() -> None:
    buffer = GuestReplyBuffer(query_id="gq")
    buffer.add("<b>" + ("长" * 5000) + "</b>")
    results = compose_guest_results(buffer)
    for result in results:
        text = result["input_message_content"]["message_text"]
        assert len(text) <= guest_reply.GUEST_MESSAGE_TEXT_LIMIT
    assert results[-1]["input_message_content"]["message_text"].endswith("…")


def test_guest_compose_single_rich_entry_uses_rich_message_content() -> None:
    buffer = GuestReplyBuffer(query_id="gq")
    buffer.add("", kind="rich", rich_message={"markdown": "# 标题\n- a"})
    results = compose_guest_results(buffer)
    assert results[0]["input_message_content"] == {"rich_message": {"markdown": "# 标题\n- a"}}
    assert "parse_mode" not in results[-1]["input_message_content"]


def test_sanitize_guest_reply_markup_drops_callback_only() -> None:
    assert sanitize_guest_reply_markup({"inline_keyboard": [[{"text": "x", "callback_data": "y"}]]}) is None
    assert sanitize_guest_reply_markup(None) is None


@pytest.mark.asyncio
async def test_flush_guest_reply_answers_once_with_html(monkeypatch) -> None:
    answer = AsyncMock(return_value={"inline_message_id": "im-1"})
    monkeypatch.setattr(account_bot_service, "answer_guest_query", answer)
    buffer = GuestReplyBuffer(query_id="gq-1")
    buffer.add("<b>ok</b>")

    outcome = await flush_guest_reply("tok", buffer)
    again = await flush_guest_reply("tok", buffer)

    assert outcome["status"] == "answered"
    assert outcome["format"] == "html"
    assert again["status"] == "skipped"
    assert again["reason_code"] == guest_reply.GUEST_REASON_REPLY_ALREADY_USED
    answer.assert_awaited_once()
    assert answer.await_args.args[:2] == ("tok", "gq-1")
    assert answer.await_args.kwargs["result"]["input_message_content"]["message_text"] == "<b>ok</b>"


@pytest.mark.asyncio
async def test_flush_guest_reply_falls_back_to_plain_text(monkeypatch) -> None:
    answer = AsyncMock(side_effect=[RuntimeError("Bad Request: can't parse entities"), {"inline_message_id": "im"}])
    monkeypatch.setattr(account_bot_service, "answer_guest_query", answer)
    buffer = GuestReplyBuffer(query_id="gq-2")
    buffer.add("<b>broken")

    outcome = await flush_guest_reply("tok", buffer)

    assert outcome["status"] == "answered"
    assert outcome["format"] == "plain"
    assert answer.await_count == 2
    assert "parse_mode" not in answer.await_args.kwargs["result"]["input_message_content"]


@pytest.mark.asyncio
async def test_flush_guest_reply_without_content_is_skipped(monkeypatch) -> None:
    answer = AsyncMock()
    monkeypatch.setattr(account_bot_service, "answer_guest_query", answer)
    outcome = await flush_guest_reply("tok", GuestReplyBuffer(query_id="gq"))
    assert outcome == {"status": "skipped", "reason_code": guest_reply.GUEST_REASON_NO_REPLY}
    answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_answer_guest_query_bot_api_payload(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    async def _call(token: str, method: str, payload: dict[str, Any], **_kwargs: Any):
        calls.append((method, payload))
        return {"inline_message_id": "im"}

    monkeypatch.setattr(account_bot_service, "call_bot_api", _call)
    result = {"type": "article", "id": "1", "title": "t", "input_message_content": {"message_text": "hi"}}
    sent = await account_bot_service.answer_guest_query("tok", "gq", result=result)

    assert sent == {"inline_message_id": "im"}
    assert calls == [("answerGuestQuery", {"guest_query_id": "gq", "result": result})]


def test_guest_result_from_action_variants() -> None:
    text_result = guest_result_from_action({"text": "<b>hi</b>", "parse_mode": "html", "title": "标题"})
    assert text_result is not None
    assert text_result["title"] == "标题"
    assert text_result["input_message_content"] == {"message_text": "<b>hi</b>", "parse_mode": "HTML"}

    custom = guest_result_from_action({"result": {"type": "photo", "photo_url": "https://x/y.jpg", "thumbnail_url": "https://x/t.jpg"}})
    assert custom is not None and custom["type"] == "photo" and custom["id"]

    rich = guest_result_from_action({"rich_message": {"markdown": "**hi**"}})
    assert rich is not None and rich["input_message_content"] == {"rich_message": {"markdown": "**hi**"}}

    assert guest_result_from_action({}) is None


@pytest.mark.asyncio
async def test_message_ops_answer_guest_query_action_shape() -> None:
    ops = BufferedMessageOps()
    action = await ops.answer_guest_query(text="hello", parse_mode="html")
    assert action == {"type": "answer_guest_query", "guest_query_id": None, "parse_mode": "html", "text": "hello"}
    assert ops.actions == [action]


def test_guest_rate_limiter_per_user_and_window() -> None:
    limiter = GuestRateLimiter(per_user=2, per_bot=10, window_seconds=60)
    assert limiter.allow(account_id=1, role="interaction_bot", user_id=5, now=0)
    assert limiter.allow(account_id=1, role="interaction_bot", user_id=5, now=1)
    assert not limiter.allow(account_id=1, role="interaction_bot", user_id=5, now=2)
    assert limiter.allow(account_id=1, role="interaction_bot", user_id=6, now=2)
    assert limiter.allow(account_id=1, role="interaction_bot", user_id=5, now=61)


# ---------------------------------------------------------------------------
# 交付执行器（E2）在访客模式下的行为
# ---------------------------------------------------------------------------
def _guest_executor(monkeypatch, *, buffer: GuestReplyBuffer | None = None):
    incoming = account_bot_runtime._extract_incoming(1, "tok", _guest_update())
    assert incoming is not None
    incoming.guest_reply = buffer or GuestReplyBuffer(query_id="gq-123")
    records = AsyncMock()
    monkeypatch.setattr("app.services.interaction.delivery.record_action", records)
    write_log = AsyncMock()
    executor = InteractionDeliveryExecutor(
        incoming=incoming,
        write_log=write_log,
        run_worker_action=AsyncMock(side_effect=AssertionError("guest mode must not call userbot")),
        log_context=lambda _incoming: {},
        trace_context=lambda _payload: {},
    )
    return incoming, executor, records


@pytest.mark.asyncio
async def test_delivery_executor_captures_messages_into_guest_buffer(monkeypatch) -> None:
    send_message = AsyncMock(side_effect=AssertionError("guest mode must not sendMessage"))
    monkeypatch.setattr(account_bot_service, "send_message", send_message)
    monkeypatch.setattr(account_bot_service, "send_rich_message", send_message)
    incoming, executor, records = _guest_executor(monkeypatch)

    await executor.apply(
        [
            {"type": "send_message", "send_via": "userbot_reply", "text": "⏳ 处理中", "chat_id": GUEST_CHAT},
            {"type": "edit_message", "message_id": 1, "text": "<b>结果</b>", "parse_mode": "html"},
            {"type": "payout", "amount": 10, "chat_id": GUEST_CHAT},
            {"type": "pin_message", "message_id": 1},
            {"type": "send_photo", "photo_base64": "AAAA", "caption": "图片说明"},
        ]
    )

    buffer = incoming.guest_reply
    assert buffer is not None
    assert [(item.kind, item.content) for item in buffer.entries] == [("html", "<b>结果</b>"), ("plain", "图片说明")]
    assert set(buffer.skipped_actions) >= {"payout", "pin_message", "send_photo"}
    send_message.assert_not_awaited()
    statuses = [(call.args[1]["type"], call.args[2], call.kwargs.get("reason_code")) for call in records.await_args_list]
    assert ("payout", TRACE_STATUS_SKIPPED, guest_reply.GUEST_REASON_ACTION_UNSUPPORTED) in statuses
    assert ("send_message", TRACE_STATUS_OK, guest_reply.GUEST_REASON_CAPTURED) in statuses


@pytest.mark.asyncio
async def test_delivery_executor_answer_guest_query_action_once(monkeypatch) -> None:
    answer = AsyncMock(return_value={"inline_message_id": "im"})
    monkeypatch.setattr(account_bot_service, "answer_guest_query", answer)
    incoming, executor, records = _guest_executor(monkeypatch)

    await executor.apply(
        [
            {"type": "answer_guest_query", "text": "第一次"},
            {"type": "answer_guest_query", "text": "第二次"},
            {"type": "send_message", "text": "之后的消息"},
        ]
    )

    answer.assert_awaited_once()
    assert answer.await_args.args[1] == "gq-123"
    assert incoming.guest_reply is not None and incoming.guest_reply.answered is True
    reasons = [call.kwargs.get("reason_code") for call in records.await_args_list]
    assert reasons.count(guest_reply.GUEST_REASON_REPLY_ALREADY_USED) == 2


@pytest.mark.asyncio
async def test_delivery_executor_answer_guest_query_missing_id(monkeypatch) -> None:
    answer = AsyncMock()
    monkeypatch.setattr(account_bot_service, "answer_guest_query", answer)
    records = AsyncMock()
    monkeypatch.setattr("app.services.interaction.delivery.record_action", records)
    incoming = account_bot_runtime.Incoming(account_id=1, token="tok", update_id=1, user_id=1, chat_id=-1, message_id=1, text="x")
    executor = InteractionDeliveryExecutor(
        incoming=incoming,
        write_log=AsyncMock(),
        run_worker_action=AsyncMock(),
        log_context=lambda _incoming: {},
        trace_context=lambda _payload: {},
    )
    await executor.apply([{"type": "answer_guest_query", "text": "hi"}])
    answer.assert_not_awaited()
    assert records.await_args.kwargs["error_code"] == guest_reply.GUEST_REASON_QUERY_ID_MISSING


# ---------------------------------------------------------------------------
# 端到端：交互 Bot / 管理 Bot
# ---------------------------------------------------------------------------
def _patch_interaction_runtime(monkeypatch, flags: dict[str, Any]) -> None:
    monkeypatch.setattr(account_bot_runtime, "AsyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(account_bot_runtime, "_event_framework_flags", AsyncMock(return_value=flags))
    monkeypatch.setattr(account_bot_runtime, "_interaction_bot_capability_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(account_bot_runtime, "_router_debug_trace_enabled", AsyncMock(return_value=False))
    monkeypatch.setattr(account_bot_runtime, "_incoming_is_userbot_command_text", AsyncMock(return_value=False))
    monkeypatch.setattr(account_bot_runtime, "_cached_interaction_routing_state", lambda _aid: None)
    monkeypatch.setattr(account_bot_runtime, "_interaction_strict_trace_requested", AsyncMock(return_value=False))
    monkeypatch.setattr(account_bot_runtime, "_write_interaction_runtime_log", AsyncMock())


@pytest.mark.asyncio
async def test_interaction_guest_message_runs_query_command_and_answers_once(monkeypatch) -> None:
    cfg = {
        "enabled": True,
        "interaction_bot_id": 999,
        "query_commands": ["规则"],
        "rules": [{"id": "r1", "name": "全局", "enabled": True}],
    }
    _patch_interaction_runtime(monkeypatch, {"trace_enabled": False, "event_bus_delivery_enabled": False})
    monkeypatch.setattr(account_bot_service, "get_transfer_notice_config", AsyncMock(return_value=cfg))
    monkeypatch.setattr(account_bot_runtime, "_render_interaction_rules_query", AsyncMock(return_value="<b>本群规则</b>"))
    send_message = AsyncMock(side_effect=AssertionError("guest mode must not sendMessage"))
    monkeypatch.setattr(account_bot_service, "send_message", send_message)
    monkeypatch.setattr(account_bot_service, "send_rich_message", send_message)
    answer = AsyncMock(return_value={"inline_message_id": "im"})
    monkeypatch.setattr(account_bot_service, "answer_guest_query", answer)

    await account_bot_runtime._handle_interaction_update(1, "bbot", _guest_update("@demo_bot 规则"))

    answer.assert_awaited_once()
    assert answer.await_args.args[:2] == ("bbot", "gq-123")
    content = answer.await_args.kwargs["result"]["input_message_content"]
    assert content == {"message_text": "<b>本群规则</b>", "parse_mode": "HTML"}
    send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_interaction_guest_message_routes_event_bus_actions_to_guest_reply(monkeypatch) -> None:
    cfg = {"enabled": True, "interaction_bot_id": 999, "rules": [], "chat_ids": [-100999]}
    _patch_interaction_runtime(monkeypatch, {"trace_enabled": False, "event_bus_delivery_enabled": True})
    monkeypatch.setattr(account_bot_service, "get_transfer_notice_config", AsyncMock(return_value=cfg))
    subscription = normalize_event_subscription(
        {"events": ["guest_message"], "scope": "guest_all", "entry_key": "ask"},
        plugin_key="ai_guest",
    )
    monkeypatch.setattr(account_bot_runtime, "_load_enabled_event_bus_subscriptions", AsyncMock(return_value=[subscription]))
    monkeypatch.setattr(
        account_bot_runtime,
        "_event_bus_cached_known_user_ids",
        AsyncMock(return_value=([], set())),
    )
    monkeypatch.setattr(account_bot_runtime, "_event_bus_active_session_participant_ids", AsyncMock(return_value=set()))
    captured: dict[str, Any] = {}

    async def _run_entry(incoming, *, plugin_key, entry_key, payload):
        captured["payload"] = payload
        captured["plugin_key"] = plugin_key
        return True, None, [
            {"type": "send_message", "text": "AI 思考中…"},
            {"type": "edit_message", "message_id": 1, "text": "AI 的最终回答"},
        ]

    monkeypatch.setattr(account_bot_runtime, "_run_worker_interaction_entry", _run_entry)
    monkeypatch.setattr(account_bot_runtime, "_guard_interaction_actions", AsyncMock(side_effect=lambda _i, _r, actions: actions))
    monkeypatch.setattr("app.services.interaction.delivery.record_action", AsyncMock())
    answer = AsyncMock(return_value={"inline_message_id": "im"})
    monkeypatch.setattr(account_bot_service, "answer_guest_query", answer)

    await account_bot_runtime._handle_interaction_update(1, "bbot", _guest_update("@demo_bot 今天天气怎么样"))

    assert captured["plugin_key"] == "ai_guest"
    assert captured["payload"]["event_type"] == "guest_message"
    assert captured["payload"]["guest"]["query_id"] == "gq-123"
    assert captured["payload"]["message_text"] == "今天天气怎么样"
    answer.assert_awaited_once()
    assert answer.await_args.kwargs["result"]["input_message_content"]["message_text"] == "AI 的最终回答"


@pytest.mark.asyncio
async def test_interaction_guest_message_ignored_when_flag_off(monkeypatch) -> None:
    _patch_interaction_runtime(monkeypatch, {"guest_updates_enabled": False})
    get_config = AsyncMock()
    monkeypatch.setattr(account_bot_service, "get_transfer_notice_config", get_config)
    answer = AsyncMock()
    monkeypatch.setattr(account_bot_service, "answer_guest_query", answer)

    await account_bot_runtime._handle_interaction_update(1, "bbot", _guest_update())

    get_config.assert_not_awaited()
    answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_interaction_guest_message_rate_limited(monkeypatch) -> None:
    _patch_interaction_runtime(monkeypatch, {"trace_enabled": False, "event_bus_delivery_enabled": False})
    get_config = AsyncMock(return_value={"enabled": True, "rules": []})
    monkeypatch.setattr(account_bot_service, "get_transfer_notice_config", get_config)
    monkeypatch.setattr(account_bot_service, "answer_guest_query", AsyncMock())
    monkeypatch.setattr(guest_reply.guest_rate_limiter, "per_user", 1)

    await account_bot_runtime._handle_interaction_update(1, "bbot", _guest_update(update_id=1))
    await account_bot_runtime._handle_interaction_update(1, "bbot", _guest_update(update_id=2))

    assert get_config.await_count == 1
    account_bot_runtime._write_interaction_runtime_log.assert_awaited()
    assert (
        account_bot_runtime._write_interaction_runtime_log.await_args.kwargs["reason_code"]
        == guest_reply.GUEST_REASON_RATE_LIMITED
    )


@pytest.mark.asyncio
async def test_management_guest_message_routes_command_and_keeps_last_chat(monkeypatch) -> None:
    user = SimpleNamespace(enabled=True, role="admin", last_chat_id=111, display_name="Owner")
    seen: list[account_bot_runtime.Incoming] = []

    async def _handle_command(incoming, role):
        seen.append(incoming)
        placeholder = await account_bot_runtime._send(incoming, "⏳ 正在查询…")
        await account_bot_runtime._send(
            incoming,
            "<b>状态</b>：在线",
            edit=True,
            edit_message_id=placeholder["message_id"],
            reply_markup={"inline_keyboard": [[{"text": "刷新", "callback_data": "x"}]]},
        )

    monkeypatch.setattr(
        account_bot_runtime,
        "_event_framework_flags",
        AsyncMock(return_value={"trace_enabled": False}),
    )
    monkeypatch.setattr(account_bot_runtime, "AsyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(account_bot_service, "find_bot_user", AsyncMock(return_value=user))
    monkeypatch.setattr(account_bot_runtime, "_router_debug_trace_enabled", AsyncMock(return_value=False))
    monkeypatch.setattr(account_bot_runtime, "_handle_command", _handle_command)
    monkeypatch.setattr(account_bot_runtime, "record_action", AsyncMock())
    send_message = AsyncMock(side_effect=AssertionError("guest mode must not sendMessage"))
    monkeypatch.setattr(account_bot_service, "send_message", send_message)
    monkeypatch.setattr(account_bot_service, "send_rich_message", send_message)
    answer = AsyncMock(return_value={"inline_message_id": "im"})
    monkeypatch.setattr(account_bot_service, "answer_guest_query", answer)

    await account_bot_runtime._handle_update(1, "mbot", _guest_update("@manager_bot /status"))

    assert seen and seen[0].text == "/status"
    assert user.last_chat_id == 111
    answer.assert_awaited_once()
    result = answer.await_args.kwargs["result"]
    assert result["input_message_content"] == {"message_text": "<b>状态</b>：在线", "parse_mode": "HTML"}
    assert "reply_markup" not in result
    send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_management_guest_free_text_goes_to_agent_in_groups(monkeypatch) -> None:
    user = SimpleNamespace(enabled=True, role="admin", last_chat_id=None, display_name=None)
    agent_calls: list[dict[str, Any]] = []

    async def _run_agent_query(**kwargs):
        agent_calls.append(kwargs)
        with pytest.raises(ValueError):
            await kwargs["draft"]("预览")
        await kwargs["send"]("最终回答", rich_markdown="**最终回答**")

    from app.services.system_agent import bot_bridge

    monkeypatch.setattr(bot_bridge, "is_agent_mode", AsyncMock(return_value=True))
    monkeypatch.setattr(bot_bridge, "run_agent_query", _run_agent_query)
    monkeypatch.setattr(account_bot_runtime, "_event_framework_flags", AsyncMock(return_value={"trace_enabled": False}))
    monkeypatch.setattr(account_bot_runtime, "AsyncSessionLocal", lambda: _DB())
    monkeypatch.setattr(account_bot_service, "find_bot_user", AsyncMock(return_value=user))
    monkeypatch.setattr(account_bot_runtime, "_router_debug_trace_enabled", AsyncMock(return_value=False))
    monkeypatch.setattr(account_bot_runtime, "record_action", AsyncMock())
    answer = AsyncMock(return_value={"inline_message_id": "im"})
    monkeypatch.setattr(account_bot_service, "answer_guest_query", answer)

    await account_bot_runtime._handle_update(1, "mbot", _guest_update("@manager_bot 帮我看看今天的收入"))

    assert agent_calls and agent_calls[0]["text"] == "帮我看看今天的收入"
    assert user.last_chat_id is None
    answer.assert_awaited_once()
    assert answer.await_args.kwargs["result"]["input_message_content"]["message_text"] == "最终回答"


# ---------------------------------------------------------------------------
# getMe 能力位 / API
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_get_bot_capabilities_reads_supports_guest_queries(monkeypatch) -> None:
    account_bot_service._BOT_CAPABILITIES_CACHE.clear()
    get_me = AsyncMock(
        return_value={
            "id": 42,
            "is_bot": True,
            "username": "demo_bot",
            "supports_guest_queries": True,
            "supports_inline_queries": False,
        }
    )
    monkeypatch.setattr(account_bot_service, "get_me", get_me)

    caps = await account_bot_service.get_bot_capabilities("tok")
    cached = await account_bot_service.get_bot_capabilities("tok")
    await account_bot_service.get_bot_capabilities("tok", refresh=True)

    assert caps["supports_guest_queries"] is True
    assert caps["supports_inline_queries"] is False
    assert caps["username"] == "demo_bot"
    assert cached == caps
    assert get_me.await_count == 2
    assert account_bot_service.bot_capabilities_from_me({"id": 1})["supports_guest_queries"] is None
    account_bot_service._BOT_CAPABILITIES_CACHE.clear()


@pytest.mark.asyncio
async def test_bot_capabilities_api_reports_both_bots(monkeypatch) -> None:
    from app.api import account_bots as api_mod

    class _Result:
        def scalar_one_or_none(self):
            return SimpleNamespace(bot_token_enc="enc")

    class _Session:
        async def execute(self, *_args, **_kwargs):
            return _Result()

    monkeypatch.setattr(account_bot_service, "ensure_account", AsyncMock())
    monkeypatch.setattr(account_bot_service, "decrypt_bot_token", lambda _row: "mtok")
    monkeypatch.setattr(account_bot_service, "get_interaction_bot_token", AsyncMock(return_value=None))
    monkeypatch.setattr(
        account_bot_runtime,
        "_event_framework_flags",
        AsyncMock(return_value={"guest_updates_enabled": True}),
    )
    monkeypatch.setattr(
        account_bot_service,
        "get_bot_capabilities",
        AsyncMock(return_value={"bot_id": 7, "username": "mgr_bot", "supports_guest_queries": False}),
    )

    response = await api_mod.get_account_bot_capabilities(1, _Session(), SimpleNamespace(id=1), refresh=False)

    assert response.management_bot.configured is True
    assert response.management_bot.supports_guest_queries is False
    assert response.management_bot.username == "mgr_bot"
    assert response.interaction_bot.configured is False
    assert response.guest_updates_enabled is True
    assert "Guest Mode" in response.guest_mode_help


@pytest.mark.asyncio
async def test_delivery_executor_replaces_module_placeholder_in_guest_buffer(monkeypatch) -> None:
    buffer = GuestReplyBuffer(query_id="gq-123")
    placeholder = buffer.add("模块启动中…")
    incoming, executor, _records = _guest_executor(monkeypatch, buffer=buffer)

    await executor.apply([{"type": "send_message", "text": "第 1 题：1 + 1 = ?"}], replace_message_id=placeholder)

    assert [item.content for item in buffer.entries] == ["第 1 题：1 + 1 = ?"]


@pytest.mark.asyncio
async def test_guest_start_session_actions_are_skipped(monkeypatch) -> None:
    incoming = account_bot_runtime._extract_incoming(1, "tok", _guest_update())
    assert incoming is not None
    start = AsyncMock()
    monkeypatch.setattr(account_bot_runtime, "_apply_interaction_start_session_action", start)

    await account_bot_runtime._apply_interaction_start_session_actions(
        incoming,
        {"id": "r1"},
        [{"type": "start_session", "data": {}}],
    )

    start.assert_not_awaited()
