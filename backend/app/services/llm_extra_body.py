"""插件 ``ctx.ai.complete(extra_body=...)`` 的受控透传（0.108.0+，能力 ``ai.extra_body``）。

按上游 API 格式白名单透传请求体字段（例如 OpenAI 兼容的 ``reasoning_effort`` / ``verbosity``），
被拒字段返回明确错误而不是静默丢弃：

- facade 层先按“所有格式白名单的并集”校验，不认识的字段直接抛错（插件立刻知道写错了）；
- 真正发请求时按选中 Provider 的格式再校验一次：该格式不支持的字段抛
  ``LLMError(CAPABILITY_MISMATCH)``，运行时会跳过这个 Provider 走备用，全部不兼容时插件拿到明确错误；
- 平台字段（model / messages / max_tokens / stream / tools …）永远不能透传。

值只在当前协程上下文生效（contextvar），不会泄漏到别的调用。
"""

from __future__ import annotations

import contextvars
import json
from typing import Any

EXTRA_BODY_MAX_BYTES = 4096

EXTRA_BODY_FIELDS: dict[str, frozenset[str]] = {
    "chat_completions": frozenset(
        {
            "reasoning_effort",
            "verbosity",
            "top_p",
            "top_k",
            "min_p",
            "frequency_penalty",
            "presence_penalty",
            "repetition_penalty",
            "seed",
            "stop",
            "response_format",
            "logit_bias",
            "enable_thinking",
            "thinking",
        }
    ),
    "responses": frozenset({"reasoning", "text", "top_p", "truncation"}),
    "anthropic": frozenset({"top_p", "top_k", "stop_sequences", "thinking"}),
}
EXTRA_BODY_ALL_FIELDS: frozenset[str] = frozenset().union(*EXTRA_BODY_FIELDS.values())
EXTRA_BODY_PLATFORM_FIELDS = frozenset(
    {
        "model",
        "messages",
        "input",
        "system",
        "instructions",
        "max_tokens",
        "max_output_tokens",
        "max_completion_tokens",
        "stream",
        "stream_options",
        "tools",
        "tool_choice",
        "user",
        "metadata",
        "store",
        "service_tier",
        "n",
        "previous_response_id",
        "include",
    }
)

_ACTIVE: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar("telepilot_llm_extra_body", default=None)


class ExtraBodyRejected(ValueError):
    """extra_body 含平台字段、未知字段或非 JSON 值。"""


def validate_extra_body(extra: Any) -> dict[str, Any] | None:
    if extra in (None, {}):
        return None
    if not isinstance(extra, dict):
        raise ExtraBodyRejected("extra_body 必须是对象")
    platform = sorted(str(key) for key in extra if str(key) in EXTRA_BODY_PLATFORM_FIELDS)
    if platform:
        raise ExtraBodyRejected(f"extra_body 不能覆盖平台字段：{', '.join(platform)}")
    unknown = sorted(str(key) for key in extra if str(key) not in EXTRA_BODY_ALL_FIELDS)
    if unknown:
        raise ExtraBodyRejected(
            f"extra_body 字段不在白名单：{', '.join(unknown)}（允许：{', '.join(sorted(EXTRA_BODY_ALL_FIELDS))}）"
        )
    try:
        encoded = json.dumps(extra, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ExtraBodyRejected("extra_body 只能包含 JSON 值") from exc
    if len(encoded.encode("utf-8")) > EXTRA_BODY_MAX_BYTES:
        raise ExtraBodyRejected(f"extra_body 超过 {EXTRA_BODY_MAX_BYTES} 字节")
    return {str(key): value for key, value in extra.items()}


def activate(extra: dict[str, Any] | None) -> contextvars.Token[Any] | None:
    if not extra:
        return None
    return _ACTIVE.set(dict(extra))


def reset(token: contextvars.Token[Any] | None) -> None:
    if token is not None:
        _ACTIVE.reset(token)


def current() -> dict[str, Any] | None:
    value = _ACTIVE.get()
    return dict(value) if value else None


def unsupported_for(api_format: str, extra: dict[str, Any]) -> list[str]:
    allowed = EXTRA_BODY_FIELDS.get(api_format, frozenset())
    return sorted(key for key in extra if key not in allowed)


__all__ = [
    "EXTRA_BODY_ALL_FIELDS",
    "EXTRA_BODY_FIELDS",
    "EXTRA_BODY_PLATFORM_FIELDS",
    "ExtraBodyRejected",
    "activate",
    "current",
    "reset",
    "unsupported_for",
    "validate_extra_body",
]
