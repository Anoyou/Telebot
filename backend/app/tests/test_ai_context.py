from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.worker import ai_runtime, command


class _AsyncMessagesClient:
    def __init__(self, messages):
        self.messages = messages

    def iter_messages(self, *_args, **_kwargs):
        async def _iter():
            for message in self.messages:
                yield message

        return _iter()


@pytest.mark.asyncio
async def test_ai_history_is_role_labelled_oldest_first_and_filters_commands() -> None:
    command.set_command_context(
        command.CommandContext(
            account_id=1,
            templates={},
            providers={},
            self_tg_user_id=99,
            command_prefix=",",
        )
    )
    event = SimpleNamespace(chat_id=123, message=SimpleNamespace(id=4))
    messages = [
        SimpleNamespace(id=4, sender_id=1, text=",ai 当前问题"),
        SimpleNamespace(id=3, sender_id=99, text="回答"),
        SimpleNamespace(id=2, sender_id=7, text=",ping"),
        SimpleNamespace(id=1, sender_id=7, text="历史问题"),
        SimpleNamespace(id=0, sender_id=7, text="   "),
    ]
    client = _AsyncMessagesClient(messages)

    result = await ai_runtime._append_ai_history(client, event, "当前问题", 10)

    assert result == "[历史对话]\nuser: 历史问题\nassistant: 回答\n\n[当前问题]\n当前问题"


@pytest.mark.asyncio
async def test_ai_history_redacts_credentials_before_clipping_and_preserves_current_prompt() -> None:
    command.set_command_context(
        command.CommandContext(account_id=1, templates={}, providers={}, self_tg_user_id=99)
    )
    authorization_secret = "synthetic-authorization-secret"
    bot_secret = "123456:synthetic_bot_token"
    provider_secret = "sk-synthetic-provider-secret"
    proxy_secret = "synthetic-proxy-password"
    history_text = (
        f"Authorization: Bearer {authorization_secret}\n"
        f"https://api.telegram.org/bot{bot_secret}/getMe\n"
        f"api_key={provider_secret}\n"
        f"https://proxy-user:{proxy_secret}@proxy.example:8080\n"
        + "x" * ai_runtime._AI_CONTEXT_MAX_CHARS
    )
    current_prompt = "当前问题 api_key=sk-current-prompt-secret"
    client = _AsyncMessagesClient([SimpleNamespace(id=1, sender_id=7, text=history_text)])

    result = await ai_runtime._append_ai_history(
        client, SimpleNamespace(chat_id=123, message=SimpleNamespace(id=2)), current_prompt, 1
    )

    for secret in (authorization_secret, bot_secret, provider_secret, proxy_secret):
        assert secret not in result
    assert "Authorization: Bearer ***" in result
    assert "https://api.telegram.org/bot***/getMe" in result
    assert "https://***:***@proxy.example:8080" in result
    history_block, current_block = result.split("\n\n[当前问题]\n", 1)
    assert len(history_block) <= ai_runtime._AI_CONTEXT_MAX_CHARS
    assert current_block == current_prompt


@pytest.mark.asyncio
async def test_ai_history_redacts_credential_crossing_character_limit(monkeypatch) -> None:
    command.set_command_context(command.CommandContext(account_id=1, templates={}, providers={}))
    monkeypatch.setattr(ai_runtime, "_AI_CONTEXT_MAX_CHARS", 43)
    history_text = "普通消息 " + "a" * 20 + " sk-" + "s" * 100
    client = _AsyncMessagesClient([SimpleNamespace(id=1, sender_id=7, text=history_text)])

    result = await ai_runtime._append_ai_history(
        client, SimpleNamespace(chat_id=123, message=SimpleNamespace(id=2)), "当前问题", 1
    )

    assert "sk-" not in result
    assert result.endswith("\n\n[当前问题]\n当前问题")


@pytest.mark.asyncio
async def test_ai_context_setting_is_normalized_and_read_from_db(monkeypatch) -> None:
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _model, _key):
            return SimpleNamespace(value={"value": 99})

    monkeypatch.setattr("app.db.base.AsyncSessionLocal", lambda: _DB())
    assert await ai_runtime._read_ai_context_messages(0) == 20


@pytest.mark.asyncio
async def test_aictx_command_persists_and_updates_worker_context(monkeypatch) -> None:
    class _DB:
        def __init__(self):
            self.row = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _model, _key):
            return self.row

        def add(self, row):
            self.row = row

        async def commit(self):
            return None

    db = _DB()
    redis = AsyncMock()
    monkeypatch.setattr(command, "AsyncSessionLocal", lambda: db)
    monkeypatch.setattr(command, "get_redis", lambda: redis)
    command.set_command_context(command.CommandContext(account_id=1, templates={}, providers={}))
    event = AsyncMock()

    await command._cmd_aictx(None, event, ["7"], 1)

    assert command.get_command_context().ai_context_messages == 7
    assert db.row.value == {"value": 7}
    assert event.edit.await_args.args == ("已设置上下文 7 条",)

