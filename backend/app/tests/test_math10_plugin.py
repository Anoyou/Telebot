"""math10 交互 Bot 插件测试。"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from app.worker.plugins.base import PluginContext
from app.worker.plugins.builtin.math10 import plugin as math10_plugin
from app.worker.plugins.builtin.math10.manifest import MANIFEST
from app.worker.plugins.builtin.math10.plugin import Math10Plugin
from app.worker.plugins.message_ops import BufferedMessageOps


class _Redis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.expires: dict[str, int] = {}
        self.deleted: list[str] = []

    async def get(self, key: str):
        return self.data.get(key)

    async def set(self, key: str, value: str, **kwargs):
        if kwargs.get("nx") and key in self.data:
            return False
        self.data[key] = value
        if kwargs.get("ex") is not None:
            self.expires[key] = int(kwargs["ex"])
        return True

    async def delete(self, *keys: str):
        deleted = 0
        for key in keys:
            self.deleted.append(key)
            if key in self.data:
                deleted += 1
                del self.data[key]
        return deleted


def test_math10_manifest_declares_interaction_entry() -> None:
    assert MANIFEST.category == "interactive"
    assert MANIFEST.interaction_profile == "session_game"
    assert MANIFEST.interaction_entries
    entry = MANIFEST.interaction_entries[0]
    assert entry["key"] == "start_math_game"
    assert entry["interaction_profile"] == "session_game"
    assert entry["launch_mode"] == "bridge"
    assert entry["preserve_command_trigger"] is True
    assert set(entry["events"]) >= {"payment_confirmed", "keyword", "message", "session_close"}
    assert entry["result_contract"]["send_via"] == ["interaction_bot", "userbot_reply"]
    assert "settlement" in entry
    assert "prize" in entry["input_schema"]["properties"]


@pytest.mark.asyncio
async def test_math10_on_interaction_start_answer_and_close(monkeypatch) -> None:
    monkeypatch.setattr(math10_plugin, "_new_math_question", lambda: ("3 + 4", 7))
    redis = _Redis()
    plugin = Math10Plugin()
    ctx = PluginContext(account_id=1, feature_key="math10", redis=redis, log=AsyncMock())

    start_actions = await plugin.on_interaction(
        ctx,
        "start_math_game",
        {
            "event_type": "payment_confirmed",
            "chat_id": -100123,
            "source_update_id": 7,
            "source_message_id": 70,
            "prize": 888,
            "valid_seconds": 120,
        },
    )

    assert start_actions == [
        {
            "type": "send_message",
            "text": "算数题测试开始\n题目：3 + 4 = ?\n奖金：888\n直接发送数字答案，答对后我会公告赢家。",
        }
    ]
    state_key = "account_bot:math10:1:-100123"
    state = json.loads(redis.data[state_key])
    assert state["active"] is True
    assert state["question"] == "3 + 4"
    assert state["answer"] == 7
    assert state["prize"] == 888
    assert state["ttl_seconds"] == 120
    assert redis.expires[state_key] == 120

    wrong_actions = await plugin.on_interaction(
        ctx,
        "start_math_game",
        {
            "event_type": "message",
            "chat_id": -100123,
            "message_text": "8",
            "message_id": 98,
            "sender_name": "AAA",
        },
    )
    assert wrong_actions == []

    answer_actions = await plugin.on_interaction(
        ctx,
        "start_math_game",
        {
            "event_type": "message",
            "chat_id": -100123,
            "message_text": "7",
            "message_id": 99,
            "sender_name": "AAA",
            "payout_account_label": "@owner",
        },
    )
    assert answer_actions == [
        {
            "type": "send_message",
            "text": "答对了：AAA\n题目：3 + 4 = 7\n奖金：888\n请由 @owner 人工回复赢家发放奖金。",
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
                "question": "3 + 4",
                "answer": 7,
                "prize": 888,
                "payout_mode": "manual",
                "payout_account_label": "@owner",
            },
            "settlement": {
                "mode": "announce_only",
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
    claim_keys = [key for key in redis.data if key.startswith("account_bot:math10_claim:")]
    assert len(claim_keys) == 1

    await plugin.on_interaction(
        ctx,
        "start_math_game",
        {"event_type": "session_close", "chat_id": -100123},
    )
    assert state_key not in redis.data
    assert claim_keys[0] not in redis.data


@pytest.mark.asyncio
async def test_math10_on_interaction_buffers_visible_messages_with_message_ops(monkeypatch) -> None:
    monkeypatch.setattr(math10_plugin, "_new_math_question", lambda: ("3 + 4", 7))
    redis = _Redis()
    messages = BufferedMessageOps()
    plugin = Math10Plugin()
    ctx = PluginContext(account_id=1, feature_key="math10", redis=redis, log=AsyncMock(), messages=messages)

    start_actions = await plugin.on_interaction(
        ctx,
        "start_math_game",
        {"event_type": "keyword", "chat_id": -100123, "prize": 888},
    )

    assert start_actions == []
    assert messages.actions == [
        {
            "type": "send_message",
            "send_via": "interaction_bot",
            "chat_id": None,
            "text": "算数题测试开始\n题目：3 + 4 = ?\n奖金：888\n直接发送数字答案，答对后我会公告赢家。",
            "reply_to_message_id": None,
        }
    ]

    answer_actions = await plugin.on_interaction(
        ctx,
        "start_math_game",
        {
            "event_type": "message",
            "chat_id": -100123,
            "message_text": "7",
            "message_id": 99,
            "sender_name": "AAA",
            "payout_account_label": "@owner",
        },
    )

    assert [item["type"] for item in answer_actions] == ["result", "end_session"]
    assert messages.actions[-1] == {
        "type": "send_message",
        "send_via": "interaction_bot",
        "chat_id": None,
        "text": "答对了：AAA\n题目：3 + 4 = 7\n奖金：888\n请由 @owner 人工回复赢家发放奖金。",
        "reply_to_message_id": 99,
    }


@pytest.mark.asyncio
async def test_math10_on_interaction_accepts_standard_envelope(monkeypatch) -> None:
    monkeypatch.setattr(math10_plugin, "_new_math_question", lambda: ("3 + 4", 7))
    redis = _Redis()
    plugin = Math10Plugin()
    ctx = PluginContext(account_id=1, feature_key="math10", redis=redis, log=AsyncMock())

    await plugin.on_interaction(
        ctx,
        "start_math_game",
        {
            "source": {
                "type": "keyword",
                "chat_id": -100123,
                "update_id": 7,
                "message_id": 70,
                "text": "开算数题",
            },
            "trigger": {"type": "keyword", "rule_id": "math10", "entry_key": "start_math_game"},
            "session": {"scope": "chat", "ttl_seconds": 120},
            "prize": 888,
            "valid_seconds": 120,
        },
    )
    state = json.loads(redis.data["account_bot:math10:1:-100123"])
    assert state["source_update_id"] == 7
    assert state["source_message_id"] == 70

    actions = await plugin.on_interaction(
        ctx,
        "start_math_game",
        {
            "source": {
                "type": "message",
                "chat_id": -100123,
                "update_id": 8,
                "message_id": 99,
                "text": "7",
            },
            "actor": {"user_id": 111, "display_name": "AAA"},
            "settlement": {"mode": "auto", "payout_account_label": "@owner"},
        },
    )

    assert actions == [
        {
            "type": "send_message",
            "text": "答对了：AAA\n题目：3 + 4 = 7\n奖金：888\n奖金将由 @owner 账号自动发放。",
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
                "question": "3 + 4",
                "answer": 7,
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
async def test_math10_auto_payout_announcement(monkeypatch) -> None:
    monkeypatch.setattr(math10_plugin, "_new_math_question", lambda: ("6 x 7", 42))
    redis = _Redis()
    plugin = Math10Plugin()
    ctx = PluginContext(account_id=1, feature_key="math10", redis=redis, log=AsyncMock())

    await plugin.on_interaction(
        ctx,
        "start_math_game",
        {"event_type": "keyword", "chat_id": -100123, "prize": 123},
    )
    actions = await plugin.on_interaction(
        ctx,
        "start_math_game",
        {
            "event_type": "message",
            "chat_id": -100123,
            "message_text": "42",
            "message_id": 99,
            "sender_name": "AAA",
            "payout_account_label": "@owner",
            "payout_mode": "auto",
        },
    )

    assert actions == [
        {
            "type": "send_message",
            "text": "答对了：AAA\n题目：6 x 7 = 42\n奖金：123\n奖金将由 @owner 账号自动发放。",
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
                "question": "6 x 7",
                "answer": 42,
                "prize": 123,
                "payout_mode": "auto",
                "payout_account_label": "@owner",
            },
            "settlement": {
                "mode": "auto",
                "amount": 123,
                "winner_user_id": None,
                "winner_name": "AAA",
                "payout_account_label": "@owner",
                "status": "announced",
            },
        },
        {"type": "end_session"},
    ]


@pytest.mark.asyncio
async def test_math10_on_interaction_accepts_legacy_entry_key(monkeypatch) -> None:
    monkeypatch.setattr(math10_plugin, "_new_math_question", lambda: ("3 + 4", 7))
    redis = _Redis()
    plugin = Math10Plugin()
    ctx = PluginContext(account_id=1, feature_key="math10", redis=redis, log=AsyncMock())

    actions = await plugin.on_interaction(
        ctx,
        "start_math10",
        {"event_type": "keyword", "chat_id": -100123, "prize": 321},
    )

    assert actions == [
        {
            "type": "send_message",
            "text": "算数题测试开始\n题目：3 + 4 = ?\n奖金：321\n直接发送数字答案，答对后我会公告赢家。",
        }
    ]


@pytest.mark.asyncio
async def test_math10_keyword_starts_game_and_escapes_winner(monkeypatch) -> None:
    monkeypatch.setattr(math10_plugin, "_new_math_question", lambda: ("6 x 7", 42))
    redis = _Redis()
    plugin = Math10Plugin()
    ctx = PluginContext(account_id=1, feature_key="math10", redis=redis, log=AsyncMock())

    await plugin.on_interaction(
        ctx,
        "start_math_game",
        {"event_type": "keyword", "chat_id": -100123, "prize": 123},
    )
    actions = await plugin.on_interaction(
        ctx,
        "start_math_game",
        {
            "event_type": "message",
            "chat_id": -100123,
            "message_text": "42",
            "message_id": 99,
            "sender_name": "A<B & C>",
            "payout_account_label": "@owner<&>",
        },
    )

    assert actions and actions[0]["text"] == (
        "答对了：A&lt;B &amp; C&gt;\n"
        "题目：6 x 7 = 42\n"
        "奖金：123\n"
        "请由 @owner&lt;&amp;&gt; 人工回复赢家发放奖金。"
    )
    assert "A<B & C>" not in actions[0]["text"]
    assert "@owner<&>" not in actions[0]["text"]
