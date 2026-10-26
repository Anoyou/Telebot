"""访客模式（Guest Mode）“🤔 占位后编辑”的实时执行链路。

插件在 ``on_event`` 里用 ``ctx.messages.apply([...])`` 实时编辑 ``answerGuestQuery``
返回的 inline 消息。0.106.0-beta.2 只给“返回动作”链路（InteractionDeliveryExecutor）
加了 inline Rich 编辑，实时 apply 走的 ``_apply_userbot_edit_message_action`` 仍要求
chat_id + message_id，于是 inline Rich 编辑在请求 Telegram 之前就以
``target_message_id_missing`` 失败，插件误以为“被 Telegram 拒绝”而退回 HTML。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.worker.plugins import loader as loader_mod

TABLE_MD = (
    "**找到 2 个种子**\n\n"
    "| 标题 | 大小 | 做种/下载 |\n|---|---|---|\n"
    "| **[A](https://pt.example/details.php?id=1)** | 1 GB | 10/2 |\n"
    "| **[B](https://pt.example/details.php?id=2)** | 2 GB | 5/0 |"
)


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {}

    async def get(self, key: str) -> Any:
        return self.values.get(key)

    async def set(self, key: str, value: Any, *args: Any, **kwargs: Any) -> bool:
        self.values[key] = value
        return True

    async def rpush(self, key: str, val: str) -> int:
        return 1

    async def ltrim(self, *args: Any, **kwargs: Any) -> bool:
        return True

    async def publish(self, *args: Any, **kwargs: Any) -> int:
        return 0

    async def expire(self, *args: Any, **kwargs: Any) -> bool:
        return True


def _state(account_id: int = 4201) -> Any:
    state = loader_mod._AccountState(account_id)
    state.redis = _FakeRedis()
    return state


@pytest.fixture
def records(monkeypatch) -> AsyncMock:
    recorder = AsyncMock()
    monkeypatch.setattr(loader_mod, "record_action", recorder)
    monkeypatch.setattr(loader_mod, "emit_action_event", AsyncMock())
    monkeypatch.setattr(
        loader_mod, "_interaction_bot_token_for_account", AsyncMock(return_value="123:secret-token")
    )
    return recorder


@pytest.mark.asyncio
async def test_live_apply_inline_rich_edit_calls_edit_inline_rich_message(monkeypatch, records) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []

    async def _edit_inline_rich(token, inline_message_id, rich_message, *, reply_markup=None):
        calls.append((token, inline_message_id, rich_message))
        return {"result": True}

    text_edit = AsyncMock(side_effect=AssertionError("Rich 编辑不能静默改成文本编辑"))
    monkeypatch.setattr(loader_mod.account_bot_service, "edit_inline_rich_message", _edit_inline_rich)
    monkeypatch.setattr(loader_mod.account_bot_service, "edit_inline_message", text_edit)
    monkeypatch.setattr(
        loader_mod.account_bot_service,
        "edit_rich_message",
        AsyncMock(side_effect=AssertionError("不能走 chat 消息编辑")),
    )

    messages = loader_mod._InteractionEntryMessageOps(
        _state(), plugin_key="interaction-ai-chat", entry_key="main"
    )
    assert messages.inline_rich_edit is True
    assert messages.live_inline_edit is True

    outcome = await messages.apply(
        [{"type": "edit_message", "inline_message_id": "im-guest-1", "rich_message": {"markdown": TABLE_MD}}]
    )

    assert outcome["ok"] is True
    assert outcome["failed"] == 0
    assert "errors" not in outcome
    assert calls == [("123:secret-token", "im-guest-1", {"markdown": TABLE_MD})]
    text_edit.assert_not_awaited()
    status = records.await_args.args[2]
    assert status == "ok"
    assert records.await_args.kwargs["actual_send_via"] == "interaction_bot"


@pytest.mark.asyncio
async def test_live_apply_inline_rich_edit_reports_telegram_error(monkeypatch, records) -> None:
    monkeypatch.setattr(
        loader_mod.account_bot_service,
        "edit_inline_rich_message",
        AsyncMock(
            side_effect=RuntimeError(
                "Bad Request: can't parse rich message https://api.telegram.org/bot123:secret-token/x"
            )
        ),
    )
    text_edit = AsyncMock()
    monkeypatch.setattr(loader_mod.account_bot_service, "edit_inline_message", text_edit)
    write_log = AsyncMock()
    monkeypatch.setattr(loader_mod, "_log", write_log)

    messages = loader_mod._InteractionEntryMessageOps(
        _state(), plugin_key="interaction-ai-chat", entry_key="main"
    )
    outcome = await messages.apply(
        [{"type": "edit_message", "inline_message_id": "im-guest-2", "rich_message": {"markdown": TABLE_MD}}]
    )

    assert outcome["ok"] is False
    assert outcome["failed"] == 1
    [error] = outcome["errors"]
    assert error["type"] == "edit_message"
    assert error["error_code"] == "telegram_api_error"
    assert "can't parse rich message" in error["error"]
    assert "secret-token" not in error["error"]
    text_edit.assert_not_awaited()

    record_kwargs = records.await_args.kwargs
    assert record_kwargs["error_code"] == "telegram_api_error"
    assert "secret-token" not in record_kwargs["error"]
    assert record_kwargs["result"]["telegram_error"] == record_kwargs["error"]

    log_kwargs = write_log.await_args.kwargs
    assert log_kwargs["telegram_error"] == error["error"]
    assert log_kwargs["error_code"] == "telegram_api_error"
    assert log_kwargs["action_result"]["errors"] == outcome["errors"]


@pytest.mark.asyncio
async def test_live_apply_inline_text_edit_falls_back_to_plain(monkeypatch, records) -> None:
    calls: list[tuple[str, str | None]] = []

    async def _edit_inline(token, inline_message_id, text, *, reply_markup=None, parse_mode="HTML"):
        calls.append((text, parse_mode))
        if parse_mode:
            raise RuntimeError("Bad Request: can't parse entities: Unsupported start tag")
        return {"result": True}

    monkeypatch.setattr(loader_mod.account_bot_service, "edit_inline_message", _edit_inline)

    ok = await loader_mod._apply_userbot_edit_message_action(
        _state(),
        SimpleNamespace(chat_id=None),
        {
            "type": "edit_message",
            "inline_message_id": "im-3",
            "text": "<b>好</b>",
            "parse_mode": "html",
            "send_via": "interaction_bot",
        },
    )

    assert ok is True
    assert calls == [("<b>好</b>", "html"), ("好", None)]


@pytest.mark.asyncio
async def test_live_apply_inline_edit_rejects_userbot_channel(monkeypatch, records) -> None:
    rich_edit = AsyncMock()
    monkeypatch.setattr(loader_mod.account_bot_service, "edit_inline_rich_message", rich_edit)
    action = {
        "type": "edit_message",
        "inline_message_id": "im-4",
        "rich_message": {"markdown": "**x**"},
        "send_via": "userbot_reply",
    }

    ok = await loader_mod._apply_userbot_edit_message_action(_state(), SimpleNamespace(chat_id=None), action)

    assert ok is False
    rich_edit.assert_not_awaited()
    assert action["_tp_error"]["error_code"] == "unsupported_send_via"


@pytest.mark.asyncio
async def test_live_apply_chat_rich_edit_still_requires_message_id(monkeypatch, records) -> None:
    """没有 inline_message_id 的 Rich 编辑行为不变，但失败原因会带回插件。"""

    messages = loader_mod._InteractionEntryMessageOps(
        _state(), plugin_key="interaction-ai-chat", entry_key="main"
    )
    outcome = await messages.apply([{"type": "edit_message", "rich_message": {"markdown": "**x**"}}])

    assert outcome["ok"] is False
    assert outcome["errors"][0]["error_code"] == "target_message_id_missing"
