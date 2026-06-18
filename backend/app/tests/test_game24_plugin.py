"""24 点插件的消息形态兼容性测试。"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.worker.plugins.base import PluginContext
from app.worker.plugins.builtin.game24 import plugin as game24_plugin
from app.worker.plugins.builtin.game24.manifest import MANIFEST
from app.worker.plugins.builtin.game24.plugin import Game24Plugin, GameState, check_answer


class _Redis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.expires: dict[str, int] = {}

    async def get(self, key: str):
        return self.data.get(key)

    async def set(self, key: str, value: str, **kwargs):
        if kwargs.get("nx") and key in self.data:
            return False
        self.data[key] = value
        if kwargs.get("ex") is not None:
            self.expires[key] = int(kwargs["ex"])
        return True


def test_game24_accepts_common_answer_formats() -> None:
    assert check_answer("答案：(1＋2＋3)×4=24", [1, 2, 3, 4])
    assert check_answer("（8－4）×（3＋3）=24", [8, 4, 3, 3])


def test_game24_manifest_schema_matches_runtime_config() -> None:
    """manifest 不能再暴露旧字段 time_limit/prize/max_players。"""

    schema = MANIFEST.config_schema
    assert schema["x-ui-mode"] == "single"
    assert set(schema["properties"].keys()) == {"command", "timeout"}
    assert schema["properties"]["command"]["default"] == "24d"
    assert schema["properties"]["timeout"]["default"] == 500


def test_game24_manifest_declares_interaction_entry() -> None:
    assert MANIFEST.category == "interactive"
    assert MANIFEST.interaction_profile == "session_game"
    assert MANIFEST.interaction_entries
    entry = MANIFEST.interaction_entries[0]
    assert entry["key"] == "start_paid_game"
    assert entry["interaction_profile"] == "session_game"
    assert entry["launch_mode"] == "hybrid"
    assert entry["session_scope"] == "chat"
    assert entry["preserve_command_trigger"] is True
    assert entry["command_fallback"]["command"] == "24d"
    assert "settlement" in entry
    assert "prize" in entry["input_schema"]["properties"]
    assert "timeout" in entry["input_schema"]["properties"]
    assert "valid_seconds" in entry["input_schema"]["properties"]


@pytest.mark.asyncio
async def test_game24_on_interaction_handles_three_event_types(monkeypatch) -> None:
    monkeypatch.setattr(game24_plugin, "generate_24_puzzle", lambda: [1, 5, 5, 5])
    redis = _Redis()
    plugin = Game24Plugin()
    ctx = PluginContext(account_id=1, feature_key="game24", redis=redis, log=AsyncMock())

    start_actions = await plugin.on_interaction(
        ctx,
        "start_paid_game",
        {
            "event_type": "payment_confirmed",
            "chat_id": -100123,
            "source_update_id": 7,
            "source_message_id": 70,
            "prize": 888,
            "timeout": 500,
            "valid_seconds": 120,
        },
    )

    assert start_actions == [
        {
            "type": "send_message",
            "text": (
                "24 点开始\n"
                "━━━━━━━━\n"
                "数字：[ 1 ] [ 5 ] [ 5 ] [ 5 ]\n"
                "奖金：888\n"
                "可用符号：+ - x ÷ * / ( )\n"
                "请直接发送算式，结果必须等于 24，并且恰好使用这 4 个数字各一次。"
            ),
        }
    ]
    state_key = "account_bot:game24:1:-100123"
    state = json.loads(redis.data[state_key])
    assert state["active"] is True
    assert state["numbers"] == [1, 5, 5, 5]
    assert state["timeout"] == 120
    assert redis.expires[state_key] == 120

    wrong_actions = await plugin.on_interaction(
        ctx,
        "start_paid_game",
        {
            "event_type": "message",
            "chat_id": -100123,
            "message_text": "1+5+5+5",
            "message_id": 98,
            "sender_name": "AAA",
        },
    )
    assert wrong_actions == []

    answer_actions = await plugin.on_interaction(
        ctx,
        "start_paid_game",
        {
            "event_type": "message",
            "chat_id": -100123,
            "message_text": "5*(5-1/5)",
            "message_id": 99,
            "sender_name": "AAA",
            "payout_account_label": "@owner",
            "payout_mode": "auto",
        },
    )
    assert answer_actions == [
        {
            "type": "send_message",
            "text": "答对了：AAA\n题目：24 点 [1 5 5 5]\n答案：5*(5-1/5) = 24\n奖金：888\n奖金将由 @owner 账号自动发放。",
            "reply_to_message_id": 99,
        },
        {
            "type": "result",
            "success": True,
            "result": {
                "status": "winner",
                "winner_user_id": None,
                "winner_name": "AAA",
                "winner_message_id": 99,
                "question": [1, 5, 5, 5],
                "answer": "5*(5-1/5)",
                "prize": 888,
                "payout_mode": "auto",
                "payout_account_label": "@owner",
            },
            "settlement": {
                "mode": "auto",
                "amount": 888,
                "winner_user_id": None,
                "winner_name": "AAA",
                "payout_account_label": "@owner",
                "status": "announced",
            },
        },
        {"type": "end_session"},
    ]
    state = json.loads(redis.data[state_key])
    assert state["active"] is False
    assert state["winner_message_id"] == 99
    assert redis.expires[state_key] == 120

    await plugin.on_interaction(
        ctx,
        "start_paid_game",
        {"event_type": "keyword", "chat_id": -100123, "prize": 123},
    )
    close_actions = await plugin.on_interaction(
        ctx,
        "start_paid_game",
        {"event_type": "session_close", "chat_id": -100123},
    )
    assert close_actions == []
    state = json.loads(redis.data[state_key])
    assert state["active"] is False


@pytest.mark.asyncio
async def test_game24_on_interaction_accepts_standard_envelope(monkeypatch) -> None:
    monkeypatch.setattr(game24_plugin, "generate_24_puzzle", lambda: [1, 5, 5, 5])
    redis = _Redis()
    plugin = Game24Plugin()
    ctx = PluginContext(account_id=1, feature_key="game24", redis=redis, log=AsyncMock())

    start_actions = await plugin.on_interaction(
        ctx,
        "start_paid_game",
        {
            "source": {
                "type": "keyword",
                "chat_id": -100123,
                "update_id": 7,
                "message_id": 70,
                "text": "开24点",
            },
            "trigger": {"type": "keyword", "rule_id": "game24", "entry_key": "start_paid_game"},
            "session": {"scope": "chat", "ttl_seconds": 120},
            "prize": 888,
            "valid_seconds": 120,
        },
    )

    assert start_actions and start_actions[0]["type"] == "send_message"
    state = json.loads(redis.data["account_bot:game24:1:-100123"])
    assert state["source_update_id"] == 7
    assert state["source_message_id"] == 70

    answer_actions = await plugin.on_interaction(
        ctx,
        "start_paid_game",
        {
            "source": {
                "type": "message",
                "chat_id": -100123,
                "update_id": 8,
                "message_id": 99,
                "text": "5*(5-1/5)",
            },
            "actor": {"user_id": 111, "display_name": "AAA"},
            "settlement": {"mode": "auto", "payout_account_label": "@owner"},
        },
    )

    assert answer_actions == [
        {
            "type": "send_message",
            "text": "答对了：AAA\n题目：24 点 [1 5 5 5]\n答案：5*(5-1/5) = 24\n奖金：888\n奖金将由 @owner 账号自动发放。",
            "reply_to_message_id": 99,
        },
        {
            "type": "result",
            "success": True,
            "result": {
                "status": "winner",
                "winner_user_id": 111,
                "winner_name": "AAA",
                "winner_message_id": 99,
                "question": [1, 5, 5, 5],
                "answer": "5*(5-1/5)",
                "prize": 888,
                "payout_mode": "auto",
                "payout_account_label": "@owner",
            },
            "settlement": {
                "mode": "auto",
                "amount": 888,
                "winner_user_id": 111,
                "winner_name": "AAA",
                "payout_account_label": "@owner",
                "status": "announced",
            },
        },
        {"type": "end_session"},
    ]


@pytest.mark.asyncio
async def test_game24_on_interaction_accepts_legacy_entry_key(monkeypatch) -> None:
    monkeypatch.setattr(game24_plugin, "generate_24_puzzle", lambda: [1, 5, 5, 5])
    redis = _Redis()
    plugin = Game24Plugin()
    ctx = PluginContext(account_id=1, feature_key="game24", redis=redis, log=AsyncMock())

    actions = await plugin.on_interaction(
        ctx,
        "start_game24",
        {"event_type": "keyword", "chat_id": -100123, "prize": 321},
    )

    assert actions == [
        {
            "type": "send_message",
            "text": (
                "24 点开始\n"
                "━━━━━━━━\n"
                "数字：[ 1 ] [ 5 ] [ 5 ] [ 5 ]\n"
                "奖金：321\n"
                "可用符号：+ - x ÷ * / ( )\n"
                "请直接发送算式，结果必须等于 24，并且恰好使用这 4 个数字各一次。"
            ),
        }
    ]


@pytest.mark.asyncio
async def test_game24_expired_interaction_state_does_not_block_new_game(monkeypatch) -> None:
    monkeypatch.setattr(game24_plugin, "generate_24_puzzle", lambda: [1, 5, 5, 5])
    redis = _Redis()
    plugin = Game24Plugin()
    ctx = PluginContext(account_id=1, feature_key="game24", redis=redis, log=AsyncMock())
    state_key = "account_bot:game24:1:-100123"
    redis.data[state_key] = json.dumps(
        {
            "account_id": 1,
            "chat_id": -100123,
            "numbers": [1, 2, 3, 4],
            "prize": 888,
            "timeout": 30,
            "active": True,
            "game_id": "old-game",
            "created_at": 1000.0,
        }
    )
    monkeypatch.setattr(game24_plugin.time, "time", lambda: 2000.0)

    actions = await plugin.on_interaction(
        ctx,
        "start_paid_game",
        {
            "event_type": "keyword",
            "chat_id": -100123,
            "prize": 123,
            "valid_seconds": 90,
        },
    )

    assert actions and actions[0]["type"] == "send_message"
    state = json.loads(redis.data[state_key])
    assert state["active"] is True
    assert state["game_id"] != "old-game"
    assert state["timeout"] == 90
    assert redis.expires[state_key] == 90


@pytest.mark.asyncio
async def test_game24_interaction_winner_announcement_escapes_html_fields(monkeypatch) -> None:
    monkeypatch.setattr(game24_plugin, "generate_24_puzzle", lambda: [1, 5, 5, 5])
    redis = _Redis()
    plugin = Game24Plugin()
    ctx = PluginContext(account_id=1, feature_key="game24", redis=redis, log=AsyncMock())
    await plugin.on_interaction(
        ctx,
        "start_paid_game",
        {"event_type": "payment_confirmed", "chat_id": -100123, "prize": 888},
    )

    actions = await plugin.on_interaction(
        ctx,
        "start_paid_game",
        {
            "event_type": "message",
            "chat_id": -100123,
            "message_text": "5*(5-1/5)",
            "message_id": 99,
            "sender_name": "A<B & C>",
            "payout_account_label": "@owner<&>",
        },
    )

    assert actions[0]["text"] == (
        "答对了：A&lt;B &amp; C&gt;\n"
        "题目：24 点 [1 5 5 5]\n"
        "答案：5*(5-1/5) = 24\n"
        "奖金：888\n"
        "请由 @owner&lt;&amp;&gt; 人工回复赢家发放奖金。"
    )
    assert "A<B & C>" not in actions[0]["text"]
    assert "@owner<&>" not in actions[0]["text"]


@pytest.mark.asyncio
async def test_game24_concurrent_answer_only_awards_once(monkeypatch) -> None:
    monkeypatch.setattr(game24_plugin, "generate_24_puzzle", lambda: [1, 5, 5, 5])
    redis = _Redis()
    plugin = Game24Plugin()
    ctx = PluginContext(account_id=1, feature_key="game24", redis=redis, log=AsyncMock())
    await plugin.on_interaction(
        ctx,
        "start_paid_game",
        {"event_type": "payment_confirmed", "chat_id": -100123, "prize": 888},
    )

    first, second = await asyncio.gather(
        plugin.on_interaction(
            ctx,
            "start_paid_game",
            {
                "event_type": "message",
                "chat_id": -100123,
                "message_text": "5*(5-1/5)",
                "message_id": 99,
                "sender_name": "AAA",
            },
        ),
        plugin.on_interaction(
            ctx,
            "start_paid_game",
            {
                "event_type": "message",
                "chat_id": -100123,
                "message_text": "5*(5-1/5)",
                "message_id": 100,
                "sender_name": "BBB",
            },
        ),
    )

    replies = [actions for actions in [first, second] if actions]
    assert len(replies) == 1
    assert replies[0][0]["reply_to_message_id"] in {99, 100}
    assert replies[0][-1] == {"type": "end_session"}


@pytest.mark.asyncio
async def test_game24_accepts_bare_message_without_outgoing() -> None:
    """回归：某些 incoming 对象没有 outgoing 属性，正确答案仍应被处理。"""

    plugin = Game24Plugin()
    plugin._games[100] = GameState(
        chat_id=100,
        trigger_msg_id=10,
        numbers=[1, 2, 3, 4],
        prize=200,
        timeout=500,
    )

    async def _broken_get_sender():
        raise AttributeError("'Message' object has no attribute 'outgoing'")

    event = SimpleNamespace(
        chat_id=100,
        raw_text="(1+2+3)*4",
        sender_id=42,
        id=99,
        get_sender=_broken_get_sender,
        reply=AsyncMock(),
    )
    client = AsyncMock()
    client.get_messages.return_value = SimpleNamespace(text="原题")
    ctx = PluginContext(
        account_id=1,
        feature_key="game24",
        client=client,
        log=AsyncMock(),
    )

    await plugin.on_message(ctx, event)

    event.reply.assert_awaited_once_with("+200")
    client.send_message.assert_not_awaited()
    client.edit_message.assert_awaited_once()
    assert plugin._games[100].active is False
    assert plugin._games[100].winner_id == 42


@pytest.mark.asyncio
async def test_game24_prize_falls_back_to_client_send_message() -> None:
    """event.reply 不可用/失败时，仍应通过 client.send_message 回复奖励。"""

    plugin = Game24Plugin()
    plugin._games[100] = GameState(
        chat_id=100,
        trigger_msg_id=10,
        numbers=[1, 2, 3, 4],
        prize=200,
        timeout=500,
    )
    event = SimpleNamespace(
        chat_id=100,
        raw_text="(1+2+3)*4",
        sender_id=42,
        id=99,
        get_sender=AsyncMock(return_value=SimpleNamespace(first_name="alice", id=42)),
        reply=AsyncMock(side_effect=RuntimeError("reply blocked")),
    )
    client = AsyncMock()
    client.get_messages.return_value = SimpleNamespace(text="原题")
    ctx = PluginContext(
        account_id=1,
        feature_key="game24",
        client=client,
        log=AsyncMock(),
    )

    await plugin.on_message(ctx, event)

    event.reply.assert_awaited_once_with("+200")
    client.send_message.assert_awaited_once_with(
        entity=100,
        message="+200",
        reply_to=99,
    )
    client.edit_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_game24_announce_when_prize_send_failed() -> None:
    """发奖完全失败时，题目消息不能谎称已发放。"""

    plugin = Game24Plugin()
    plugin._games[100] = GameState(
        chat_id=100,
        trigger_msg_id=10,
        numbers=[1, 2, 3, 4],
        prize=200,
        timeout=500,
    )
    event = SimpleNamespace(
        chat_id=100,
        raw_text="(1+2+3)*4",
        sender_id=42,
        id=99,
        get_sender=AsyncMock(return_value=SimpleNamespace(first_name="我的备注", username="alice_public", contact=True, id=42)),
        reply=AsyncMock(side_effect=RuntimeError("reply blocked")),
    )
    client = AsyncMock()
    client.send_message.side_effect = RuntimeError("send blocked")
    client.get_messages.return_value = SimpleNamespace(text="原题")
    ctx = PluginContext(
        account_id=1,
        feature_key="game24",
        client=client,
        log=AsyncMock(),
    )

    await plugin.on_message(ctx, event)

    text = client.edit_message.call_args.kwargs["text"]
    assert "alice_public" in text
    assert "我的备注" not in text
    assert "奖励消息发送失败" in text
    assert "已发放" not in text


@pytest.mark.asyncio
async def test_game24_prize_falls_back_to_plain_message_when_reply_to_fails() -> None:
    """频道/匿名消息 reply_to 失败时，至少要把 +奖金普通消息发出去。"""

    plugin = Game24Plugin()
    plugin._games[100] = GameState(
        chat_id=100,
        trigger_msg_id=10,
        numbers=[10, 9, 12, 4],
        prize=2000,
        timeout=500,
    )
    event = SimpleNamespace(
        chat_id=100,
        raw_text="12*10/(9-4)",
        sender_id=None,
        id=99,
        get_sender=AsyncMock(return_value=None),
        reply=AsyncMock(side_effect=RuntimeError("reply blocked")),
    )
    client = AsyncMock()

    async def _send_message(**kwargs):
        if "reply_to" in kwargs:
            raise RuntimeError("reply_to blocked")
        return SimpleNamespace(id=101)

    client.send_message.side_effect = _send_message
    client.get_messages.return_value = SimpleNamespace(text="原题")
    ctx = PluginContext(
        account_id=1,
        feature_key="game24",
        client=client,
        log=AsyncMock(),
    )

    await plugin.on_message(ctx, event)

    assert client.send_message.await_count == 2
    assert client.send_message.await_args_list[0].kwargs == {
        "entity": 100,
        "message": "+2000",
        "reply_to": 99,
    }
    assert client.send_message.await_args_list[1].kwargs == {
        "entity": 100,
        "message": "+2000",
    }
    text = client.edit_message.call_args.kwargs["text"]
    assert "已发放" in text
