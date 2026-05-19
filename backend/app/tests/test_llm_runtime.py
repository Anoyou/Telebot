"""LLM Runtime 单元测试（Fallback、Retry、Usage Record）。

覆盖：
- Fallback chain 构造
- Retry 策略（可重试/不可重试错误）
- Usage record 记录
- 长消息分段
- 隐私日志脱敏
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.llm_client import LLMCallFailed, LLMError
from app.services.llm_dto import LLMProviderDTO
from app.services.llm_invoke import _api_format_for_call
from app.services.llm_runtime import (
    FallbackChain,
    UsageRecord,
    build_fallback_chain,
)
from app.worker.command import (
    _ensure_html_safe,
    _safe_exception_text,
    _safe_log_text,
    _split_long_message,
)

# ════════════════════════════════════════════════════════════
# 1) LLMProviderDTO 测试
# ════════════════════════════════════════════════════════════


def test_dto_from_dict() -> None:
    """从 dict 构造 LLMProviderDTO。"""
    d = {
        "id": 42,
        "name": "test-provider",
        "provider": "openai",
        "api_format": "chat_completions",
        "base_url": "https://api.example.com/v1",
        "default_model": "gpt-4o",
        "api_key_enc": "encrypted-key",
        "proxy_url": "socks5://127.0.0.1:1080",
        "modality": "text",
        "tags": ["chat", "code"],
        "cost_tier": 2,
    }
    dto = LLMProviderDTO.from_dict(d)

    assert dto.id == 42
    assert dto.name == "test-provider"
    assert dto.provider == "openai"
    assert dto.api_format == "chat_completions"
    assert dto.base_url == "https://api.example.com/v1"
    assert dto.default_model == "gpt-4o"
    assert dto.api_key_enc == "encrypted-key"
    assert dto.proxy_url == "socks5://127.0.0.1:1080"
    assert dto.modality == "text"
    assert dto.tags == ["chat", "code"]
    assert dto.cost_tier == 2


def test_native_image_openai_chat_provider_uses_responses_for_text_model() -> None:
    """原生生图绑定普通 OpenAI 主模型时，应走 Responses image_generation 工具。"""
    dto = LLMProviderDTO(
        id=1,
        name="ChatGPT",
        provider="openai",
        default_model="gpt-5.5",
        api_format="chat_completions",
    )

    assert _api_format_for_call(dto, web_search=False, native_image=True) == "responses"


def test_native_image_openai_chat_provider_keeps_images_api_for_image_model() -> None:
    """gpt-image / dall-e 模型仍走 /images/generations。"""
    dto = LLMProviderDTO(
        id=1,
        name="Images",
        provider="openai",
        default_model="gpt-image-2",
        api_format="chat_completions",
    )

    assert _api_format_for_call(dto, web_search=False, native_image=True) is None


def test_dto_from_dict_missing_fields() -> None:
    """dict 缺少字段时有默认值。"""
    d = {"id": 1, "name": "test", "provider": "openai"}
    dto = LLMProviderDTO.from_dict(d)

    assert dto.id == 1
    assert dto.name == "test"
    assert dto.api_format is None
    assert dto.base_url is None
    assert dto.default_model == ""
    assert dto.modality == "text"
    assert dto.tags == []
    assert dto.cost_tier == 2


def test_dto_has_api_key() -> None:
    """has_api_key 属性正确判断。"""
    # ollama 不需要 key
    dto_ollama = LLMProviderDTO(id=1, name="ollama", provider="ollama", api_key_enc=None)
    assert dto_ollama.has_api_key is True
    assert dto_ollama.is_ollama is True

    # 有 key
    dto_with_key = LLMProviderDTO(id=2, name="openai", provider="openai", api_key_enc="sk-xxx")
    assert dto_with_key.has_api_key is True

    # 没 key
    dto_no_key = LLMProviderDTO(id=3, name="openai", provider="openai", api_key_enc=None)
    assert dto_no_key.has_api_key is False


# ════════════════════════════════════════════════════════════
# 2) FallbackChain 测试
# ════════════════════════════════════════════════════════════


def test_fallback_chain_primary_first() -> None:
    """FallbackChain.all_providers 顺序正确：primary 在前。"""
    p1 = LLMProviderDTO(id=1, name="primary", provider="openai")
    p2 = LLMProviderDTO(id=2, name="fallback1", provider="openai")
    p3 = LLMProviderDTO(id=3, name="fallback2", provider="openai")

    chain = FallbackChain(primary=p1, fallbacks=[p2, p3])
    providers = chain.all_providers

    assert providers[0].name == "primary"
    assert providers[1].name == "fallback1"
    assert providers[2].name == "fallback2"


def test_build_fallback_chain() -> None:
    """build_fallback_chain 正确构造 chain。"""
    providers = {
        1: LLMProviderDTO(id=1, name="primary", provider="openai", tags=["chat"], cost_tier=2),
        2: LLMProviderDTO(id=2, name="fallback", provider="openai", tags=["chat"], cost_tier=1, api_key_enc="key"),
        3: LLMProviderDTO(id=3, name="same-tag-cheaper", provider="openai", tags=["chat"], cost_tier=1, api_key_enc="key"),
    }
    primary = providers[1]

    chain = build_fallback_chain(
        primary=primary,
        providers=providers,
        fallback_provider_id=2,
        matched_tag="chat",
    )

    assert chain.primary.id == 1
    # fallback_provider_id 应该被加入
    assert len(chain.fallbacks) >= 1


def test_build_fallback_chain_skips_same_id() -> None:
    """build_fallback_chain 跳过与 primary 相同 id 的 provider。"""
    providers = {
        1: LLMProviderDTO(id=1, name="primary", provider="openai"),
    }
    primary = providers[1]

    chain = build_fallback_chain(primary=primary, providers=providers)
    # 应该没有 fallback（因为没有其他 provider）
    assert len(chain.fallbacks) == 0


# ════════════════════════════════════════════════════════════
# 3) Retry 策略测试
# ════════════════════════════════════════════════════════════


def test_llm_error_retryable_flag() -> None:
    """LLMError.retryable 标志正确设置。"""
    # 网络错误
    err_network = LLMError("连接超时", retryable=True)
    assert err_network.retryable is True

    # 认证错误不可重试
    err_auth = LLMError("401 Unauthorized", retryable=False)
    assert err_auth.retryable is False


def test_llm_call_failed_error_type() -> None:
    """LLMCallFailed 正确记录错误类型。"""
    err = LLMCallFailed(
        "429 Rate Limited",
        provider_id=1,
        provider_name="test",
        error_type="rate_limit",
        status_code=429,
        retryable=True,
    )
    assert err.error_type == "rate_limit"
    assert err.status_code == 429
    assert err.retryable is True


# ════════════════════════════════════════════════════════════
# 4) Usage Record 测试
# ════════════════════════════════════════════════════════════


def test_usage_record_fields() -> None:
    """UsageRecord 包含所有必要字段。"""
    record = UsageRecord(
        provider_id=42,
        provider_name="test-provider",
        model="gpt-4o",
        input_tokens=100,
        output_tokens=50,
        latency_ms=1500,
        success=True,
        used_fallback=False,
        fallback_chain=["test-provider"],
    )
    assert record.provider_id == 42
    assert record.provider_name == "test-provider"
    assert record.model == "gpt-4o"
    assert record.input_tokens == 100
    assert record.output_tokens == 50
    assert record.success is True
    assert record.used_fallback is False


# ════════════════════════════════════════════════════════════
# 5) 隐私日志脱敏测试
# ════════════════════════════════════════════════════════════


def test_safe_exception_text_strips_sk_key() -> None:
    """_safe_exception_text 正确脱敏 sk- token。"""
    e = RuntimeError("auth failed: token sk-veryverysecret-XYZ rejected")
    msg = _safe_exception_text(e)
    assert "sk-veryverysecret-XYZ" not in msg
    assert "<redacted>" in msg


def test_safe_exception_text_strips_bearer_token() -> None:
    """_safe_exception_text 正确脱敏 Bearer token。"""
    e = RuntimeError("auth failed: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9")
    msg = _safe_exception_text(e)
    assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in msg


def test_safe_log_text_does_not_log_full_content() -> None:
    """_safe_log_text 不记录完整原文。"""
    from app.worker.command import _safe_log_text

    long_text = "A" * 500
    msg = _safe_log_text(long_text, max_len=100)
    # 应该显示长度，而不是完整内容
    assert "<len=500>" in msg
    # 不应该包含完整的 500 个 A
    assert msg.count("A") < 200  # 预览最多 100 字符


def test_safe_log_text_masks_tokens() -> None:
    """_safe_log_text 脱敏 token。"""
    text = "sk-my-secret-key-12345"
    msg = _safe_log_text(text)
    assert "sk-my-secret" not in msg


# ════════════════════════════════════════════════════════════
# 6) 长消息分段测试
# ════════════════════════════════════════════════════════════


def test_split_long_message_short_text() -> None:
    """短文本不分割。"""
    text = "Hello, world!"
    parts = _split_long_message(text, threshold=4000)
    assert len(parts) == 1
    assert parts[0] == text


def test_split_long_message_by_paragraphs() -> None:
    """按段落分割长文本。"""
    para = "A" * 2000
    text = f"{para}\n\n{para}\n\n{para}"
    parts = _split_long_message(text, threshold=3000)
    # 应该至少有 2 段
    assert len(parts) >= 2
    for part in parts:
        assert len(part) <= 3000


def test_split_long_message_single_long_paragraph() -> None:
    """单个超长段落按句子分割。"""
    sentence = "这是测试句子。" * 500
    parts = _split_long_message(sentence, threshold=2000)
    assert len(parts) >= 2
    for part in parts:
        assert len(part) <= 2000


def test_split_long_message_empty() -> None:
    """空文本处理。"""
    parts = _split_long_message("", threshold=100)
    assert parts == [""]


def test_ensure_html_safe_closes_tags() -> None:
    """_ensure_html_safe 补全未闭合的 HTML 标签。"""
    text = "<b>未闭合的粗体</b>\n\n<i>未闭合的斜体"
    safe = _ensure_html_safe(text)
    # 应该补全 </i>
    assert "</i>" in safe
    # 已闭合的不应重复
    assert safe.count("</b>") == 1


def test_ensure_html_safe_preserves_valid() -> None:
    """有效的 HTML 保持不变。"""
    text = "<b>粗体</b>\n<i>斜体</i>\n<code>代码</code>"
    safe = _ensure_html_safe(text)
    assert safe == text


# ════════════════════════════════════════════════════════════
# 7) call_with_fallback 集成测试
# ════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_call_with_fallback_success_on_primary(monkeypatch) -> None:
    """主 provider 成功时直接返回。"""
    from app.services import llm_runtime as _rt

    primary = LLMProviderDTO(
        id=1,
        name="primary",
        provider="openai",
        default_model="gpt-4o",
        api_key_enc="sk-test",
    )

    class _FakeClient:
        async def complete(self, system, user, max_tokens=512, images=None):
            from app.services.llm_client import LLMResult
            return LLMResult(text="ok", model="gpt-4o", input_tokens=1, output_tokens=1)

    async def fake_call(*a, **k):
        return _FakeClient()

    chain = FallbackChain(primary=primary)

    with patch("app.services.llm_runtime.build_client_from_dto", fake_call):
        result, provider, used_fb = await _rt.call_with_fallback(
            chain, "sys", "user", max_tokens=100
        )

    assert result.text == "ok"
    assert provider.id == 1
    assert used_fb is False


@pytest.mark.asyncio
async def test_call_with_fallback_falls_back_on_failure(monkeypatch) -> None:
    """主 provider 失败后尝试 fallback。"""
    from app.services import llm_runtime as _rt

    primary = LLMProviderDTO(
        id=1, name="primary", provider="openai", default_model="gpt-4o", api_key_enc="sk-test"
    )
    fallback = LLMProviderDTO(
        id=2, name="fallback", provider="openai", default_model="gpt-4o", api_key_enc="sk-test"
    )

    attempt_order = []

    class _PrimaryClient:
        async def complete(self, system, user, max_tokens=512, images=None):
            attempt_order.append("primary")
            from app.services.llm_client import LLMError
            raise LLMError("primary failed", retryable=True)

    class _FallbackClient:
        async def complete(self, system, user, max_tokens=512, images=None):
            attempt_order.append("fallback")
            from app.services.llm_client import LLMResult
            return LLMResult(text="fallback-ok", model="gpt-4o", input_tokens=1, output_tokens=1)

    async def fake_build(dto, **k):
        if dto.id == 1:
            return _PrimaryClient()
        return _FallbackClient()

    chain = FallbackChain(primary=primary, fallbacks=[fallback])

    with patch("app.services.llm_runtime.build_client_from_dto", fake_build):
        result, provider, used_fb = await _rt.call_with_fallback(
            chain, "sys", "user", max_tokens=100
        )

    assert result.text == "fallback-ok"
    assert provider.id == 2
    assert used_fb is True
    assert "primary" in attempt_order
    assert "fallback" in attempt_order


@pytest.mark.asyncio
async def test_call_with_fallback_all_fail_raises(monkeypatch) -> None:
    """所有 provider 都失败时抛出 LLMCallFailed。"""
    from app.services import llm_runtime as _rt

    primary = LLMProviderDTO(
        id=1, name="primary", provider="openai", default_model="gpt-4o", api_key_enc="sk-test"
    )
    fallback = LLMProviderDTO(
        id=2, name="fallback", provider="openai", default_model="gpt-4o", api_key_enc="sk-test"
    )

    class _FailingClient:
        async def complete(self, system, user, max_tokens=512, images=None):
            # 不可重试的错误（认证失败）
            from app.services.llm_client import LLMError
            raise LLMError("401 Unauthorized", retryable=False)

    async def fake_build(*a, **k):
        return _FailingClient()

    chain = FallbackChain(primary=primary, fallbacks=[fallback])

    with patch("app.services.llm_runtime.build_client_from_dto", fake_build):
        with pytest.raises(LLMCallFailed) as exc_info:
            await _rt.call_with_fallback(chain, "sys", "user", max_tokens=100)

    # 最后失败的 provider 应该是 primary（因为第一个尝试）
    assert exc_info.value.error_type in ("auth", "unknown")


# ════════════════════════════════════════════════════════════
# 8) Scheduler FloodWait 修复测试
# ════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_scheduler_floodwait_calls_engine_correctly(monkeypatch) -> None:
    """scheduler FloodWaitError 时正确调用 engine.on_flood_wait。"""
    from app.worker.plugins.builtin.scheduler.plugin import SchedulerPlugin

    plugin = SchedulerPlugin()

    # Mock engine
    mock_engine = AsyncMock()
    mock_engine.on_flood_wait = AsyncMock()
    mock_engine.acquire = AsyncMock()
    mock_engine.acquire.return_value = AsyncMock()
    mock_engine.acquire.return_value.allowed = True
    mock_engine.acquire.return_value.wait_seconds = 0

    # Mock client
    mock_client = AsyncMock()

    class FakeFloodWaitError(Exception):
        seconds = 10

    mock_client.send_message = AsyncMock(side_effect=FakeFloodWaitError())

    class MockCtx:
        account_id = 1
        engine = mock_engine
        client = mock_client
        log = AsyncMock()

    ctx = MockCtx()

    # 触发 FloodWaitError
    await plugin._send_with_ratelimit(ctx, 123, "test message")

    # 验证 on_flood_wait 被调用（不带 peer_id）
    mock_engine.on_flood_wait.assert_called_once()
    args = mock_engine.on_flood_wait.call_args
    # 确认只传了 2 个参数（action 和 exc）
    assert len(args[0]) == 2
    assert args[0][0] == "send_message_group"
    assert isinstance(args[0][1], FakeFloodWaitError)


__all__ = []
