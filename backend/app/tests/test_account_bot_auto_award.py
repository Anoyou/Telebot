"""账号 Bot 算数题联动发奖测试。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.worker import runtime


def test_parse_account_bot_winner_notice() -> None:
    assert (
        runtime._parse_account_bot_winner_notice(
            "答对了：AAA\n题目：7 - 1 = 6\n奖金：123\n请由 @owner 人工回复赢家发放奖金。"
        )
        == 123
    )
    assert (
        runtime._parse_account_bot_winner_notice(
            "答对了：AAA\n题目：7 - 1 = 6\n奖金：123\n奖金将由 @owner 账号自动发放。"
        )
        == 123
    )
    assert runtime._parse_account_bot_winner_notice("奖金：123") is None


@pytest.mark.asyncio
async def test_load_account_bot_auto_award_config_reads_math_rule(monkeypatch) -> None:
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, _model, _key):  # noqa: ANN001
            return SimpleNamespace(
                value={
                    "enabled": True,
                    "interaction_bot_id": 8807483916,
                    "interaction_bot_username": "Bbot",
                    "chat_ids": [-100999],
                    "action": "notice",
                    "rules": [
                        {"enabled": True, "action": "notice", "chat_ids": [-100999]},
                        {"enabled": True, "action": "math10", "chat_ids": [-100123]},
                    ],
                }
            )

    monkeypatch.setattr(runtime, "AsyncSessionLocal", lambda: _DB())

    cfg = await runtime._load_account_bot_auto_award_config(1)

    assert cfg == {"bot_id": 8807483916, "bot_username": "bbot", "chat_ids": [-100123]}


@pytest.mark.asyncio
async def test_load_account_bot_auto_award_config_merges_math_rule_chats(monkeypatch) -> None:
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, _model, _key):  # noqa: ANN001
            return SimpleNamespace(
                value={
                    "enabled": True,
                    "interaction_bot_id": 8807483916,
                    "interaction_bot_username": "Bbot",
                    "chat_ids": [-100999],
                    "action": "notice",
                    "rules": [
                        {"enabled": True, "action": "math10", "chat_ids": [-100123]},
                        {"enabled": True, "action": "notice", "chat_ids": [-100456]},
                        {"enabled": True, "action": "math10", "chat_ids": [-100789, -100123]},
                    ],
                }
            )

    monkeypatch.setattr(runtime, "AsyncSessionLocal", lambda: _DB())

    cfg = await runtime._load_account_bot_auto_award_config(1)

    assert cfg == {"bot_id": 8807483916, "bot_username": "bbot", "chat_ids": [-100123, -100789]}


@pytest.mark.asyncio
async def test_load_account_bot_auto_award_config_allows_bot_id_without_username(monkeypatch) -> None:
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, _model, _key):  # noqa: ANN001
            return SimpleNamespace(
                value={
                    "enabled": True,
                    "interaction_bot_id": 8807483916,
                    "action": "math10",
                    "chat_ids": [-100123],
                }
            )

    monkeypatch.setattr(runtime, "AsyncSessionLocal", lambda: _DB())

    cfg = await runtime._load_account_bot_auto_award_config(1)

    assert cfg == {"bot_id": 8807483916, "bot_username": None, "chat_ids": [-100123]}


@pytest.mark.asyncio
async def test_load_account_bot_auto_award_config_reads_game24_module_rule(monkeypatch) -> None:
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, _model, _key):  # noqa: ANN001
            return SimpleNamespace(
                value={
                    "enabled": True,
                    "interaction_bot_id": 8807483916,
                    "interaction_bot_username": "Bbot",
                    "rules": [
                        {
                            "enabled": True,
                            "action": "module",
                            "module_key": "game24",
                            "chat_ids": [-100123],
                        },
                    ],
                }
            )

    monkeypatch.setattr(runtime, "AsyncSessionLocal", lambda: _DB())

    cfg = await runtime._load_account_bot_auto_award_config(1)

    assert cfg == {"bot_id": 8807483916, "bot_username": "bbot", "chat_ids": [-100123]}


@pytest.mark.asyncio
async def test_load_account_bot_auto_award_config_reads_math10_module_rule(monkeypatch) -> None:
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, _model, _key):  # noqa: ANN001
            return SimpleNamespace(
                value={
                    "enabled": True,
                    "interaction_bot_id": 8807483916,
                    "interaction_bot_username": "Bbot",
                    "rules": [
                        {
                            "enabled": True,
                            "action": "module",
                            "module_key": "math10",
                            "chat_ids": [-100456],
                        },
                    ],
                }
            )

    monkeypatch.setattr(runtime, "AsyncSessionLocal", lambda: _DB())

    cfg = await runtime._load_account_bot_auto_award_config(1)

    assert cfg == {"bot_id": 8807483916, "bot_username": "bbot", "chat_ids": [-100456]}


@pytest.mark.asyncio
async def test_load_account_bot_auto_award_config_reads_dice_grid_module_rule(monkeypatch) -> None:
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, _model, _key):  # noqa: ANN001
            return SimpleNamespace(
                value={
                    "enabled": True,
                    "interaction_bot_id": 8807483916,
                    "interaction_bot_username": "Bbot",
                    "rules": [
                        {
                            "enabled": True,
                            "action": "module",
                            "module_key": "dice_grid_hunt",
                            "chat_ids": [-100789],
                        },
                    ],
                }
            )

    monkeypatch.setattr(runtime, "AsyncSessionLocal", lambda: _DB())

    cfg = await runtime._load_account_bot_auto_award_config(1)

    assert cfg == {"bot_id": 8807483916, "bot_username": "bbot", "chat_ids": [-100789]}


@pytest.mark.asyncio
async def test_account_bot_auto_award_replies_when_sender_id_matches(monkeypatch) -> None:
    class _Redis:
        async def set(self, key, value, *, ex, nx):  # noqa: ANN001
            assert key.startswith(runtime._ACCOUNT_BOT_AUTO_AWARD_DEDUPE_PREFIX)
            assert key.endswith(":-100123:77:66:123")
            assert value == "1"
            assert ex == runtime._ACCOUNT_BOT_AUTO_AWARD_DEDUPE_TTL_SECONDS
            assert nx is True
            return True

    client = SimpleNamespace(send_message=AsyncMock())
    event = SimpleNamespace(
        raw_text="答对了：AAA\n题目：7 - 1 = 6\n奖金：123\n请由 @owner 人工回复赢家发放奖金。",
        reply_to_msg_id=66,
        chat_id=-100123,
        id=77,
        sender=SimpleNamespace(id=8807483916, username="Bbot"),
    )
    monkeypatch.setattr(
        runtime,
        "_load_account_bot_auto_award_config",
        AsyncMock(return_value={"bot_id": 8807483916, "bot_username": "bbot", "chat_id": -100123}),
    )
    monkeypatch.setattr(runtime, "_account_bot_auto_award_trace_enabled", AsyncMock(return_value=False))
    monkeypatch.setattr(runtime, "_log", AsyncMock())

    handled = await runtime._try_account_bot_auto_award(client, _Redis(), 1, event)

    assert handled is True
    assert client.send_message.await_count == 1
    assert client.send_message.await_args.kwargs == {
        "entity": -100123,
        "message": "+123",
        "reply_to": 66,
    }


@pytest.mark.asyncio
async def test_account_bot_auto_award_rejects_sender_id_mismatch_before_username_fallback(monkeypatch) -> None:
    class _Redis:
        async def set(self, *_args, **_kwargs):  # noqa: ANN001
            raise AssertionError("dedupe should not run for untrusted sender")

    client = SimpleNamespace(send_message=AsyncMock())
    event = SimpleNamespace(
        raw_text="答对了：AAA\n题目：7 - 1 = 6\n奖金：123",
        reply_to_msg_id=66,
        chat_id=-100123,
        id=77,
        sender=SimpleNamespace(id=12345, username="Bbot"),
    )
    monkeypatch.setattr(
        runtime,
        "_load_account_bot_auto_award_config",
        AsyncMock(return_value={"bot_id": 8807483916, "bot_username": "bbot", "chat_id": -100123}),
    )

    handled = await runtime._try_account_bot_auto_award(client, _Redis(), 1, event)

    assert handled is False
    assert client.send_message.await_count == 0


@pytest.mark.asyncio
async def test_account_bot_auto_award_falls_back_to_username_without_bot_id(monkeypatch) -> None:
    class _Redis:
        async def set(self, key, value, *, ex, nx):  # noqa: ANN001
            assert key.endswith(":-100123:77:66:123")
            assert value == "1"
            assert ex == runtime._ACCOUNT_BOT_AUTO_AWARD_DEDUPE_TTL_SECONDS
            assert nx is True
            return True

    client = SimpleNamespace(send_message=AsyncMock())
    event = SimpleNamespace(
        raw_text="答对了：AAA\n题目：7 - 1 = 6\n奖金：123",
        reply_to_msg_id=66,
        chat_id=-100123,
        id=77,
        sender=SimpleNamespace(username="Bbot"),
    )
    monkeypatch.setattr(
        runtime,
        "_load_account_bot_auto_award_config",
        AsyncMock(return_value={"bot_username": "bbot", "chat_id": -100123}),
    )
    monkeypatch.setattr(runtime, "_account_bot_auto_award_trace_enabled", AsyncMock(return_value=False))
    monkeypatch.setattr(runtime, "_log", AsyncMock())

    handled = await runtime._try_account_bot_auto_award(client, _Redis(), 1, event)

    assert handled is True
    assert client.send_message.await_count == 1


@pytest.mark.asyncio
async def test_account_bot_auto_award_ignores_duplicate_notice(monkeypatch) -> None:
    class _Redis:
        async def set(self, *_args, **_kwargs):  # noqa: ANN001
            return None

    client = SimpleNamespace(send_message=AsyncMock())
    event = SimpleNamespace(
        raw_text="答对了：AAA\n题目：7 - 1 = 6\n奖金：123",
        reply_to_msg_id=66,
        chat_id=-100123,
        id=77,
        sender=SimpleNamespace(id=8807483916, username="Bbot"),
    )
    monkeypatch.setattr(
        runtime,
        "_load_account_bot_auto_award_config",
        AsyncMock(return_value={"bot_id": 8807483916, "bot_username": "bbot", "chat_id": -100123}),
    )

    handled = await runtime._try_account_bot_auto_award(client, _Redis(), 1, event)

    assert handled is True
    assert client.send_message.await_count == 0


@pytest.mark.asyncio
async def test_account_bot_auto_award_dedupes_by_notice_message(monkeypatch) -> None:
    seen_keys: list[str] = []

    class _Redis:
        async def set(self, key, *_args, **_kwargs):  # noqa: ANN001
            seen_keys.append(key)
            return len(seen_keys) == 1

    client = SimpleNamespace(send_message=AsyncMock())
    monkeypatch.setattr(
        runtime,
        "_load_account_bot_auto_award_config",
        AsyncMock(return_value={"bot_id": 8807483916, "bot_username": "bbot", "chat_id": -100123}),
    )
    monkeypatch.setattr(runtime, "_account_bot_auto_award_trace_enabled", AsyncMock(return_value=False))
    monkeypatch.setattr(runtime, "_log", AsyncMock())

    for notice_id in (77, 77):
        event = SimpleNamespace(
            raw_text="答对了：AAA\n题目：7 - 1 = 6\n奖金：123",
            reply_to_msg_id=66,
            chat_id=-100123,
            id=notice_id,
            sender=SimpleNamespace(id=8807483916, username="Bbot"),
        )
        handled = await runtime._try_account_bot_auto_award(client, _Redis(), 1, event)
        assert handled is True

    assert seen_keys == [
        f"{runtime._ACCOUNT_BOT_AUTO_AWARD_DEDUPE_PREFIX}1:-100123:77:66:123",
        f"{runtime._ACCOUNT_BOT_AUTO_AWARD_DEDUPE_PREFIX}1:-100123:77:66:123",
    ]
    assert client.send_message.await_count == 1


@pytest.mark.asyncio
async def test_account_bot_auto_award_records_trace_action(monkeypatch) -> None:
    class _Redis:
        async def set(self, *_args, **_kwargs):  # noqa: ANN001
            return True

    trace = SimpleNamespace(trace_id="evt_auto_award")
    client = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(id=888)))
    event = SimpleNamespace(
        raw_text="答对了：AAA\n题目：7 - 1 = 6\n奖金：123",
        reply_to_msg_id=66,
        chat_id=-100123,
        id=77,
        sender=SimpleNamespace(id=8807483916, username="Bbot"),
    )
    start_trace = AsyncMock(return_value=trace)
    record_span = AsyncMock()
    record_action = AsyncMock()
    finish_trace = AsyncMock()
    dispatch_event = MagicMock(side_effect=runtime.dispatch_event)
    monkeypatch.setattr(
        runtime,
        "_load_account_bot_auto_award_config",
        AsyncMock(return_value={"bot_id": 8807483916, "bot_username": "bbot", "chat_id": -100123}),
    )
    monkeypatch.setattr(runtime, "_account_bot_auto_award_trace_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(runtime, "start_trace", start_trace)
    monkeypatch.setattr(runtime, "dispatch_event", dispatch_event)
    monkeypatch.setattr(runtime, "record_span", record_span)
    monkeypatch.setattr(runtime, "record_action", record_action)
    monkeypatch.setattr(runtime, "finish_trace", finish_trace)
    monkeypatch.setattr(runtime, "_log", AsyncMock())

    handled = await runtime._try_account_bot_auto_award(client, _Redis(), 1, event)

    assert handled is True
    dispatch_event.assert_called_once()
    subscription = dispatch_event.call_args.args[1][0]
    assert subscription.plugin_key == "account_bot_auto_award"
    assert subscription.entry_key == "auto_award"
    assert subscription.events == ["payment_confirmed"]
    assert subscription.dispatch_mode == "account_bot_auto_award"
    start_trace.assert_awaited_once()
    assert start_trace.await_args.args[0]["event_type"] == "payment_confirmed"
    assert start_trace.await_args.args[0]["trigger"]["source"] == "account_bot_auto_award"
    record_action.assert_awaited_once()
    assert record_action.await_args.args[1]["send_via"] == "userbot_reply"
    assert record_action.await_args.args[2] == runtime.TRACE_STATUS_OK
    assert record_action.await_args.kwargs["actual_send_via"] == "userbot_reply"
    finish_trace.assert_awaited_once_with(trace, runtime.TRACE_STATUS_OK)


@pytest.mark.asyncio
async def test_account_bot_auto_award_skips_when_dedupe_fails(monkeypatch) -> None:
    class _Redis:
        async def set(self, *_args, **_kwargs):  # noqa: ANN001
            raise RuntimeError("redis unavailable")

    client = SimpleNamespace(send_message=AsyncMock())
    event = SimpleNamespace(
        raw_text="答对了：AAA\n题目：7 - 1 = 6\n奖金：123",
        reply_to_msg_id=66,
        chat_id=-100123,
        id=77,
        sender=SimpleNamespace(id=8807483916, username="Bbot"),
    )
    log_mock = AsyncMock()
    monkeypatch.setattr(
        runtime,
        "_load_account_bot_auto_award_config",
        AsyncMock(return_value={"bot_id": 8807483916, "bot_username": "bbot", "chat_id": -100123}),
    )
    monkeypatch.setattr(runtime, "_log", log_mock)

    redis = _Redis()
    handled = await runtime._try_account_bot_auto_award(client, redis, 1, event)

    assert handled is True
    assert client.send_message.await_count == 0
    assert log_mock.await_args.args[0] is redis
    assert log_mock.await_args.args[1:4] == (
        1,
        "warn",
        "临时算数题自动发奖：幂等检查失败，已跳过本次自动发奖。",
    )


@pytest.mark.asyncio
async def test_account_bot_auto_award_requires_configured_chat(monkeypatch) -> None:
    client = SimpleNamespace(send_message=AsyncMock())
    event = SimpleNamespace(
        raw_text="答对了：AAA\n题目：7 - 1 = 6\n奖金：123",
        reply_to_msg_id=66,
        chat_id=-100999,
        id=77,
        sender=SimpleNamespace(id=8807483916, username="Bbot"),
    )
    monkeypatch.setattr(
        runtime,
        "_load_account_bot_auto_award_config",
        AsyncMock(return_value={"bot_id": 8807483916, "bot_username": "bbot", "chat_id": -100123}),
    )

    handled = await runtime._try_account_bot_auto_award(client, SimpleNamespace(), 1, event)

    assert handled is False
    assert client.send_message.await_count == 0
