"""Safe text-completion facade exposed to plugins as ``ctx.ai``.

The facade is intentionally thin: provider selection, token clamping and
metadata redaction live here, while the actual model invocation reuses the
shared LLM runtime so fallback, retries, usage logging and account budgets stay
consistent with first-party AI commands.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from sqlalchemy import select

from ...crypto import decrypt_str
from ...db.base import AsyncSessionLocal
from ...db.models.account import Proxy
from ...db.models.command import (
    LLM_API_FORMAT_ANTHROPIC_MESSAGES,
    LLM_API_FORMAT_CHAT_COMPLETIONS,
    LLM_API_FORMAT_RESPONSES,
    LLMProvider,
    default_api_format_for,
)
from ...services import llm_account_budget, llm_runtime, plugin_ai_quota
from ...services.ai_feature import is_ai_enabled
from ...services.llm_agent import AgentCallbacks, AgentLimits, run_agent, tools_from_manifest
from ...services.llm_agent_observability import (
    AgentObservationContext,
    build_agent_observability_callbacks,
)
from ...services.llm_client import (
    LLMCallFailed,
    LLMError,
    LLMResult,
)
from ...services.llm_client import (
    build_client_from_dto as build_llm_client,
)
from ...services.llm_dto import LLMProviderDTO
from ...services.llm_invoke import (
    invoke as invoke_ai_runtime,
)
from ...services.llm_invoke import (
    invoke_structured,
)
from ...services.llm_key_pool import open_selection_or_raise, resolve_pool_key_proxies
from ...services.llm_protocol import MessageRole, ModelMessage, ModelRequest, ModelUsage
from ...settings import settings
from ...util.proxy import ProxyConfigError

log = logging.getLogger(__name__)

ProviderLoader = Callable[[], Awaitable[Mapping[int, LLMProviderDTO]]]
BotFileLoader = Callable[[str, int], Awaitable[bytes]]

DEFAULT_PLUGIN_AI_MAX_TOKENS = 4096
DEFAULT_PLUGIN_AI_TIMEOUT_SECONDS = 600
# 识图：单次最多几张图、每张图的字节上限（Bot API getFile 下载上限为 20MB，这里更保守）。
PLUGIN_AI_MAX_IMAGES = 4
PLUGIN_AI_MAX_IMAGE_BYTES = 8 * 1024 * 1024
_WEB_SEARCH_CONTEXT_SIZES = frozenset({"low", "medium", "high"})
_SAFE_MODEL_KEYS = frozenset(
    {
        "id",
        "name",
        "label",
        "display_name",
        "modality",
        "max_tokens",
        "context_window",
        # 0.106.0-beta.13+：插件据此只列出 Provider 里已启用的模型（例如“换模型重试”菜单），
        # 未启用模型传给 complete(model=...) 会被拒绝。
        "enabled",
    }
)


class PluginAIError(RuntimeError):
    """Base class for plugin AI facade failures."""


class AIUnavailableError(PluginAIError):
    """Raised when no provider is available or the LLM runtime fails."""


class AIQuotaError(PluginAIError):
    """Raised when account/plugin LLM quota or provider rate limits block a call."""


class AIResponseTruncated(AIUnavailableError):
    """模型没有给出最终答案：输出额度被推理过程耗尽（finish_reason=length）或只返回了推理。

    平台绝不把推理过程（reasoning_content / ``<think>``）当作 ``AIResult.text`` 返回。
    插件可捕获后调大 ``max_tokens``、传 ``thinking="disabled"`` 重试，或提示用户换模型。
    """


class AICapabilityError(AIUnavailableError):
    """Raised when every candidate provider/model lacks a requested capability.

    例如请求了 ``web_search`` / ``images``，但所选 Provider 及 fallback 链都不支持。
    插件可捕获后去掉该能力降级重试；能力不匹配在发出网络请求前就会判定，重试很便宜。
    """


@dataclass(frozen=True)
class AIResult:
    """Desensitized result returned by ``PluginAI.complete``."""

    text: str
    model: str
    provider_id: int
    provider_name: str
    used_fallback: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    sources: list[dict[str, Any]] = field(default_factory=list)
    # 阶段 E：脱敏路由摘要（不含 key / base_url / 代理 / 内部分类器细节）。
    routing: dict[str, Any] = field(default_factory=dict)
    # 推理模型的思考过程（只读参考，绝不并入 text；插件不要把它发给用户）。
    reasoning: str | None = None
    reasoning_tokens: int = 0
    # Provider 原始结束原因（stop / length / …）。
    finish_reason: str | None = None


@dataclass(frozen=True)
class AIProviderInfo:
    """Desensitized provider metadata returned by ``PluginAI.list_providers``."""

    id: int
    name: str
    provider: str
    default_model: str
    api_format: str | None = None
    modality: str = "text"
    tags: list[str] = field(default_factory=list)
    cost_tier: int = 2
    models: list[dict[str, Any]] = field(default_factory=list)
    has_api_key: bool = False


@dataclass(frozen=True)
class AIAgentResult:
    text: str
    model: str
    provider_id: int
    provider_name: str
    used_fallback: bool
    steps: int
    tool_calls: int
    input_tokens: int
    output_tokens: int
    # 阶段 E：脱敏路由摘要。
    routing: dict[str, Any] = field(default_factory=dict)


class PluginAI:
    """MVP plugin AI facade for safe text completion."""

    def __init__(
        self,
        *,
        account_id: int | None,
        plugin_key: str,
        provider_loader: ProviderLoader | None = None,
        max_tokens_limit: int | None = None,
        timeout_limit_seconds: int | None = None,
        allow_agent: bool = False,
        allow_vision: bool = False,
        manifest: Mapping[str, Any] | None = None,
        bot_file_loader: BotFileLoader | None = None,
    ) -> None:
        self.account_id = account_id
        self.plugin_key = plugin_key
        self._provider_loader = provider_loader or load_llm_providers
        self._allow_agent = bool(allow_agent)
        self._allow_vision = bool(allow_vision)
        self._bot_file_loader = bot_file_loader
        self._manifest = dict(manifest or {})
        self.max_tokens_limit = _positive_int(
            max_tokens_limit,
            _positive_int(
                getattr(settings, "plugin_ai_max_output_tokens", None),
                DEFAULT_PLUGIN_AI_MAX_TOKENS,
            ),
        )
        self.timeout_limit_seconds = _positive_int(
            timeout_limit_seconds,
            _positive_int(
                getattr(settings, "plugin_ai_timeout_seconds", None),
                DEFAULT_PLUGIN_AI_TIMEOUT_SECONDS,
            ),
        )

    @classmethod
    def from_context(cls, ctx: Any) -> PluginAI:
        """Build the facade from a ``PluginContext``-like object."""

        return cls(
            account_id=getattr(ctx, "account_id", None),
            plugin_key=str(getattr(ctx, "feature_key", "") or "unknown"),
            allow_agent=bool(getattr(ctx, "allow_ai_agent", False)),
            allow_vision=bool(getattr(ctx, "allow_ai_vision", False)),
            manifest=getattr(ctx, "plugin_manifest", None),
        )

    @property
    def vision_enabled(self) -> bool:
        """插件是否声明了 ``ai_vision``（可向模型传图片、可读取交互 Bot 收到的图片）。"""

        return self._allow_vision

    async def fetch_bot_image(self, file_id: str, *, max_bytes: int = PLUGIN_AI_MAX_IMAGE_BYTES) -> bytes:
        """用当前账号的交互 Bot 下载一张 Bot API 图片（``message.media.file_id``）。

        需要 ``ai_vision`` 权限；只用于把用户发来的图片交给模型理解。超过 ``max_bytes``
        （上限 8MB）直接拒绝，不落盘、不返回文件路径或 Bot Token。
        """

        if not self._allow_vision:
            raise AIUnavailableError("插件未声明 ai_vision 权限，不能读取图片")
        if self._bot_file_loader is None:
            raise AIUnavailableError("当前运行环境不支持读取交互 Bot 图片")
        raw_id = str(file_id or "").strip()
        if not raw_id:
            raise AIUnavailableError("缺少图片 file_id")
        limit = max(1, min(_positive_int(max_bytes, PLUGIN_AI_MAX_IMAGE_BYTES), PLUGIN_AI_MAX_IMAGE_BYTES))
        try:
            data = await self._bot_file_loader(raw_id, limit)
        except PluginAIError:
            raise
        except Exception as exc:  # noqa: BLE001 - 下载错误统一脱敏成 facade 错误
            raise AIUnavailableError(f"读取图片失败：{type(exc).__name__}: {exc}") from exc
        if not data:
            raise AIUnavailableError("读取图片失败：文件为空")
        return bytes(data)

    async def list_providers(self) -> list[AIProviderInfo]:
        """Return providers without encrypted API keys, proxy URLs or base URLs."""

        providers = await self._load_providers()
        return [_provider_info(dto) for dto in sorted(providers.values(), key=lambda p: p.id)]

    async def complete(
        self,
        system: str,
        user: str,
        *,
        provider: int | str | None = None,
        provider_tag: str | None = None,
        route: str | None = None,
        tag: str | None = None,
        tags: list[str] | tuple[str, ...] | None = None,
        model: str | None = None,
        override_model: str | None = None,
        max_tokens: int = 512,
        timeout: int = DEFAULT_PLUGIN_AI_TIMEOUT_SECONDS,
        timeout_seconds: int | None = None,
        images: list[bytes] | tuple[bytes, ...] | None = None,
        web_search: bool = False,
        web_search_context_size: str | None = None,
        thinking: str | None = None,
        **_ignored: Any,
    ) -> AIResult:
        """Call a text LLM through TelePilot's shared LLM runtime.

        返回的 ``AIResult.text`` 只包含模型的最终 content；推理过程单独放在
        ``AIResult.reasoning``。模型没有给出最终答案（输出被推理耗尽等）时抛
        :class:`AIResponseTruncated`。``thinking="disabled"/"enabled"`` 对支持的
        OpenAI 兼容上游（智谱/Z.ai、DeepSeek、DashScope Qwen）关闭/开启思考，其余上游忽略。

        ``provider`` accepts an id or provider name. ``provider_tag`` /
        ``tag`` / first ``tags`` item select the cheapest usable provider with
        that tag. ``route`` 显式选择 ``fixed`` / ``tag`` / ``auto``（阶段 E）；
        留空时按旧参数推断，保持向后兼容。

        ``images``（需要 ``ai_vision`` 权限）把图片字节交给支持图片输入的模型；
        ``web_search`` 请求原生联网搜索（Responses API；OpenAI chat_completions
        Provider 会按 ``web_search_api_format`` 临时切到 Responses）。不支持时运行时
        会跳过该 Provider 走 fallback；全部不兼容时抛 :class:`AICapabilityError`。
        联网搜索的引用来源在 ``AIResult.sources``。
        """

        if tag is not None or tags:
            import warnings

            warnings.warn(
                "ctx.ai.complete tag/tags 是兼容别名，新模块请使用 provider_tag",
                DeprecationWarning,
                stacklevel=2,
            )
            from .deprecations import note_deprecated

            note_deprecated("ai.tag_alias", plugin_key=self.plugin_key, account_id=self.account_id, detail="ctx.ai.complete")
        system_prompt = str(system or "")
        user_prompt = str(user or "")
        if not system_prompt.strip() and not user_prompt.strip():
            raise AIUnavailableError("ctx.ai.complete 需要 system 或 user 内容")
        image_list = self._validated_images(images)
        search_size = str(web_search_context_size or "").strip().lower() or None
        if search_size is not None and search_size not in _WEB_SEARCH_CONTEXT_SIZES:
            search_size = None

        providers = await self._load_providers()
        selected_tag = provider_tag or tag
        if selected_tag is None and tags:
            selected_tag = str(tags[0]) if tags[0] else None
        primary, matched_tag, resolved_mode = await _resolve_route(
            providers,
            provider=provider,
            provider_tag=selected_tag,
            route=route,
            user_content=user_prompt,
        )
        clamped_tokens = self._clamp_max_tokens(max_tokens)
        clamped_timeout = self._clamp_timeout(timeout_seconds if timeout_seconds is not None else timeout)
        requested_model = str(model or override_model or "").strip() or None
        selected_model = _enabled_model_for_dto(primary, requested_model) if requested_model else None
        if requested_model and not selected_model:
            raise AIUnavailableError(f"模型 {requested_model} 未在 provider {primary.name} 中启用")
        routed_model = None if selected_model else _enabled_model_for_dto(primary, None)
        if not selected_model and not routed_model:
            raise AIUnavailableError(f"provider {primary.name} 没有已启用模型")
        thinking_mode = _normalize_thinking(thinking)
        if thinking_mode != "disabled" and is_reasoning_model(primary, selected_model or routed_model):
            floor = max(0, int(getattr(settings, "plugin_ai_reasoning_min_output_tokens", 0) or 0))
            if floor and clamped_tokens < floor:
                raised = self._clamp_max_tokens(floor)
                if raised > clamped_tokens:
                    log.info(
                        "[plugin-ai] 推理模型 max_tokens 抬高 plugin=%s model=%s requested=%s effective=%s",
                        self.plugin_key,
                        selected_model or routed_model,
                        clamped_tokens,
                        raised,
                    )
                    clamped_tokens = raised
        quota_ticket: plugin_ai_quota.PluginAIQuotaTicket | None = None
        try:
            estimated_tokens = _estimate_total_tokens(system_prompt, user_prompt, clamped_tokens)
            estimated_tokens += 1024 * len(image_list)
            quota_ticket = await plugin_ai_quota.acquire(
                self.plugin_key,
                self.account_id,
                estimated_tokens=estimated_tokens,
            )
            # The shared runtime enforces account budgets and records actual usage.
            result, used_provider, used_fallback = await invoke_ai_runtime(
                primary,
                providers,
                system_prompt,
                user_prompt,
                override_model=selected_model,
                routed_model=routed_model,
                max_tokens=clamped_tokens,
                images=image_list or None,
                web_search=bool(web_search),
                web_search_context_size=search_size,
                timeout_seconds=clamped_timeout,
                account_id=self.account_id,
                source=f"plugin:{self.plugin_key}",
                matched_tag=matched_tag,
                thinking=_normalize_thinking(thinking),
                # 插件的 timeout_seconds 是整次 complete() 的总时限：重试与备用 Provider
                # 都在这段时间内完成，主 Provider 超时也能在时限内走到备用（0.106.0-beta.20+）。
                deadline_seconds=float(clamped_timeout),
            )
            await plugin_ai_quota.release(
                quota_ticket,
                int(result.input_tokens or 0) + int(result.output_tokens or 0),
            )
        except LLMCallFailed as exc:
            await plugin_ai_quota.release(quota_ticket, 0)
            raise _facade_error_from_llm_call(exc) from exc
        # acquire() 抛 PluginAIQuotaExceeded 时 ticket 仍为 None，Redis 计数也未 ZADD，无需 release
        except plugin_ai_quota.PluginAIQuotaExceeded as exc:
            raise AIQuotaError(str(exc)) from exc
        except (LLMError, ValueError) as exc:
            await plugin_ai_quota.release(quota_ticket, 0)
            raise AIUnavailableError(str(exc)) from exc
        except Exception:
            await plugin_ai_quota.release(quota_ticket, 0)
            raise

        ai_result = _result_from_llm(result, used_provider, used_fallback)
        object.__setattr__(
            ai_result,
            "routing",
            _routing_summary(
                used_provider,
                mode=resolved_mode,
                matched_tag=matched_tag,
                selected_model=result.model or selected_model or routed_model,
                used_fallback=used_fallback,
            ),
        )
        return ai_result

    def _validated_images(self, images: Any) -> list[bytes]:
        if not images:
            return []
        if not self._allow_vision:
            raise AIUnavailableError("插件未声明 ai_vision 权限，不能向模型传图片")
        if isinstance(images, (bytes, bytearray)):
            images = [images]
        items = [bytes(item) for item in images if isinstance(item, (bytes, bytearray)) and item]
        if not items:
            return []
        if len(items) > PLUGIN_AI_MAX_IMAGES:
            raise AIUnavailableError(f"单次最多传 {PLUGIN_AI_MAX_IMAGES} 张图片")
        for item in items:
            if len(item) > PLUGIN_AI_MAX_IMAGE_BYTES:
                raise AIUnavailableError("图片过大（单张上限 8MB）")
        return items

    async def run_agent(
        self,
        system: str,
        user: str,
        *,
        handlers: Mapping[str, Callable[[dict[str, Any]], Awaitable[Any]]],
        provider: int | str | None = None,
        provider_tag: str | None = None,
        route: str | None = None,
        model: str | None = None,
        max_tokens: int = 1024,
        max_steps: int = 8,
        max_tool_calls: int = 24,
        max_total_tokens: int = 16_384,
        timeout_seconds: int = DEFAULT_PLUGIN_AI_TIMEOUT_SECONDS,
    ) -> AIAgentResult:
        """Run manifest-declared tools through the bounded internal AgentRuntime.

        ``route`` 显式选择 ``fixed`` / ``tag`` / ``auto``（阶段 E）；Agent 路由会
        预先排除没有已启用模型的 Provider（无法支撑 tools 调用）。
        """

        if not self._allow_agent:
            raise AIUnavailableError("插件未声明独立 ai_agent 权限")
        tools = tools_from_manifest(self._manifest, handlers)
        if not tools:
            raise AIUnavailableError("manifest 未声明可执行的 agent_tools，或宿主未注册 handler")
        providers = await self._load_providers()
        primary, matched_tag, resolved_mode = await _resolve_route(
            providers,
            provider=provider,
            provider_tag=provider_tag,
            route=route,
            require_tools=True,
            user_content=str(user or ""),
        )
        explicit_model = str(model or "").strip()
        selected_model = _tools_model_for_dto(primary, explicit_model)
        if selected_model is None:
            raise AIUnavailableError("所选模型不支持 tools，且该 Provider 没有其它可用 tools 模型")
        request = ModelRequest(
            model=selected_model,
            messages=(
                ModelMessage.text(MessageRole.SYSTEM, str(system or "")),
                ModelMessage.text(MessageRole.USER, str(user or "")),
            ),
            tools=tuple(tool.spec for tool in tools.values()),
            max_output_tokens=self._clamp_max_tokens(max_tokens),
            metadata={"model_pinned": bool(explicit_model)},
        )
        limits = AgentLimits(
            max_steps=max(1, min(int(max_steps), 16)),
            max_tool_calls=max(1, min(int(max_tool_calls), 64)),
            max_calls_per_turn=8,
            max_same_call=3,
            max_total_tokens=max(1, min(int(max_total_tokens), 100_000)),
            timeout_seconds=float(self._clamp_timeout(timeout_seconds)),
        )
        quota_ticket: plugin_ai_quota.PluginAIQuotaTicket | None = None
        agent_actual_tokens = 0
        active_provider = primary
        active_model = selected_model
        used_provider = primary
        used_fallback = False
        attempted_models: dict[int, str] = {}
        try:
            quota_ticket = await plugin_ai_quota.acquire(
                self.plugin_key,
                self.account_id,
                estimated_tokens=limits.max_total_tokens,
            )

            async def track_model_progress(event: dict[str, Any]) -> None:
                if str(event.get("type") or "") != "model_attempt":
                    return
                provider_id = event.get("provider_id")
                attempted_model = str(event.get("model") or "").strip()
                if isinstance(provider_id, int) and attempted_model:
                    attempted_models[provider_id] = attempted_model

            async def model_call(current: ModelRequest):
                nonlocal active_model, active_provider, used_provider, used_fallback
                provider_request = replace(current, model=active_model)
                response, actual_provider, fallback = await invoke_structured(
                    active_provider,
                    providers,
                    provider_request,
                    account_id=self.account_id,
                    source=f"plugin:{self.plugin_key}:agent",
                    matched_tag=matched_tag,
                    progress_callback=track_model_progress,
                )
                used_provider = actual_provider
                used_fallback = used_fallback or fallback or actual_provider.id != primary.id
                active_provider = actual_provider
                active_model = (
                    attempted_models.get(actual_provider.id)
                    or _tools_model_for_dto(actual_provider)
                    or active_model
                )
                return response

            callbacks = (
                build_agent_observability_callbacks(
                    AgentObservationContext(
                        account_id=int(self.account_id),
                        plugin_key=self.plugin_key,
                    )
                )
                if self.account_id is not None
                else AgentCallbacks()
            )
            observed_usage = callbacks.on_usage

            async def track_usage(usage: ModelUsage) -> None:
                nonlocal agent_actual_tokens
                agent_actual_tokens = usage.total_tokens
                if observed_usage is not None:
                    await observed_usage(usage)

            callbacks.on_usage = track_usage
            result = await run_agent(
                model_call,
                request,
                tools,
                limits=limits,
                callbacks=callbacks,
            )
            await plugin_ai_quota.release(quota_ticket, result.usage.total_tokens)
        except plugin_ai_quota.PluginAIQuotaExceeded as exc:
            raise AIQuotaError(str(exc)) from exc
        except LLMCallFailed as exc:
            await plugin_ai_quota.release(quota_ticket, agent_actual_tokens)
            raise _facade_error_from_llm_call(exc) from exc
        except Exception:
            await plugin_ai_quota.release(quota_ticket, agent_actual_tokens)
            raise

        return AIAgentResult(
            text=result.text,
            model=result.model,
            provider_id=used_provider.id,
            provider_name=used_provider.name,
            used_fallback=used_fallback,
            steps=result.steps,
            tool_calls=result.tool_calls,
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            routing=_routing_summary(
                used_provider,
                mode=resolved_mode,
                matched_tag=matched_tag,
                selected_model=result.model or selected_model,
                used_fallback=used_fallback,
            ),
        )

    async def stream_complete(
        self,
        system: str,
        user: str,
        *,
        provider: int | str | None = None,
        provider_tag: str | None = None,
        route: str | None = None,
        tag: str | None = None,
        tags: list[str] | tuple[str, ...] | None = None,
        model: str | None = None,
        override_model: str | None = None,
        max_tokens: int = 512,
        timeout: int = DEFAULT_PLUGIN_AI_TIMEOUT_SECONDS,
        timeout_seconds: int | None = None,
        temperature: float | None = None,
        reasoning_effort: str | None = None,
        **_ignored: Any,
    ) -> AsyncIterator[str]:
        """Yield text deltas from providers with native streaming support.

        The plugin-facing contract is intentionally narrow: each yielded item
        is a text delta string. Provider fallback is not attempted mid-stream,
        because partial text may already have been delivered to the plugin.
        """

        if tag is not None or tags:
            import warnings

            warnings.warn(
                "ctx.ai.stream_complete tag/tags 是兼容别名，新模块请使用 provider_tag",
                DeprecationWarning,
                stacklevel=2,
            )
            from .deprecations import note_deprecated

            note_deprecated("ai.tag_alias", plugin_key=self.plugin_key, account_id=self.account_id, detail="ctx.ai.stream_complete")
        system_prompt = str(system or "")
        user_prompt = str(user or "")
        if not system_prompt.strip() and not user_prompt.strip():
            raise AIUnavailableError("ctx.ai.stream_complete 需要 system 或 user 内容")

        providers = await self._load_providers()
        selected_tag = provider_tag or tag
        if selected_tag is None and tags:
            selected_tag = str(tags[0]) if tags[0] else None
        primary, _matched_tag, _resolved_mode = await _resolve_route(
            providers,
            provider=provider,
            provider_tag=selected_tag,
            route=route,
            user_content=str(user or ""),
        )
        api_format = _effective_api_format(primary)
        if api_format not in {
            LLM_API_FORMAT_CHAT_COMPLETIONS,
            LLM_API_FORMAT_RESPONSES,
            LLM_API_FORMAT_ANTHROPIC_MESSAGES,
        }:
            raise AIUnavailableError(
                f"provider {primary.name} 暂不支持 streaming；请使用 "
                "chat_completions、responses 或 anthropic_messages provider"
            )

        clamped_tokens = self._clamp_max_tokens(max_tokens)
        clamped_timeout = self._clamp_timeout(timeout_seconds if timeout_seconds is not None else timeout)
        requested_model = str(model or override_model or "").strip() or None
        selected_model = _enabled_model_for_dto(primary, requested_model) if requested_model else None
        if requested_model and not selected_model:
            raise AIUnavailableError(f"模型 {requested_model} 未在 provider {primary.name} 中启用")
        routed_model = None if selected_model else _enabled_model_for_dto(primary, None)
        if not selected_model and not routed_model:
            raise AIUnavailableError(f"provider {primary.name} 没有已启用模型")
        quota_ticket: plugin_ai_quota.PluginAIQuotaTicket | None = None
        budget_ticket: llm_account_budget.LLMAccountBudgetTicket | None = None
        quota_settled = False
        budget_settled = False
        actual_tokens = 0
        final_input_tokens = 0
        final_output_tokens = 0
        final_model = selected_model or routed_model or primary.default_model
        response_preview_parts: list[str] = []
        response_preview_chars = 0
        started_at = time.monotonic()
        estimated_tokens = 0
        provider_call_started = False
        stream_terminal_received = False
        active_client: Any | None = None
        terminal_chunk: Any | None = None
        key_trace: dict[str, Any] = {}
        selection: Any | None = None

        async def settle_interrupted(error_type: str) -> None:
            """Conservatively charge a stream once provider execution started."""

            nonlocal budget_settled, quota_settled
            charged_tokens = estimated_tokens if provider_call_started else 0
            await llm_account_budget.settle(
                budget_ticket,
                actual_tokens=charged_tokens,
                actual_provider=primary if provider_call_started else None,
                success=False,
                charge=provider_call_started,
            )
            budget_settled = True
            await plugin_ai_quota.release(quota_ticket, charged_tokens)
            quota_settled = True
            await _emit_stream_usage(
                account_id=self.account_id,
                plugin_key=self.plugin_key,
                provider=primary,
                model=final_model,
                input_tokens=charged_tokens,
                output_tokens=0,
                success=False,
                error_type=error_type,
                started_at=started_at,
                request_preview=llm_runtime.request_preview_for_usage(system_prompt, user_prompt),
                response_preview=llm_runtime.preview_text_for_usage("".join(response_preview_parts)),
                outcome=active_client,
            )

        try:
            estimated_tokens = _estimate_total_tokens(system_prompt, user_prompt, clamped_tokens)
            quota_ticket = await plugin_ai_quota.acquire(
                self.plugin_key,
                self.account_id,
                estimated_tokens=estimated_tokens,
            )
            budget_ticket = await llm_account_budget.acquire(
                self.account_id,
                primary,
                estimated_tokens,
            )
            # 走密钥池选钥：pool-only Provider（无主 Key、只有池内 Key）此前直接 401。
            selection = await open_selection_or_raise(primary, scope=None)
            effective_provider = selection.apply(primary)
            key_trace.update(selection.usage_fields())
            client = build_llm_client(
                effective_provider,
                override_model=selected_model or routed_model,
                proxy_url=effective_provider.proxy_url,
                api_format_override=None,
            )
            if inspect.isawaitable(client):
                client = await client
            active_client = client
            provider_call_started = True
            async with asyncio.timeout(clamped_timeout):
                async for chunk in client.stream_complete(
                    system_prompt,
                    user_prompt,
                    max_tokens=clamped_tokens,
                    temperature=temperature,
                    reasoning_effort=reasoning_effort,
                    timeout_seconds=clamped_timeout,
                ):
                    if getattr(chunk, "done", False):
                        stream_terminal_received = True
                        terminal_chunk = chunk
                        final_input_tokens = int(getattr(chunk, "input_tokens", None) or 0)
                        final_output_tokens = int(getattr(chunk, "output_tokens", None) or 0)
                        final_model = str(getattr(chunk, "model", None) or final_model or "")
                        continue
                    final_model = str(getattr(chunk, "model", None) or final_model or "")
                    if getattr(chunk, "is_reasoning", False):
                        # 推理过程不是答案，不交给插件
                        continue
                    delta = str(getattr(chunk, "delta", "") or "")
                    if delta:
                        if response_preview_chars < 2000:
                            kept = delta[: max(0, 2000 - response_preview_chars)]
                            response_preview_parts.append(kept)
                            response_preview_chars += len(kept)
                        yield delta
            if not stream_terminal_received:
                raise LLMError("模型流式响应提前结束，没有返回最终状态。", retryable=True)
            actual_tokens = final_input_tokens + final_output_tokens
            if actual_tokens <= 0 and quota_ticket is not None:
                actual_tokens = int(quota_ticket.estimated_tokens or 0)
                final_input_tokens = actual_tokens
                final_output_tokens = 0
            key_trace.update(selection.usage_fields())
            await selection.report_success(tokens=actual_tokens)
            await llm_account_budget.settle(
                budget_ticket,
                actual_tokens=actual_tokens,
                actual_provider=primary,
                success=True,
            )
            budget_settled = True
            await plugin_ai_quota.release(quota_ticket, actual_tokens)
            quota_settled = True
            await _emit_stream_usage(
                account_id=self.account_id,
                plugin_key=self.plugin_key,
                provider=primary,
                model=final_model,
                input_tokens=final_input_tokens,
                output_tokens=final_output_tokens,
                success=True,
                started_at=started_at,
                request_preview=llm_runtime.request_preview_for_usage(system_prompt, user_prompt),
                response_preview=llm_runtime.preview_text_for_usage("".join(response_preview_parts)),
                key_trace=key_trace,
                outcome=terminal_chunk,
            )
        except plugin_ai_quota.PluginAIQuotaExceeded as exc:
            raise AIQuotaError(str(exc)) from exc
        except llm_account_budget.LLMAccountBudgetExceeded as exc:
            await plugin_ai_quota.release(quota_ticket, 0)
            quota_settled = True
            await _emit_stream_usage(
                account_id=self.account_id,
                plugin_key=self.plugin_key,
                provider=primary,
                model=final_model,
                success=False,
                error_type="budget_exceeded",
                started_at=started_at,
                request_preview=llm_runtime.request_preview_for_usage(system_prompt, user_prompt),
                key_trace=key_trace,
            )
            raise AIQuotaError(str(exc)) from exc
        except (GeneratorExit, asyncio.CancelledError) as exc:
            await settle_interrupted("consumer_closed" if isinstance(exc, GeneratorExit) else "cancelled")
            raise
        except TimeoutError as exc:
            await settle_interrupted("timeout")
            raise AIUnavailableError(f"ctx.ai.stream_complete 超过 {clamped_timeout:g} 秒总超时时间") from exc
        except (LLMError, ValueError, NotImplementedError) as exc:
            # 密钥作用域错误（401/403/429）要让池内其余密钥进入冷却/换钥统计。
            # 流式不中途换 Provider（可能已向插件吐出增量），但必须留下失败记录。
            if selection is not None:
                with contextlib.suppress(Exception):
                    key_trace.update(selection.usage_fields())
                    await selection.report_failure(exc)
            await settle_interrupted(type(exc).__name__)
            raise AIUnavailableError(str(exc)) from exc
        except Exception:
            await settle_interrupted("unexpected_error")
            raise
        finally:
            if budget_ticket is not None and not budget_settled:
                charged_tokens = estimated_tokens if provider_call_started else 0
                await llm_account_budget.settle(
                    budget_ticket,
                    actual_tokens=charged_tokens,
                    actual_provider=primary if provider_call_started else None,
                    success=False,
                    charge=provider_call_started,
                )
            if quota_ticket is not None and not quota_settled:
                await plugin_ai_quota.release(
                    quota_ticket,
                    estimated_tokens if provider_call_started else 0,
                )

    async def _load_providers(self) -> dict[int, LLMProviderDTO]:
        if not await is_ai_enabled():
            raise AIUnavailableError("AI 能力已在系统设置中关闭")
        providers = dict(await self._provider_loader())
        if not providers:
            raise AIUnavailableError("没有可用的 LLM provider")
        return providers

    def _clamp_max_tokens(self, value: int) -> int:
        requested = _positive_int(value, 512)
        return max(1, min(requested, self.max_tokens_limit))

    def _clamp_timeout(self, value: int) -> int:
        requested = _positive_int(value, self.timeout_limit_seconds)
        return max(1, min(requested, self.timeout_limit_seconds))


async def load_llm_providers() -> dict[int, LLMProviderDTO]:
    """Load provider DTOs from DB without exposing decrypted keys to plugins."""

    async with AsyncSessionLocal() as db:
        rows = list((await db.execute(select(LLMProvider))).scalars().all())
        proxy_ids = {int(row.proxy_id) for row in rows if getattr(row, "proxy_id", None) is not None}
        for row in rows:
            for key_row in getattr(row, "pool_keys", None) or []:
                if key_row.proxy_id is not None:
                    proxy_ids.add(int(key_row.proxy_id))
        proxies: dict[int, Proxy] = {}
        if proxy_ids:
            proxy_rows = list(
                (await db.execute(select(Proxy).where(Proxy.id.in_(proxy_ids)))).scalars().all()
            )
            proxies = {int(row.id): row for row in proxy_rows}

    providers: dict[int, LLMProviderDTO] = {}
    for row in rows:
        dto = LLMProviderDTO.from_orm_row(row)
        proxy_id = getattr(row, "proxy_id", None)
        if proxy_id is not None:
            try:
                dto.proxy_url = _proxy_url_from_row(proxies.get(int(proxy_id)))
            except ProxyConfigError as exc:
                log.error(
                    "PluginAI Provider %s 引用了缺失或不受支持的代理 %s，已从路由排除: %s",
                    dto.id,
                    proxy_id,
                    exc,
                )
                continue
        resolve_pool_key_proxies(dto, proxies, _proxy_url_from_row, log_prefix="PluginAI Provider")
        providers[int(dto.id)] = dto
    return providers


def _routing_summary(
    dto: LLMProviderDTO,
    *,
    mode: str,
    matched_tag: str | None,
    selected_model: str | None,
    used_fallback: bool = False,
) -> dict[str, Any]:
    """构造脱敏路由摘要（阶段 E）。

    只暴露 provider_id / name / 模式 / 命中 tag / 生效模型 / 协议 / 身份 与
    是否 fallback；**绝不**含 api_key、base_url、代理或内部分类器细节。
    """
    return {
        "mode": mode,
        "provider_id": int(dto.id),
        "provider_name": dto.name,
        "matched_tag": matched_tag,
        "model": selected_model or (dto.default_model or None),
        "api_format": dto.api_format,
        "client_identity_profile": getattr(dto, "client_identity_profile", "auto"),
        "used_fallback": bool(used_fallback),
    }


def _explicit_enabled_models(dto: LLMProviderDTO) -> list[str]:
    """严格返回 ``models[].enabled == true`` 的模型 id（不回落 default_model）。"""
    enabled: list[str] = []
    for item in dto.models or []:
        if isinstance(item, dict) and bool(item.get("enabled")):
            mid = str(item.get("id") or "").strip()
            if mid and mid not in enabled:
                enabled.append(mid)
    return enabled


def _enabled_model_for_dto(dto: LLMProviderDTO, explicit: str | None) -> str | None:
    """为插件路由选择模型，不允许显式参数绕过 Provider 启用清单。"""
    explicit_clean = str(explicit or "").strip()
    if explicit_clean:
        if dto.has_model_list() and explicit_clean not in dto.enabled_model_ids():
            return None
        return explicit_clean
    return dto.pick_enabled_model()


def _tools_model_for_dto(dto: LLMProviderDTO, explicit: str | None = None) -> str | None:
    """返回一个已启用且声明支持 tools 的模型。"""
    explicit_clean = str(explicit or "").strip()
    if explicit_clean:
        enabled = _explicit_enabled_models(dto)
        if dto.has_model_list() and explicit_clean not in enabled:
            return None
        return explicit_clean if dto.capabilities_for_model(explicit_clean).tools else None
    candidate = _enabled_model_for_dto(dto, None)
    if candidate and dto.capabilities_for_model(candidate).tools:
        return candidate
    for model_id in _explicit_enabled_models(dto):
        if dto.capabilities_for_model(model_id).tools:
            return model_id
    return None


async def _resolve_route(
    providers: Mapping[int, LLMProviderDTO],
    *,
    provider: int | str | None,
    provider_tag: str | None,
    route: str | None,
    require_tools: bool = False,
    user_content: str | None = None,
) -> tuple[LLMProviderDTO, str | None, str]:
    """统一 fixed / tag / auto 路由解析（阶段 E；阶段 F 收口 #4）。

    - ``route`` 显式指定 ``fixed`` / ``tag`` / ``auto``；留空时按旧行为推断
      （给了 provider→fixed、给了 provider_tag→tag、都没有→auto）。
    - **显式** ``route="auto"``：复用共享 Router（``llm_router.pick_provider``），按
      内容特征（code/math/vision/reason 等规则）选 Provider，与模板 auto 行为一致；
      插件受限——分类器与全局 fallback 一律禁用（那是宿主级能力）。
    - **推断** auto（旧参数全缺省）：保持向后兼容的"chat 优先 / cost_tier 升序"，
      不引入内容路由，避免改变既有插件行为。
    - 插件不能指定 UA / 身份 / 密钥 / 代理 / 内部分类器 / 全局 fallback：
      此函数只在"已配置 key 的候选"内做启用/能力/tag 过滤，不触碰这些维度。
    - ``require_tools``（run_agent）：预先排除不支持 tools 的模型（无已启用模型的
      Provider 视为不可用）。
    返回 ``(dto, matched_tag, resolved_mode)``。
    """
    usable = [p for p in providers.values() if p.has_api_key]
    if require_tools:
        # Agent 路由：预先排除不支持 tools 的 Provider——必须存在一个可用模型
        # （显式启用的模型，或在没有显式启用清单时回落到 default_model）。
        # 若 Provider 有显式 models 清单但全部禁用且无 default_model，则视为不可用。
        usable = [p for p in usable if _tools_model_for_dto(p) is not None]
    if not usable:
        raise AIUnavailableError("没有可用的 LLM provider（未配置 key 或无已启用模型）")

    explicit = str(route or "").strip().lower()
    mode = explicit if explicit in {"fixed", "tag", "auto"} else ""
    if not mode:
        if provider is not None:
            mode = "fixed"
        elif provider_tag:
            mode = "tag"
        else:
            mode = "auto"

    if mode == "fixed":
        if provider is None:
            raise AIUnavailableError("route=fixed 需要指定 provider")
        selected = _find_provider(usable, provider)
        if selected is None:
            raise AIUnavailableError(f"找不到可用 provider: {provider}")
        return selected, None, "fixed"

    if mode == "tag":
        tag = str(provider_tag or "").strip()
        if not tag:
            raise AIUnavailableError("route=tag 需要指定 provider_tag")
        tagged = [p for p in usable if tag in set(p.tags or [])]
        if not tagged:
            raise AIUnavailableError(f"找不到带有 tag={tag} 的可用 provider")
        tagged.sort(key=lambda p: (p.cost_tier, p.id))
        return tagged[0], tag, "tag"

    # auto。显式 route="auto" → 走共享 Router（内容路由）；推断 auto → 旧兼容行为。
    if explicit == "auto":
        selected = await _shared_router_auto(usable, user_content, require_tools=require_tools)
        if selected is not None:
            dto, matched = selected
            return dto, matched, "auto"

    # 推断 auto / 共享 Router 无结果时的兜底：chat 优先、cost_tier 升序（省钱）。
    chat = [p for p in usable if "chat" in set(p.tags or [])]
    pool = list(chat or usable)
    pool.sort(key=lambda p: (p.cost_tier, p.id))
    return pool[0], ("chat" if chat else None), "auto"


async def _shared_router_auto(
    usable: list[LLMProviderDTO],
    user_content: str | None,
    *,
    require_tools: bool = False,
) -> tuple[LLMProviderDTO, str | None] | None:
    """用共享 Router 对可用候选做内容路由（插件版：无分类器 / 无全局 fallback）。

    返回 ``(dto, matched_tag)``；候选为空或 Router 未命中时返回 None 交由上层兜底。
    """
    from ...services import llm_router

    by_id = {int(p.id): p for p in usable}
    if not by_id:
        return None
    # 构造共享 Router 需要的 provider dict。必须用 `to_runtime_dict()` 而不是 `to_dict()`
    # + 手补 api_key_enc：共享 Router 的 `_has_api_key` 还检查池内 `keys[].api_key_enc`，
    # 只用展示版 dict 会让"仅密钥池、未设主 Key"的 Provider 从路由候选里消失。
    router_pool: dict[int, dict[str, Any]] = {}
    for pid, dto in by_id.items():
        d = dto.to_runtime_dict()
        router_pool[pid] = d
    try:
        decision = await llm_router.pick_provider(
            str(user_content or ""),
            None,
            False,
            router_pool,
            classifier_provider_id=None,  # 插件不能用内部分类器
            fallback_provider_id=None,  # 插件不能用全局 fallback
        )
    except Exception:  # noqa: BLE001 - Router 异常时回落到上层兜底策略
        return None
    chosen = by_id.get(int(decision.provider_id))
    if chosen is None:
        return None
    if require_tools and _tools_model_for_dto(chosen) is None:
        # Router 选中的 Provider 无可用模型（不支持 tools）→ 交回上层兜底。
        return None
    return chosen, (decision.matched_tag or None)


def _find_provider(providers: list[LLMProviderDTO], provider: int | str) -> LLMProviderDTO | None:
    raw = str(provider).strip()
    if raw.isdigit():
        pid = int(raw)
        for item in providers:
            if item.id == pid:
                return item
    lowered = raw.lower()
    for item in providers:
        if item.name.lower() == lowered:
            return item
    return None


def _provider_info(dto: LLMProviderDTO) -> AIProviderInfo:
    return AIProviderInfo(
        id=dto.id,
        name=dto.name,
        provider=dto.provider,
        default_model=dto.default_model,
        api_format=dto.api_format,
        modality=dto.modality,
        tags=list(dto.tags or []),
        cost_tier=int(dto.cost_tier or 2),
        models=[_safe_model_metadata(item) for item in (dto.models or []) if isinstance(item, dict)],
        has_api_key=bool(dto.has_api_key),
    )


def _safe_model_metadata(item: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): value for key, value in item.items() if str(key) in _SAFE_MODEL_KEYS}


def _result_from_llm(result: LLMResult, provider: LLMProviderDTO, used_fallback: bool) -> AIResult:
    sources = [dict(item) for item in (getattr(result, "sources", None) or []) if isinstance(item, dict)]
    if getattr(result, "text_from_reasoning", False):
        # 防御：运行时已拒绝这种结果；直接调用 client 的路径也绝不把推理当正文。
        raise AIResponseTruncated("模型只返回了推理过程，没有给出最终答案")
    return AIResult(
        text=str(result.text or ""),
        model=str(result.model or provider.default_model or ""),
        provider_id=int(provider.id),
        provider_name=provider.name,
        used_fallback=bool(used_fallback),
        input_tokens=int(result.input_tokens or 0),
        output_tokens=int(result.output_tokens or 0),
        sources=sources,
        reasoning=getattr(result, "reasoning", None) or None,
        reasoning_tokens=int(getattr(result, "reasoning_tokens", 0) or 0),
        finish_reason=getattr(result, "provider_status", None) or None,
    )


async def _emit_stream_usage(
    *,
    account_id: int | None,
    plugin_key: str,
    provider: LLMProviderDTO,
    model: str | None,
    success: bool,
    started_at: float,
    input_tokens: int = 0,
    output_tokens: int = 0,
    error_type: str | None = None,
    request_preview: str | None = None,
    response_preview: str | None = None,
    key_trace: dict[str, Any] | None = None,
    outcome: object | None = None,
) -> None:
    await llm_runtime._emit_usage(
        llm_runtime.UsageRecord(
            provider_id=int(provider.id),
            account_id=account_id,
            provider_name=provider.name,
            model=model or provider.default_model,
            client_identity_profile=llm_runtime.resolve_usage_client_identity_profile(provider),
            provider_key_id=(key_trace or {}).get("provider_key_id"),
            provider_key_label=(key_trace or {}).get("provider_key_label"),
            input_tokens=max(0, int(input_tokens or 0)),
            output_tokens=max(0, int(output_tokens or 0)),
            latency_ms=max(0, int((time.monotonic() - started_at) * 1000)),
            success=success,
            error_type=error_type,
            source=f"plugin:{plugin_key}",
            used_fallback=False,
            fallback_chain=[provider.name],
            request_preview=request_preview,
            response_preview=response_preview,
            **llm_runtime.usage_transport_fields(provider, outcome),
        )
    )


def _effective_api_format(provider: LLMProviderDTO) -> str:
    configured = str(provider.api_format or "").strip().lower()
    if configured:
        return configured
    return default_api_format_for(provider.provider)


_REASONING_MODEL_MARKERS = (
    "glm-4.5",
    "glm-4.6",
    "glm-4.7",
    "glm-5",
    "glm-z1",
    "deepseek-r1",
    "deepseek-reasoner",
    "qwq",
    "qwen3",
    "thinking",
    "reasoner",
    "kimi-k2",
    "kimi-k3",
    "gpt-5",
    "grok-4",
    "magistral",
)
_REASONING_MODEL_PREFIXES = ("o1", "o3", "o4")


def is_reasoning_model(dto: LLMProviderDTO, model: str | None) -> bool:
    """模型是否会消耗输出 Token 做推理：模型清单声明了推理档位，或模型名属于常见推理系列。"""

    name = str(model or dto.default_model or "").strip()
    if not name:
        return False
    for item in dto.models or []:
        if isinstance(item, dict) and str(item.get("id") or "") == name:
            meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else item
            if meta.get("supported_reasoning_levels") or meta.get("reasoning_efforts"):
                return True
    base = name.lower().rsplit("/", 1)[-1]
    if any(base == prefix or base.startswith(f"{prefix}-") for prefix in _REASONING_MODEL_PREFIXES):
        return True
    return any(marker in base for marker in _REASONING_MODEL_MARKERS)


def _normalize_thinking(value: Any) -> str | None:
    mode = str(value or "").strip().lower()
    if mode in {"disabled", "off", "false", "none"}:
        return "disabled"
    if mode in {"enabled", "on", "true"}:
        return "enabled"
    return None


def _facade_error_from_llm_call(exc: LLMCallFailed) -> PluginAIError:
    message = str(exc)
    if exc.error_type in {"budget_exceeded", "rate_limit"}:
        return AIQuotaError(message)
    if exc.error_type in {
        llm_runtime.AI_RESPONSE_TRUNCATED_CATEGORY,
        llm_runtime.AI_NO_FINAL_ANSWER_CATEGORY,
    }:
        return AIResponseTruncated(message)
    scope = getattr(getattr(exc, "scope", None), "value", getattr(exc, "scope", None))
    if str(scope or "") == "capability_mismatch":
        return AICapabilityError(message)
    return AIUnavailableError(message)


def _estimate_total_tokens(system_prompt: str, user_prompt: str, max_output_tokens: int) -> int:
    """Conservative quota reservation: prompt estimate + requested output cap."""

    prompt_bytes = len(system_prompt.encode("utf-8")) + len(user_prompt.encode("utf-8"))
    prompt_estimate = max(1, (prompt_bytes + 3) // 4)
    return max(1, int(max_output_tokens or 0) + prompt_estimate)


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return int(default)
    return parsed if parsed > 0 else int(default)


def _proxy_url_from_row(proxy: Proxy | None) -> str:
    if proxy is None:
        raise ProxyConfigError("Provider 引用的代理不存在，拒绝回落直连")
    ptype = str(proxy.type or "").lower()
    if ptype == "socks5":
        scheme = "socks5"
    elif ptype in {"http", "https"}:
        scheme = "http"
    else:
        raise ProxyConfigError(f"代理类型 {proxy.type!r} 不能用于 LLM，拒绝回落直连")

    password = ""
    if proxy.password_enc:
        try:
            password = decrypt_str(proxy.password_enc)
        except Exception as exc:  # noqa: BLE001 - 坏凭据不能被降级为空密码继续路由
            raise ProxyConfigError(
                "PluginAI Provider 代理凭据无法解密，请重新保存或更换代理"
            ) from exc

    from urllib.parse import quote

    auth = ""
    if proxy.username:
        auth = quote(str(proxy.username), safe="")
        if password:
            auth = f"{auth}:{quote(password, safe='')}"
        auth = f"{auth}@"
    return f"{scheme}://{auth}{proxy.host}:{int(proxy.port)}"


__all__ = [
    "AIAgentResult",
    "AICapabilityError",
    "AIProviderInfo",
    "AIQuotaError",
    "AIResult",
    "AIUnavailableError",
    "PluginAI",
    "PluginAIError",
    "load_llm_providers",
]
