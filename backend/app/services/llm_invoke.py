"""Unified helper for standard LLM invocations."""
from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from ..db.models.command import (
    LLM_API_FORMAT_CHAT_COMPLETIONS,
    LLM_API_FORMAT_RESPONSES,
    LLM_PROVIDER_OPENAI,
    LLM_WEB_SEARCH_API_FORMAT_AUTO,
)
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
    web_search: bool = False,
    web_search_context_size: str | None = None,
    temperature: float | None = None,
    reasoning_effort: str | None = None,
    timeout_seconds: int | None = None,
    native_image: bool = False,
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
        api_format_override = _api_format_for_call(provider_dto, web_search=web_search)
        if client_factory is not None:
            kwargs = {
                "override_model": override_model,
                "proxy_url": proxy_url or provider_dto.proxy_url,
            }
            if _accepts_kwarg(client_factory, "api_format_override"):
                kwargs["api_format_override"] = api_format_override
            return client_factory(provider_dto, **kwargs)
        kwargs = {
            "override_model": override_model,
            "proxy_url": proxy_url or provider_dto.proxy_url,
        }
        if _accepts_kwarg(llm_client.build_client, "api_format_override"):
            kwargs["api_format_override"] = api_format_override
        return llm_client.build_client(provider_dto, **kwargs)

    return await call_with_fallback(
        chain,
        system,
        user,
        override_model=override_model,
        max_tokens=max_tokens,
        images=images,
        web_search=web_search,
        web_search_context_size=web_search_context_size,
        temperature=temperature,
        reasoning_effort=reasoning_effort,
        timeout_seconds=timeout_seconds,
        native_image=native_image,
        client_factory=_build_runtime_client,
        account_id=account_id,
        source=source,
    )


def _accepts_kwarg(fn: Callable[..., Any], name: str) -> bool:
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return True
    return name in sig.parameters or any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
    )


def _api_format_for_call(provider: LLMProviderDTO, *, web_search: bool) -> str | None:
    """Return a per-call API format override.

    Default chat can stay on /chat/completions while web-search calls switch to
    /responses for OpenAI-compatible providers that support both protocols.
    """
    if not web_search:
        return None

    configured = (provider.web_search_api_format or LLM_WEB_SEARCH_API_FORMAT_AUTO).strip().lower()
    if configured and configured != LLM_WEB_SEARCH_API_FORMAT_AUTO:
        return configured

    current = (provider.api_format or "").strip().lower()
    if provider.provider.lower() == LLM_PROVIDER_OPENAI and current == LLM_API_FORMAT_CHAT_COMPLETIONS:
        return LLM_API_FORMAT_RESPONSES
    return None


def resolved_api_format_for_call(provider: LLMProviderDTO, *, web_search: bool) -> str:
    """Return the effective API format after per-call overrides are applied."""
    override = _api_format_for_call(provider, web_search=web_search)
    if override:
        return override
    configured = (provider.api_format or "").strip().lower()
    if configured:
        return configured
    from ..db.models.command import default_api_format_for

    return default_api_format_for(provider.provider)
