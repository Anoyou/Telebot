"""Unified runtime for standard LLM invocations."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from . import llm_client
from .llm_client import LLMResult
from .llm_dto import LLMProviderDTO
from .llm_runtime import build_fallback_chain, call_with_fallback


async def invoke(
    primary_provider: LLMProviderDTO,
    providers: dict[int, LLMProviderDTO],
    system: str,
    user: str,
    *,
    override_model: str | None = None,
    max_tokens: int = 512,
    images: list[bytes] | None = None,
    account_id: int | None = None,
    source: str | None = None,
    fallback_provider_id: int | None = None,
    matched_tag: str | None = None,
    client_factory: Callable[..., Any | Awaitable[Any]] | None = None,
) -> tuple[LLMResult, LLMProviderDTO, bool]:
    """Call a standard LLM provider with shared fallback / retry / usage logic."""

    chain = build_fallback_chain(
        primary_provider,
        providers=providers,
        fallback_provider_id=fallback_provider_id,
        matched_tag=matched_tag,
    )

    def _build_runtime_client(
        provider_dto: LLMProviderDTO,
        *,
        override_model: str | None = None,
        proxy_url: str | None = None,
    ):
        if client_factory is not None:
            return client_factory(
                provider_dto,
                override_model=override_model,
                proxy_url=proxy_url or provider_dto.proxy_url,
            )
        return llm_client.build_client(
            provider_dto,
            override_model=override_model,
            proxy_url=proxy_url or provider_dto.proxy_url,
        )

    return await call_with_fallback(
        chain,
        system,
        user,
        override_model=override_model,
        max_tokens=max_tokens,
        images=images,
        client_factory=_build_runtime_client,
        account_id=account_id,
        source=source,
    )
