"""插件流式编辑 / 草稿：``ctx.messages.stream_editor()`` / ``ctx.messages.stream_edit()``。

能力标记 ``ctx.messages.streaming_edit is True``（0.106.0-beta.20+）。

插件把模型流（``ctx.ai.stream_complete``）的文本交给编辑器，平台负责：

- **节流**：只保留最新快照，私聊 / inline 约 1 次/秒、群约 1.5 次/秒（可用
  ``min_interval`` 调整，限制在 0.5–10 s）；``min_chars`` 控制两次中间编辑之间的最少新增字数。
- **429**：中间编辑不等待限流（``rate_limit_wait_budget=0``），被 429 就按 ``retry_after``
  推迟下一次、用最新快照重发；“message is not modified” 静默忽略。
- **轻量中间态**：中间编辑直接走交互 Bot 的 ``editMessageText``（或私聊
  ``sendMessageDraft``），默认纯文本、不写动作记录，避免每次中间编辑都落库；超过
  4000 字时中间态只显示开头并以“…”收尾。
- **最终一次走标准动作**：``finish()`` 先停掉节流器并等在途请求结束，再通过
  ``ctx.messages.apply`` 发出最终 ``edit_message``（或草稿模式下的 ``send_message``），
  因此 Rich Message、紧凑表格、表格默认样式、inline 消息编辑、动作记录与 Trace 全部沿用
  既有链路；最终编辑失败会在结果的 ``errors`` 里带回原因。

目标：

- ``message_id``（+ ``chat_id``）或 ``inline_message_id``：编辑已有占位（例如「🤔」），``mode="edit"``；
- 只给私聊 ``chat_id``（>0）且没有占位：``mode="draft"``，用 Bot API 草稿流式显示，
  ``finish()`` 时清空草稿并发送真实消息。

中间编辑只支持交互 Bot 通道；拿不到交互 Bot token、或处于开发模式 dry-run 时，
中间更新静默跳过（``stats["disabled"]``），``finish()`` 照常执行。
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import AsyncIterable, Awaitable, Callable
from typing import Any

from ...services import account_bot_service
from ...services.telegram_stream import (
    ThrottledTextPusher,
    clamp_interval,
    default_stream_interval,
)

log = logging.getLogger(__name__)

STREAM_PREVIEW_MAX_CHARS = 4000
_VALID_PARSE_MODES = {"plain", "html"}


def _preview(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)] + "…"


class PluginStreamEditor:
    """一次流式回复的编辑器；由 ``ctx.messages.stream_editor(...)`` 创建。"""

    def __init__(
        self,
        *,
        apply: Callable[[list[dict[str, Any]]], Awaitable[dict[str, Any]]],
        token_provider: Callable[[], Awaitable[str | None]],
        chat_id: int | None = None,
        message_id: int | None = None,
        inline_message_id: str | None = None,
        mode: str = "auto",
        parse_mode: str = "plain",
        min_interval: float | None = None,
        min_chars: int = 0,
        render: Callable[[str], str] | None = None,
        max_preview_chars: int = STREAM_PREVIEW_MAX_CHARS,
        dry_run: bool = False,
    ) -> None:
        self._apply = apply
        self._token_provider = token_provider
        self.chat_id = int(chat_id) if chat_id is not None else None
        self.message_id = int(message_id) if message_id is not None else None
        self.inline_message_id = str(inline_message_id) if inline_message_id else None
        resolved = str(mode or "auto").strip().lower()
        if resolved == "auto":
            if self.message_id is not None or self.inline_message_id:
                resolved = "edit"
            elif self.chat_id is not None and self.chat_id > 0:
                resolved = "draft"
            else:
                resolved = "edit"
        if resolved not in {"edit", "draft"}:
            raise ValueError("stream_editor mode 只能是 auto / edit / draft")
        if resolved == "edit" and self.message_id is None and not self.inline_message_id:
            raise ValueError("stream_editor 编辑模式需要 message_id（配合 chat_id）或 inline_message_id")
        if resolved == "edit" and self.message_id is not None and self.chat_id is None:
            raise ValueError("stream_editor 用 message_id 编辑时需要 chat_id")
        if resolved == "draft" and (self.chat_id is None or self.chat_id <= 0):
            raise ValueError("stream_editor 草稿模式只支持私聊 chat_id（>0）")
        self.mode = resolved
        parse = str(parse_mode or "plain").strip().lower()
        if parse not in _VALID_PARSE_MODES:
            raise ValueError("stream_editor parse_mode 只能是 plain / html")
        self.parse_mode = parse
        self._max_preview_chars = max(16, min(int(max_preview_chars or STREAM_PREVIEW_MAX_CHARS), STREAM_PREVIEW_MAX_CHARS))
        self._draft_id = secrets.randbelow(2**31 - 2) + 1
        self._token: str | None = None
        self._token_loaded = False
        self._dry_run = bool(dry_run)
        self._text = ""
        self._finished = False
        self._draft_shown = False
        interval = clamp_interval(
            min_interval,
            default=default_stream_interval(self.chat_id if self.inline_message_id is None else None),
        )
        self._pusher = ThrottledTextPusher(
            self._push,
            min_interval=interval,
            min_chars=max(0, int(min_chars or 0)),
            render=render,
            name="plugin-stream-edit",
        )
        self.stats: dict[str, Any] = {"mode": self.mode, "interval": interval, "disabled": False}

    # —— 中间态 ——

    @property
    def text(self) -> str:
        """目前累计的完整文本（未截断、未渲染）。"""

        return self._text

    async def _ensure_token(self) -> str | None:
        if not self._token_loaded:
            self._token_loaded = True
            try:
                self._token = await self._token_provider()
            except Exception:  # noqa: BLE001
                log.debug("stream editor token load failed", exc_info=True)
                self._token = None
            if not self._token:
                self.stats["disabled"] = True
        return self._token

    async def _push(self, text: str) -> None:
        if self._dry_run:
            self.stats["disabled"] = True
            return
        token = await self._ensure_token()
        if not token:
            return
        preview = _preview(text, self._max_preview_chars)
        parse_mode = "HTML" if self.parse_mode == "html" else None
        if self.mode == "draft":
            assert self.chat_id is not None
            await account_bot_service.send_message_draft(
                token,
                self.chat_id,
                self._draft_id,
                preview,
                parse_mode=parse_mode,
            )
            self._draft_shown = True
            return
        payload: dict[str, Any] = {"text": preview, "disable_web_page_preview": True}
        if self.inline_message_id:
            payload["inline_message_id"] = self.inline_message_id
        else:
            payload["chat_id"] = self.chat_id
            payload["message_id"] = self.message_id
        if parse_mode:
            payload["parse_mode"] = parse_mode
        await account_bot_service.call_bot_api(
            token,
            "editMessageText",
            payload,
            rate_limit_wait_budget=0.0,
        )

    async def update(self, text: str) -> bool:
        """用完整快照更新（节流）；返回本次是否当场发出了一次中间编辑。"""

        if self._finished:
            raise RuntimeError("stream editor 已结束")
        self._text = "" if text is None else str(text)
        if not self._text.strip():
            return False
        return await self._pusher.offer(self._text)

    async def append(self, delta: str) -> bool:
        """追加一段增量文本（节流）。"""

        return await self.update(self._text + ("" if delta is None else str(delta)))

    async def consume(self, chunks: AsyncIterable[str]) -> str:
        """把一个文本增量流（如 ``ctx.ai.stream_complete(...)``）边收边显示，返回完整文本。"""

        async for chunk in chunks:
            if chunk:
                await self.append(str(chunk))
        return self._text

    # —— 收尾 ——

    async def abort(self) -> None:
        """放弃本次流式（不做最终编辑）；草稿模式会清空草稿。"""

        if self._finished:
            return
        self._finished = True
        await self._pusher.aclose()
        await self._clear_draft()

    async def _clear_draft(self) -> None:
        if self.mode != "draft" or not self._draft_shown or self._dry_run:
            return
        token = await self._ensure_token()
        if not token or self.chat_id is None:
            return
        try:
            await account_bot_service.send_message_draft(token, self.chat_id, self._draft_id, "")
        except Exception:  # noqa: BLE001
            log.debug("stream editor draft clear failed", exc_info=True)

    async def finish(
        self,
        *,
        text: str | None = None,
        parse_mode: str | None = None,
        html: str | None = None,
        markdown: str | None = None,
        blocks: list[dict[str, Any]] | None = None,
        reply_markup: dict[str, Any] | None = None,
        compact_tables: bool | None = None,
        reply_to_message_id: int | None = None,
    ) -> dict[str, Any]:
        """停掉节流器并通过标准动作发出最终内容；返回 ``ctx.messages.apply`` 的结果。

        不给任何内容参数时用累计文本（``parse_mode`` 默认沿用编辑器的）。给了
        ``html`` / ``markdown`` / ``blocks`` 之一时最终发 Rich Message。
        """

        if self._finished:
            raise RuntimeError("stream editor 已结束")
        self._finished = True
        await self._pusher.aclose()
        rich_selected = sum(value not in (None, "", []) for value in (html, markdown, blocks))
        if rich_selected > 1:
            raise ValueError("finish 只能提供 html、markdown、blocks 其中一个")
        action: dict[str, Any]
        if self.mode == "draft":
            await self._clear_draft()
            action = {"type": "send_message", "chat_id": self.chat_id, "send_via": "interaction_bot"}
            if reply_to_message_id is not None:
                action["reply_to_message_id"] = int(reply_to_message_id)
        else:
            action = {"type": "edit_message", "send_via": "interaction_bot"}
            if self.inline_message_id:
                action["inline_message_id"] = self.inline_message_id
            else:
                action["chat_id"] = self.chat_id
                action["message_id"] = self.message_id
        if rich_selected:
            rich_message: dict[str, Any] = {}
            if html not in (None, ""):
                rich_message["html"] = html
            elif markdown not in (None, ""):
                rich_message["markdown"] = markdown
            else:
                rich_message["blocks"] = blocks
            action["rich_message"] = rich_message
            if compact_tables:
                action["compact_tables"] = True
        else:
            final_text = self._text if text is None else str(text)
            if not final_text.strip():
                raise ValueError("finish 没有可发送的内容")
            action["text"] = final_text
            action["parse_mode"] = str(parse_mode or self.parse_mode)
        if reply_markup is not None:
            action["reply_markup"] = dict(reply_markup)
        result = await self._apply([action])
        out = dict(result) if isinstance(result, dict) else {"ok": bool(result)}
        out["stream"] = self.snapshot_stats()
        return out

    def snapshot_stats(self) -> dict[str, Any]:
        stats = dict(self.stats)
        stats.update(self._pusher.stats)
        stats["chars"] = len(self._text)
        return stats


async def stream_edit(
    editor: PluginStreamEditor,
    chunks: AsyncIterable[str],
    *,
    finish: bool = True,
    **finish_kwargs: Any,
) -> dict[str, Any]:
    """``consume`` + ``finish`` 的便捷组合；中途异常时放弃流式并把异常继续抛出。"""

    try:
        text = await editor.consume(chunks)
    except BaseException:
        await editor.abort()
        raise
    if not finish:
        return {"text": text, "stream": editor.snapshot_stats()}
    if not text.strip() and not any(finish_kwargs.get(key) for key in ("text", "html", "markdown", "blocks")):
        await editor.abort()
        return {"ok": False, "text": text, "error": "empty_stream", "stream": editor.snapshot_stats()}
    result = await editor.finish(**finish_kwargs)
    result["text"] = text
    return result


__all__ = ["PluginStreamEditor", "STREAM_PREVIEW_MAX_CHARS", "stream_edit"]
