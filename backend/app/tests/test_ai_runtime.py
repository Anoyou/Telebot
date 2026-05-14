from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.worker import ai_runtime
from app.worker import command as wcmd
from app.worker.command import CommandContext


@pytest.fixture(autouse=True)
def _reset_ctx():
    wcmd.set_command_context(CommandContext(account_id=1, templates={}, providers={}))
    yield
    wcmd.set_command_context(CommandContext(account_id=1, templates={}, providers={}))


@pytest.mark.asyncio
async def test_run_ai_wrapper_delegates_to_ai_runtime(monkeypatch) -> None:
    client = AsyncMock()
    event = AsyncMock()
    tpl = {"name": "ai", "type": "ai", "config": {"provider_id": 1}}
    invoked = AsyncMock()
    monkeypatch.setattr(ai_runtime, "invoke", invoked)

    await wcmd._run_ai(client, event, ["hi"], tpl, account_id=7)

    invoked.assert_awaited_once_with(client, event, ["hi"], tpl, 7)


@pytest.mark.asyncio
async def test_ai_runtime_missing_provider_id_shows_error() -> None:
    client = AsyncMock()
    event = AsyncMock()

    await ai_runtime.invoke(client, event, ["hi"], {"name": "ai", "type": "ai", "config": {}}, 1)

    event.edit.assert_awaited_once()
    assert "provider_id" in event.edit.call_args.args[0]


@pytest.mark.asyncio
async def test_ai_runtime_provider_not_loaded_returns_friendly_error(monkeypatch) -> None:
    from app.worker import runtime as worker_runtime

    monkeypatch.setattr(worker_runtime, "_refresh_command_context", AsyncMock(return_value=None))
    wcmd.set_command_context(CommandContext(account_id=1, templates={}, providers={}))

    client = AsyncMock()
    event = AsyncMock()
    tpl = {"name": "ai", "type": "ai", "config": {"provider_id": 99}}

    await ai_runtime.invoke(client, event, ["q"], tpl, 1)

    event.edit.assert_awaited()
    assert "99" in event.edit.call_args.args[0]


@pytest.mark.asyncio
async def test_ai_runtime_rejects_non_vision_provider_before_download(monkeypatch) -> None:
    from app.worker import runtime as worker_runtime

    monkeypatch.setattr(worker_runtime, "_refresh_command_context", AsyncMock(return_value=None))

    replied = AsyncMock()
    replied.text = ""
    replied.message = ""
    replied.photo = object()
    replied.download_media = AsyncMock(return_value=b"bad")

    wcmd.set_command_context(
        CommandContext(
            account_id=1,
            templates={},
            providers={
                7: {
                    "id": 7,
                    "name": "text-only",
                    "provider": "openai",
                    "api_key_enc": None,
                    "base_url": None,
                    "default_model": "gpt-4o",
                    "modality": "text",
                    "tags": [],
                    "cost_tier": 1,
                    "notes": None,
                    "proxy_url": None,
                    "models": [],
                }
            },
        )
    )

    client = AsyncMock()
    event = AsyncMock()
    event.get_reply_message = AsyncMock(return_value=replied)
    event.message = AsyncMock()
    event.message.photo = None
    tpl = {"name": "ai", "type": "ai", "config": {"provider_id": 7, "routing_mode": "fixed"}}

    await ai_runtime.invoke(client, event, ["问图"], tpl, 1)

    event.edit.assert_awaited()
    assert "识图" in event.edit.call_args.args[0] or "vision" in event.edit.call_args.args[0]
    replied.download_media.assert_not_awaited()
