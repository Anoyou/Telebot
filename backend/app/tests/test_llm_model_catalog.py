"""模型元数据解析（Magpie / OpenRouter / LiteLLM）、合并策略、路由窗口兜底与 Magpie 识别。"""

from __future__ import annotations

from typing import Any

import pytest

from app.schemas.command import ProviderModel
from app.services import llm_router
from app.services.llm_model_catalog import (
    ModelMetadata,
    is_chat_model,
    merge_model_entry,
    parse_model_entry,
    parse_models_payload,
)
from app.services.llm_quick_verify import suggested_name, suggested_provider

MAGPIE_ENTRY = {
    "id": "openai/gpt-5.5",
    "object": "model",
    "owned_by": "openai",
    "display_name": "GPT-5.5",
    "reasoning": True,
    "supported_reasoning_levels": [{"effort": "low"}, {"effort": "high"}, {"effort": "xhigh"}],
    "context_window": 400000,
    "context_length": 400000,
    "max_input_tokens": 400000,
    "max_output_tokens": 128000,
    "native_endpoints": ["/v1/responses", "/v1/chat/completions"],
    "modalities": {"input": ["text", "image"]},
}


def test_parse_magpie_entry() -> None:
    meta = parse_model_entry(MAGPIE_ENTRY)
    assert meta.label == "GPT-5.5"
    assert meta.owned_by == "openai"
    assert meta.context_window == 400000
    assert meta.max_output_tokens == 128000
    assert meta.input_modalities == ["text", "image"]
    assert meta.supported_reasoning_levels == ["low", "high", "xhigh"]
    assert meta.supported_api_formats == ["responses", "chat_completions"]
    fields = meta.to_fields()
    assert fields["supports_images"] is True
    assert fields["reasoning_efforts"] == ["low", "high", "xhigh"]
    # 解析结果必须能直接喂给 ProviderModel。
    ProviderModel(id="openai/gpt-5.5", **fields)


def test_parse_openrouter_and_litellm_entries() -> None:
    openrouter = parse_model_entry(
        {
            "id": "anthropic/claude-sonnet-4.5",
            "name": "Anthropic: Claude Sonnet 4.5",
            "context_length": 200000,
            "architecture": {"modality": "text+image->text", "input_modalities": ["text", "image"]},
            "top_provider": {"max_completion_tokens": 64000},
        }
    )
    assert openrouter.label == "Anthropic: Claude Sonnet 4.5"
    assert openrouter.context_window == 200000
    assert openrouter.max_output_tokens == 64000
    assert openrouter.input_modalities == ["text", "image"]

    litellm = parse_model_entry(
        {"id": "gpt-4o-mini", "max_input_tokens": 128000, "max_output_tokens": 16384, "owned_by": "openai"}
    )
    assert litellm.context_window == 128000
    assert litellm.max_output_tokens == 16384
    assert litellm.label is None


def test_parse_label_dropped_when_same_as_id_and_bad_values_ignored() -> None:
    meta = parse_model_entry(
        {
            "id": "vendor/model-x",
            "display_name": "model-x",
            "context_window": "not-a-number",
            "max_output_tokens": -5,
            "supported_reasoning_levels": ["none", "min", "bogus", "MAX"],
            "default_reasoning_level": "medium",
        }
    )
    assert meta.label is None
    assert meta.context_window is None
    assert meta.max_output_tokens is None
    assert meta.supported_reasoning_levels == ["minimal", "max"]
    # 默认档位不在支持列表里时丢弃，避免写出矛盾配置。
    assert meta.default_reasoning_level is None
    assert parse_model_entry("gpt").is_empty()


def test_parse_payload_skips_image_models_and_duplicates() -> None:
    payload = {
        "object": "list",
        "data": [
            MAGPIE_ENTRY,
            {"id": "openai/gpt-image-1", "kind": "image", "object": "model"},
            {"id": "openai/gpt-5.5", "object": "model"},
            "plain-string",
            {"id": "   "},
        ],
    }
    catalog = parse_models_payload(payload)
    assert list(catalog) == ["openai/gpt-5.5"]
    assert not is_chat_model({"id": "x", "kind": "image"})
    assert parse_models_payload({"models": [{"id": "a"}, {"id": "b"}]}).keys() == {"a", "b"}
    assert parse_models_payload({"nope": 1}) == {}


def test_merge_keeps_user_fields_and_fills_blanks() -> None:
    existing = {
        "id": "openai/gpt-5.5",
        "enabled": True,
        "custom": True,
        "label": "我的 GPT",
        "context_window": 123,
        "supports_tools": False,
    }
    merged = merge_model_entry(existing, parse_model_entry(MAGPIE_ENTRY))
    assert merged["enabled"] is True
    assert merged["custom"] is True
    assert merged["label"] == "我的 GPT"
    assert merged["context_window"] == 123
    assert merged["supports_tools"] is False
    assert merged["max_output_tokens"] == 128000
    assert merged["input_modalities"] == ["text", "image"]
    assert merged["metadata_source"] == "upstream"

    fresh = merge_model_entry(
        {"id": "openai/gpt-5.5", "enabled": False, "custom": False}, parse_model_entry(MAGPIE_ENTRY)
    )
    assert fresh["label"] == "GPT-5.5"
    assert fresh["enabled"] is False
    assert merge_model_entry(None, ModelMetadata())["metadata_source"] == "upstream"


def _provider(
    pid: int, *, cost_tier: int, context_window: int | None, tags: list[str] | None = None
) -> dict[str, Any]:
    model: dict[str, Any] = {"id": "m", "enabled": True}
    if context_window is not None:
        model["context_window"] = context_window
    return {
        "id": pid,
        "name": f"p{pid}",
        "provider": "openai",
        "api_key_enc": "fernet-token-fake",
        "base_url": None,
        "default_model": "m",
        "modality": "text",
        "tags": list(tags or []),
        "cost_tier": cost_tier,
        "models": [model],
    }


@pytest.mark.asyncio
async def test_router_long_context_falls_back_to_context_window_metadata() -> None:
    long_text = "测试" * 1000
    pool = {
        1: _provider(1, cost_tier=1, context_window=2048, tags=["chat"]),
        2: _provider(2, cost_tier=3, context_window=200000, tags=["chat"]),
        3: _provider(3, cost_tier=2, context_window=128000, tags=["chat"]),
    }
    decision = await llm_router.pick_provider("总结一下", long_text, False, providers=pool)
    # 窗口放得下的里面选最便宜：3（cost 2）而不是 2（cost 3）；1 窗口不够。
    assert decision.provider_id == 3
    assert decision.matched_tag == "long_context"
    assert "context_window" in decision.reason


@pytest.mark.asyncio
async def test_router_long_context_tag_still_wins_over_metadata() -> None:
    long_text = "测试" * 1000
    pool = {
        1: _provider(1, cost_tier=1, context_window=200000, tags=["chat"]),
        2: _provider(2, cost_tier=3, context_window=None, tags=["long_context"]),
    }
    decision = await llm_router.pick_provider("总结一下", long_text, False, providers=pool)
    assert decision.provider_id == 2
    assert decision.reason == "matched tag=long_context"


@pytest.mark.asyncio
async def test_router_without_metadata_keeps_legacy_path() -> None:
    long_text = "测试" * 1000
    pool = {1: _provider(1, cost_tier=1, context_window=None, tags=["chat"])}
    decision = await llm_router.pick_provider("总结一下", long_text, False, providers=pool)
    assert decision.provider_id == 1
    assert decision.matched_tag == "chat"


def test_magpie_detection_in_quick_verify() -> None:
    assert suggested_name("http://127.0.0.1:3425/v1") == "Magpie 网关"
    assert suggested_name("https://magpie.internal.example/v1") == "Magpie 网关"
    assert suggested_name("https://api.example.com/v1") == "api.example.com"
    # 本机 3425 端口即便没填 Key 也不该被当成 Ollama。
    assert suggested_provider("chat_completions", "http://127.0.0.1:3425/v1", "") == "openai"
    assert suggested_provider("chat_completions", "http://127.0.0.1:11434/v1", "") == "ollama"
    assert suggested_provider("anthropic_messages", "http://127.0.0.1:3425", "k") == "anthropic"
