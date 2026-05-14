"""Worker 命令的文本与消息格式化工具。"""
from __future__ import annotations

from typing import Any

_LONG_MESSAGE_THRESHOLD = 3900  # TG 单条上限约 4096，预留缓冲
_LONG_MESSAGE_SAFE_THRESHOLD = 3900


def _safe_exception_text(e: BaseException, max_len: int = 200) -> str:
    """把异常信息净化成可安全展示的短字符串。"""
    import re

    msg = f"{type(e).__name__}: {e}"
    msg = re.sub(r"\(?/[^()\s'\"]+\.py\)?", "<path>", msg)
    msg = re.sub(r"\(?[A-Za-z]:[\\/][^()\s'\"]+\.py\)?", "<path>", msg)
    msg = re.sub(r"sk-[A-Za-z0-9_-]{8,}", "<redacted>", msg)
    msg = re.sub(r"Bearer\s+[A-Za-z0-9_.\-]{8,}", "Bearer <redacted>", msg)
    if len(msg) > max_len:
        msg = msg[:max_len] + "…"
    return msg


def _humanize_llm_error(e: BaseException, max_len: int = 360) -> str:
    """把 LLM 调用错误翻译成用户可执行的提示。"""
    raw = str(e)
    text = _safe_exception_text(e, max_len=max_len)
    lowered = raw.lower()

    if "budget_exceeded" in lowered or "已达上限" in raw:
        return _safe_exception_text(RuntimeError(raw), max_len=max_len)
    if "usage_limit" in lowered or "quota" in lowered or "insufficient_quota" in lowered:
        return "模型服务额度已用完或账户余额不足。请更换 provider / API Key，或等待额度恢复。"
    if "429" in raw or "rate_limit" in lowered or "too many requests" in lowered:
        return "模型服务正在限流。请稍后重试，或切换到备用 provider。"
    if "401" in raw or "403" in raw or "unauthorized" in lowered or "forbidden" in lowered or "auth" in lowered:
        return "模型鉴权失败：API Key 无效、过期，或当前账号没有权限。请检查 provider 配置。"
    if "404" in raw or "model not found" in lowered:
        return "模型或接口不存在。请检查 provider endpoint、api_format 和模型名称。"
    if "timeout" in lowered:
        return "模型响应超时。请稍后重试，或调低 max_tokens / 换更快的 provider。"
    if "connect" in lowered or "network" in lowered or "proxy" in lowered or "ssl" in lowered:
        return "连接模型服务失败。请检查网络、代理和 provider endpoint。"
    if "所有 provider 都失败" in raw:
        return "所有可用 provider 都调用失败。请检查主 provider 和 fallback provider 配置。"
    return text


def _safe_log_text(text: str, max_len: int = 200) -> str:
    """把用户内容净化成可安全记录日志的形式。"""
    if not text:
        return "<empty>"
    if not isinstance(text, str):
        text = str(text)
    length = len(text)
    preview_len = max(0, max_len - 1) if len(text) > max_len else max_len
    preview = text[:preview_len] if len(text) > max_len else text
    import re

    preview = re.sub(r"sk-[A-Za-z0-9_-]{4,}", "<sk>", preview)
    if length > max_len:
        return f'<len={length}> "{preview}..."'
    return f'<len={length}> "{preview}"'


def _dto_to_fake_row(dto) -> Any:
    """将 LLMProviderDTO 转为临时 ORM 行（向后兼容 build_client）。"""
    from ...db.models.command import LLMProvider as LLMProviderModel

    return LLMProviderModel(
        id=dto.id,
        name=dto.name,
        provider=dto.provider,
        api_key_enc=dto.api_key_enc,
        base_url=dto.base_url,
        default_model=dto.default_model,
        api_format=dto.api_format,
    )


def _split_single_block(text: str, threshold: int) -> list[str]:
    """分割超长文本块，优先按换行，其次按句子。"""
    if len(text) <= threshold:
        return [text]

    lines = text.split("\n")
    current = ""
    parts: list[str] = []
    for idx, line in enumerate(lines):
        candidate = f"{current}\n{line}".strip() if current else line
        if len(candidate) <= threshold:
            current = candidate
        else:
            if current:
                parts.append(current)
            remaining = "\n".join(lines[idx:])
            if len(remaining) <= threshold:
                parts.append(remaining)
                return parts
            parts.extend(_split_by_sentence(remaining, threshold))
            return parts

    if current:
        parts.append(current)
    return parts


def _split_by_sentence(text: str, threshold: int) -> list[str]:
    """按句子分割超长文本。"""
    import re

    sentences = re.split(r"([。！？.!?\n])", text)
    if len(sentences) <= 1:
        return [text[i : i + threshold] for i in range(0, len(text), threshold)]
    current = ""
    result_parts: list[str] = []

    for i in range(0, len(sentences) - 1, 2):
        sent = sentences[i] + (sentences[i + 1] if i + 1 < len(sentences) else "")
        if len(current) + len(sent) <= threshold:
            current += sent
        else:
            if current:
                result_parts.append(current)
            if len(sent) > threshold:
                result_parts.extend(
                    sent[i : i + threshold] for i in range(0, len(sent), threshold)
                )
                current = ""
            else:
                current = sent

    if current:
        result_parts.append(current)

    return result_parts


def _split_long_message(
    text: str,
    threshold: int = _LONG_MESSAGE_THRESHOLD,
) -> list[str]:
    """将长文本分割成多个短段。"""
    if len(text) <= threshold:
        return [text]

    parts: list[str] = []
    paragraphs = text.split("\n\n")
    current = ""
    for para in paragraphs:
        if len(current) + len(para) + 2 <= threshold:
            current = (current + "\n\n" + para).strip()
        else:
            if current:
                parts.append(current)
                current = ""
            if len(para) > threshold:
                parts.extend(_split_single_block(para, threshold))
            else:
                current = para

    if current:
        parts.append(current)

    return parts


def _ensure_html_safe(text: str) -> str:
    """确保 HTML 文本安全（避免截断导致标签不闭合）。"""
    import re

    unclosed_patterns = [
        (r"<b>(?!</b>)", "</b>"),
        (r"<i>(?!</i>)", "</i>"),
        (r"<code>(?!</code>)", "</code>"),
        (r"<pre>(?!</pre>)", "</pre>"),
        (r"<blockquote>(?!</blockquote>)", "</blockquote>"),
    ]

    result = text
    for pattern, closing in unclosed_patterns:
        if re.search(pattern, result) and closing not in result:
            result = result + "\n" + closing

    return result


async def _send_long_message(
    client,
    chat_id: int,
    text: str,
    first_msg_id: int | None,
    parse_mode: str | None = None,
    *,
    _max_chunk: int = _LONG_MESSAGE_THRESHOLD,
) -> None:
    """发送长消息，自动分段。"""
    chunks = _split_long_message(text, _max_chunk)

    if not chunks:
        return

    first = chunks[0]
    remaining = chunks[1:]

    if first_msg_id:
        try:
            await client.edit_message(chat_id, first_msg_id, first, parse_mode=parse_mode)
        except Exception:
            try:
                await client.edit_message(chat_id, first_msg_id, first)
            except Exception:
                await client.send_message(chat_id, first)
    else:
        await client.send_message(chat_id, first, parse_mode=parse_mode)

    for chunk in remaining:
        if parse_mode == "html":
            chunk = _ensure_html_safe(chunk)
        try:
            await client.send_message(chat_id, chunk, parse_mode=parse_mode)
        except Exception:
            try:
                await client.send_message(chat_id, chunk)
            except Exception:
                pass


def _replied_media_placeholder(msg: Any) -> str:
    """被回复消息没正文（媒体类）时返回占位字符串。"""
    if getattr(msg, "photo", None) is not None:
        return "📷 [图片]"
    if getattr(msg, "video_note", None) is not None:
        return "📹 [视频留言]"
    if getattr(msg, "video", None) is not None:
        return "🎬 [视频]"
    if getattr(msg, "voice", None) is not None:
        return "🎤 [语音]"
    if getattr(msg, "sticker", None) is not None:
        return "[贴纸]"
    if getattr(msg, "audio", None) is not None:
        return "🎵 [音频]"
    if getattr(msg, "gif", None) is not None:
        return "🖼️ [GIF]"
    if getattr(msg, "document", None) is not None:
        return "📎 [文件]"
    if getattr(msg, "geo", None) is not None:
        return "📍 [位置]"
    if getattr(msg, "contact", None) is not None:
        return "👤 [联系人]"
    if getattr(msg, "poll", None) is not None:
        return "📊 [投票]"
    return ""
