"""「在原消息上编辑」模式的 AI 分段流式输出（0.120.0-beta.2）。

开关「AI 回答分段流式输出」（``ai_stream_edit``，默认关）打开后，``,ai`` 指令在「普通」与
「在原消息上编辑（富文本）」两种回复方式下边生成边编辑指令消息：

- 只在**一段写完**（出现空行）时才把已完成的段落编辑上去，不逐字刷新；
- 编辑走 :class:`~app.services.telegram_stream.ThrottledTextPusher` 节流（私聊 2 秒、群 3 秒一次，
  遇到 FloodWait 按 Telegram 要求的秒数推迟），中间态只是纯文本 + 「生成中」提示；
- 生成结束后由调用方照旧做**一次**最终编辑（引用 → 分割线 → 回答 → 分割线 → 署名；
  富文本不可用时自动回退普通格式）。

模型调用走 ``llm_invoke.stream_structured``（与 System Agent 同一条预算 / 用量 / 密钥池 / fallback
链路）。任何在**第一段显示之前**的失败（Provider 不支持流式、联网搜索格式不兼容、网络错误……）
都返回 ``None``，由调用方改走原来的一次性调用，用户无感；已经显示过内容后才失败时把错误写回
消息（避免重复扣费重试）。
"""

from __future__ import annotations

import html
import logging
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

STREAM_EDIT_SWITCH = "ai_stream_edit"
PRIVATE_INTERVAL_SECONDS = 2.0
GROUP_INTERVAL_SECONDS = 3.0
# 一段迟迟不结束（长列表 / 代码块）时，累计到这么多字也先按整行推一次
LONG_PARAGRAPH_CHARS = 600
# 中间态最多显示这么多字（Telegram 单条 4096）；超过后不再刷新，最终编辑会分条发完整答案
MAX_PREVIEW_CHARS = 3500
GENERATING_HINT = "✍️ 生成中…"


def stream_interval(chat_id: Any) -> float:
    try:
        is_group = chat_id is not None and int(chat_id) < 0
    except (TypeError, ValueError):
        is_group = False
    return GROUP_INTERVAL_SECONDS if is_group else PRIVATE_INTERVAL_SECONDS


def completed_paragraphs(text: str) -> str:
    """只取已经写完的段落：截到最后一个空行；一段太长时退到最后一个整行。"""

    value = str(text or "")
    cut = value.rfind("\n\n")
    if cut > 0:
        done = value[:cut]
        tail = value[cut:]
        # 空行之后又写了很长一段还没结束：退到最后一个整行
        if len(tail) >= LONG_PARAGRAPH_CHARS:
            line_cut = value.rfind("\n")
            if line_cut > cut:
                done = value[:line_cut]
        return done.rstrip()
    if len(value) >= LONG_PARAGRAPH_CHARS:
        line_cut = value.rfind("\n")
        if line_cut > 0:
            return value[:line_cut].rstrip()
    return ""


def preview_html(paragraphs: str) -> str:
    """中间态：已完成段落（HTML 转义的纯文本）+ 生成中提示。"""

    body = paragraphs.strip()
    if len(body) > MAX_PREVIEW_CHARS:
        body = body[:MAX_PREVIEW_CHARS].rstrip() + "\n…"
    return f"{html.escape(body)}\n\n<i>{GENERATING_HINT}</i>"


def stream_edit_eligible(
    *,
    pref: dict[str, Any],
    event: Any,
    cfg: dict[str, Any],
    command_mode: str,
    inline_handle: Any,
    native_image_mode: bool,
    has_images: bool,
    has_self_image: bool,
    has_self_audio: bool,
) -> bool:
    """只对「在原消息上编辑」的纯文本问答开流式；其余情况保持原行为。"""

    from ..services import inline_ai_relay

    if not bool(pref.get(STREAM_EDIT_SWITCH, False)):
        return False
    if pref.get("style") not in {inline_ai_relay.REPLY_STYLE_NORMAL, inline_ai_relay.REPLY_STYLE_EDIT_RICH}:
        return False
    if inline_handle is not None or native_image_mode or has_images or has_self_image or has_self_audio:
        return False
    if command_mode not in {"chat", "search"}:
        return False
    if str(cfg.get("send_mode") or "edit").lower() != "edit":
        return False
    return bool(getattr(event, "out", False)) and getattr(event, "chat_id", None) is not None


class _RetryAfter(Exception):
    """把 Telethon FloodWaitError（``.seconds``）翻译成节流器认识的 ``retry_after``。"""

    def __init__(self, seconds: float, original: Exception) -> None:
        super().__init__(str(original))
        self.retry_after = float(seconds or 1.0)
        self.error_code = 429


@dataclass(slots=True)
class StreamOutcome:
    """``result`` 为 ``None`` 且 ``shown`` 为 False 表示没显示任何内容，调用方改走一次性调用。"""

    result: Any = None
    used_provider: Any = None
    shown: bool = False
    failed: bool = False


async def stream_answer_into_message(
    event: Any,
    *,
    provider_dto: Any,
    provider_dtos: dict[int, Any],
    system: str,
    user_msg: str,
    override_model: str | None,
    routed_model: str | None,
    max_tokens: int,
    temperature: float | None,
    reasoning_effort: str | None,
    timeout_seconds: int | None,
    web_search: bool,
    web_search_context_size: str | None,
    account_id: int,
    triggered_by_account_id: int | None,
    source: str,
    fallback_provider_id: int | None,
    matched_tag: str | None,
    runtime_metadata: dict[str, Any] | None = None,
    stream_fn: Any = None,
) -> StreamOutcome:
    from ..services.llm_call_context import reset_current_runtime_metadata, set_current_runtime_metadata
    from ..services.llm_client import LLMResult
    from ..services.llm_protocol import MessageRole, ModelMessage, ModelRequest
    from ..services.telegram_stream import ThrottledTextPusher

    if stream_fn is None:
        from ..services.llm_invoke import stream_structured as stream_fn

    model = (
        str(override_model or "").strip()
        or str(routed_model or "").strip()
        or str(getattr(provider_dto, "default_model", "") or "").strip()
    )
    if not model and hasattr(provider_dto, "pick_enabled_model"):
        model = str(provider_dto.pick_enabled_model() or "").strip()
    if not model:
        return StreamOutcome()
    metadata: dict[str, Any] = {"model_pinned": bool(str(override_model or "").strip())}
    if timeout_seconds:
        metadata["_request_timeout_seconds"] = int(timeout_seconds)
    messages = []
    if str(system or "").strip():
        messages.append(ModelMessage.text(MessageRole.SYSTEM, system))
    messages.append(ModelMessage.text(MessageRole.USER, user_msg))
    try:
        request = ModelRequest(
            model=model,
            messages=tuple(messages),
            max_output_tokens=max(1, int(max_tokens or 512)),
            temperature=temperature,
            reasoning_effort=reasoning_effort,
            stream=True,
            web_search=bool(web_search),
            web_search_context_size=web_search_context_size,
            metadata=metadata,
        )
    except ValueError:
        return StreamOutcome()

    async def push(payload: str) -> None:
        try:
            await event.edit(payload, parse_mode="html")
        except Exception as exc:  # noqa: BLE001
            seconds = getattr(exc, "seconds", None)
            if seconds is not None and "flood" in type(exc).__name__.lower():
                raise _RetryAfter(seconds, exc) from exc
            raise

    pusher = ThrottledTextPusher(
        push,
        min_interval=stream_interval(getattr(event, "chat_id", None)),
        render=preview_html,
        name="ai-stream-edit",
    )
    outcome = StreamOutcome()
    parts: list[str] = []
    last_paragraphs = ""
    response = None
    used_provider = None
    token = set_current_runtime_metadata(runtime_metadata) if runtime_metadata is not None else None
    try:
        async for stream_event, used, _fallback in stream_fn(
            provider_dto,
            provider_dtos,
            request,
            account_id=account_id,
            triggered_by_account_id=triggered_by_account_id,
            source=source,
            fallback_provider_id=fallback_provider_id,
            matched_tag=matched_tag,
        ):
            used_provider = used
            if stream_event.delta:
                parts.append(stream_event.delta)
                paragraphs = completed_paragraphs("".join(parts))
                if paragraphs and paragraphs != last_paragraphs and len(last_paragraphs) < MAX_PREVIEW_CHARS:
                    last_paragraphs = paragraphs
                    outcome.shown = True
                    await pusher.offer(paragraphs)
            if stream_event.response is not None:
                response = stream_event.response
    except Exception as exc:  # noqa: BLE001
        await pusher.aclose()
        if not outcome.shown:
            log.info("[ai-stream] stream unavailable before first paragraph (%s); using one-shot call", exc)
            return StreamOutcome()
        from .command import _humanize_llm_error

        log.warning("[ai-stream] stream failed after partial output: %s", exc)
        try:
            await event.edit(f"✗ AI 回答中断：{_humanize_llm_error(exc)}")
        except Exception:  # noqa: BLE001
            pass
        outcome.failed = True
        return outcome
    finally:
        if token is not None:
            reset_current_runtime_metadata(token)
    # 等最后一次中间编辑结束，保证最终编辑一定在它之后
    await pusher.aclose()

    text = response.text if response is not None else "".join(parts).strip()
    if not text.strip():
        if not outcome.shown:
            return StreamOutcome()
        try:
            await event.edit("✗ AI 没有返回内容")
        except Exception:  # noqa: BLE001
            pass
        outcome.failed = True
        return outcome
    usage = getattr(response, "usage", None)
    outcome.result = LLMResult(
        text=text,
        model=str(getattr(response, "model", "") or model),
        input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        sources=[dict(item) for item in (getattr(response, "sources", None) or ())],
        reasoning=getattr(response, "reasoning_content", None),
        reasoning_tokens=int(getattr(usage, "reasoning_tokens", 0) or 0),
    )
    outcome.used_provider = used_provider
    return outcome


__all__ = [
    "STREAM_EDIT_SWITCH",
    "StreamOutcome",
    "completed_paragraphs",
    "preview_html",
    "stream_answer_into_message",
    "stream_edit_eligible",
    "stream_interval",
]
