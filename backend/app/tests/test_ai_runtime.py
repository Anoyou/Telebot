from __future__ import annotations

import base64
from unittest.mock import AsyncMock, Mock

import pytest

from app.services import llm_invoke as service_ai_runtime
from app.services.llm_client import LLMResult
from app.services.llm_dto import LLMProviderDTO
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
async def test_ai_runtime_image_mode_bridges_to_codex_image(monkeypatch) -> None:
    from app.worker import command as worker_command

    wcmd.set_command_context(CommandContext(account_id=1, templates={}, providers={}))
    dispatched = AsyncMock(return_value=True)
    monkeypatch.setattr(worker_command, "dispatch_plugin_command", dispatched)

    client = AsyncMock()
    event = AsyncMock()
    tpl = {
        "name": "image",
        "type": "ai",
        "config": {"mode": "image", "image_backend": "codex_image"},
    }

    await ai_runtime.invoke(client, event, ["画一只猫"], tpl, 1)

    dispatched.assert_awaited_once_with(
        client,
        event,
        ["画一只猫"],
        1,
        plugin_key="codex_image",
        method=None,
    )
    event.edit.assert_not_awaited()


@pytest.mark.asyncio
async def test_ai_runtime_ai_subcommand_image_consumes_mode(monkeypatch) -> None:
    from app.worker import command as worker_command

    wcmd.set_command_context(CommandContext(account_id=1, templates={}, providers={}))
    dispatched = AsyncMock(return_value=True)
    monkeypatch.setattr(worker_command, "dispatch_plugin_command", dispatched)

    client = AsyncMock()
    event = AsyncMock()
    tpl = {"name": "ai", "type": "ai", "config": {"mode": "chat", "provider_id": 1}}

    await ai_runtime.invoke(client, event, ["image", "画一只猫"], tpl, 1)

    dispatched.assert_awaited_once()
    assert dispatched.call_args.args[2] == ["画一只猫"]


@pytest.mark.asyncio
async def test_ai_runtime_video_mode_bridges_to_plugin(monkeypatch) -> None:
    from app.worker import command as worker_command

    wcmd.set_command_context(CommandContext(account_id=1, templates={}, providers={}))
    dispatched = AsyncMock(return_value=True)
    monkeypatch.setattr(worker_command, "dispatch_plugin_command", dispatched)

    client = AsyncMock()
    event = AsyncMock()
    tpl = {
        "name": "video",
        "type": "ai",
        "config": {"mode": "video", "video_plugin_key": "video_bridge"},
    }

    await ai_runtime.invoke(client, event, ["生成 5 秒海浪"], tpl, 1)

    dispatched.assert_awaited_once_with(
        client,
        event,
        ["生成 5 秒海浪"],
        1,
        plugin_key="video_bridge",
        method=None,
    )
    event.edit.assert_not_awaited()


@pytest.mark.asyncio
async def test_ai_runtime_ai_subcommand_video_consumes_mode(monkeypatch) -> None:
    from app.worker import command as worker_command

    wcmd.set_command_context(CommandContext(account_id=1, templates={}, providers={}))
    dispatched = AsyncMock(return_value=True)
    monkeypatch.setattr(worker_command, "dispatch_plugin_command", dispatched)

    client = AsyncMock()
    event = AsyncMock()
    tpl = {"name": "ai", "type": "ai", "config": {"mode": "chat", "provider_id": 1}}

    await ai_runtime.invoke(client, event, ["video", "生成 5 秒海浪"], tpl, 1)

    dispatched.assert_awaited_once()
    assert dispatched.call_args.args[2] == ["生成 5 秒海浪"]


@pytest.mark.asyncio
async def test_ai_runtime_image_llm_uses_native_image_generation(monkeypatch) -> None:
    img_b64 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 128).decode("ascii")
    fake_result = LLMResult(
        text="",
        model="gpt-5.5",
        input_tokens=3,
        output_tokens=0,
        image_data=[f"data:image/png;base64,{img_b64}"],
    )
    invoke_mock = AsyncMock(
        return_value=(
            fake_result,
            LLMProviderDTO(id=1, name="primary", provider="openai", default_model="gpt-5.5"),
            False,
        )
    )
    monkeypatch.setattr(service_ai_runtime, "invoke", invoke_mock)
    monkeypatch.setattr("app.worker.runtime._refresh_command_context", AsyncMock(return_value=None))

    wcmd.set_command_context(
        CommandContext(
            account_id=1,
            templates={},
            providers={
                1: {
                    "id": 1,
                    "name": "primary",
                    "provider": "openai",
                    "default_model": "gpt-5.5",
                    "api_format": "responses",
                    "api_key_enc": None,
                    "modality": "text",
                    "tags": [],
                    "cost_tier": 2,
                    "models": [],
                }
            },
        )
    )

    client = AsyncMock()
    event = AsyncMock()
    event.chat_id = 123
    event.get_reply_message = AsyncMock(return_value=None)
    event.message = AsyncMock()
    event.message.photo = None
    event.message.document = None
    event.message.voice = None
    event.message.audio = None
    tpl = {
        "name": "image",
        "type": "ai",
        "config": {"mode": "image", "image_backend": "llm", "provider_id": 1, "max_tokens": 1024},
    }

    await ai_runtime.invoke(client, event, ["画一只猫"], tpl, 1)

    invoke_mock.assert_awaited_once()
    kwargs = invoke_mock.await_args.kwargs
    assert kwargs["native_image"] is True
    assert kwargs["max_tokens"] == 1024
    system = invoke_mock.await_args.args[2]
    assert "严格规则" not in system
    client.send_file.assert_awaited_once()
    sent_file = client.send_file.await_args.args[1]
    assert sent_file.name == "ai_image.png"
    event.delete.assert_awaited()


def test_ai_runtime_model_display_name_prefers_label() -> None:
    assert (
        ai_runtime._model_display_name(  # noqa: SLF001
            "gpt-5.5",
            {"models": [{"id": "gpt-5.5", "label": "GPT-5.5"}]},
        )
        == "GPT-5.5"
    )


def test_ai_runtime_model_display_name_prettifies_id() -> None:
    assert (
        ai_runtime._model_display_name("claude-3-5-sonnet", {"models": []})  # noqa: SLF001
        == "Claude 3.5 Sonnet"
    )
    assert ai_runtime._model_display_name("gpt-4o", {"models": []}) == "GPT-4o"  # noqa: SLF001


def test_ai_runtime_api_format_context_tracks_effective_protocol() -> None:
    provider = {
        "id": 1,
        "name": "OpenAI",
        "provider": "openai",
        "default_model": "gpt-5.5",
        "api_format": "chat_completions",
        "web_search_api_format": "auto",
    }

    normal = ai_runtime._api_format_render_context(provider, web_search=False)  # noqa: SLF001
    assert normal["api_format"] == "chat_completions"
    assert normal["api_protocol"] == "chat_completions"
    assert normal["configured_api_format"] == "chat_completions"
    assert normal["web_search_api_format"] == "auto"
    assert normal["endpoint"] == "/chat/completions"
    assert normal["web_search"] == ""

    search = ai_runtime._api_format_render_context(provider, web_search=True)  # noqa: SLF001
    assert search["api_format"] == "responses"
    assert search["api_protocol"] == "responses"
    assert search["configured_api_format"] == "chat_completions"
    assert search["web_search_api_format"] == "auto"
    assert search["endpoint"] == "/responses"
    assert search["web_search"] == "true"


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


@pytest.mark.asyncio
async def test_service_ai_runtime_delegates_shared_fallback_logic(monkeypatch) -> None:
    primary = LLMProviderDTO(id=1, name="primary", provider="openai", default_model="gpt-4o")
    fallback = LLMProviderDTO(id=2, name="fallback", provider="openai", default_model="gpt-4o-mini")
    providers = {1: primary, 2: fallback}
    chain = object()
    result = LLMResult(text="ok", model="gpt-4o", input_tokens=1, output_tokens=2)

    build_chain = Mock(return_value=chain)
    call = AsyncMock(return_value=(result, fallback, True))
    monkeypatch.setattr(service_ai_runtime, "build_fallback_chain", build_chain)
    monkeypatch.setattr(service_ai_runtime, "call_with_fallback", call)

    got = await service_ai_runtime.invoke(
        primary,
        providers,
        "sys",
        "user",
        override_model="custom",
        max_tokens=99,
        images=[b"img"],
        account_id=7,
        source="scheduler",
        fallback_provider_id=2,
        matched_tag="scheduler",
    )

    build_chain.assert_called_once_with(
        primary,
        providers=providers,
        fallback_provider_id=2,
        matched_tag="scheduler",
    )
    call.assert_awaited_once()
    kwargs = call.await_args.kwargs
    assert kwargs["source"] == "scheduler"
    assert kwargs["account_id"] == 7
    assert kwargs["override_model"] == "custom"
    assert kwargs["max_tokens"] == 99
    assert kwargs["images"] == [b"img"]
    assert got == (result, fallback, True)


@pytest.mark.asyncio
async def test_worker_ai_runtime_uses_shared_service_invoke(monkeypatch) -> None:
    fake_result = LLMResult(text="answer", model="gpt-4o", input_tokens=3, output_tokens=4)
    invoke_mock = AsyncMock(return_value=(fake_result, LLMProviderDTO(id=1, name="primary", provider="openai", default_model="gpt-4o"), False))
    monkeypatch.setattr(service_ai_runtime, "invoke", invoke_mock)
    monkeypatch.setattr("app.worker.runtime._refresh_command_context", AsyncMock(return_value=None))

    wcmd.set_command_context(
        CommandContext(
            account_id=1,
            templates={},
            providers={
                1: {
                    "id": 1,
                    "name": "primary",
                    "provider": "openai",
                    "default_model": "gpt-4o",
                    "api_key_enc": None,
                    "modality": "text",
                    "tags": [],
                    "cost_tier": 2,
                }
            },
        )
    )

    client = AsyncMock()
    event = AsyncMock()
    event.get_reply_message = AsyncMock(return_value=None)
    event.message = AsyncMock()
    event.message.photo = None
    event.message.document = None
    event.message.voice = None
    event.message.audio = None
    tpl = {"name": "ai", "type": "ai", "config": {"provider_id": 1}}

    await ai_runtime.invoke(client, event, ["hello"], tpl, 1)

    invoke_mock.assert_awaited_once()
    provider_dto, provider_map, system, user_msg = invoke_mock.await_args.args[:4]
    assert provider_dto.id == 1
    assert provider_map[1].name == "primary"
    assert user_msg == "hello"
    assert "严格规则" in system
    event.edit.assert_awaited()
