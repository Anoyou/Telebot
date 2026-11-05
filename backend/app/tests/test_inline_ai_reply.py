"""AI 回复 inline 代发（0.119.0）：Bot 侧中继、UserBot 代发、回退与插件能力。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.services import account_bot_service, inline_ai_relay
from app.worker import ai_runtime, inline_ai_reply


class _FakeRedis:
    def __init__(self) -> None:
        self.kv: dict[str, Any] = {}
        self.lists: dict[str, list[str]] = {}

    async def set(self, key: str, value: Any, ex: int | None = None) -> bool:
        self.kv[key] = value
        return True

    async def get(self, key: str) -> Any:
        return self.kv.get(key)

    async def delete(self, *keys: str) -> int:
        count = 0
        for key in keys:
            count += int(self.kv.pop(key, None) is not None) + int(self.lists.pop(key, None) is not None)
        return count

    async def rpush(self, key: str, value: str) -> int:
        self.lists.setdefault(key, []).append(value)
        return len(self.lists[key])

    async def expire(self, key: str, ttl: int) -> bool:
        return True

    async def blpop(self, keys: list[str], timeout: int = 0) -> Any:
        for key in keys:
            items = self.lists.get(key)
            if items:
                return (key, items.pop(0))
        return None


@pytest.fixture
def redis(monkeypatch: pytest.MonkeyPatch) -> _FakeRedis:
    fake = _FakeRedis()
    monkeypatch.setattr(inline_ai_relay, "get_redis", lambda: fake)
    return fake


def _incoming(**kwargs: Any) -> SimpleNamespace:
    base = {
        "account_id": 7,
        "token": "bot-token",
        "user_id": 1001,
        "callback_id": None,
        "callback_data": None,
        "inline_query_id": None,
        "chosen_inline_result_id": None,
        "inline_query_text": None,
        "text": "",
        "native_raw": {},
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_parse_query_token_only_accepts_prefixed_safe_tokens() -> None:
    assert inline_ai_relay.parse_query_token("tpai:abc_DEF-1") == "abc_DEF-1"
    assert inline_ai_relay.parse_query_token("hello") is None
    assert inline_ai_relay.parse_query_token("tpai:") is None
    assert inline_ai_relay.parse_query_token("tpai:a b") is None
    assert inline_ai_relay.normalize_reply_style({"value": "inline"}) == "inline"
    assert inline_ai_relay.normalize_reply_style("weird") == "normal"
    assert inline_ai_relay.normalize_delete_command(None) is True
    assert inline_ai_relay.normalize_delete_command({"enabled": False}) is False


@pytest.mark.asyncio
async def test_relay_answers_owner_with_button_placeholder_and_forwards_inline_message_id(
    redis: _FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    answer = AsyncMock()
    monkeypatch.setattr(account_bot_service, "answer_inline_query", answer)
    token = await inline_ai_relay.issue_token(account_id=7, owner_user_id=1001, chat_id=-100123)

    handled = await inline_ai_relay.handle_interaction_update(
        _incoming(inline_query_id="iq1", inline_query_text=f"tpai:{token}")
    )
    assert handled is True
    kwargs = answer.await_args.kwargs
    assert kwargs["cache_time"] == 0 and kwargs["is_personal"] is True
    result = kwargs["results"][0]
    assert result["id"] == inline_ai_relay.result_id_for(token)
    # 没有 inline keyboard 时 chosen_inline_result 不带 inline_message_id。
    assert result["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == inline_ai_relay.NOOP_CALLBACK_DATA

    chosen = _incoming(
        chosen_inline_result_id=inline_ai_relay.result_id_for(token),
        inline_query_text=f"tpai:{token}",
        native_raw={"chosen_inline_result": {"inline_message_id": "IMID-1"}},
    )
    assert await inline_ai_relay.handle_interaction_update(chosen) is True
    assert await inline_ai_relay.wait_inline_message_id(token, timeout=1) == "IMID-1"
    assert await inline_ai_relay.read_feedback_status(7) == inline_ai_relay.FEEDBACK_OK
    # 一次性：令牌已作废，再次选中不会再推送。
    assert await inline_ai_relay.handle_interaction_update(chosen) is True
    assert await inline_ai_relay.wait_inline_message_id(token, timeout=1) is None


@pytest.mark.asyncio
async def test_relay_rejects_other_users_and_other_accounts(redis: _FakeRedis, monkeypatch: pytest.MonkeyPatch) -> None:
    answer = AsyncMock()
    monkeypatch.setattr(account_bot_service, "answer_inline_query", answer)
    token = await inline_ai_relay.issue_token(account_id=7, owner_user_id=1001, chat_id=None)

    await inline_ai_relay.handle_interaction_update(
        _incoming(user_id=2002, inline_query_id="iq1", inline_query_text=f"tpai:{token}")
    )
    assert answer.await_args.kwargs["results"] == []
    await inline_ai_relay.handle_interaction_update(
        _incoming(account_id=8, inline_query_id="iq2", inline_query_text=f"tpai:{token}")
    )
    assert answer.await_args.kwargs["results"] == []
    # 伪造的 chosen 不会推送 inline_message_id。
    await inline_ai_relay.handle_interaction_update(
        _incoming(
            user_id=2002,
            chosen_inline_result_id=inline_ai_relay.result_id_for(token),
            inline_query_text=f"tpai:{token}",
            native_raw={"chosen_inline_result": {"inline_message_id": "X"}},
        )
    )
    assert await inline_ai_relay.wait_inline_message_id(token, timeout=1) is None


@pytest.mark.asyncio
async def test_relay_ignores_normal_inline_queries_and_answers_noop_button(
    redis: _FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    answer_cb = AsyncMock()
    monkeypatch.setattr(account_bot_service, "answer_callback", answer_cb)
    assert await inline_ai_relay.handle_interaction_update(_incoming(inline_query_id="iq", inline_query_text="天气")) is False
    assert await inline_ai_relay.handle_interaction_update(_incoming(callback_id="cb", callback_data="other")) is False
    assert await inline_ai_relay.handle_interaction_update(
        _incoming(callback_id="cb", callback_data=inline_ai_relay.NOOP_CALLBACK_DATA)
    ) is True
    answer_cb.assert_awaited_once()


class _Result:
    def __init__(self, sent: Any = None, exc: BaseException | None = None) -> None:
        self.sent = sent if sent is not None else SimpleNamespace(id=55)
        self.exc = exc
        self.calls: list[tuple[Any, dict[str, Any]]] = []

    async def click(self, entity: Any, **kwargs: Any) -> Any:
        self.calls.append((entity, kwargs))
        if self.exc is not None:
            raise self.exc
        return self.sent


class _Client:
    def __init__(self, results: list[Any]) -> None:
        self.results = results
        self.queries: list[tuple[Any, str, Any]] = []
        self.deleted: list[tuple[Any, list[int]]] = []

    async def inline_query(self, bot: Any, query: str, entity: Any = None) -> list[Any]:
        self.queries.append((bot, query, entity))
        return self.results

    async def delete_messages(self, entity: Any, ids: list[int]) -> None:
        self.deleted.append((entity, ids))


@pytest.fixture
def bot_ready(monkeypatch: pytest.MonkeyPatch, redis: _FakeRedis) -> None:
    from app.worker import command

    monkeypatch.setattr(
        account_bot_service,
        "get_bot_capabilities",
        AsyncMock(return_value={"username": "tp_bot", "supports_inline_queries": True}),
    )
    monkeypatch.setattr(command, "acquire_direct_userbot_action_rate_limit", AsyncMock(return_value=(True, {})))


@pytest.mark.asyncio
async def test_start_inline_reply_sends_via_bot_with_reply_and_waits_for_inline_id(
    bot_ready: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(inline_ai_relay, "wait_inline_message_id", AsyncMock(return_value="IMID"))
    result = _Result()
    client = _Client([result])

    handle, reason = await inline_ai_reply.start_inline_reply(
        client, account_id=7, owner_user_id=1001, chat_id=-100123, reply_to_message_id=42, bot_token="bot-token"
    )
    assert reason == ""
    assert handle is not None and handle.inline_message_id == "IMID" and handle.message_id == 55
    bot, query, entity = client.queries[0]
    assert bot == "tp_bot" and query.startswith("tpai:") and entity == -100123
    assert result.calls == [(-100123, {"reply_to": 42, "hide_via": False})]


@pytest.mark.asyncio
async def test_start_inline_reply_falls_back_when_group_forbids_inline(bot_ready: None, redis: _FakeRedis) -> None:
    forbidden = type("ChatSendInlineForbiddenError", (Exception,), {})
    client = _Client([_Result(exc=forbidden("CHAT_SEND_INLINE_FORBIDDEN"))])

    handle, reason = await inline_ai_reply.start_inline_reply(
        client, account_id=7, owner_user_id=1001, chat_id=-100123, bot_token="bot-token"
    )
    assert handle is None and reason == "chat_send_inline_forbidden"
    assert not any(key.startswith("tp:inline_ai:tok:") for key in redis.kv)
    assert client.deleted == []


@pytest.mark.asyncio
async def test_start_inline_reply_timeout_deletes_placeholder_and_marks_feedback_missing(
    bot_ready: None, redis: _FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(inline_ai_relay, "wait_inline_message_id", AsyncMock(return_value=None))
    client = _Client([_Result(sent=SimpleNamespace(id=77))])

    handle, reason = await inline_ai_reply.start_inline_reply(
        client, account_id=7, owner_user_id=1001, chat_id=-100123, bot_token="bot-token"
    )
    assert handle is None and reason == "chosen_inline_result_timeout"
    assert client.deleted == [(-100123, [77])]  # 不重复发送：占位删掉后再走普通回复
    assert await inline_ai_relay.read_feedback_status(7) == inline_ai_relay.FEEDBACK_MISSING


@pytest.mark.asyncio
async def test_start_inline_reply_skips_when_bot_inline_mode_off(monkeypatch: pytest.MonkeyPatch, redis: _FakeRedis) -> None:
    monkeypatch.setattr(
        account_bot_service,
        "get_bot_capabilities",
        AsyncMock(return_value={"username": "tp_bot", "supports_inline_queries": False}),
    )
    client = _Client([_Result()])
    handle, reason = await inline_ai_reply.start_inline_reply(
        client, account_id=7, owner_user_id=1001, chat_id=-100123, bot_token="bot-token"
    )
    assert handle is None and reason == "bot_inline_disabled"
    assert client.queries == []


@pytest.mark.asyncio
async def test_handle_finish_prefers_rich_compact_table_then_html(monkeypatch: pytest.MonkeyPatch) -> None:
    rich = AsyncMock()
    plain = AsyncMock()
    monkeypatch.setattr(account_bot_service, "edit_inline_rich_message", rich)
    monkeypatch.setattr(account_bot_service, "edit_inline_message", plain)
    handle = inline_ai_reply.InlineReplyHandle(
        client=None, bot_token="t", inline_message_id="IM", chat_id=-1, message_id=1, account_id=7
    )
    used = await handle.finish(markdown="| a | b |\n|---|---|\n| 1 | 2 |", html="<b>x</b>", plain="x")
    assert used == "rich" and handle.finalized
    sent_rich = rich.await_args.args[2]
    assert "<table" in sent_rich["markdown"] and "compact" in sent_rich["markdown"]
    assert rich.await_args.kwargs["reply_markup"] == {"inline_keyboard": []}
    plain.assert_not_awaited()

    rich.side_effect = RuntimeError("Bad Request: can't parse rich message")
    handle2 = inline_ai_reply.InlineReplyHandle(
        client=None, bot_token="t", inline_message_id="IM", chat_id=-1, message_id=1, account_id=7
    )
    assert await handle2.finish(markdown="hi", html="<b>hi</b>", plain="hi") == "html"
    assert plain.await_args.kwargs["parse_mode"] == "HTML"


@pytest.mark.asyncio
async def test_proxy_event_routes_edit_to_inline_message_and_ignores_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    plain = AsyncMock()
    monkeypatch.setattr(account_bot_service, "edit_inline_message", plain)
    handle = inline_ai_reply.InlineReplyHandle(
        client=None, bot_token="t", inline_message_id="IM", chat_id=-1, message_id=1, account_id=7
    )
    original = SimpleNamespace(chat_id=-1, id=9, edit=AsyncMock(), delete=AsyncMock())
    proxy = handle.proxy(original)
    await proxy.edit("✗ AI 调用失败")
    await proxy.delete()
    assert proxy.chat_id == -1 and proxy.id == 9
    original.edit.assert_not_awaited()
    original.delete.assert_not_awaited()
    assert plain.await_args.args[2] == "✗ AI 调用失败"
    assert handle.finalized


def test_inline_reply_eligible_only_for_owner_text_chat() -> None:
    owner = SimpleNamespace(out=True, chat_id=-100)
    assert ai_runtime._inline_reply_eligible(
        event=owner, command_mode="chat", native_image_mode=False, has_self_image=False, has_self_audio=False
    )
    assert ai_runtime._inline_reply_eligible(
        event=owner, command_mode="search", native_image_mode=False, has_self_image=False, has_self_audio=False
    )
    assert not ai_runtime._inline_reply_eligible(
        event=SimpleNamespace(out=False, chat_id=-100), command_mode="chat",
        native_image_mode=False, has_self_image=False, has_self_audio=False,
    )
    assert not ai_runtime._inline_reply_eligible(
        event=owner, command_mode="image", native_image_mode=True, has_self_image=False, has_self_audio=False
    )
    assert not ai_runtime._inline_reply_eligible(
        event=owner, command_mode="chat", native_image_mode=False, has_self_image=True, has_self_audio=False
    )


@pytest.mark.asyncio
async def test_maybe_start_inline_reply_respects_setting_and_deletes_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(inline_ai_relay, "read_reply_preference", AsyncMock(return_value={"style": "normal"}))
    event = SimpleNamespace(chat_id=-100, id=5, delete=AsyncMock())
    assert await ai_runtime._maybe_start_inline_reply(
        None, event, ctx=SimpleNamespace(self_tg_user_id=1), account_id=7, replied=None
    ) is None

    fake_handle = SimpleNamespace(finalized=False)
    start = AsyncMock(return_value=(fake_handle, ""))
    monkeypatch.setattr(inline_ai_reply, "start_inline_reply", start)
    monkeypatch.setattr(
        inline_ai_relay, "read_reply_preference", AsyncMock(return_value={"style": "inline", "delete_command": True})
    )
    got = await ai_runtime._maybe_start_inline_reply(
        None, event, ctx=SimpleNamespace(self_tg_user_id=1), account_id=7, replied=SimpleNamespace(id=3)
    )
    assert got is fake_handle
    assert start.await_args.kwargs["reply_to_message_id"] == 3
    event.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_plugin_inline_via_bot_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.worker import command

    monkeypatch.setattr(command, "get_command_context", lambda: SimpleNamespace(self_tg_user_id=1001))
    monkeypatch.setattr(inline_ai_relay, "read_reply_preference", AsyncMock(return_value={"style": "normal"}))
    monkeypatch.setattr(inline_ai_reply, "_bot_in_chat", AsyncMock(return_value=False))
    client = _Client([])
    out = await inline_ai_reply.plugin_inline_via_bot(client, account_id=7, chat_id=-100123, requester_user_id=1001)
    assert out["ok"] is False and out["reason"] == "style_normal"

    monkeypatch.setattr(
        inline_ai_relay, "read_reply_preference", AsyncMock(return_value={"style": "inline", "delete_command": True})
    )
    out = await inline_ai_reply.plugin_inline_via_bot(client, account_id=7, chat_id=-100123, requester_user_id=2002)
    assert out["reason"] == "not_owner"

    handle = SimpleNamespace(inline_message_id="IM", message_id=9)
    start = AsyncMock(return_value=(handle, ""))
    monkeypatch.setattr(inline_ai_reply, "start_inline_reply", start)
    # 普通群（非 -100）里 Bot 与 UserBot 的消息 ID 不一致：不引用、不删除。
    out = await inline_ai_reply.plugin_inline_via_bot(
        client, account_id=7, chat_id=-4567, requester_user_id=1001, reply_to_message_id=5, delete_message_id=5
    )
    assert out["ok"] is True and out["deleted_message"] is False
    assert start.await_args.kwargs["reply_to_message_id"] is None
    assert client.deleted == []
    out = await inline_ai_reply.plugin_inline_via_bot(
        client, account_id=7, chat_id=-1001234567890, requester_user_id=1001, reply_to_message_id=5, delete_message_id=5
    )
    assert out["deleted_message"] is True and client.deleted == [(-1001234567890, [5])]
    assert start.await_args.kwargs["reply_to_message_id"] == 5


def test_capability_registered_for_live_and_entry_facades() -> None:
    from app.worker.plugins.capabilities import CAPABILITY_INDEX, FACADE_ENTRY, FACADE_LIVE

    cap = CAPABILITY_INDEX["messages.inline_via_bot"]
    assert cap.facades == frozenset({FACADE_LIVE, FACADE_ENTRY})


@pytest.mark.asyncio
async def test_plugin_inline_via_bot_prefers_bot_when_bot_in_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    """0.120.1：交互 Bot 就在群里（插件事件来自 Bot）时默认由 Bot 直接回复，不借账号代发。"""

    from app.worker import command

    monkeypatch.setattr(command, "get_command_context", lambda: SimpleNamespace(self_tg_user_id=1001))
    in_chat = AsyncMock(return_value=True)
    monkeypatch.setattr(inline_ai_reply, "_bot_in_chat", in_chat)
    start = AsyncMock(return_value=(SimpleNamespace(inline_message_id="IM", message_id=9), ""))
    monkeypatch.setattr(inline_ai_reply, "start_inline_reply", start)
    monkeypatch.setattr(
        inline_ai_relay, "read_reply_preference", AsyncMock(return_value={"style": "inline", "delete_command": True})
    )
    client = _Client([])
    out = await inline_ai_reply.plugin_inline_via_bot(
        client, account_id=7, chat_id=-1001234567890, requester_user_id=1001, delete_message_id=5
    )
    assert out == {"ok": False, "reason": "bot_reply_preferred", "inline_message_id": None, "message_id": None, "deleted_message": False}
    start.assert_not_awaited()
    assert client.deleted == []

    # 非账号本人：先按 not_owner 拒绝，不去查 Bot 成员关系。
    in_chat.reset_mock()
    out = await inline_ai_reply.plugin_inline_via_bot(client, account_id=7, chat_id=-1001234567890, requester_user_id=2)
    assert out["reason"] == "not_owner"
    in_chat.assert_not_awaited()

    # Bot 不在群里：照旧 inline 代发。
    in_chat.return_value = False
    out = await inline_ai_reply.plugin_inline_via_bot(client, account_id=7, chat_id=-1001234567890, requester_user_id=1001)
    assert out["ok"] is True

    # 开关关掉：恢复旧行为，不查成员关系。
    in_chat.reset_mock()
    in_chat.return_value = True
    monkeypatch.setattr(
        inline_ai_relay,
        "read_reply_preference",
        AsyncMock(return_value={"style": "inline", "delete_command": True, "ai_plugin_reply_via_bot": False}),
    )
    out = await inline_ai_reply.plugin_inline_via_bot(client, account_id=7, chat_id=-1001234567890, requester_user_id=1001)
    assert out["ok"] is True
    in_chat.assert_not_awaited()


@pytest.mark.asyncio
async def test_bot_in_chat_uses_membership_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import account_bot_service

    monkeypatch.setattr(inline_ai_reply, "_load_bot_token", AsyncMock(return_value="123:abc"))
    monkeypatch.setattr(account_bot_service, "get_bot_capabilities", AsyncMock(return_value={"bot_id": 42}))
    probe = AsyncMock(return_value=[{"chat_id": -100, "status": "joined"}])
    monkeypatch.setattr(account_bot_service, "inspect_bot_chat_memberships", probe)
    assert await inline_ai_reply._bot_in_chat(7, -100) is True
    assert probe.await_args.args == ("123:abc", 42, [-100])
    probe.return_value = [{"chat_id": -100, "status": "not_joined"}]
    assert await inline_ai_reply._bot_in_chat(7, -100) is False
    probe.side_effect = RuntimeError("boom")
    assert await inline_ai_reply._bot_in_chat(7, -100) is False
    # 私聊不算“群里”
    assert await inline_ai_reply._bot_in_chat(7, 555) is False
