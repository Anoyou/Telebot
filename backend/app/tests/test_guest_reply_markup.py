"""访客模式（Bot API 10.0 Guest Mode）回复的 callback 按钮（0.106.0-beta.17）。

覆盖：
- 插件显式 ``answer_guest_query``（text / rich_message / 完整 result）保留 callback_data 按钮，
  平台合成的访客回复缓冲仍然只保留 URL/复制类按钮；
- 实时 ``ctx.messages.apply`` 把 SentGuestMessage.inline_message_id 交还给插件；能力标记 ``guest_reply_markup``；
- 访客回复上的按钮点击（只有 inline_message_id + from，没有 message/chat）解析、Event Bus 信封与
  ``guest_all`` 范围匹配，端到端投递给插件并按 inline_message_id 编辑文本与按钮、answer_callback；
- 记下 guest_query_id → inline_message_id → 原访客会话，inline callback 投递时带上 ``origin``；
- 回归：非访客群里失败消息的 ``edit_message`` 带 ``reply_markup``（含 ``send_on_edit_failure`` 改新发）原样透传。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.services import account_bot_runtime, account_bot_service
from app.services.event_bus import match_subscriptions, normalize_bot_update, normalize_event_subscription
from app.services.interaction import guest_reply
from app.services.interaction.guest_reply import (
    GuestReplyBuffer,
    compose_guest_results,
    guest_result_from_action,
)
from app.tests.test_guest_mode import _patch_interaction_runtime
from app.worker.plugins import loader as loader_mod
from app.worker.plugins.message_ops import BufferedMessageOps

RETRY_MARKUP = {
    "inline_keyboard": [
        [
            {"text": "🔄 重试", "callback_data": "iac:r:0123456789ab"},
            {"text": "🔀 换模型重试", "callback_data": "iac:m:0123456789ab"},
        ]
    ]
}
GUEST_CHAT = -100555


def _inline_callback_update(*, user_id: int = 4242, data: str = "iac:r:0123456789ab") -> dict[str, Any]:
    return {
        "update_id": 9101,
        "callback_query": {
            "id": "cq-inline-1",
            "from": {"id": user_id, "first_name": "Clicker", "username": "clicker"},
            "inline_message_id": "im-guest-9",
            "chat_instance": "ci-777",
            "data": data,
        },
    }


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {}

    async def get(self, key: str) -> Any:
        return self.values.get(key)

    async def set(self, key: str, value: Any, *args: Any, **kwargs: Any) -> bool:
        self.values[key] = value
        return True

    async def rpush(self, *args: Any, **kwargs: Any) -> int:
        return 1

    async def ltrim(self, *args: Any, **kwargs: Any) -> bool:
        return True

    async def publish(self, *args: Any, **kwargs: Any) -> int:
        return 0

    async def expire(self, *args: Any, **kwargs: Any) -> bool:
        return True


# ---------------------------------------------------------------------------
# answer_guest_query 透传 callback 按钮
# ---------------------------------------------------------------------------
def test_guest_result_from_action_keeps_callback_buttons() -> None:
    text = guest_result_from_action({"text": "失败了", "parse_mode": "html", "reply_markup": RETRY_MARKUP})
    assert text is not None and text["reply_markup"] == RETRY_MARKUP

    rich = guest_result_from_action({"rich_message": {"markdown": "**失败**"}, "reply_markup": RETRY_MARKUP})
    assert rich is not None and rich["reply_markup"] == RETRY_MARKUP
    assert rich["input_message_content"] == {"rich_message": {"markdown": "**失败**"}}

    custom = guest_result_from_action(
        {
            "result": {
                "type": "article",
                "title": "t",
                "input_message_content": {"message_text": "x"},
                "reply_markup": RETRY_MARKUP,
            }
        }
    )
    assert custom is not None and custom["reply_markup"] == RETRY_MARKUP


def test_platform_composed_guest_reply_still_drops_callback_buttons() -> None:
    buffer = GuestReplyBuffer(query_id="gq")
    buffer.add("<b>平台菜单</b>", reply_markup={"inline_keyboard": [[{"text": "设置", "callback_data": "ab:1:menu:_"}]]})
    results = compose_guest_results(buffer)
    assert results and all("reply_markup" not in item for item in results)
    assert guest_reply.sanitize_guest_reply_markup(RETRY_MARKUP) is None
    assert guest_reply.sanitize_guest_reply_markup(RETRY_MARKUP, allow_callback=True) == RETRY_MARKUP


def test_message_ops_advertise_guest_reply_markup_capability() -> None:
    assert BufferedMessageOps.guest_reply_markup is True
    assert loader_mod._LiveMessageOps.guest_reply_markup is True
    assert issubclass(loader_mod._InteractionEntryMessageOps, BufferedMessageOps)
    assert loader_mod._InteractionEntryMessageOps.guest_reply_markup is True


@pytest.mark.asyncio
async def test_live_apply_answer_guest_query_keeps_markup_and_returns_inline_message_id(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    async def _answer(token: str, guest_query_id: str, *, result: dict[str, Any]) -> dict[str, Any]:
        calls.append({"token": token, "guest_query_id": guest_query_id, "result": result})
        return {"inline_message_id": "im-guest-1"}

    monkeypatch.setattr(loader_mod.account_bot_service, "answer_guest_query", _answer)
    monkeypatch.setattr(loader_mod, "record_action", AsyncMock())
    monkeypatch.setattr(loader_mod, "emit_action_event", AsyncMock())
    monkeypatch.setattr(loader_mod, "_interaction_bot_token_for_account", AsyncMock(return_value="123:secret"))
    state = loader_mod._AccountState(4301)
    state.redis = _FakeRedis()
    messages = loader_mod._InteractionEntryMessageOps(state, plugin_key="interaction-ai-chat", entry_key="main")

    outcome = await messages.apply(
        [
            {
                "type": "answer_guest_query",
                "guest_query_id": "gq-1",
                "text": "模型调用失败",
                "parse_mode": "html",
                "reply_markup": RETRY_MARKUP,
                "send_via": ["interaction_bot"],
                "save_message_id_key": "iac:failure:1",
            }
        ]
    )

    assert outcome["ok"] is True
    assert outcome["inline_message_id"] == "im-guest-1"
    assert outcome["inline_message_ids"] == ["im-guest-1"]
    assert calls[0]["result"]["reply_markup"] == RETRY_MARKUP
    assert await messages.read_saved_inline_message_id("iac:failure:1") == "im-guest-1"

    live = loader_mod._LiveMessageOps(state, plugin_key="interaction-ai-chat", default_send_via=["interaction_bot"])
    action = await live.answer_guest_query(guest_query_id="gq-2", text="hi", reply_markup=RETRY_MARKUP)
    assert action["inline_message_id"] == "im-guest-1"
    assert calls[1]["result"]["reply_markup"] == RETRY_MARKUP


# ---------------------------------------------------------------------------
# inline callback 解析、信封与订阅范围
# ---------------------------------------------------------------------------
def test_extract_incoming_parses_inline_callback_without_chat() -> None:
    incoming = account_bot_runtime._extract_incoming(1, "tok", _inline_callback_update())

    assert incoming is not None
    assert incoming.event_type == "callback_query"
    assert incoming.callback_id == "cq-inline-1"
    assert incoming.callback_data == "iac:r:0123456789ab"
    assert incoming.inline_message_id == "im-guest-9"
    assert incoming.chat_instance == "ci-777"
    assert incoming.chat_id is None and incoming.message_id is None
    assert incoming.user_id == 4242


def test_inline_callback_envelope_and_guest_all_scope() -> None:
    event = normalize_bot_update(1, _inline_callback_update())
    assert event["source"]["inline_message_id"] == "im-guest-9"

    guest_sub = normalize_event_subscription({"events": ["callback_query"], "scope": "guest_all"}, plugin_key="ai")
    chat_sub = normalize_event_subscription({"events": ["callback_query"], "scope": "all_allowed_chats"}, plugin_key="ai")
    state = {"allowed_chat_ids": "*", "owner_user_ids": [], "known_user_ids": []}
    [guest_decision] = match_subscriptions(event, [guest_sub], state)
    [chat_decision] = match_subscriptions(event, [chat_sub], state)
    assert guest_decision.matched is True
    assert chat_decision.matched is False
    inline_sub = normalize_event_subscription({"events": ["callback_query"], "scope": "inline_all"}, plugin_key="ai")
    [inline_decision] = match_subscriptions(event, [inline_sub], state)
    assert inline_decision.matched is True

    chat_event = normalize_bot_update(
        1,
        {
            "update_id": 1,
            "callback_query": {
                "id": "cq",
                "from": {"id": 1},
                "data": "x",
                "message": {"message_id": 5, "chat": {"id": -100, "type": "supergroup"}},
            },
        },
    )
    [guest_on_chat] = match_subscriptions(chat_event, [guest_sub], state)
    assert guest_on_chat.matched is False


def test_incoming_trace_payload_carries_inline_callback_without_guest_identity() -> None:
    incoming = account_bot_runtime._extract_incoming(1, "tok", _inline_callback_update())
    assert incoming is not None
    incoming.inline_origin = {"guest_query_id": "gq-1", "chat_id": GUEST_CHAT, "chat_type": "supergroup", "caller_user_id": 501}
    payload = account_bot_runtime._incoming_trace_payload(incoming)

    assert payload["source"]["inline_message_id"] == "im-guest-9"
    assert payload["inline_callback"]["inline_message_id"] == "im-guest-9"
    assert payload["inline_callback"]["clicker_user_id"] == 4242
    assert payload["inline_callback"]["origin"]["chat_id"] == GUEST_CHAT
    # 不伪装成访客消息：没有 guest 信封（插件把 guest.caller_user_id 当发送者），chat 为空。
    assert "guest" not in payload
    assert payload["chat"]["id"] is None
    assert payload["sender"]["user_id"] == 4242


@pytest.mark.asyncio
async def test_guest_origin_roundtrip(monkeypatch) -> None:
    redis = _FakeRedis()

    async def _call(method: str, *args: Any, **kwargs: Any) -> Any:
        return await getattr(redis, method)(*args, **kwargs)

    monkeypatch.setattr(guest_reply, "_guest_origin_redis_call", _call)
    await guest_reply.remember_guest_query_origin(
        "tok", "gq-1", {"chat_id": GUEST_CHAT, "chat_type": "supergroup", "caller_user_id": 501, "message_id": 77}
    )
    await guest_reply.link_guest_inline_origin("tok", "gq-1", "im-1")

    assert await guest_reply.guest_inline_origin("tok", "im-1") == {
        "guest_query_id": "gq-1",
        "chat_id": GUEST_CHAT,
        "chat_type": "supergroup",
        "caller_user_id": 501,
        "message_id": 77,
    }
    # 不同 Bot Token 互不可见
    assert await guest_reply.guest_inline_origin("other", "im-1") is None
    assert await guest_reply.guest_inline_origin("tok", "missing") is None


@pytest.mark.asyncio
async def test_answer_guest_query_links_inline_origin(monkeypatch) -> None:
    async def _call(token: str, method: str, payload: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        return {"inline_message_id": "im-xyz"}

    linked: list[tuple[str, str, str]] = []

    async def _link(token: str, query_id: str, inline_id: str) -> None:
        linked.append((token, query_id, inline_id))

    monkeypatch.setattr(account_bot_service, "call_bot_api", _call)
    monkeypatch.setattr(guest_reply, "link_guest_inline_origin", _link)
    sent = await account_bot_service.answer_guest_query("tok", "gq-9", result={"type": "article", "id": "1"})

    assert sent == {"inline_message_id": "im-xyz"}
    assert linked == [("tok", "gq-9", "im-xyz")]


# ---------------------------------------------------------------------------
# 端到端：交互 Bot 收到 inline callback → 插件 → answer_callback + 按 inline_message_id 编辑
# ---------------------------------------------------------------------------
def _patch_event_bus(monkeypatch, subscriptions: list[Any], cfg: dict[str, Any]) -> None:
    _patch_interaction_runtime(monkeypatch, {"trace_enabled": False, "event_bus_delivery_enabled": True})
    monkeypatch.setattr(account_bot_service, "get_transfer_notice_config", AsyncMock(return_value=cfg))
    monkeypatch.setattr(account_bot_runtime, "_load_enabled_event_bus_subscriptions", AsyncMock(return_value=subscriptions))
    monkeypatch.setattr(account_bot_runtime, "_event_bus_cached_known_user_ids", AsyncMock(return_value=([1001], set())))
    monkeypatch.setattr(account_bot_runtime, "_event_bus_active_session_participant_ids", AsyncMock(return_value=set()))
    monkeypatch.setattr("app.services.interaction.delivery.record_action", AsyncMock())
    # Event Bus 信封的 bot 身份来自 getMe：测试里不能真的请求 Telegram（会留下绑定到已关闭事件循环的共享客户端）。
    monkeypatch.setattr(account_bot_service, "get_bot_capabilities", AsyncMock(return_value={"id": 999, "username": "demo_bot"}))


@pytest.mark.asyncio
async def test_inline_callback_routes_to_guest_all_plugin_and_edits_inline_message(monkeypatch) -> None:
    cfg = {"enabled": True, "interaction_bot_id": 999, "rules": [], "chat_ids": [-100999]}
    subscriptions = [
        normalize_event_subscription({"events": ["callback_query"], "scope": "all_allowed_chats"}, plugin_key="interaction-ai-chat"),
        normalize_event_subscription({"events": ["callback_query"], "scope": "guest_all"}, plugin_key="interaction-ai-chat"),
    ]
    _patch_event_bus(monkeypatch, subscriptions, cfg)
    origin = {"guest_query_id": "gq-1", "chat_id": GUEST_CHAT, "chat_type": "supergroup", "caller_user_id": 501}
    monkeypatch.setattr(account_bot_runtime, "guest_inline_origin", AsyncMock(return_value=origin))
    captured: list[dict[str, Any]] = []

    async def _run_entry(incoming, *, plugin_key, entry_key, payload):
        captured.append(payload)
        return True, None, [
            {"type": "answer_callback", "callback_query_id": payload["callback_query_id"], "text": "开始重试", "send_via": "interaction_bot"},
            {
                "type": "edit_message",
                "inline_message_id": payload["inline_message_id"],
                "text": "模型调用失败",
                "parse_mode": "html",
                "reply_markup": RETRY_MARKUP,
                "send_via": "interaction_bot",
            },
        ]

    monkeypatch.setattr(account_bot_runtime, "_run_worker_interaction_entry", _run_entry)
    answer = AsyncMock()
    monkeypatch.setattr(account_bot_service, "answer_callback", answer)
    edit_inline = AsyncMock(return_value={"result": True})
    monkeypatch.setattr(account_bot_service, "edit_inline_message", edit_inline)
    monkeypatch.setattr(account_bot_service, "edit_message", AsyncMock(side_effect=AssertionError("inline 消息不能按 chat 编辑")))

    await account_bot_runtime._handle_interaction_update(1, "bbot", _inline_callback_update())

    assert len(captured) == 1, "只有 guest_all 订阅匹配 inline callback，不能重复投递"
    payload = captured[0]
    assert payload["event_type"] == "callback_query"
    assert payload["inline_message_id"] == "im-guest-9"
    assert payload["callback_data"] == "iac:r:0123456789ab"
    assert payload["chat_id"] is None
    assert payload["sender_user_id"] == 4242
    assert payload["inline_callback"]["chat_instance"] == "ci-777"
    assert payload["inline_callback"]["origin"] == origin
    assert payload["admin_user_ids"] == [1001]
    answer.assert_awaited_once()
    assert answer.await_args.args[:2] == ("bbot", "cq-inline-1")
    edit_inline.assert_awaited_once()
    assert edit_inline.await_args.args[:3] == ("bbot", "im-guest-9", "模型调用失败")
    assert edit_inline.await_args.kwargs["reply_markup"] == RETRY_MARKUP


@pytest.mark.asyncio
async def test_inline_callback_without_guest_subscription_is_not_delivered(monkeypatch) -> None:
    cfg = {"enabled": True, "interaction_bot_id": 999, "rules": [], "chat_ids": []}
    subscriptions = [
        normalize_event_subscription({"events": ["callback_query"], "scope": "all_allowed_chats"}, plugin_key="old-plugin"),
    ]
    _patch_event_bus(monkeypatch, subscriptions, cfg)
    monkeypatch.setattr(account_bot_runtime, "guest_inline_origin", AsyncMock(return_value=None))
    run_entry = AsyncMock(return_value=(True, None, []))
    monkeypatch.setattr(account_bot_runtime, "_run_worker_interaction_entry", run_entry)
    monkeypatch.setattr(account_bot_service, "answer_callback", AsyncMock())

    await account_bot_runtime._handle_interaction_update(1, "bbot", _inline_callback_update())

    run_entry.assert_not_awaited()


@pytest.mark.asyncio
async def test_inline_callback_reaches_guest_all_when_account_allowed_chats_is_set(monkeypatch) -> None:
    """账号「允许会话」非空时（0.106.0-beta.16 投递范围），没有 chat 的 inline 回调仍投递给 guest_all。"""

    cfg = {"enabled": True, "interaction_bot_id": 999, "rules": [], "chat_ids": []}
    subscriptions = [
        normalize_event_subscription({"events": ["callback_query"], "scope": "guest_all"}, plugin_key="interaction-ai-chat"),
    ]
    _patch_event_bus(monkeypatch, subscriptions, cfg)
    allowed = AsyncMock(return_value=frozenset({-100777}))
    monkeypatch.setattr(account_bot_runtime, "_account_allowed_chat_ids", allowed)
    monkeypatch.setattr(account_bot_runtime, "guest_inline_origin", AsyncMock(return_value=None))
    run_entry = AsyncMock(return_value=(True, None, []))
    monkeypatch.setattr(account_bot_runtime, "_run_worker_interaction_entry", run_entry)
    monkeypatch.setattr(account_bot_service, "answer_callback", AsyncMock())

    await account_bot_runtime._handle_interaction_update(1, "bbot", _inline_callback_update())

    run_entry.assert_awaited_once()
    payload = run_entry.await_args.kwargs["payload"]
    assert payload["inline_message_id"]
    assert payload["chat_id"] is None
    allowed.assert_awaited()


# ---------------------------------------------------------------------------
# 回归：非访客群失败消息的按钮（edit_message + reply_markup，含编辑失败改新发）
# ---------------------------------------------------------------------------
def _group_message_update() -> dict[str, Any]:
    return {
        "update_id": 1,
        "message": {
            "message_id": 88,
            "text": "@helper_bot 今年分最高的是啥",
            "from": {"id": 601, "first_name": "A"},
            "chat": {"id": -100777, "type": "supergroup"},
        },
    }


def _failure_edit_action() -> dict[str, Any]:
    # interaction-ai-chat 0.4.7 失败时返回的动作（经 worker 归一化后 send_via 为字符串）。
    return {
        "type": "edit_message",
        "chat_id": -100777,
        "message_id": 901,
        "text": "模型调用失败。请联系管理员。",
        "parse_mode": "html",
        "send_via": "interaction_bot",
        "send_on_edit_failure": True,
        "reply_to_message_id": 88,
        "reply_markup": RETRY_MARKUP,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("edit_fails", [False, True])
async def test_group_failure_edit_keeps_reply_markup(monkeypatch, edit_fails: bool) -> None:
    cfg = {"enabled": True, "interaction_bot_id": 999, "rules": [], "chat_ids": []}
    subscriptions = [normalize_event_subscription({"events": ["message"], "scope": "all_allowed_chats"}, plugin_key="interaction-ai-chat")]
    _patch_event_bus(monkeypatch, subscriptions, cfg)
    monkeypatch.setattr(
        account_bot_runtime,
        "_run_worker_interaction_entry",
        AsyncMock(return_value=(True, None, [_failure_edit_action()])),
    )
    calls: list[tuple[str, dict[str, Any]]] = []

    async def _edit(token, chat_id, message_id, text, **kwargs):
        calls.append(("edit", kwargs))
        if edit_fails:
            raise RuntimeError("Bad Request: message to edit not found")
        return {"message_id": message_id}

    async def _send(token, chat_id, text, **kwargs):
        calls.append(("send", kwargs))
        return {"message_id": 902}

    monkeypatch.setattr(account_bot_service, "edit_message", _edit)
    monkeypatch.setattr(account_bot_service, "send_message", _send)

    await account_bot_runtime._handle_interaction_update(1, "bbot", _group_message_update())

    assert calls[0][0] == "edit"
    assert calls[0][1]["reply_markup"] == RETRY_MARKUP
    if edit_fails:
        assert calls[-1][0] == "send"
        assert calls[-1][1]["reply_markup"] == RETRY_MARKUP
