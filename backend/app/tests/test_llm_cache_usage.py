from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import BigInteger, Integer, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.llm_usage import LLMUsageItem, list_recent_llm_usage
from app.db.models.llm_usage import LLMUsage
from app.services import llm_runtime
from app.services.llm_codecs.anthropic import usage_from_anthropic
from app.services.llm_codecs.chat_completions import usage_from_chat
from app.services.llm_codecs.responses import usage_from_responses
from app.services.llm_dto import LLMProviderDTO
from app.services.llm_protocol import (
    MessageRole,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelStreamEvent,
    TextContent,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("codec, payload, expected", [
    (usage_from_anthropic, {"input_tokens": 20, "output_tokens": 5, "cache_read_input_tokens": 60,
                            "cache_creation_input_tokens": 10}, (60, 10)),
    (usage_from_chat, {"prompt_tokens": 20, "completion_tokens": 5,
                      "prompt_tokens_details": {"cached_tokens": 12}}, (12, 0)),
    (usage_from_responses, {"input_tokens": 20, "output_tokens": 5,
                           "input_tokens_details": {"cached_tokens": 12, "cache_write_tokens": 4}}, (12, 4)),
])
async def test_parsed_cache_usage_reaches_runtime_record(monkeypatch, stream, codec, payload, expected) -> None:
    response = ModelResponse(model="test-model", content=(TextContent("ok"),), usage=codec(payload))
    records = []

    async def capture(record):
        records.append(record)

    class Client:
        async def stream_invoke(self, request):
            yield ModelStreamEvent(response=response)

    class Selection:
        def apply(self, provider):
            return provider

        def usage_fields(self):
            return {}

        report_success = AsyncMock()
        close = AsyncMock()

    monkeypatch.setattr(llm_runtime, "_usage_callbacks", [capture])
    monkeypatch.setattr(llm_runtime, "_check_budget", AsyncMock(return_value=llm_runtime.BudgetCheck()))
    monkeypatch.setattr(llm_runtime.llm_account_budget, "settle", AsyncMock())
    monkeypatch.setattr(llm_runtime, "_invoke_model_with_retry", AsyncMock(return_value=response))
    monkeypatch.setattr(llm_runtime, "_open_key_selection", AsyncMock(return_value=Selection()))
    provider = LLMProviderDTO(id=1, name="test", provider="openai", default_model="test-model")
    chain = llm_runtime.FallbackChain(primary=provider)
    request = ModelRequest(model="test-model", messages=(ModelMessage.text(MessageRole.USER, "hello"),))
    if stream:
        events = [event async for event in llm_runtime.stream_model_with_fallback(
            chain, request, source="system_agent", client_factory=lambda *args, **kwargs: Client(),
        )]
        assert events[-1][0].response == response
    else:
        result, _, _ = await llm_runtime.invoke_model_with_fallback(chain, request, source="system_agent")
        assert result == response
    assert len(records) == 1
    assert (records[0].cache_read_tokens, records[0].cache_write_tokens) == expected


@pytest.mark.asyncio
async def test_cache_usage_columns_are_exposed_by_recent_api(monkeypatch) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    # SQLite autoincrement requires INTEGER; production keeps the existing BIGINT key.
    monkeypatch.setattr(LLMUsage.__table__.c.id, "type", BigInteger().with_variant(Integer(), "sqlite"))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE TABLE account (id INTEGER PRIMARY KEY)"))
            await conn.execute(text("CREATE TABLE llm_provider (id INTEGER PRIMARY KEY)"))
            await conn.run_sync(LLMUsage.__table__.create)
        async with factory() as db:
            db.add_all([
                LLMUsage(input_tokens=20, output_tokens=5, cache_read_tokens=60, cache_write_tokens=10,
                         source="system_agent", success=True),
                LLMUsage(input_tokens=10, output_tokens=2, source="system_agent", success=True),
                LLMUsage(input_tokens=10, output_tokens=2, cache_read_tokens=0, cache_write_tokens=0,
                         source="system_agent", success=True),
            ])
            await db.commit()
        async with factory() as db:
            rows = (await db.execute(select(LLMUsage).order_by(LLMUsage.id))).scalars().all()
            assert (rows[0].cache_read_tokens, rows[0].cache_write_tokens) == (60, 10)
            response = await list_recent_llm_usage(db, object(), limit=100)
            items = {item.id: item.model_dump(mode="json") for item in response.items}
            assert items[rows[0].id]["cache_read_tokens"] == 60
            assert items[rows[0].id]["cache_write_tokens"] == 10
            assert items[rows[1].id]["cache_read_tokens"] is None
            assert items[rows[1].id]["cache_write_tokens"] is None
            assert items[rows[2].id]["cache_read_tokens"] == 0
            assert items[rows[2].id]["cache_write_tokens"] == 0
        schema = LLMUsageItem.model_json_schema()
        assert "cache_read_tokens" in schema["properties"]
        assert "cache_write_tokens" in schema["properties"]
    finally:
        await engine.dispose()
