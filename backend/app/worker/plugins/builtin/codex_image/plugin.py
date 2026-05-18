"""Codex 图片生成插件 — 通过 Codex API 调用 GPT 图片生成模型。

功能：
  - 纯文本生成图片：,{command} 提示词
  - 参考图+提示词生成：回复图片后 ,{command} 提示词
  - Token 管理：,{command} token <token> 保存 / ,{command} token 查看

配置存储：
  - command / access_token / model / max_wait_seconds 等存储在 account_feature.config
  - 前端通过独立配置页管理，保留 schema 字段用于表单和校验

技术要点：
  - 流式 SSE 读取 Codex 响应，支持 partial_image_b64 逐步获取
  - 若流式结束后仍在 in_progress，轮询 GET 接口直到完成
  - 超时保护：默认 10 分钟
  - 图片发送后删除原命令消息

来源：TeleBox_Plugins/codex_image → 适配 TeleBot 插件框架
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import html
import io
import json
import re
import time
from typing import Any

import httpx

from app.worker.command import current_command_prefix
from app.worker.plugins.base import Plugin, PluginContext, register

# ─── 常量 ───────────────────────────────────────────────

CODEX_URL = "https://chatgpt.com/backend-api/codex/responses"
DEFAULT_MODEL = "gpt-5.5"
CODEX_TOOL_FALLBACK_MODEL = "gpt-5.4"
DEFAULT_IMAGE_MODEL = "auto"  # 底层图片模型，auto 表示由 OpenAI 自动选择
DEFAULT_MAX_WAIT = 600  # 10 分钟
DEFAULT_COMMAND = "cximg"
DEFAULT_STATUS_INTERVAL = 20
DEFAULT_INSTRUCTIONS = "You are a helpful assistant. Use tools when available."
DEFAULT_REASONING_EFFORT = "low"
DEFAULT_MESSAGE_TEMPLATE = (
    "<b>🎨 Codex 图片生成</b>\n"
    "<b>状态:</b> {status}\n"
    "<b>提示词:</b> {prompt}\n"
    "<b>主模型:</b> {model} · <b>图片模型:</b> {image_model}\n"
    "<b>尺寸:</b> {image_size} · <b>比例:</b> {aspect_ratio} · <b>格式:</b> {image_format}\n"
    "<b>耗时:</b> {elapsed}"
    "{?revised_prompt}\n<b>修订提示词:</b> {revised_prompt}{/?}"
)
DEFAULT_IMAGE_SIZE = "1024x1024"
DEFAULT_ASPECT_RATIO = "1:1"
DEFAULT_IMAGE_FORMAT = "png"

# 支持的主模型列表（支持 image_generation 工具）
SUPPORTED_MAIN_MODELS = [
    "gpt-4o", "gpt-4o-mini",
    "gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano",
    "o3",
    "gpt-5", "gpt-5-nano",
    "gpt-5.2", "gpt-5.4-mini", "gpt-5.4-nano",
    "gpt-5.5",
]

# 支持的底层图片模型
SUPPORTED_IMAGE_MODELS = ["auto", "gpt-image-2", "gpt-image-1.5", "gpt-image-1", "gpt-image-1-mini"]

SUPPORTED_IMAGE_SIZES = {"auto", "1024x1024", "1536x1024", "1024x1536", "from_reference"}
SUPPORTED_ASPECT_RATIOS = {"auto", "1:1", "3:2", "2:3", "4:3", "3:4", "16:9", "9:16", "from_reference"}
SUPPORTED_IMAGE_FORMATS = {"png", "jpeg", "webp"}
LEGACY_MODEL_ALIASES = {
    "gpt-5.4": DEFAULT_MODEL,
}
_STREAM_RECOVERABLE_ERRORS = (
    httpx.RemoteProtocolError,
    httpx.ReadError,
    httpx.ReadTimeout,
    httpx.ConnectError,
    httpx.ConnectTimeout,
)
_CODEX_HEADERS_BASE = {
    "User-Agent": "TelePilot codex_image/1.1",
    "Origin": "https://chatgpt.com",
    "Referer": "https://chatgpt.com/codex",
}

# ─── 工具函数 ───────────────────────────────────────────


def _html_escape(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )


def _strip_html_tags(text: str) -> str:
    """HTML 解析失败时兜底展示纯文本，避免把标签原样发到 Telegram。"""
    return re.sub(r"</?[^>]+>", "", str(text or ""))


def _looks_like_html(text: str, content_type: str = "") -> bool:
    sample = str(text or "").lstrip()[:300].lower()
    ctype = str(content_type or "").lower()
    return "text/html" in ctype or sample.startswith("<!doctype html") or sample.startswith("<html")


def _compact_html_text(text: str, max_len: int = 160) -> str:
    cleaned = _strip_html_tags(text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len] + "…"
    return cleaned


async def _edit_html(event: Any, text: str) -> None:
    """编辑消息并按 HTML 解析；模板写坏时退回纯文本。"""
    try:
        await event.edit(text, parse_mode="html")
    except Exception:
        await event.edit(_strip_html_tags(text))


def _safe_error_text(text: str, max_len: int = 500) -> str:
    """脱敏可展示错误文本：隐藏 token / 本地路径 / 过长原文。"""
    out = str(text or "")
    out = re.sub(r"\(?/[^()\s'\"]+\.(?:py|json|env|db|session)\)?", "<path>", out)
    out = re.sub(r"\(?[A-Za-z]:[\\/][^()\s'\"]+\.(?:py|json|env|db|session)\)?", "<path>", out)
    out = re.sub(r"sk-[A-Za-z0-9_-]{8,}", "<redacted>", out)
    out = re.sub(r"Bearer\s+[A-Za-z0-9_.\-]{8,}", "Bearer <redacted>", out)
    out = re.sub(
        r"(?i)(access[_-]?token|api[_-]?key|secret|token)\s*[=:]\s*['\"]?[A-Za-z0-9_.\-]{8,}['\"]?",
        r"\1=<redacted>",
        out,
    )
    if len(out) > max_len:
        out = out[:max_len] + "…"
    return out


def _with_error_prefix(text: str) -> str:
    msg = str(text or "").strip()
    if msg.startswith("❌"):
        return msg
    return f"❌ {msg}" if msg else "❌ 操作失败"


def _is_probably_image_base64(value: str) -> bool:
    raw = str(value or "").strip()
    if len(raw) < 64:
        return False
    if not re.fullmatch(r"[A-Za-z0-9+/=\s_-]+", raw):
        return False
    normalized = raw.replace("-", "+").replace("_", "/")
    normalized = re.sub(r"\s+", "", normalized)
    padding = "=" * (-len(normalized) % 4)
    try:
        head = base64.b64decode((normalized + padding)[:256], validate=False)
    except (binascii.Error, ValueError):
        return False
    return (
        head.startswith(b"\x89PNG\r\n\x1a\n")
        or head.startswith(b"\xff\xd8\xff")
        or (head[:4] == b"RIFF" and head[8:12] == b"WEBP")
    )


def _extract_codex_artifacts(value: Any) -> tuple[str | None, str | None, str | None]:
    """Extract image/error fields from Codex/Responses shaped payloads."""
    image_base64: str | None = None
    revised_prompt: str | None = None
    error_info: str | None = None

    def visit(node: Any, parent_type: str = "") -> None:
        nonlocal image_base64, revised_prompt, error_info
        if node is None:
            return
        if isinstance(node, list):
            for item in node:
                visit(item, parent_type)
            return
        if not isinstance(node, dict):
            return

        node_type = str(node.get("type") or parent_type or "")
        for key in ("partial_image_b64", "image_base64", "b64_json"):
            candidate = node.get(key)
            if isinstance(candidate, str) and candidate:
                image_base64 = candidate
        result = node.get("result")
        if (
            isinstance(result, str)
            and result
            and ("image_generation" in node_type or _is_probably_image_base64(result))
        ):
            image_base64 = result

        revised = node.get("revised_prompt")
        if isinstance(revised, str) and revised:
            revised_prompt = revised

        err = node.get("error")
        if isinstance(err, dict):
            error_info = str(err.get("message") or err.get("code") or err)
        elif isinstance(err, str) and err:
            error_info = err

        for nested in node.values():
            if isinstance(nested, (dict, list)):
                visit(nested, node_type)

    visit(value)
    return image_base64, revised_prompt, error_info


def _summarize_codex_sse_event(obj: dict[str, Any]) -> dict[str, Any]:
    event_type = str(obj.get("type") or "")
    response = obj.get("response") if isinstance(obj.get("response"), dict) else {}
    item = obj.get("item") if isinstance(obj.get("item"), dict) else {}
    output = response.get("output") if isinstance(response, dict) else None
    output_types: list[str] = []
    if isinstance(output, list):
        output_types = [
            str(part.get("type") or "")
            for part in output
            if isinstance(part, dict) and part.get("type")
        ]
    image_base64, revised_prompt, error_info = _extract_codex_artifacts(obj)
    return {
        "event_type": event_type,
        "response_id": response.get("id") or obj.get("response_id") or obj.get("id"),
        "response_status": response.get("status") or obj.get("status"),
        "response_model": response.get("model"),
        "item_type": item.get("type"),
        "item_status": item.get("status"),
        "output_types": output_types[:10],
        "has_image": bool(image_base64),
        "has_revised_prompt": bool(revised_prompt),
        "has_error": bool(error_info),
    }


def _format_seconds(seconds: Any) -> str | None:
    try:
        total = max(0, int(seconds))
    except (TypeError, ValueError):
        return None
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}天")
    if hours:
        parts.append(f"{hours}小时")
    if minutes and len(parts) < 2:
        parts.append(f"{minutes}分钟")
    return "".join(parts) or "不到1分钟"


def _humanize_codex_error(status_code: int, detail: str, *, content_type: str = "") -> str:
    """把 Codex API 错误翻译成人话，避免原始 JSON 和敏感信息外泄。"""
    safe_detail = _safe_error_text(detail)
    if _looks_like_html(detail, content_type):
        html_hint = _compact_html_text(detail)
        status_hint = f"HTTP {status_code}" if status_code else "非 JSON/SSE 响应"
        return (
            f"❌ Codex 返回了网页而不是 API 数据（{status_hint}）。\n"
            "💡 通常是 Access Token 失效/复制错、ChatGPT 登录态过期、或网络/代理被防护页拦截。"
            "请重新从 `.codex/auth.json` 复制 `access_token` 保存；如果后端跑在 Docker/服务器里，也要确认它能通过可用代理访问 `chatgpt.com`。\n"
            f"页面摘要：{_safe_error_text(html_hint)}"
        )
    payload: Any = None
    try:
        payload = json.loads(detail)
    except Exception:
        payload = None
    err = payload.get("error") if isinstance(payload, dict) else None
    err_type = str(err.get("type") or "") if isinstance(err, dict) else ""
    message = _safe_error_text(str(err.get("message") or "")) if isinstance(err, dict) else ""
    lowered_detail = safe_detail.lower()

    if (
        "safety" in lowered_detail
        or "content_policy" in lowered_detail
        or "unsafe" in lowered_detail
        or "safety_violations" in lowered_detail
        or "rejected by the safety system" in lowered_detail
    ):
        violations = ""
        match = re.search(r"safety_violations=\[([^\]]+)\]", safe_detail)
        if match:
            violations = f"（命中类别：{_safe_error_text(match.group(1))}）"
        return (
            f"❌ 图片被 Codex 安全审核拦截了{violations}。\n"
            "💡 提示词或参考图可能触发了内容安全策略。可以换一种非暴力、非伤害的描述，"
            "或更换参考图后再试。"
        )

    if err_type == "usage_limit_reached":
        plan = _safe_error_text(str(err.get("plan_type") or "unknown")) if isinstance(err, dict) else "unknown"
        resets = _format_seconds(err.get("resets_in_seconds") if isinstance(err, dict) else None)
        suffix = f"预计 {resets} 后恢复。" if resets else "请稍后再试。"
        return (
            f"❌ Codex 额度已用完（当前计划：{plan}）。{suffix}\n"
            f"💡 这个月的免费/付费额度用光了。可以更换 Access Token，或等额度周期重置。"
        )

    if status_code in {401, 403}:
        hint = ""
        if message:
            lowered = message.lower()
            if "expired" in lowered or "过期" in lowered:
                hint = "Token 已过期，需要重新登录 Codex 获取新的 Token。"
            elif "invalid" in lowered or "无效" in lowered:
                hint = "Token 格式不对或已失效，可能复制时少了字符。"
            elif "permission" in lowered or "权限" in lowered:
                hint = "当前账号没有图片生成权限，可能需要升级计划。"
            else:
                hint = f"Codex 说：{message}"
        if not hint:
            hint = "Access Token 无效、过期，或当前账号没有权限。"
        return (
            f"❌ Codex 鉴权失败：{hint}\n"
            f"💡 去配置页重新保存 Token 试试，或者重新登录 Codex 拿新 Token。"
        )

    if status_code == 429:
        return (
            "❌ Codex 请求太频繁，被限流了。\n"
            "💡 等几分钟再试。如果经常出现，说明当前 Token 的并发/频率配额不够，可以换个 Token。"
        )

    if status_code == 404:
        model_hint = ""
        if message:
            lowered = message.lower()
            if "model" in lowered:
                model_hint = "你配置的模型名可能写错了，或者 OpenAI 已经下线了这个模型。"
        if not model_hint:
            model_hint = "接口地址可能变了，或者模型名不对。"
        return (
            f"❌ Codex 接口或模型不可用。\n"
            f"💡 {model_hint}检查一下配置页的模型名称和接口地址。"
        )

    if status_code >= 500:
        return (
            f"❌ Codex 服务端出问题了（HTTP {status_code}）。\n"
            f"💡 这是 OpenAI 那边的问题，不是你的锅。等几分钟再试，一般会自动恢复。"
        )

    if message:
        # 尝试根据常见错误消息给出建议
        lowered = message.lower()
        if "safety" in lowered or "content_policy" in lowered or "unsafe" in lowered:
            return (
                "❌ 图片被 Codex 安全审核拦截了。\n"
                "💡 提示词可能触发了内容安全策略，换个描述试试。"
            )
        if "timeout" in lowered or "timed out" in lowered:
            return (
                "❌ Codex 生成超时了。\n"
                "💡 可能是图片太复杂或服务器繁忙，试试简化提示词，或把最大等待时间调大。"
            )
        if "quota" in lowered or "insufficient" in lowered:
            return (
                "❌ Codex 余额不足。\n"
                "💡 账户没钱了或额度用完了，去 OpenAI 后台看看，或者换个有额度的 Token。"
            )
        return f"❌ Codex 请求失败：{message}"

    return f"❌ Codex 请求失败（HTTP {status_code}）：{safe_detail}"


def _humanize_codex_exception(exc: BaseException) -> str:
    """把非 HTTP 状态类异常转成用户可执行提示。"""
    raw = str(exc)
    lowered = raw.lower()
    if isinstance(exc, _STREAM_RECOVERABLE_ERRORS) or "incomplete chunked read" in lowered:
        return (
            "❌ Codex 流式连接中断了，没完整收到响应。\n"
            "💡 这次任务已经无法继续补全，因为 Codex 图片结果只在流式响应里返回。"
            "请稍后重试；如果经常出现，通常是网络、代理或上游连接不稳定。"
        )
    if "timeout" in lowered or isinstance(exc, TimeoutError):
        return (
            "❌ Codex 响应超时了。\n"
            "💡 可能是服务器太忙或图片生成太慢。稍后再试，或者在配置页把最大等待时间调大一点。"
        )
    if "proxy" in lowered or "connect" in lowered or "network" in lowered or "ssl" in lowered:
        return (
            "❌ 连接 Codex 服务器失败了。\n"
            "💡 检查一下网络是否正常、代理配置是否正确，或者 OpenAI 服务是否在维护。"
        )
    return f"❌ 生成失败：{_safe_error_text(raw)}"


def _mask_token(token: str) -> str:
    """遮蔽 token，只显示首尾几位。"""
    if not token:
        return "(未配置)"
    if len(token) <= 10:
        return f"{token[:2]}***{token[-2:]}"
    return f"{token[:4]}***{token[-4:]}"


def _format_duration(ms: float) -> str:
    """毫秒转人类可读时长。"""
    total_seconds = max(0, round(ms / 1000))
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    if minutes <= 0:
        return f"{seconds}秒"
    return f"{minutes}分{seconds}秒"


def _get_config_value(ctx: PluginContext, key: str, default: Any = None) -> Any:
    """从 ctx.config 获取配置值。"""
    cfg = ctx.config or {}
    return cfg.get(key, default)


def _normalize_main_model(value: Any) -> str:
    model = str(value or "").strip()
    model = LEGACY_MODEL_ALIASES.get(model, model)
    return model if model in SUPPORTED_MAIN_MODELS else DEFAULT_MODEL


def _command_name(ctx: PluginContext) -> str:
    value = str(_get_config_value(ctx, "command", DEFAULT_COMMAND) or DEFAULT_COMMAND).strip()
    return value or DEFAULT_COMMAND


def _normalize_choice(value: Any, allowed: set[str], default: str) -> str:
    out = str(value or "").strip().lower()
    return out if out in allowed else default


def _normalize_size(value: Any, reference_size: str | None = None) -> str:
    """标准化图片尺寸，支持 from_reference 使用参考图尺寸。"""
    aliases = {
        "square": "1024x1024",
        "landscape": "1536x1024",
        "portrait": "1024x1536",
        "横图": "1536x1024",
        "竖图": "1024x1536",
        "方图": "1024x1024",
        "原图": "from_reference",
        "参考图": "from_reference",
    }
    raw = str(value or "").strip().lower()
    normalized = aliases.get(raw, raw)

    # 如果是 from_reference 且有参考图尺寸，使用参考图尺寸
    if normalized == "from_reference":
        return reference_size if reference_size else DEFAULT_IMAGE_SIZE

    return _normalize_choice(normalized, SUPPORTED_IMAGE_SIZES, DEFAULT_IMAGE_SIZE)


def _normalize_aspect_ratio(value: Any, reference_ratio: str | None = None) -> str:
    """标准化画面比例，支持 from_reference 使用参考图比例。"""
    raw = str(value or "").strip().lower().replace("：", ":")

    # 别名处理
    aliases = {
        "原图": "from_reference",
        "参考图": "from_reference",
    }
    normalized = aliases.get(raw, raw)

    # 如果是 from_reference 且有参考图比例，使用参考图比例；无参考图则 fallback 到默认
    if normalized == "from_reference":
        return reference_ratio if reference_ratio else DEFAULT_ASPECT_RATIO

    return _normalize_choice(normalized, SUPPORTED_ASPECT_RATIOS, DEFAULT_ASPECT_RATIO)


def _normalize_image_format(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw == "jpg":
        raw = "jpeg"
    return _normalize_choice(raw, SUPPORTED_IMAGE_FORMATS, DEFAULT_IMAGE_FORMAT)


def _parse_generation_args(
    args: list[str], 
    ctx: PluginContext, 
    reference_size: str | None = None,
    reference_ratio: str | None = None,
) -> tuple[str, dict[str, str]]:
    """解析命令级覆盖项，返回清理后的 prompt 和图片选项。
    
    Args:
        args: 命令参数列表
        ctx: 插件上下文
        reference_size: 参考图尺寸（如 "1024x1024"），用于 from_reference
        reference_ratio: 参考图比例（如 "1:1"），用于 from_reference
    """
    opts = {
        "image_size": _normalize_size(_get_config_value(ctx, "image_size", DEFAULT_IMAGE_SIZE), reference_size),
        "aspect_ratio": _normalize_aspect_ratio(_get_config_value(ctx, "aspect_ratio", DEFAULT_ASPECT_RATIO), reference_ratio),
        "image_format": _normalize_image_format(_get_config_value(ctx, "image_format", DEFAULT_IMAGE_FORMAT)),
    }
    key_map = {
        "--size": "image_size",
        "-s": "image_size",
        "--resolution": "image_size",
        "--分辨率": "image_size",
        "size": "image_size",
        "resolution": "image_size",
        "分辨率": "image_size",
        "--ratio": "aspect_ratio",
        "-r": "aspect_ratio",
        "--aspect": "aspect_ratio",
        "--比例": "aspect_ratio",
        "ratio": "aspect_ratio",
        "aspect": "aspect_ratio",
        "比例": "aspect_ratio",
        "--format": "image_format",
        "-f": "image_format",
        "--格式": "image_format",
        "format": "image_format",
        "格式": "image_format",
    }
    prompt_parts: list[str] = []
    i = 0
    while i < len(args):
        token = args[i]
        key = token
        value: str | None = None
        if "=" in token:
            key, value = token.split("=", 1)
        normalized_key = key_map.get(key.lower())
        if normalized_key:
            if value is None and i + 1 < len(args):
                value = args[i + 1]
                i += 1
            if value:
                if normalized_key == "image_size":
                    opts["image_size"] = _normalize_size(value, reference_size)
                elif normalized_key == "aspect_ratio":
                    opts["aspect_ratio"] = _normalize_aspect_ratio(value, reference_ratio)
                elif normalized_key == "image_format":
                    opts["image_format"] = _normalize_image_format(value)
            i += 1
            continue
        prompt_parts.append(token)
        i += 1
    return " ".join(prompt_parts).strip(), opts


def _effective_prompt(
    prompt: str,
    aspect_ratio: str,
    image_size: str,
    image_format: str,
    *,
    has_reference: bool = False,
) -> str:
    task_hint = (
        "任务：基于随请求附带的参考图生成或编辑一张新图片。"
        "参考图只用于视觉参考；不要回答图片里的问题，不要解释图片内容，不要输出文字分析。"
        "请直接生成图片。"
        if has_reference
        else "任务：根据用户提示词生成一张新图片。不要只输出绘图提示词、故事或文字分析，请直接生成图片。"
    )
    hints: list[str] = []
    if aspect_ratio and aspect_ratio != "auto":
        hints.append(f"画面比例要求：{aspect_ratio}。")
    if image_size and image_size not in {"auto", "from_reference"}:
        hints.append(f"图片尺寸要求：{image_size}。")
    if image_format and image_format != "png":
        hints.append(f"输出格式偏好：{image_format}。")
    parts = [task_hint, f"用户提示词：{prompt}"]
    if hints:
        parts.append("\n".join(hints))
    return "\n\n".join(parts)


def _extract_pseudo_image_prompt(text: Any) -> str | None:
    raw = str(text or "")
    if "image_generation" not in raw:
        return None
    match = re.search(r"<image_generation\b[^>]*\bprompt=(['\"])(.*?)\1", raw, re.S)
    if not match:
        return None
    prompt = html.unescape(match.group(2)).strip()
    return prompt or None


def _extract_text_fallback_prompt(text: Any) -> str | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    pseudo = _extract_pseudo_image_prompt(raw)
    if pseudo:
        return pseudo
    candidates = [
        r"(?:英文版\s*prompt|English\s*prompt)\s*[:：]\s*(.+)$",
        r"(?:提示词|prompt)\s*[:：]\s*(.+?)(?:\n\s*\n|$)",
    ]
    for pattern in candidates:
        match = re.search(pattern, raw, re.I | re.S)
        if not match:
            continue
        prompt = re.sub(r"[*_`>]+", "", match.group(1)).strip()
        prompt = re.split(r"\n\s*(?:\*\*?[^:\n]+[:：]|\w+[:：])", prompt, maxsplit=1)[0].strip()
        if prompt:
            return prompt[:1000]
    return None


def _render_message(template: str, values: dict[str, Any], *, limit: int = 4000) -> str:
    """渲染用户消息模板；只转义占位符值，保留模板里的 HTML 标签。"""
    from app.services.llm_format import render_output

    text = render_output(template or DEFAULT_MESSAGE_TEMPLATE, values, escape_format="html")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _image_ext_from_bytes(data: bytes, preferred_format: str = DEFAULT_IMAGE_FORMAT) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if preferred_format == "jpeg":
        return ".jpg"
    if preferred_format == "webp":
        return ".webp"
    return ".png"


async def _update_account_config(ctx: PluginContext, key: str, value: Any) -> None:
    """更新 account_feature.config 中的某个字段并持久化。"""
    from sqlalchemy import select

    from app.db.base import AsyncSessionLocal
    from app.db.models.feature import AccountFeature

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(AccountFeature).where(
                AccountFeature.account_id == ctx.account_id,
                AccountFeature.feature_key == ctx.feature_key,
            )
        )
        af = result.scalar_one_or_none()
        if af:
            af.config = {**(af.config or {}), key: value}
            await db.commit()


# ─── Codex API 调用 ────────────────────────────────────


async def _call_codex_image(
    prompt: str,
    token: str,
    model: str = DEFAULT_MODEL,
    image_model: str = DEFAULT_IMAGE_MODEL,
    reference_image: dict[str, str] | None = None,
    update_status: Any | None = None,
    max_wait: int = DEFAULT_MAX_WAIT,
    instructions: str = DEFAULT_INSTRUCTIONS,
    reasoning_effort: str = DEFAULT_REASONING_EFFORT,
    image_size: str = DEFAULT_IMAGE_SIZE,
    image_format: str = DEFAULT_IMAGE_FORMAT,
    poll_interval: int = DEFAULT_STATUS_INTERVAL,
    debug_log: Any | None = None,
) -> dict[str, str | None]:
    """调用 Codex 图片生成 API。

    Args:
        prompt: 生成提示词
        token: Bearer token
        model: 主模型名（如 gpt-5.5）
        image_model: 底层图片模型（如 gpt-image-2），auto 表示自动选择
        reference_image: 参考图 {base64, mime_type, width, height}，可选
        update_status: 异步状态回调 async (text) -> None
        max_wait: 最大等待秒数

    Returns:
        {image_base64, revised_prompt, status, response_id}
    """
    deadline = time.monotonic() + max_wait
    poll_interval = max(10, min(300, int(poll_interval or DEFAULT_STATUS_INTERVAL)))

    # 构建请求体
    content = prompt
    if reference_image:
        content = [
            {"type": "input_text", "text": prompt},
            {
                "type": "input_image",
                "image_url": f"data:{reference_image['mime_type']};base64,{reference_image['base64']}",
            },
        ]

    # ChatGPT/Codex 内部端点只接受最小工具声明；model/size/output_format
    # 这类 Responses 公网 API 扩展字段会让工具失效，进而退化成纯文本输出。
    image_tool: dict[str, Any] = {"type": "image_generation"}

    payload = {
        "model": model,
        "instructions": instructions or DEFAULT_INSTRUCTIONS,
        "input": [{"role": "user", "content": content}],
        # ChatGPT/Codex OAuth 端点要求 store=false；图片结果必须从 SSE 流里取。
        "store": False,
        "tools": [image_tool],
        "reasoning": {"effort": reasoning_effort or DEFAULT_REASONING_EFFORT},
        "include": [],
        "tool_choice": "auto",
        "parallel_tool_calls": True,
        "prompt_cache_key": None,
        "stream": True,
    }

    headers = {
        **_CODEX_HEADERS_BASE,
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream, application/json",
    }

    result: dict[str, str | None] = {
        "image_base64": None,
        "revised_prompt": None,
        "status": None,
        "response_id": None,
        "debug_output_types": None,
        "debug_last_event_type": None,
        "debug_text_sample": None,
    }
    stream_error: BaseException | None = None

    # ── 流式读取 SSE ──────────────────────────────────
    for stream_attempt in range(2):
        remaining_timeout = max(1.0, deadline - time.monotonic())
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(remaining_timeout)) as client:
                async with client.stream("POST", CODEX_URL, json=payload, headers=headers) as resp:
                    if resp.status_code != 200:
                        body = await resp.aread()
                        raise CodexApiError(
                            resp.status_code,
                            body.decode("utf-8", errors="replace")[:500],
                            content_type=resp.headers.get("content-type", ""),
                        )

                    content_type = resp.headers.get("content-type", "")
                    if "html" in content_type.lower():
                        body = await resp.aread()
                        raise CodexApiError(
                            resp.status_code,
                            body.decode("utf-8", errors="replace")[:500],
                            content_type=content_type,
                        )

                    buffer = ""
                    async for raw_chunk in resp.aiter_text():
                        buffer += raw_chunk

                        while "\n\n" in buffer:
                            raw_event, buffer = buffer.split("\n\n", 1)
                            data_lines = [
                                line[6:].strip()
                                for line in raw_event.splitlines()
                                if line.startswith("data: ") and line[6:].strip()
                            ]

                            for data_line in data_lines:
                                if data_line == "[DONE]":
                                    continue
                                try:
                                    obj = json.loads(data_line)
                                except (json.JSONDecodeError, ValueError):
                                    continue

                                event_type = obj.get("type", "")
                                result["debug_last_event_type"] = event_type
                                if event_type == "response.output_text.delta":
                                    delta = str(obj.get("delta") or "")
                                    if delta:
                                        result["debug_text_sample"] = (
                                            str(result.get("debug_text_sample") or "") + delta
                                        )[:500]
                                if debug_log:
                                    await debug_log(
                                        "debug",
                                        "[codex_image] sse event",
                                        **_summarize_codex_sse_event(obj),
                                    )
                                if event_type == "response.created":
                                    result["response_id"] = (obj.get("response") or {}).get("id") or result["response_id"]
                                    result["status"] = (obj.get("response") or {}).get("status") or result["status"]
                                elif event_type == "response.image_generation_call.partial_image":
                                    result["image_base64"] = obj.get("partial_image_b64") or result["image_base64"]
                                    result["revised_prompt"] = obj.get("revised_prompt") or result["revised_prompt"]
                                    result["status"] = obj.get("status") or result["status"]
                                elif (
                                    "image_generation" in event_type
                                    or event_type in {"response.output_item.done", "response.output_item.added"}
                                ):
                                    image_b64, revised, _ = _extract_codex_artifacts(obj)
                                    result["image_base64"] = image_b64 or result["image_base64"]
                                    result["revised_prompt"] = revised or result["revised_prompt"]
                                    result["status"] = obj.get("status") or result["status"]
                                elif event_type == "response.completed":
                                    resp_obj = obj.get("response", {})
                                    output = resp_obj.get("output") if isinstance(resp_obj, dict) else None
                                    if isinstance(output, list):
                                        result["debug_output_types"] = ",".join(
                                            str(part.get("type") or "")
                                            for part in output
                                            if isinstance(part, dict) and part.get("type")
                                        )[:300]
                                    image_b64, revised, error_info = _extract_codex_artifacts(resp_obj)
                                    result["image_base64"] = image_b64 or result["image_base64"]
                                    result["revised_prompt"] = revised or result["revised_prompt"]
                                    if error_info:
                                        raise CodexApiError(200, error_info)
                                    result["status"] = resp_obj.get("status") or result["status"]
                                    result["response_id"] = resp_obj.get("id") or result["response_id"]
                                elif "error" in event_type:
                                    _, _, error_info = _extract_codex_artifacts(obj)
                                    if error_info:
                                        raise CodexApiError(200, error_info)
            stream_error = None
            break
        except _STREAM_RECOVERABLE_ERRORS as exc:
            stream_error = exc
            if stream_attempt == 0 and not result["response_id"] and time.monotonic() < deadline:
                if update_status:
                    await update_status("Codex 流式连接刚开始中断，正在自动重试...")
                continue
            break

    # 如果流式结束后已经有图片，直接返回
    if result["image_base64"]:
        return result

    # store=false 时 Codex 不允许后续 GET 轮询，所有可用图片结果都应来自 SSE 流。
    if stream_error is not None and result["response_id"] and update_status:
        await update_status("Codex 流式连接中断；当前端点无法轮询补全，准备返回错误...")

    if stream_error is not None:
        raise RuntimeError(_humanize_codex_exception(stream_error)) from stream_error
    if not result["response_id"]:
        return result

    # store=false 时 Codex 不允许后续 GET 轮询，所有可用结果都应来自 SSE 流。
    return result

    # ── 轮询补全 ──────────────────────────────────────
    attempt = 0
    while True:
        attempt += 1
        now = time.monotonic()
        if now >= deadline:
            raise TimeoutError("生成超时，已强制停止（超过10分钟）")

        await asyncio.sleep(min(float(poll_interval), max(1.0, deadline - now)))

        if time.monotonic() >= deadline:
            raise TimeoutError("生成超时，已强制停止（超过10分钟）")

        if update_status:
            await update_status(f"⏳ 正在等待 Codex 返回结果...（第 {attempt} 次检查）")

        polled = await _poll_codex_response(client_ref=None, token=token, response_id=result["response_id"], deadline=deadline)
        if polled is None:
            # HTTP 请求本身就失败了，展示给用户
            if update_status:
                await update_status(f"⚠️ 轮询请求失败，{poll_interval}秒后重试...（第 {attempt} 次）")
            continue

        # 有错误信息 → 直接报错，不再空等
        if polled.get("error"):
            poll_status = int(polled.get("error_status_code") or 0)
            poll_content_type = str(polled.get("error_content_type") or "")
            # Auth/login/html responses will not become a valid image by waiting.
            if poll_status in {401, 403, 404} or _looks_like_html(str(polled["error"]), poll_content_type):
                raise RuntimeError(
                    _humanize_codex_error(
                        poll_status,
                        str(polled["error"]),
                        content_type=poll_content_type,
                    )
                )
            if update_status:
                await update_status(f"⚠️ 轮询返回异常，继续等待 Codex 结果...（第 {attempt} 次）")
            continue

        # 状态不再是 in_progress → 可能完成也可能失败
        if polled.get("status") and polled["status"] not in ("in_progress", "queued", "completed"):
            # failed / cancelled / incomplete 等终态
            status_desc = polled["status"]
            error_msg = polled.get("error") or f"任务状态异常：{status_desc}"
            raise RuntimeError(_humanize_codex_error(0, error_msg))

        if polled.get("image_base64"):
            return {**result, **polled}
        if polled.get("status") == "completed":
            return {
                **result,
                **polled,
                "image_base64": polled.get("image_base64") or result.get("image_base64"),
                "revised_prompt": polled.get("revised_prompt") or result.get("revised_prompt"),
            }

        # 还在进行中，显示实际状态
        if update_status:
            status_hint = polled.get("status") or "处理中"
            await update_status(f"⏳ Codex {status_hint}...（第 {attempt} 次检查）")

    return result  # pragma: no cover


async def _poll_codex_response(
    client_ref: Any,
    token: str,
    response_id: str,
    deadline: float,
) -> dict[str, str | None] | None:
    """轮询 Codex 响应状态。"""
    remaining_timeout = max(1.0, min(60.0, deadline - time.monotonic()))
    headers = {
        **_CODEX_HEADERS_BASE,
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(remaining_timeout)) as client:
            resp = await client.get(f"{CODEX_URL}/{response_id}", headers=headers)

            # HTTP 错误 → 返回 error 字段而不是静默吞掉
            if resp.status_code >= 400:
                try:
                    err_body = resp.json()
                    err_msg = (err_body.get("error", {}) or {}).get("message") if isinstance(err_body.get("error"), dict) else err_body.get("error")
                except Exception:
                    err_msg = resp.text[:500] if resp.text else f"HTTP {resp.status_code}"
                return {
                    "image_base64": None,
                    "revised_prompt": None,
                    "status": "failed",
                    "response_id": response_id,
                    "error": str(err_msg) if err_msg else f"HTTP {resp.status_code}",
                    "error_status_code": str(resp.status_code),
                    "error_content_type": resp.headers.get("content-type", ""),
                }

            content_type = resp.headers.get("content-type", "")
            if _looks_like_html(resp.text[:500], content_type):
                return {
                    "image_base64": None,
                    "revised_prompt": None,
                    "status": "failed",
                    "response_id": response_id,
                    "error": resp.text[:500],
                    "error_status_code": str(resp.status_code),
                    "error_content_type": content_type,
                }

            body = resp.json()
            data = body.get("response", body)
            if not data or not isinstance(data, dict):
                return None

            image_base64, revised_prompt, error_info = _extract_codex_artifacts(data)

            return {
                "image_base64": image_base64,
                "revised_prompt": revised_prompt,
                "status": data.get("status") if isinstance(data.get("status"), str) else None,
                "response_id": data.get("id") if isinstance(data.get("id"), str) else response_id,
                "error": error_info,
                "error_status_code": None,
                "error_content_type": None,
            }
    except Exception:
        return None


# ─── 异常 ───────────────────────────────────────────────


class CodexApiError(Exception):
    """Codex API 调用失败。"""

    def __init__(self, status_code: int, detail: str, *, content_type: str = "") -> None:
        self.status_code = status_code
        self.detail = detail
        self.content_type = content_type
        super().__init__(f"Codex API 错误 ({status_code}): {detail}")


# ─── 帮助文本 ───────────────────────────────────────────

HELP_TEXT = """<b>Codex 图片生成插件</b>

通过 Codex API 调用 GPT 图片生成模型。

<b>用法：</b>
<code>{prefix}cximg 提示词</code> — 纯文本生成图片
回复图片后发送 <code>{prefix}cximg 提示词</code> — 参考图生成
<code>{prefix}cximg token 你的 access token</code> — 保存 Token
<code>{prefix}cximg token</code> — 查看当前 Token

<b>Token 获取：</b>
通常在 <code>.codex/auth.json</code> 文件中找到 access_token"""


# ─── 插件主类 ───────────────────────────────────────────


@register
class CodexImagePlugin(Plugin):
    key = "codex_image"
    display_name = "Codex 图片生成"
    description = "通过 Codex API 调用 GPT 图片生成模型，命令前缀跟随系统设置。"
    message_channels = {"incoming", "outgoing"}
    owner_only = True
    command_config_keys = {"command"}

    def __init__(self) -> None:
        super().__init__()
        self._command_name = DEFAULT_COMMAND

    async def on_startup(self, ctx: PluginContext) -> None:
        self._command_name = _command_name(ctx)
        self.commands = {self._command_name: self._cmd_handler}
        if ctx.log:
            await ctx.log("info", f"[codex_image] 启动，指令名={self._command_name}")

    # ── 命令入口 ──────────────────────────────────────

    async def _cmd_handler(
        self,
        client: Any,
        event: Any,
        args: list[str],
        account_id: int,
        ctx: PluginContext,
    ) -> None:
        try:
            await self._dispatch(ctx, args, event)
        except Exception as exc:
            try:
                await _edit_html(event, f"❌ 操作失败: {_html_escape(_safe_error_text(str(exc)))}")
            except Exception:
                pass

    # ── 命令分发 ──────────────────────────────────────

    async def _dispatch(
        self, ctx: PluginContext, args: list[str], event
    ) -> None:
        sub = args[0].lower() if args else ""

        if sub in {"token", "令牌"}:
            await self._cmd_token(ctx, args[1:], event)
            return

        # 先检查参考图，获取尺寸信息用于参数解析
        reference_image = None
        reference_size: str | None = None
        reference_ratio: str | None = None
        reply_msg = await event.get_reply_message()
        if reply_msg and reply_msg.media:
            try:
                reference_image = await self._download_reference_image(ctx, reply_msg)
                # 提取参考图尺寸
                if reference_image.get("width") and reference_image.get("height"):
                    w = reference_image["width"]
                    h = reference_image["height"]
                    reference_size = f"{w}x{h}"
                    # 计算比例（简化为最接近的常见比例，±0.05 容差）
                    ratio = w / h
                    if 0.95 <= ratio <= 1.05:
                        reference_ratio = "1:1"
                    elif 1.45 <= ratio <= 1.55:
                        reference_ratio = "3:2"
                    elif 0.64 <= ratio <= 0.69:
                        reference_ratio = "2:3"
                    elif 1.28 <= ratio <= 1.38:
                        reference_ratio = "4:3"
                    elif 0.72 <= ratio <= 0.78:
                        reference_ratio = "3:4"
                    elif 1.72 <= ratio <= 1.83:
                        reference_ratio = "16:9"
                    elif 0.54 <= ratio <= 0.59:
                        reference_ratio = "9:16"
                    else:
                        reference_ratio = f"{w}:{h}"  # 使用原始比例
            except Exception as exc:
                await _edit_html(event, f"❌ 参考图下载失败：{_html_escape(_safe_error_text(str(exc)))}")
                return

        prompt, image_opts = _parse_generation_args(args, ctx, reference_size, reference_ratio)
        cmd = _command_name(ctx)
        usage_cmd = f"{_html_escape(current_command_prefix())}{_html_escape(cmd)}"
        if not prompt:
            await _edit_html(
                event,
                f"❌ 请输入提示词，例如：<code>{usage_cmd} 一只戴墨镜的柴犬坐在跑车里</code>\n"
                f"• 指定比例：<code>{usage_cmd} --比例 4:3 云海里的城市</code>\n"
                f"• 指定尺寸/格式：<code>{usage_cmd} --size 1536x1024 --format jpeg 海边日落</code>\n"
                f"• 使用原图尺寸：<code>{usage_cmd} --size 原图 云海里的城市</code>（回复图片时）\n"
                f"• 设置 Token：<code>{usage_cmd} token 你的codex access token</code>"
            )
            return

        reply_to_id = getattr(reply_msg, "id", None) if reply_msg else getattr(event, "id", None)
        await self._cmd_generate(ctx, prompt, event, image_opts, reference_image, reply_to_id=reply_to_id)

    # ── Token 管理 ────────────────────────────────────

    async def _cmd_token(
        self, ctx: PluginContext, args: list[str], event
    ) -> None:
        token_value = " ".join(args).strip()
        current_token = _get_config_value(ctx, "access_token", "")
        cmd = _command_name(ctx)
        usage_cmd = f"{_html_escape(current_command_prefix())}{_html_escape(cmd)}"

        if not token_value:
            await _edit_html(
                event,
                f"🔐 当前 Token：{_mask_token(current_token)}\n"
                f"• 设置方式：<code>{usage_cmd} token 你的codex access token（通常在 .codex/auth.json）</code>"
            )
            return

        await _update_account_config(ctx, "access_token", token_value)
        # 更新运行时 config
        ctx.config["access_token"] = token_value
        await event.edit("✅ 已保存 Codex Access Token")

    # ── 图片生成 ──────────────────────────────────────

    async def _cmd_generate(
        self,
        ctx: PluginContext,
        prompt: str,
        event,
        image_opts: dict[str, str] | None = None,
        reference_image: dict[str, Any] | None = None,
        *,
        reply_to_id: int | None = None,
    ) -> None:
        token = _get_config_value(ctx, "access_token", "")
        cmd = _command_name(ctx)
        usage_cmd = f"{_html_escape(current_command_prefix())}{_html_escape(cmd)}"
        if not token:
            await _edit_html(
                event,
                f"❌ 缺少鉴权，请先使用 <code>{usage_cmd} token 你的codex access token（通常在 .codex/auth.json）</code> 保存 Token"
            )
            return

        model = _normalize_main_model(_get_config_value(ctx, "model", DEFAULT_MODEL))
        image_model = _get_config_value(ctx, "image_model", DEFAULT_IMAGE_MODEL)
        max_wait = int(_get_config_value(ctx, "max_wait_seconds", DEFAULT_MAX_WAIT))
        status_interval = int(_get_config_value(ctx, "status_interval_seconds", DEFAULT_STATUS_INTERVAL))
        status_interval = max(10, min(300, status_interval))
        delete_command_message = bool(_get_config_value(ctx, "delete_command_message", True))
        show_revised_prompt = bool(_get_config_value(ctx, "show_revised_prompt", True))
        instructions = str(_get_config_value(ctx, "custom_instructions", "") or DEFAULT_INSTRUCTIONS)
        reasoning_effort = str(_get_config_value(ctx, "reasoning_effort", DEFAULT_REASONING_EFFORT) or DEFAULT_REASONING_EFFORT)
        message_template = str(_get_config_value(ctx, "message_template", DEFAULT_MESSAGE_TEMPLATE) or DEFAULT_MESSAGE_TEMPLATE)
        image_opts = image_opts or {}
        
        # 获取参考图尺寸信息（如果有的话）
        ref_size: str | None = None
        if reference_image and reference_image.get("width") and reference_image.get("height"):
            ref_size = f"{reference_image['width']}x{reference_image['height']}"
        
        image_size = _normalize_size(image_opts.get("image_size") or _get_config_value(ctx, "image_size", DEFAULT_IMAGE_SIZE), ref_size)
        aspect_ratio = _normalize_aspect_ratio(image_opts.get("aspect_ratio") or _get_config_value(ctx, "aspect_ratio", DEFAULT_ASPECT_RATIO), None)
        image_format = _normalize_image_format(image_opts.get("image_format") or _get_config_value(ctx, "image_format", DEFAULT_IMAGE_FORMAT))

        # 显示的 image_model 值
        display_image_model = image_model if image_model != "auto" else "自动选择"

        started_at = time.monotonic()
        last_status_at = 0.0
        current_phase = "已检测到参考图，正在生成图片" if reference_image else "正在根据提示词生成图片"

        def render_status(
            status: str,
            elapsed: str,
            revised_prompt: str = "",
            response_id: str = "",
            limit: int = 4000,
        ) -> str:
            return _render_message(
                message_template,
                {
                    "status": status,
                    "prompt": prompt,
                    "elapsed": elapsed,
                    "model": model,
                    "image_model": display_image_model,
                    "command": cmd,
                    "image_size": image_size,
                    "aspect_ratio": aspect_ratio,
                    "image_format": image_format,
                    "has_reference": "是" if reference_image else "",
                    "revised_prompt": revised_prompt if show_revised_prompt else "",
                    "response_id": response_id,
                },
                limit=limit,
            )

        await _edit_html(event, render_status(current_phase, "0秒"))

        async def update_status(phase: str) -> None:
            nonlocal last_status_at, current_phase
            current_phase = phase
            now = time.monotonic()
            if now - last_status_at < status_interval:
                return
            last_status_at = now
            elapsed = _format_duration((now - started_at) * 1000)
            try:
                await _edit_html(event, render_status(phase, elapsed))
            except Exception:
                pass

        # 心跳更新
        heartbeat_stop = False

        async def heartbeat() -> None:
            nonlocal heartbeat_stop
            while not heartbeat_stop:
                await asyncio.sleep(status_interval)
                if heartbeat_stop:
                    break
                await update_status(current_phase)

        hb_task = asyncio.create_task(heartbeat())

        try:
            if ctx.log:
                await ctx.log(
                    "info",
                    "[codex_image] generation started",
                    model=str(model),
                    image_model=str(image_model),
                    image_size=image_size,
                    aspect_ratio=aspect_ratio,
                    image_format=image_format,
                    has_reference=bool(reference_image),
                    status_interval=status_interval,
                    max_wait=max_wait,
                )
            result = await _call_codex_image(
                prompt=_effective_prompt(
                    prompt,
                    aspect_ratio,
                    image_size,
                    image_format,
                    has_reference=bool(reference_image),
                ),
                token=token,
                model=model,
                image_model=image_model,
                reference_image=reference_image,
                update_status=update_status,
                max_wait=max_wait,
                instructions=instructions,
                reasoning_effort=reasoning_effort,
                image_size=image_size,
                image_format=image_format,
                poll_interval=status_interval,
                debug_log=ctx.log,
            )
        except CodexApiError as exc:
            heartbeat_stop = True
            hb_task.cancel()
            elapsed = _format_duration((time.monotonic() - started_at) * 1000)
            if ctx.log:
                await ctx.log(
                    "warn",
                    "[codex_image] api error",
                    status_code=exc.status_code,
                    elapsed=elapsed,
                )
            await _edit_html(
                event,
                f"{_html_escape(_with_error_prefix(_humanize_codex_error(exc.status_code, exc.detail, content_type=exc.content_type)))}\n⏱️ 耗时：{elapsed}"
            )
            return
        except TimeoutError as exc:
            heartbeat_stop = True
            hb_task.cancel()
            elapsed = _format_duration((time.monotonic() - started_at) * 1000)
            if ctx.log:
                await ctx.log("warn", "[codex_image] timeout", elapsed=elapsed)
            await _edit_html(event, f"{_html_escape(_with_error_prefix(_safe_error_text(str(exc))))}\n⏱️ 耗时：{elapsed}")
            return
        except Exception as exc:
            heartbeat_stop = True
            hb_task.cancel()
            elapsed = _format_duration((time.monotonic() - started_at) * 1000)
            safe_error = _safe_error_text(str(exc), max_len=300)
            if ctx.log:
                await ctx.log(
                    "warn",
                    "[codex_image] generation failed",
                    error=type(exc).__name__,
                    detail=safe_error,
                    elapsed=elapsed,
                )
            await _edit_html(event, f"{_html_escape(_with_error_prefix(_humanize_codex_exception(exc)))}\n⏱️ 耗时：{elapsed}")
            return

        heartbeat_stop = True
        hb_task.cancel()
        elapsed = _format_duration((time.monotonic() - started_at) * 1000)

        returned_prompt = _extract_text_fallback_prompt(result.get("debug_text_sample"))
        fallback_prompt = returned_prompt or prompt
        if (
            not result.get("image_base64")
            and result.get("debug_text_sample")
            and model != CODEX_TOOL_FALLBACK_MODEL
        ):
            if ctx.log:
                await ctx.log(
                    "warn",
                    "[codex_image] text prompt returned, retrying fallback model",
                    original_model=str(model),
                    fallback_model=CODEX_TOOL_FALLBACK_MODEL,
                    text_sample=str(result.get("debug_text_sample") or ""),
                    extracted_prompt=returned_prompt or "",
                    fallback_prompt=fallback_prompt,
                )
            await _edit_html(event, render_status("Codex 返回了文本提示词，正在用兼容模型重试...", elapsed))
            try:
                result = await _call_codex_image(
                    prompt=_effective_prompt(
                        fallback_prompt,
                        aspect_ratio,
                        image_size,
                        image_format,
                        has_reference=bool(reference_image),
                    ),
                    token=token,
                    model=CODEX_TOOL_FALLBACK_MODEL,
                    image_model="auto",
                    reference_image=reference_image,
                    update_status=update_status,
                    max_wait=max(60, int(max_wait - (time.monotonic() - started_at))),
                    instructions=DEFAULT_INSTRUCTIONS,
                    reasoning_effort=DEFAULT_REASONING_EFFORT,
                    image_size=image_size,
                    image_format=image_format,
                    poll_interval=status_interval,
                    debug_log=ctx.log,
                )
                elapsed = _format_duration((time.monotonic() - started_at) * 1000)
            except Exception as exc:
                if ctx.log:
                    await ctx.log(
                        "warn",
                        "[codex_image] fallback generation failed",
                        error=type(exc).__name__,
                        detail=_safe_error_text(str(exc), max_len=300),
                    )

        if not result.get("image_base64"):
            status_info = result.get("status", "")
            status_text = f"（status: {_html_escape(status_info)}）" if status_info else ""
            if ctx.log:
                await ctx.log(
                    "warn",
                    "[codex_image] no image returned",
                    status=str(status_info or ""),
                    response_id=str(result.get("response_id") or ""),
                    last_event_type=str(result.get("debug_last_event_type") or ""),
                    output_types=str(result.get("debug_output_types") or ""),
                    text_sample=str(result.get("debug_text_sample") or ""),
                    elapsed=elapsed,
                )
            await _edit_html(event, f"❌ 未收到生成图片{status_text}\n⏱️ 耗时：{elapsed}")
            return

        # 发送图片
        try:
            image_bytes = base64.b64decode(result["image_base64"])
            caption = render_status(
                "已完成",
                elapsed,
                revised_prompt=str(result.get("revised_prompt") or ""),
                response_id=str(result.get("response_id") or ""),
                limit=1024,
            )

            client = ctx.client
            if not client:
                await _edit_html(event, "❌ 客户端未初始化")
                return
            ext = _image_ext_from_bytes(image_bytes, image_format)
            file_name = f"codex_image_{int(time.time())}{ext}"
            send_reply_to = reply_to_id or getattr(event, "id", None)

            try:
                image_file = io.BytesIO(image_bytes)
                image_file.name = file_name
                await client.send_file(
                    event.chat_id,
                    image_file,
                    caption=caption,
                    parse_mode="html",
                    reply_to=send_reply_to,
                    force_document=False,
                )
            except Exception as send_exc:
                if ctx.log:
                    await ctx.log(
                        "warn",
                        "[codex_image] send with HTML caption failed, retrying plain text",
                        error=type(send_exc).__name__,
                        detail=_safe_error_text(str(send_exc))[:300],
                    )
                image_file = io.BytesIO(image_bytes)
                image_file.name = file_name
                await client.send_file(
                    event.chat_id,
                    image_file,
                    caption=_strip_html_tags(caption)[:1024],
                    reply_to=send_reply_to,
                    force_document=False,
                )

            # 删除原命令消息
            if delete_command_message:
                try:
                    await event.delete()
                except Exception:
                    await event.edit("✅ 图片生成完成")
            else:
                await event.edit("✅ 图片生成完成")
            if ctx.log:
                await ctx.log(
                    "info",
                    "[codex_image] generation completed",
                    elapsed=elapsed,
                    response_id=str(result.get("response_id") or ""),
                    image_format=image_format,
                )

        except Exception as exc:
            error_msg = _safe_error_text(str(exc))
            if ctx.log:
                await ctx.log(
                    "warn",
                    "[codex_image] send image failed",
                    error=type(exc).__name__,
                    detail=error_msg[:500],
                    elapsed=elapsed,
                )
            await _edit_html(event, f"❌ 图片发送失败：{type(exc).__name__}: {_html_escape(error_msg)}")

    # ── 参考图下载 ────────────────────────────────────

    async def _download_reference_image(
        self, ctx: PluginContext, reply_msg: Any
    ) -> dict[str, str | int | None]:
        """从回复消息中下载参考图，返回 {base64, mime_type, width, height}。"""
        from app.worker.media import _download_with_retry

        client = ctx.client
        if not client:
            raise RuntimeError("客户端未初始化")

        # 下载媒体（带 file_reference 过期重试）
        media_bytes = await _download_with_retry(client, reply_msg)
        if not media_bytes:
            raise RuntimeError("未能获取参考图数据")

        # 推断 MIME 类型
        mime_type = "image/png"
        width: int | None = None
        height: int | None = None
        
        if hasattr(reply_msg, "media") and reply_msg.media:
            doc = getattr(reply_msg.media, "document", None)
            if doc:
                doc_mime = getattr(doc, "mime_type", None)
                if doc_mime and doc_mime.startswith("image/"):
                    mime_type = doc_mime
                # 尝试从 document 的 attributes 中获取尺寸
                for attr in getattr(doc, "attributes", []):
                    if hasattr(attr, "w") and hasattr(attr, "h"):
                        width = attr.w
                        height = attr.h
                        break
            elif hasattr(reply_msg.media, "photo"):
                mime_type = "image/jpeg"
                # Telegram photo 可能有多层尺寸，取最大的
                photo = reply_msg.media.photo
                if hasattr(photo, "sizes"):
                    for size in photo.sizes:
                        if hasattr(size, "w") and hasattr(size, "h"):
                            if width is None or size.w > width:
                                width = size.w
                                height = size.h

        b64 = base64.b64encode(media_bytes).decode("utf-8")
        return {"base64": b64, "mime_type": mime_type, "width": width, "height": height}


# ─── dry-run 支持（无规则，不适用，但预留接口）──────────


def _dry_run_match(
    cfg: dict[str, Any],
    text: str,
    chat_id: int | None = None,
) -> tuple[bool, str | None]:
    """Codex Image 不使用规则匹配，dry-run 始终返回提示信息。"""
    token = cfg.get("access_token", "")
    if not token:
        return False, "未配置 access_token，无法调用 Codex API"
    return True, f"[dry-run] 将使用提示词「{text[:50]}」调用 Codex API 生成图片"


PLUGIN_CLASS = CodexImagePlugin


__all__ = [
    "CodexImagePlugin",
    "PLUGIN_CLASS",
    "_dry_run_match",
    "_extract_codex_artifacts",
    "_image_ext_from_bytes",
    "_parse_generation_args",
]
