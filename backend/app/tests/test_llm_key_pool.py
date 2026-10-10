from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services import llm_account_budget, llm_key_pool, llm_runtime
from app.services.llm_client import LLMError, LLMResult
from app.services.llm_dto import LLMProviderDTO
from app.services.llm_protocol import (
    MessageRole,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelStreamEvent,
    ModelUsage,
    TextContent,
)


@pytest.fixture(autouse=True)
def _isolated_pool_state(monkeypatch):
    llm_key_pool.reset_state_for_tests()
    store: dict[str, dict[int, tuple[int, int]]] = {}
    affinity: dict[str, int | None] = {}

    async def fake_load(provider_id: int, key_ids: list[int]):
        return {k: v for k, v in store.get(str(provider_id), {}).items() if k in key_ids}

    async def fake_add(provider_id: int, key_id: int, *, requests: int = 1, tokens: int = 0):
        day = store.setdefault(str(provider_id), {})
        r, t = day.get(key_id, (0, 0))
        day[key_id] = (r + requests, t + tokens)

    async def fake_get_affinity(provider_id: int, scope: str | None):
        if not scope:
            return False
        return affinity.get(f"{provider_id}:{scope}", False)

    async def fake_set_affinity(provider_id: int, scope: str | None, key_id: int | None):
        if scope:
            affinity[f"{provider_id}:{scope}"] = key_id

    monkeypatch.setattr(llm_key_pool, "load_daily_usage", fake_load)
    monkeypatch.setattr(llm_key_pool, "add_daily_usage", fake_add)
    monkeypatch.setattr(llm_key_pool, "get_affinity", fake_get_affinity)
    monkeypatch.setattr(llm_key_pool, "set_affinity", fake_set_affinity)
    yield {"usage": store, "affinity": affinity}
    llm_key_pool.reset_state_for_tests()


def _provider(*, main_key: str | None = "main-enc", strategy: str = "order", keys=None) -> LLMProviderDTO:
    return LLMProviderDTO(
        id=42,
        name="pooled",
        provider="openai",
        api_format="chat_completions",
        default_model="gpt-x",
        api_key_enc=main_key,
        key_strategy=strategy,
        keys=keys
        if keys is not None
        else [
            {"id": 1, "label": "k-a", "api_key_enc": "enc-a", "enabled": True, "priority": 10},
            {"id": 2, "label": "k-b", "api_key_enc": "enc-b", "enabled": True, "priority": 20},
            {"id": 3, "label": "k-off", "api_key_enc": "enc-off", "enabled": False, "priority": 5},
        ],
    )


def test_build_candidates_orders_main_first_then_priority() -> None:
    candidates = llm_key_pool.build_candidates(_provider())
    assert [c.label for c in candidates] == ["主密钥", "k-a", "k-b"]
    assert candidates[0].is_main and candidates[0].api_key_enc == "main-enc"


def test_build_candidates_without_main_key_uses_pool_only() -> None:
    candidates = llm_key_pool.build_candidates(_provider(main_key=None))
    assert [c.label for c in candidates] == ["k-a", "k-b"]


def test_build_candidates_without_pool_keeps_single_main_even_if_empty() -> None:
    candidates = llm_key_pool.build_candidates(_provider(main_key=None, keys=[]))
    assert len(candidates) == 1 and candidates[0].is_main and candidates[0].api_key_enc is None


@pytest.mark.asyncio
async def test_open_selection_without_pool_never_touches_redis(monkeypatch) -> None:
    def boom(*_a, **_k):
        raise AssertionError("redis must not be touched")

    monkeypatch.setattr(llm_key_pool, "load_daily_usage", boom)
    monkeypatch.setattr(llm_key_pool, "get_affinity", boom)
    provider = _provider(keys=[])
    selection = await llm_key_pool.open_selection(provider, scope="account:1")
    assert selection.has_pool is False
    assert selection.apply(provider) is provider
    assert selection.usage_fields() == {"provider_key_id": None, "provider_key_label": None}
    assert await selection.report_failure(LLMError("429", status_code=429)) is False


@pytest.mark.asyncio
async def test_apply_replaces_credentials_and_falls_back_to_provider_proxy() -> None:
    provider = _provider(main_key=None)
    provider.proxy_url = "socks5://p:1080"
    provider.keys[0]["proxy_url"] = "http://key-proxy:8080"
    selection = await llm_key_pool.open_selection(provider)
    effective = selection.apply(provider)
    assert effective is not provider
    assert effective.api_key_enc == "enc-a"
    assert effective.proxy_url == "http://key-proxy:8080"
    assert selection.usage_fields() == {"provider_key_id": 1, "provider_key_label": "k-a"}
    assert await selection.report_failure(LLMError("401", status_code=401)) is True
    assert selection.apply(provider).proxy_url == "socks5://p:1080"


@pytest.mark.asyncio
async def test_rotate_strategy_cycles_start_position() -> None:
    provider = _provider(main_key=None, strategy="rotate")
    first = await llm_key_pool.open_selection(provider)
    second = await llm_key_pool.open_selection(provider)
    third = await llm_key_pool.open_selection(provider)
    assert [s.current.label for s in (first, second, third)] == ["k-a", "k-b", "k-a"]


@pytest.mark.asyncio
async def test_least_used_prefers_lowest_daily_tokens(_isolated_pool_state) -> None:
    _isolated_pool_state["usage"]["42"] = {1: (3, 5000), 2: (1, 100)}
    provider = _provider(main_key=None, strategy="least_used")
    selection = await llm_key_pool.open_selection(provider)
    assert selection.current.label == "k-b"


@pytest.mark.asyncio
async def test_daily_limits_exclude_keys_and_exhaust_pool(_isolated_pool_state) -> None:
    _isolated_pool_state["usage"]["42"] = {1: (10, 0), 2: (0, 999)}
    provider = _provider(main_key=None)
    provider.keys[0]["daily_request_limit"] = 10
    provider.keys[1]["daily_token_limit"] = 500
    selection = await llm_key_pool.open_selection(provider)
    assert selection.current is None
    with pytest.raises(LLMError) as exc_info:
        await llm_runtime._open_key_selection(provider, scope=None)
    assert exc_info.value.category == "key_pool_exhausted"
    assert exc_info.value.scope.value == "provider_local"


@pytest.mark.asyncio
async def test_affinity_pins_session_to_last_successful_key(_isolated_pool_state) -> None:
    provider = _provider(main_key=None, strategy="rotate")
    first = await llm_key_pool.open_selection(provider, scope="session:s1")
    assert first.current.label == "k-a"
    await first.report_success(tokens=10)
    # 轮转本应轮到 k-b，但同一会话亲和回到 k-a
    second = await llm_key_pool.open_selection(provider, scope="session:s1")
    assert second.current.label == "k-a"
    # 其它会话没有亲和记录，继续按轮转游标走（第 3、4 次分别落到 k-a、k-b）
    assert (await llm_key_pool.open_selection(provider, scope="session:s2")).current.label == "k-a"
    assert (await llm_key_pool.open_selection(provider, scope="session:s2")).current.label == "k-b"


@pytest.mark.asyncio
async def test_rate_limited_key_cools_down_and_moves_to_back() -> None:
    provider = _provider(main_key=None)
    selection = await llm_key_pool.open_selection(provider)
    assert selection.current.label == "k-a"
    assert await selection.report_failure(LLMError("too many", status_code=429)) is True
    assert selection.current.label == "k-b"
    assert llm_key_pool.key_health(42, 1)["state"] == "cooling"
    fresh = await llm_key_pool.open_selection(provider)
    assert fresh.current.label == "k-b"


@pytest.mark.asyncio
async def test_transient_error_does_not_rotate_keys() -> None:
    provider = _provider(main_key=None)
    selection = await llm_key_pool.open_selection(provider)
    assert await selection.report_failure(LLMError("连接超时", retryable=True, category="timeout")) is False
    assert selection.current.label == "k-a"


@pytest.mark.asyncio
async def test_call_with_retry_rotates_on_auth_failure_without_consuming_retries() -> None:
    provider = _provider(main_key="main-enc")
    seen: list[str | None] = []

    class _Client:
        def __init__(self, key_enc: str | None) -> None:
            self.key_enc = key_enc

        async def complete(self, system: str, user: str, **_kwargs):
            seen.append(self.key_enc)
            if self.key_enc == "main-enc":
                raise LLMError("401 Unauthorized", retryable=False, status_code=401)
            return LLMResult(text="ok", model="gpt-x", input_tokens=3, output_tokens=2)

    trace: dict = {}
    result = await llm_runtime._call_with_retry(
        provider,
        "sys",
        "hi",
        override_model=None,
        max_tokens=16,
        images=None,
        web_search=False,
        web_search_context_size=None,
        temperature=None,
        reasoning_effort=None,
        timeout_seconds=None,
        native_image=False,
        log_prompt_preview=False,
        client_factory=lambda row, **_k: _Client(row.api_key_enc),
        max_retries=0,
        key_trace=trace,
    )
    assert result.text == "ok"
    assert seen == ["main-enc", "enc-a"]
    assert trace == {"provider_key_id": 1, "provider_key_label": "k-a"}
    assert llm_key_pool.key_health(42, None)["consecutive_failures"] == 1


@pytest.mark.asyncio
async def test_call_with_retry_raises_when_all_keys_rejected() -> None:
    provider = _provider(main_key="main-enc")

    class _Client:
        async def complete(self, system: str, user: str, **_kwargs):
            raise LLMError("401 Unauthorized", retryable=False, status_code=401)

    with pytest.raises(LLMError):
        await llm_runtime._call_with_retry(
            provider,
            "sys",
            "hi",
            override_model=None,
            max_tokens=16,
            images=None,
            web_search=False,
            web_search_context_size=None,
            temperature=None,
            reasoning_effort=None,
            timeout_seconds=None,
            native_image=False,
            log_prompt_preview=False,
            client_factory=lambda *_a, **_k: _Client(),
            max_retries=0,
        )
    assert all(llm_key_pool.key_health(42, kid)["consecutive_failures"] == 1 for kid in (None, 1, 2))


@pytest.mark.asyncio
async def test_stream_rotates_key_before_first_delta_and_attributes_usage(monkeypatch) -> None:
    provider = _provider(main_key=None)
    ticket = llm_account_budget.LLMAccountBudgetTicket(7, 42, 20)
    records = []

    class _Client:
        def __init__(self, key_enc: str | None) -> None:
            self.key_enc = key_enc

        async def stream_invoke(self, request: ModelRequest):
            if self.key_enc == "enc-a":
                raise LLMError("rate limited", status_code=429, retryable=True)
            yield ModelStreamEvent(delta="ok")
            yield ModelStreamEvent(
                response=ModelResponse(
                    model=request.model,
                    content=(TextContent("ok"),),
                    usage=ModelUsage(input_tokens=5, output_tokens=3),
                )
            )

    monkeypatch.setattr(
        llm_runtime,
        "_check_budget",
        AsyncMock(return_value=llm_runtime.BudgetCheck(ticket=ticket)),
    )
    monkeypatch.setattr(llm_account_budget, "settle", AsyncMock())

    async def emit(record) -> None:
        records.append(record)

    monkeypatch.setattr(llm_runtime, "_emit_usage", emit)
    events = [
        item
        async for item in llm_runtime.stream_model_with_fallback(
            llm_runtime.FallbackChain(provider, []),
            ModelRequest(
                model="gpt-x",
                messages=(ModelMessage.text(MessageRole.USER, "question"),),
                max_output_tokens=20,
                metadata={"max_retries_per_model": 0},
            ),
            account_id=7,
            source="agent:test",
            client_factory=lambda row, **_k: _Client(row.api_key_enc),
        )
    ]
    assert events[-1][0].response is not None
    assert len(records) == 1 and records[0].success is True
    assert records[0].provider_key_id == 2
    assert records[0].provider_key_label == "k-b"
