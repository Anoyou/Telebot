"""PagerMaid translate 逻辑的 Telethon 重写示例。"""

from __future__ import annotations

from typing import Any

from app.db.models.command import LLMProvider as LLMProviderModel
from app.worker.command import get_command_context
from app.worker.plugins.base import Plugin, register


_LANG_MAP = {
    "zh": "Chinese",
    "zh-cn": "Chinese (Simplified)",
    "zh-tw": "Chinese (Traditional)",
    "en": "English",
    "ja": "Japanese",
    "jp": "Japanese",
    "ko": "Korean",
    "fr": "French",
    "de": "German",
    "es": "Spanish",
    "ru": "Russian",
    "it": "Italian",
    "pt": "Portuguese",
    "ar": "Arabic",
    "th": "Thai",
    "vi": "Vietnamese",
}


def _normalize_target(raw: str | None) -> tuple[str, bool]:
    token = (raw or "").strip().lower()
    if token in ("", "zh"):
        return "Chinese", False
    if token == "auto":
        return "auto", True
    return _LANG_MAP.get(token, raw.strip()), False


def _pick_provider(providers: dict[int, dict[str, Any]]) -> tuple[int, dict[str, Any]] | None:
    if not providers:
        return None
    for pid, p in providers.items():
        tags = {str(t).strip().lower() for t in (p.get("tags") or []) if isinstance(t, str)}
        if "translate" in tags and (p.get("api_key_enc") or str(p.get("provider")) == "ollama"):
            return int(pid), p
    for pid, p in providers.items():
        if p.get("api_key_enc") or str(p.get("provider")) == "ollama":
            return int(pid), p
    return None


async def fy_handler(client, event, args, account_id, ctx):
    replied = await event.get_reply_message()
    if replied is None:
        await event.edit("用法：回复一条消息后发送 ,fy <lang|auto>，例如 ,fy zh 或 ,fy auto")
        return

    source_text = (replied.raw_text or replied.message or "").strip()
    if not source_text:
        await event.edit("✗ 被回复消息没有可翻译文本")
        return

    target_raw = args[0] if args else "zh"
    target_lang, auto_mode = _normalize_target(target_raw)

    cctx = get_command_context()
    if cctx is None or not cctx.providers:
        await event.edit("✗ 当前账号没有可用的 LLM Provider")
        return

    chosen = _pick_provider(cctx.providers)
    if chosen is None:
        await event.edit("✗ 没找到可用 provider（请在系统设置里配置 API Key）")
        return
    provider_id, provider_dict = chosen

    system_prompt = (
        "你是翻译助手。只输出译文本身，不要解释。"
        if not auto_mode
        else "你是翻译助手。先自动识别原文语言，再翻译成自然中文。只输出译文。"
    )
    user_prompt = (
        f"把下面文本翻译成 {target_lang}：\n\n{source_text}"
        if not auto_mode
        else f"请自动识别语言并翻译：\n\n{source_text}"
    )

    await event.edit(f"翻译中... ({provider_dict.get('name', provider_id)})")

    from app.services.llm_client import LLMError, build_client

    fake_row = LLMProviderModel(
        id=provider_id,
        name=str(provider_dict.get("name", "")),
        provider=str(provider_dict.get("provider", "")),
        api_key_enc=provider_dict.get("api_key_enc"),
        base_url=provider_dict.get("base_url"),
        default_model=str(provider_dict.get("default_model", "")),
    )

    try:
        llm = build_client(
            fake_row,
            override_model=provider_dict.get("default_model"),
            proxy_url=provider_dict.get("proxy_url"),
        )
        result = await llm.complete(system_prompt, user_prompt, max_tokens=1024)
    except LLMError as e:
        await event.edit(f"✗ 翻译失败：{e}")
        return
    except Exception as e:  # noqa: BLE001
        await event.edit(f"✗ 翻译失败：{type(e).__name__}: {str(e)[:100]}")
        return

    text = (result.text or "").strip()
    if not text:
        await event.edit("✗ 模型返回空结果")
        return

    await event.edit(text)


@register
class TranslatePlugin(Plugin):
    key = "translate"
    display_name = "翻译助手"
    commands = {"fy": fy_handler}


__all__ = ["TranslatePlugin"]
