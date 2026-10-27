"""推理模型的思考过程绝不当作最终答案返回给插件/用户。

复现：GLM-5.3-Flash（Z.ai，OpenAI 兼容 chat_completions，始终开启推理）在插件
max_tokens=800 时输出额度被推理耗尽，content 为空、finish_reason=length；协议层
曾用 reasoning_content 兜底正文，插件把截断的英文推理草稿当答案发进群里。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.services import account_bot_service
from app.services import llm_runtime as _rt
from app.services.llm_client import (
    LLMCallFailed,
    LLMError,
    LLMResult,
    LLMStreamChunk,
    OpenAIClient,
    split_think_tags,
    thinking_param_style,
)
from app.services.llm_dto import LLMProviderDTO
from app.services.llm_protocol import StopReason
from app.services.llm_runtime import FallbackChain
from app.worker.plugins import ai_facade
from app.worker.plugins.ai_facade import AIResponseTruncated, AIUnavailableError, PluginAI

DRAFT = 'The user is asking again whether "空枪" ... Let me organize the data into a table as suggested: • Columns: ...'


def _chat_payload(*, content: Any, reasoning: str | None, finish: str = "stop", reasoning_tokens: int = 0) -> dict:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    return {
        "model": "glm-5.3-flash",
        "choices": [{"finish_reason": finish, "message": message}],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 800,
            "completion_tokens_details": {"reasoning_tokens": reasoning_tokens},
        },
    }


class _Resp:
    status_code = 200
    text = ""
    headers: dict[str, str] = {}

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _HTTP:
    def __init__(self, payload: dict, sink: dict | None = None) -> None:
        self._payload = payload
        self._sink = sink if sink is not None else {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, headers=None, json=None, **kwargs):
        self._sink["body"] = json
        return _Resp(self._payload)


async def _complete(payload: dict, *, base_url: str = "https://api.z.ai/api/paas/v4", model: str = "glm-5.3-flash", **kwargs):
    sink: dict = {}
    with patch("app.services.llm_client.httpx.AsyncClient", return_value=_HTTP(payload, sink)):
        result = await OpenAIClient("sk", base_url, model).complete("sys", "空枪有资源吗", **kwargs)
    return result, sink.get("body") or {}


@pytest.mark.asyncio
async def test_reasoning_content_never_enters_final_text() -> None:
    result, _ = await _complete(_chat_payload(content="| 标题 | 大小 |\n|---|---|", reasoning=DRAFT, reasoning_tokens=300))

    assert result.text == "| 标题 | 大小 |\n|---|---|"
    assert DRAFT not in result.text
    assert result.reasoning == DRAFT
    assert result.reasoning_tokens == 300
    assert result.text_from_reasoning is False


@pytest.mark.asyncio
async def test_empty_content_with_reasoning_is_flagged_not_final() -> None:
    result, _ = await _complete(_chat_payload(content="", reasoning=DRAFT, finish="length", reasoning_tokens=800))

    # 测活兼容：text 仍可用推理兜底，但必须被明确标记
    assert result.text_from_reasoning is True
    assert result.stop_reason is StopReason.MAX_TOKENS
    assert result.reasoning == DRAFT


@pytest.mark.asyncio
async def test_think_tags_in_content_are_split_out() -> None:
    result, _ = await _complete(_chat_payload(content="<think>Let me check the list...</think>\n站里有 3 个结果。", reasoning=None))

    assert result.text == "站里有 3 个结果。"
    assert "Let me" in (result.reasoning or "")
    assert result.text_from_reasoning is False


def test_split_think_tags_unclosed_drops_tail() -> None:
    visible, reasoning = split_think_tags("<think>The user is asking whether")
    assert visible == ""
    assert reasoning.startswith("The user is asking")
    assert split_think_tags("普通答案") == ("普通答案", "")


@pytest.mark.asyncio
async def test_thinking_param_only_for_supported_hosts() -> None:
    _, zai_body = await _complete(_chat_payload(content="好", reasoning=None), thinking="disabled")
    assert zai_body["thinking"] == {"type": "disabled"}

    _, openai_body = await _complete(
        _chat_payload(content="好", reasoning=None),
        base_url="https://api.openai.com/v1",
        model="gpt-4.1",
        thinking="disabled",
    )
    assert "thinking" not in openai_body
    assert thinking_param_style("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen3-max") == "enable_thinking"
    assert thinking_param_style("https://api.deepseek.com", "deepseek-chat") == "thinking"
    assert thinking_param_style("https://api.openai.com/v1", "gpt-4.1") is None


def _primary() -> LLMProviderDTO:
    return LLMProviderDTO(id=1, name="zai", provider="openai", default_model="glm-5.3-flash")


@pytest.mark.asyncio
async def test_runtime_truncated_reasoning_raises_clear_error(monkeypatch, caplog) -> None:
    async def fake_call(provider_dto, *args, **kwargs):
        return LLMResult(
            text=DRAFT,
            model="glm-5.3-flash",
            input_tokens=100,
            output_tokens=800,
            stop_reason=StopReason.MAX_TOKENS,
            provider_status="length",
            reasoning=DRAFT,
            reasoning_tokens=800,
            text_from_reasoning=True,
        )

    monkeypatch.setattr(_rt, "_call_with_retry", fake_call)
    monkeypatch.setattr(_rt, "_emit_usage", AsyncMock())
    caplog.set_level("INFO", logger="app.services.llm_runtime")

    with pytest.raises(LLMCallFailed) as info:
        await _rt.call_with_fallback(FallbackChain(primary=_primary(), fallbacks=[]), "sys", "user", max_tokens=800)

    assert info.value.error_type == _rt.AI_RESPONSE_TRUNCATED_CATEGORY
    assert "finish_reason=length" in str(info.value)
    assert DRAFT not in str(info.value)
    assert any("没有给出最终答案" in record.getMessage() and "reasoning_tokens=800" in record.getMessage() for record in caplog.records)


@pytest.mark.asyncio
async def test_runtime_reasoning_only_without_truncation_is_rejected(monkeypatch) -> None:
    async def fake_call(provider_dto, *args, **kwargs):
        return LLMResult(text=DRAFT, model="m", input_tokens=1, output_tokens=1, stop_reason=StopReason.COMPLETED, reasoning=DRAFT, text_from_reasoning=True)

    monkeypatch.setattr(_rt, "_call_with_retry", fake_call)
    monkeypatch.setattr(_rt, "_emit_usage", AsyncMock())

    with pytest.raises(LLMCallFailed) as info:
        await _rt.call_with_fallback(FallbackChain(primary=_primary(), fallbacks=[]), "sys", "user")
    assert info.value.error_type == _rt.AI_NO_FINAL_ANSWER_CATEGORY


@pytest.mark.asyncio
async def test_runtime_passes_thinking_to_client(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_call(provider_dto, *args, **kwargs):
        captured.update(kwargs)
        return LLMResult(text="好", model="m", input_tokens=1, output_tokens=1)

    monkeypatch.setattr(_rt, "_call_with_retry", fake_call)
    monkeypatch.setattr(_rt, "_emit_usage", AsyncMock())
    await _rt.call_with_fallback(FallbackChain(primary=_primary(), fallbacks=[]), "sys", "user", thinking="disabled")
    assert captured["thinking"] == "disabled"


def _facade_provider(model: str = "glm-5.3-flash") -> LLMProviderDTO:
    return LLMProviderDTO(
        id=1,
        name="zai",
        provider="openai",
        api_format="chat_completions",
        base_url="https://api.z.ai/api/paas/v4",
        default_model=model,
        api_key_enc="enc",
        modality="text",
        tags=["chat"],
        models=[{"id": model, "label": model, "enabled": True}],
    )


def _facade(monkeypatch, invoke, model: str = "glm-5.3-flash") -> PluginAI:
    async def _enabled(*_a, **_k):
        return True

    async def _loader():
        return {1: _facade_provider(model)}

    async def _acquire(*_a, **_k):
        return object()

    monkeypatch.setattr(ai_facade, "is_ai_enabled", _enabled)
    monkeypatch.setattr(ai_facade, "invoke_ai_runtime", invoke)
    monkeypatch.setattr(ai_facade.plugin_ai_quota, "acquire", _acquire)
    monkeypatch.setattr(ai_facade.plugin_ai_quota, "release", AsyncMock())
    return PluginAI(account_id=7, plugin_key="demo", provider_loader=_loader)


@pytest.mark.asyncio
async def test_facade_returns_only_final_content_with_separate_reasoning(monkeypatch) -> None:
    async def _invoke(primary, providers, system, user, **kwargs):
        return (
            LLMResult(text="最终中文答案", model="glm-5.3-flash", input_tokens=1, output_tokens=2, reasoning=DRAFT, reasoning_tokens=50, provider_status="stop"),
            primary,
            False,
        )

    result = await _facade(monkeypatch, _invoke).complete("sys", "问")
    assert result.text == "最终中文答案"
    assert result.reasoning == DRAFT
    assert result.reasoning_tokens == 50
    assert result.finish_reason == "stop"


@pytest.mark.asyncio
async def test_facade_maps_truncation_to_ai_response_truncated(monkeypatch) -> None:
    async def _invoke(primary, providers, system, user, **kwargs):
        raise LLMCallFailed("所有 provider 都失败。", provider_id=1, error_type=_rt.AI_RESPONSE_TRUNCATED_CATEGORY)

    with pytest.raises(AIResponseTruncated) as info:
        await _facade(monkeypatch, _invoke).complete("sys", "问")
    assert isinstance(info.value, AIUnavailableError)


@pytest.mark.asyncio
async def test_facade_rejects_reasoning_fallback_text_defensively(monkeypatch) -> None:
    async def _invoke(primary, providers, system, user, **kwargs):
        return LLMResult(text=DRAFT, model="m", input_tokens=1, output_tokens=1, text_from_reasoning=True), primary, False

    with pytest.raises(AIResponseTruncated):
        await _facade(monkeypatch, _invoke).complete("sys", "问")


@pytest.mark.asyncio
async def test_facade_raises_max_tokens_floor_for_reasoning_models(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    async def _invoke(primary, providers, system, user, **kwargs):
        calls.append(kwargs)
        return LLMResult(text="好", model="m", input_tokens=1, output_tokens=1), primary, False

    facade = _facade(monkeypatch, _invoke)
    await facade.complete("sys", "问", max_tokens=800)
    await facade.complete("sys", "问", max_tokens=800, thinking="disabled")

    assert calls[0]["max_tokens"] == 4096
    assert calls[0]["thinking"] is None
    assert calls[1]["max_tokens"] == 800
    assert calls[1]["thinking"] == "disabled"


@pytest.mark.asyncio
async def test_facade_keeps_max_tokens_for_regular_models(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    async def _invoke(primary, providers, system, user, **kwargs):
        calls.append(kwargs)
        return LLMResult(text="好", model="m", input_tokens=1, output_tokens=1), primary, False

    await _facade(monkeypatch, _invoke, model="gpt-4.1-mini").complete("sys", "问", max_tokens=800)
    assert calls[0]["max_tokens"] == 800


def test_is_reasoning_model_heuristics() -> None:
    assert ai_facade.is_reasoning_model(_facade_provider("glm-5.3-flash"), "glm-5.3-flash")
    assert ai_facade.is_reasoning_model(_facade_provider("deepseek-reasoner"), "deepseek-reasoner")
    assert ai_facade.is_reasoning_model(_facade_provider("o3-mini"), "o3-mini")
    assert not ai_facade.is_reasoning_model(_facade_provider("gpt-4.1-mini"), "gpt-4.1-mini")
    assert not ai_facade.is_reasoning_model(_facade_provider("glm-4-flash"), "glm-4-flash")


def test_stream_reasoning_chunks_are_marked() -> None:
    chunk = LLMStreamChunk(delta="思考", is_reasoning=True)
    assert chunk.is_reasoning is True
    assert LLMStreamChunk(delta="答").is_reasoning is False


@pytest.mark.asyncio
async def test_edit_inline_rich_message_payload(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    async def _call(token, method, payload, **kwargs):
        captured.update(method=method, payload=payload)
        return True

    monkeypatch.setattr(account_bot_service, "call_bot_api", _call)
    await account_bot_service.edit_inline_rich_message("tok", "im-9", {"markdown": "| a | b |\n|---|---|\n| 1 | 2 |"})

    assert captured["method"] == "editMessageText"
    assert captured["payload"]["inline_message_id"] == "im-9"
    assert "chat_id" not in captured["payload"]
    assert "text" not in captured["payload"]
    # 0.106.0-beta.15 起管道表格在发送出口统一转为带边框 + 隔行着色的表格
    assert captured["payload"]["rich_message"]["markdown"].startswith("<table bordered striped>\n<tr><th>a</th>")


def test_llm_error_is_importable() -> None:
    # 保持导出稳定：插件按类名识别 AIResponseTruncated
    assert AIResponseTruncated.__name__ == "AIResponseTruncated"
    assert issubclass(LLMError, Exception)
