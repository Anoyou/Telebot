"""账号 Bot 算数题联动发奖测试。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.worker import runtime


def test_parse_account_bot_winner_notice() -> None:
    assert (
        runtime._parse_account_bot_winner_notice(
            "答对了：AAA\n题目：7 - 1 = 6\n奖金：123\n请由 userbot 账号人工回复赢家发放奖金。"
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

    assert cfg == {"bot_username": "bbot", "chat_ids": [-100123]}


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

    assert cfg == {"bot_username": "bbot", "chat_ids": [-100123, -100789]}


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

    assert cfg == {"bot_username": "bbot", "chat_ids": [-100123]}


@pytest.mark.asyncio
async def test_account_bot_auto_award_replies_to_winner_answer(monkeypatch) -> None:
    class _Redis:
        async def set(self, key, value, *, ex, nx):  # noqa: ANN001
            assert key.startswith(runtime._ACCOUNT_BOT_AUTO_AWARD_DEDUPE_PREFIX)
            assert key.endswith(":-100123:66:123")
            assert value == "1"
            assert ex == runtime._ACCOUNT_BOT_AUTO_AWARD_DEDUPE_TTL_SECONDS
            assert nx is True
            return True

    client = SimpleNamespace(send_message=AsyncMock())
    event = SimpleNamespace(
        raw_text="答对了：AAA\n题目：7 - 1 = 6\n奖金：123\n请由 userbot 账号人工回复赢家发放奖金。",
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
        sender=SimpleNamespace(username="Bbot"),
    )
    monkeypatch.setattr(
        runtime,
        "_load_account_bot_auto_award_config",
        AsyncMock(return_value={"bot_username": "bbot", "chat_id": -100123}),
    )

    handled = await runtime._try_account_bot_auto_award(client, _Redis(), 1, event)

    assert handled is True
    assert client.send_message.await_count == 0


@pytest.mark.asyncio
async def test_account_bot_auto_award_dedupes_by_answer_message(monkeypatch) -> None:
    seen_keys: list[str] = []

    class _Redis:
        async def set(self, key, *_args, **_kwargs):  # noqa: ANN001
            seen_keys.append(key)
            return len(seen_keys) == 1

    client = SimpleNamespace(send_message=AsyncMock())
    monkeypatch.setattr(
        runtime,
        "_load_account_bot_auto_award_config",
        AsyncMock(return_value={"bot_username": "bbot", "chat_id": -100123}),
    )
    monkeypatch.setattr(runtime, "_log", AsyncMock())

    for notice_id in (77, 88):
        event = SimpleNamespace(
            raw_text="答对了：AAA\n题目：7 - 1 = 6\n奖金：123",
            reply_to_msg_id=66,
            chat_id=-100123,
            id=notice_id,
            sender=SimpleNamespace(username="Bbot"),
        )
        handled = await runtime._try_account_bot_auto_award(client, _Redis(), 1, event)
        assert handled is True

    assert seen_keys == [
        f"{runtime._ACCOUNT_BOT_AUTO_AWARD_DEDUPE_PREFIX}1:-100123:66:123",
        f"{runtime._ACCOUNT_BOT_AUTO_AWARD_DEDUPE_PREFIX}1:-100123:66:123",
    ]
    assert client.send_message.await_count == 1


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
        sender=SimpleNamespace(username="Bbot"),
    )
    log_mock = AsyncMock()
    monkeypatch.setattr(
        runtime,
        "_load_account_bot_auto_award_config",
        AsyncMock(return_value={"bot_username": "bbot", "chat_id": -100123}),
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
        sender=SimpleNamespace(username="Bbot"),
    )
    monkeypatch.setattr(
        runtime,
        "_load_account_bot_auto_award_config",
        AsyncMock(return_value={"bot_username": "bbot", "chat_id": -100123}),
    )

    handled = await runtime._try_account_bot_auto_award(client, SimpleNamespace(), 1, event)

    assert handled is False
    assert client.send_message.await_count == 0
