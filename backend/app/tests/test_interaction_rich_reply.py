"""交互 Bot 富文本回复：HTML 解析失败回退纯文本、占位消息编辑、Guest Mode inline 编辑。"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.services import account_bot_runtime, account_bot_service
from app.services.event_bus import normalize_bot_update
from app.services.interaction.delivery import InteractionDeliveryExecutor
from app.services.interaction.guest_reply import GuestReplyBuffer

CHAT = -100777
PARSE_ERROR = "Bad Request: can't parse entities: Unsupported start tag \"foo\" at byte offset 3"


def _group_update(text: str = "@demo_bot 你好") -> dict[str, Any]:
    return {
        "update_id": 5001,
        "message": {
            "message_id": 42,
            "date": 1_778_000_000,
            "text": text,
            "from": {"id": 4242, "first_name": "User", "username": "user"},
            "chat": {"id": CHAT, "type": "supergroup", "title": "群"},
        },
    }


def _guest_update() -> dict[str, Any]:
    return {
        "update_id": 9001,
        "guest_message": {
            "message_id": 77,
            "guest_query_id": "gq-1",
            "date": 1_778_000_000,
            "text": "@demo_bot 这是什么",
            "from": {"id": 4242, "first_name": "Guest"},
            "chat": {"id": CHAT, "type": "supergroup"},
        },
    }


def _executor(monkeypatch, update: dict[str, Any], *, guest: bool = False):
    incoming = account_bot_runtime._extract_incoming(1, "tok", update)
    assert incoming is not None
    if guest:
        incoming.guest_reply = GuestReplyBuffer(query_id="gq-1")
    records = AsyncMock()
    monkeypatch.setattr("app.services.interaction.delivery.record_action", records)
    executor = InteractionDeliveryExecutor(
        incoming=incoming,
        write_log=AsyncMock(),
        run_worker_action=AsyncMock(side_effect=AssertionError("must not call userbot")),
        log_context=lambda _incoming: {},
        trace_context=lambda _payload: {},
    )
    return incoming, executor, records


@pytest.mark.asyncio
async def test_send_message_html_parse_error_retries_as_plain_text(monkeypatch) -> None:
    calls: list[tuple[str, Any]] = []

    async def _send(token, chat_id, text, **kwargs):
        calls.append((text, kwargs.get("parse_mode")))
        if kwargs.get("parse_mode"):
            raise RuntimeError(PARSE_ERROR)
        return {"message_id": 9}

    monkeypatch.setattr(account_bot_service, "send_message", _send)
    _incoming, executor, _records = _executor(monkeypatch, _group_update())

    await executor.apply(
        [{"type": "send_message", "chat_id": CHAT, "text": "<b>粗体</b> &amp; 文本", "parse_mode": "html", "send_via": "interaction_bot"}]
    )

    assert calls == [("<b>粗体</b> &amp; 文本", "html"), ("粗体 & 文本", None)]


@pytest.mark.asyncio
async def test_send_message_non_parse_error_is_not_retried(monkeypatch) -> None:
    send = AsyncMock(side_effect=RuntimeError("Forbidden: bot was kicked"))
    monkeypatch.setattr(account_bot_service, "send_message", send)
    _incoming, executor, _records = _executor(monkeypatch, _group_update())

    await executor.apply([{"type": "send_message", "chat_id": CHAT, "text": "<b>x</b>", "parse_mode": "html", "send_via": "interaction_bot"}])

    assert send.await_count == 1


@pytest.mark.asyncio
async def test_edit_message_html_parse_error_retries_as_plain_text(monkeypatch) -> None:
    calls: list[tuple[int, str, Any]] = []

    async def _edit(token, chat_id, message_id, text, **kwargs):
        calls.append((message_id, text, kwargs.get("parse_mode")))
        if kwargs.get("parse_mode"):
            raise RuntimeError(PARSE_ERROR)
        return {"message_id": message_id}

    monkeypatch.setattr(account_bot_service, "edit_message", _edit)
    _incoming, executor, _records = _executor(monkeypatch, _group_update())

    await executor.apply(
        [{"type": "edit_message", "chat_id": CHAT, "message_id": 55, "text": "<i>答案</i>", "parse_mode": "html", "send_via": "interaction_bot"}]
    )

    assert calls == [(55, "<i>答案</i>", "html"), (55, "答案", None)]


@pytest.mark.asyncio
async def test_edit_failure_sends_new_message_when_requested(monkeypatch) -> None:
    monkeypatch.setattr(account_bot_service, "edit_message", AsyncMock(side_effect=RuntimeError("Bad Request: message to edit not found")))
    send = AsyncMock(return_value={"message_id": 10})
    monkeypatch.setattr(account_bot_service, "send_message", send)
    _incoming, executor, records = _executor(monkeypatch, _group_update())

    await executor.apply(
        [
            {
                "type": "edit_message",
                "chat_id": CHAT,
                "message_id": 55,
                "text": "最终答案",
                "send_via": "interaction_bot",
                "send_on_edit_failure": True,
                "reply_to_message_id": 42,
            }
        ]
    )

    send.assert_awaited_once()
    assert send.await_args.args[2] == "最终答案"
    assert send.await_args.kwargs["reply_to_message_id"] == 42
    assert records.await_args.kwargs["result"]["edit_fallback_send"] is True


@pytest.mark.asyncio
async def test_edit_failure_without_flag_does_not_send(monkeypatch) -> None:
    monkeypatch.setattr(account_bot_service, "edit_message", AsyncMock(side_effect=RuntimeError("message to edit not found")))
    send = AsyncMock()
    monkeypatch.setattr(account_bot_service, "send_message", send)
    _incoming, executor, _records = _executor(monkeypatch, _group_update())

    await executor.apply([{"type": "edit_message", "chat_id": CHAT, "message_id": 55, "text": "x", "send_via": "interaction_bot"}])

    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_guest_inline_edit_bypasses_guest_buffer(monkeypatch) -> None:
    calls: list[tuple[str, str, Any]] = []

    async def _edit_inline(token, inline_message_id, text, **kwargs):
        calls.append((inline_message_id, text, kwargs.get("parse_mode")))
        if kwargs.get("parse_mode"):
            raise RuntimeError(PARSE_ERROR)
        return {"result": True}

    monkeypatch.setattr(account_bot_service, "edit_inline_message", _edit_inline)
    incoming, executor, _records = _executor(monkeypatch, _guest_update(), guest=True)

    await executor.apply(
        [{"type": "edit_message", "inline_message_id": "im-1", "text": "<b>答</b>", "parse_mode": "html", "send_via": "interaction_bot"}]
    )

    assert calls == [("im-1", "<b>答</b>", "html"), ("im-1", "答", None)]
    assert incoming.guest_reply is not None and incoming.guest_reply.entries == []


@pytest.mark.asyncio
async def test_guest_inline_edit_supports_rich_message(monkeypatch) -> None:
    rich_calls: list[tuple[str, dict[str, Any]]] = []

    async def _edit_inline_rich(token, inline_message_id, rich_message, **kwargs):
        rich_calls.append((inline_message_id, rich_message))
        return True

    text_edit = AsyncMock(side_effect=AssertionError("rich edit must not fall back to text silently"))
    monkeypatch.setattr(account_bot_service, "edit_inline_rich_message", _edit_inline_rich)
    monkeypatch.setattr(account_bot_service, "edit_inline_message", text_edit)
    incoming, executor, records = _executor(monkeypatch, _guest_update(), guest=True)

    await executor.apply(
        [
            {
                "type": "edit_message",
                "inline_message_id": "im-1",
                "rich_message": {"markdown": "| 标题 | 大小 |\n|---|---|\n| **[A](https://pt.example/details.php?id=1)** | 1 GB |"},
                "send_via": "interaction_bot",
            }
        ]
    )

    assert rich_calls and rich_calls[0][0] == "im-1"
    assert rich_calls[0][1]["markdown"].startswith("| 标题 |")
    text_edit.assert_not_awaited()
    assert incoming.guest_reply is not None and incoming.guest_reply.entries == []
    assert records.await_args.args[2] == "ok"


@pytest.mark.asyncio
async def test_guest_inline_rich_edit_failure_is_reported(monkeypatch) -> None:
    monkeypatch.setattr(
        account_bot_service,
        "edit_inline_rich_message",
        AsyncMock(side_effect=RuntimeError("Bad Request: rich_message is not supported")),
    )
    text_edit = AsyncMock()
    monkeypatch.setattr(account_bot_service, "edit_inline_message", text_edit)
    _incoming, executor, records = _executor(monkeypatch, _guest_update(), guest=True)

    await executor.apply(
        [{"type": "edit_message", "inline_message_id": "im-1", "rich_message": {"markdown": "**x**"}, "send_via": "interaction_bot"}]
    )

    text_edit.assert_not_awaited()
    assert records.await_args.args[2] != "ok"
    assert records.await_args.kwargs["error_code"] == "telegram_api_error"


@pytest.mark.asyncio
async def test_guest_answer_html_parse_error_falls_back_to_plain(monkeypatch) -> None:
    results: list[dict[str, Any]] = []

    async def _answer(token, query_id, *, result):
        results.append(result)
        if result["input_message_content"].get("parse_mode"):
            raise RuntimeError(PARSE_ERROR)
        return {"inline_message_id": "im"}

    monkeypatch.setattr(account_bot_service, "answer_guest_query", _answer)
    incoming, executor, _records = _executor(monkeypatch, _guest_update(), guest=True)

    await executor.apply([{"type": "answer_guest_query", "text": "<b>好</b>", "parse_mode": "html"}])

    assert len(results) == 2
    assert results[1]["input_message_content"] == {"message_text": "好"}
    assert incoming.guest_reply is not None and incoming.guest_reply.answered is True


@pytest.mark.asyncio
async def test_edit_inline_message_bot_api_payload(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    async def _call(token, method, payload, **kwargs):
        captured.update(method=method, payload=payload)
        return {"result": True}

    monkeypatch.setattr(account_bot_service, "call_bot_api", _call)
    await account_bot_service.edit_inline_message("tok", "im-9", "<b>x</b>", parse_mode="html")

    assert captured["method"] == "editMessageText"
    assert captured["payload"]["inline_message_id"] == "im-9"
    assert "chat_id" not in captured["payload"]
    assert captured["payload"]["parse_mode"] == "HTML"


def test_parse_entities_error_detection() -> None:
    assert account_bot_service.is_bot_parse_entities_error(PARSE_ERROR)
    assert account_bot_service.is_bot_parse_entities_error("Bad Request: can't find end tag corresponding to start tag \"b\"")
    assert not account_bot_service.is_bot_parse_entities_error("Forbidden: bot was blocked by the user")


def test_reply_to_media_summary_is_exposed() -> None:
    update = _group_update()
    update["message"]["reply_to_message"] = {
        "message_id": 41,
        "caption": "看图",
        "photo": [
            {"file_id": "small", "file_unique_id": "s", "width": 90, "height": 90},
            {"file_id": "large", "file_unique_id": "l", "width": 1280, "height": 720, "file_size": 1000},
        ],
        "from": {"id": 5151, "first_name": "Other"},
        "chat": {"id": CHAT, "type": "supergroup"},
    }
    event = normalize_bot_update(1, update, channel="interaction_bot")
    assert event["reply_to"]["media"]["type"] == "photo"
    assert event["reply_to"]["media"]["file_id"] == "large"

    incoming = account_bot_runtime._extract_incoming(1, "tok", update)
    assert incoming is not None
    payload = account_bot_runtime._incoming_trace_payload(incoming)
    assert payload["reply_to"]["media"]["file_id"] == "large"


@pytest.mark.asyncio
async def test_interaction_bot_identity_is_best_effort(monkeypatch) -> None:
    incoming = account_bot_runtime._extract_incoming(1, "tok", _group_update())
    assert incoming is not None
    monkeypatch.setattr(
        account_bot_service,
        "get_bot_capabilities",
        AsyncMock(return_value={"bot_id": 999, "username": "demo_bot", "supports_guest_queries": True}),
    )
    assert await account_bot_runtime._interaction_bot_identity(incoming) == {"id": 999, "username": "demo_bot"}

    monkeypatch.setattr(
        account_bot_service,
        "get_bot_capabilities",
        AsyncMock(return_value={"bot_id": 999, "username": "demo_bot", "can_read_all_group_messages": False}),
    )
    assert await account_bot_runtime._interaction_bot_identity(incoming) == {
        "id": 999,
        "username": "demo_bot",
        "can_read_all_group_messages": False,
    }

    monkeypatch.setattr(account_bot_service, "get_bot_capabilities", AsyncMock(side_effect=RuntimeError("offline")))
    assert await account_bot_runtime._interaction_bot_identity(incoming) is None


@pytest.mark.asyncio
async def test_download_bot_file_enforces_size_limit(monkeypatch) -> None:
    async def _call(token, method, payload, **kwargs):
        assert method == "getFile"
        return {"file_id": payload["file_id"], "file_size": 50, "file_path": "photos/a.jpg"}

    monkeypatch.setattr(account_bot_service, "call_bot_api", _call)
    with pytest.raises(ValueError, match="过大"):
        await account_bot_service.download_bot_file("tok", "f1", max_bytes=10)


class _DictRedis:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    async def set(self, key: str, value: Any, ex: int | None = None) -> bool:
        self.data[key] = value
        return True

    async def get(self, key: str) -> Any:
        return self.data.get(key)


@pytest.mark.asyncio
async def test_worker_saves_and_reads_guest_inline_message_id() -> None:
    from app.worker.plugins import loader as loader_mod

    state = loader_mod._AccountState(account_id=1)
    state.redis = _DictRedis()
    action = {"type": "answer_guest_query", "save_message_id_key": "iac:placeholder:abc"}

    await loader_mod._save_action_inline_message_id(state, action, {"inline_message_id": "AgAAA-inline"})

    assert await loader_mod._read_action_inline_message_id(state, "iac:placeholder:abc") == "AgAAA-inline"
    # 普通 message_id 读取不会把 inline id 误当作整数。
    assert await loader_mod._read_action_message_id(state, "iac:placeholder:abc") is None
    assert await loader_mod._read_action_inline_message_id(state, "missing") is None
