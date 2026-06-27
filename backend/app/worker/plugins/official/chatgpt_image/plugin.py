"""ChatGPT2API。

这是一个 TelePilot 原生插件版的 chatgpt2api-lite：token 池保存在插件配置中，
运行时按 token 轮询调用 ChatGPT Web 图片链路，并把结果发回 Telegram。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from html import escape as html_escape
from html import unescape as html_unescape
from typing import Any

from app.worker.command import current_command_prefix
from app.worker.media import collect_image_sources, download_image_bytes
from app.worker.plugins.base import Plugin, PluginContext, register

from .client import (
    ChatGPTWebImageClient,
    ImageRequest,
    ImageResult,
    humanize_error,
)
from .image_utils import telegram_file
from .importers import (
    CPAConfig,
    ImportClient,
    Sub2APIConfig,
    extract_auth_session_access_token,
    parse_names,
)
from .manifest import DEFAULT_MESSAGE_TEMPLATE
from .token_pool import (
    TokenEntry,
    TokenPool,
    format_token_entries,
    mask_token,
    parse_token_entries,
    parse_token_lines,
)

PLUGIN_VERSION = "0.1.0"
DEFAULT_MODELS = [
    "gpt-image-2",
    "codex-gpt-image-2",
    "auto",
    "gpt-5",
    "gpt-5-1",
    "gpt-5-2",
    "gpt-5-3",
    "gpt-5-3-mini",
    "gpt-5-mini",
]

CONFIG_RELOAD_KEYS = {
    "command",
    "edit_command",
    "admin_command",
    "token",
    "tokens",
    "default_model",
    "available_models",
    "default_count",
    "max_count",
    "default_size",
    "image_format",
    "output_mode",
    "message_template",
    "style_templates",
    "default_style",
    "timeout",
    "poll_timeout",
    "poll_interval",
    "remember_last_image",
    "reference_image_limit",
    "skip_failed_seconds",
    "auto_disable_invalid_tokens",
    "health_check_enabled",
    "health_check_interval",
    "sub2api_base_url",
    "sub2api_email",
    "sub2api_password",
    "sub2api_api_key",
    "sub2api_group_id",
    "cpa_base_url",
    "cpa_secret_key",
    "cpa_file_names",
    "log_prompt_preview",
}


@dataclass(frozen=True)
class ChatGPTImageConfig:
    command: str = "draw"
    edit_command: str = "edit"
    admin_command: str = "gptimg"
    token: str = ""
    tokens: list[TokenEntry] = None  # type: ignore[assignment]
    default_model: str = "gpt-image-2"
    available_models: list[str] = None  # type: ignore[assignment]
    default_count: int = 1
    max_count: int = 4
    default_size: str = "1:1"
    image_format: str = "png"
    output_mode: str = "auto"
    message_template: str = DEFAULT_MESSAGE_TEMPLATE
    style_templates: dict[str, str] = None  # type: ignore[assignment]
    default_style: str = ""
    timeout: int = 300
    poll_timeout: int = 180
    poll_interval: int = 10
    remember_last_image: bool = True
    reference_image_limit: int = 6
    skip_failed_seconds: int = 600
    auto_disable_invalid_tokens: bool = True
    health_check_enabled: bool = False
    health_check_interval: int = 3600
    sub2api_base_url: str = ""
    sub2api_email: str = ""
    sub2api_password: str = ""
    sub2api_api_key: str = ""
    sub2api_group_id: str = ""
    cpa_base_url: str = ""
    cpa_secret_key: str = ""
    cpa_file_names: list[str] = None  # type: ignore[assignment]
    log_prompt_preview: bool = True


@dataclass(frozen=True)
class ParsedImageCommand:
    prompt: str
    model: str
    count: int
    style: str
    size: str


def _clean_command(value: Any, default: str) -> str:
    command = str(value or "").strip()
    if not command or re.search(r"\s", command):
        return default
    return command[:32]


def _int_range(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _line_list(value: Any, fallback: list[str]) -> list[str]:
    items = [line.strip() for line in str(value or "").replace(",", "\n").splitlines() if line.strip()]
    return list(dict.fromkeys(items)) or list(fallback)


def _parse_style_templates(value: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in str(value or "").splitlines():
        raw = line.strip()
        if not raw or "=" not in raw:
            continue
        name, template = raw.split("=", 1)
        name = name.strip()
        template = template.strip()
        if name and template:
            out[name] = template
    return out


def _load_config(raw: dict[str, Any] | None) -> ChatGPTImageConfig:
    cfg = raw or {}
    available_models = _line_list(cfg.get("available_models"), DEFAULT_MODELS)
    max_count = _int_range(cfg.get("max_count"), 4, 1, 4)
    default_count = _int_range(cfg.get("default_count"), 1, 1, max_count)
    output_mode = str(cfg.get("output_mode") or "auto").strip()
    if output_mode not in {"auto", "image", "file"}:
        output_mode = "auto"
    image_format = str(cfg.get("image_format") or "png").strip()
    if image_format not in {"png", "jpeg", "webp"}:
        image_format = "png"
    return ChatGPTImageConfig(
        command=_clean_command(cfg.get("command"), "draw"),
        edit_command=_clean_command(cfg.get("edit_command"), "edit"),
        admin_command=_clean_command(cfg.get("admin_command"), "gptimg"),
        token=str(cfg.get("token") or ""),
        tokens=parse_token_entries(cfg.get("tokens"), cfg.get("token")),
        default_model=str(cfg.get("default_model") or "gpt-image-2"),
        available_models=available_models,
        default_count=default_count,
        max_count=max_count,
        default_size=str(cfg.get("default_size") or "1:1").strip() or "1:1",
        image_format=image_format,
        output_mode=output_mode,
        message_template=str(cfg.get("message_template") or DEFAULT_MESSAGE_TEMPLATE).strip() or DEFAULT_MESSAGE_TEMPLATE,
        style_templates=_parse_style_templates(cfg.get("style_templates")),
        default_style=str(cfg.get("default_style") or "").strip(),
        timeout=_int_range(cfg.get("timeout"), 300, 30, 900),
        poll_timeout=_int_range(cfg.get("poll_timeout"), 180, 30, 900),
        poll_interval=_int_range(cfg.get("poll_interval"), 10, 3, 60),
        remember_last_image=bool(cfg.get("remember_last_image", True)),
        reference_image_limit=_int_range(cfg.get("reference_image_limit"), 6, 1, 10),
        skip_failed_seconds=_int_range(cfg.get("skip_failed_seconds"), 600, 0, 86400),
        auto_disable_invalid_tokens=bool(cfg.get("auto_disable_invalid_tokens", True)),
        health_check_enabled=bool(cfg.get("health_check_enabled", False)),
        health_check_interval=_int_range(cfg.get("health_check_interval"), 3600, 300, 86400),
        sub2api_base_url=str(cfg.get("sub2api_base_url") or "").strip(),
        sub2api_email=str(cfg.get("sub2api_email") or "").strip(),
        sub2api_password=str(cfg.get("sub2api_password") or "").strip(),
        sub2api_api_key=str(cfg.get("sub2api_api_key") or "").strip(),
        sub2api_group_id=str(cfg.get("sub2api_group_id") or "").strip(),
        cpa_base_url=str(cfg.get("cpa_base_url") or "").strip(),
        cpa_secret_key=str(cfg.get("cpa_secret_key") or "").strip(),
        cpa_file_names=parse_names(cfg.get("cpa_file_names")),
        log_prompt_preview=bool(cfg.get("log_prompt_preview", True)),
    )


def _event_message(event: Any) -> Any:
    return getattr(event, "message", event)


def _event_chat_id(event: Any) -> int | None:
    return getattr(event, "chat_id", None) or getattr(_event_message(event), "chat_id", None)


def _event_message_id(event: Any) -> int | None:
    msg = _event_message(event)
    return getattr(msg, "id", None) or getattr(event, "id", None)


async def _safe_edit(event: Any, text: str) -> None:
    try:
        await event.edit(text)
    except Exception:
        try:
            await event.respond(text)
        except Exception:
            return


def _preview(text: str, limit: int = 80) -> str:
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(clean) > limit:
        return clean[:limit] + "..."
    return clean


def _format_seconds(value: float) -> str:
    if value < 1:
        return f"{value * 1000:.0f}ms"
    return f"{value:.1f}s"


def _escape_html_value(value: Any) -> str:
    return html_escape(str(value or ""), quote=False)


def _strip_html_tags(text: str) -> str:
    return html_unescape(re.sub(r"</?[^>]+>", "", str(text or "")))


def _render_style(prompt: str, style: str, templates: dict[str, str]) -> str:
    template = templates.get(style)
    if not template:
        return prompt
    return template.replace("{prompt}", prompt)


def _render_message_template(template: str, values: dict[str, str]) -> str:
    out = template or DEFAULT_MESSAGE_TEMPLATE
    out = re.sub(
        r"\{\?([a-zA-Z0-9_]+)\}([\s\S]*?)\{/\?\}",
        lambda match: match.group(2) if values.get(match.group(1)) else "",
        out,
    )
    return re.sub(r"\{([a-zA-Z0-9_]+)\}", lambda match: _escape_html_value(values.get(match.group(1), "")), out)


def _parse_image_args(args: list[str], cfg: ChatGPTImageConfig) -> ParsedImageCommand:
    model = cfg.default_model
    count = cfg.default_count
    style = cfg.default_style
    size = cfg.default_size
    prompt_parts: list[str] = []
    index = 0
    while index < len(args):
        item = args[index]
        if item in {"-m", "--model"} and index + 1 < len(args):
            model = args[index + 1]
            index += 2
            continue
        if item in {"-n", "--count"} and index + 1 < len(args):
            count = _int_range(args[index + 1], cfg.default_count, 1, cfg.max_count)
            index += 2
            continue
        if item in {"-s", "--style"} and index + 1 < len(args):
            style = args[index + 1]
            index += 2
            continue
        if item in {"--size", "--ratio"} and index + 1 < len(args):
            size = args[index + 1]
            index += 2
            continue
        prompt_parts.append(item)
        index += 1
    prompt = " ".join(prompt_parts).strip()
    if model not in cfg.available_models:
        raise ValueError(f"模型 {model} 不在可选模型列表中，请先在配置页加入或改用 models 命令查看。")
    return ParsedImageCommand(prompt=prompt, model=model, count=count, style=style, size=size)


@register
class ChatGPTImagePlugin(Plugin):
    key = "chatgpt_image"
    display_name = "ChatGPT2API"
    message_channels = {"incoming", "outgoing"}
    owner_only = True
    command_config_keys = CONFIG_RELOAD_KEYS

    def __init__(self) -> None:
        self._cfg = ChatGPTImageConfig(
            available_models=list(DEFAULT_MODELS),
            style_templates={},
            tokens=[],
            cpa_file_names=[],
        )
        self._pool = TokenPool()
        self._last_images_by_chat: dict[int, list[bytes]] = {}
        self._ctx: PluginContext | None = None
        self._proxy_url = ""

    async def on_startup(self, ctx: PluginContext) -> None:
        self._ctx = ctx
        self._cfg = _load_config(ctx.config)
        self._proxy_url = str(ctx.account_proxy_url or "").strip()
        self._pool.sync(self._cfg.tokens)
        self.commands = {
            self._cfg.command: self._cmd_generate,
            self._cfg.edit_command: self._cmd_edit,
            self._cfg.admin_command: self._cmd_admin,
        }
        if self._cfg.health_check_enabled and ctx.scheduler is not None:
            ctx.scheduler.register(
                "health-check",
                {"kind": "interval", "interval_sec": self._cfg.health_check_interval},
                self._health_check_job,
                replace=True,
            )
        await self._log(
            ctx,
            "info",
            f"ChatGPT2API v{PLUGIN_VERSION} 已启动。",
            commands=[self._cfg.command, self._cfg.edit_command, self._cfg.admin_command],
            token_count=len(self._pool.tokens),
            health_check_enabled=self._cfg.health_check_enabled,
            timeout=self._cfg.timeout,
            poll_timeout=self._cfg.poll_timeout,
            poll_interval=self._cfg.poll_interval,
            proxy_mode="account",
            has_proxy=bool(self._proxy_url),
        )

    async def on_shutdown(self, ctx: PluginContext) -> None:
        if ctx.scheduler is not None:
            ctx.scheduler.unregister_all()
        self._ctx = None

    async def _cmd_generate(self, client: Any, event: Any, args: list[str], account_id: int, ctx: PluginContext) -> None:
        await self._handle_image_request(ctx, event, args, reference_images=[])

    async def _cmd_edit(self, client: Any, event: Any, args: list[str], account_id: int, ctx: PluginContext) -> None:
        if args and args[0].lower() == "last":
            chat_id = _event_chat_id(event)
            if chat_id is None or chat_id not in self._last_images_by_chat:
                await _safe_edit(event, "没有可续改的最近图片。请先生成一张图，或回复图片使用编辑命令。")
                return
            await self._handle_image_request(ctx, event, args[1:], reference_images=self._last_images_by_chat[chat_id])
            return

        reference_images = await self._collect_reference_images(ctx, event)
        if not reference_images:
            prefix = current_command_prefix()
            await _safe_edit(event, f"请回复一张图片后使用 {prefix}{self._cfg.edit_command} 提示词，或使用 {prefix}{self._cfg.edit_command} last 提示词。")
            return
        await self._handle_image_request(ctx, event, args, reference_images=reference_images)

    async def _handle_image_request(
        self,
        ctx: PluginContext,
        event: Any,
        args: list[str],
        *,
        reference_images: list[bytes],
    ) -> None:
        try:
            parsed = _parse_image_args(args, self._cfg)
        except ValueError as exc:
            await _safe_edit(event, str(exc))
            return
        if not parsed.prompt:
            await _safe_edit(event, f"请在命令后写提示词。例：{current_command_prefix()}{self._cfg.command} 一只漂浮在太空里的猫")
            return
        prompt = _render_style(parsed.prompt, parsed.style, self._cfg.style_templates)
        started = time.monotonic()
        await _safe_edit(event, f"正在生成图片：模型 {parsed.model}，数量 {parsed.count}，请稍等...")
        await self._log(
            ctx,
            "info",
            "ChatGPT 图片任务开始。",
            action="edit" if reference_images else "generate",
            model=parsed.model,
            count=parsed.count,
            reference_count=len(reference_images),
            prompt_preview=_preview(prompt) if self._cfg.log_prompt_preview else "",
        )

        try:
            state = self._pool.choose()
            results = await self._run_with_token(state.token, prompt, parsed, reference_images)
            self._pool.mark_success(state.token)
        except Exception as exc:  # noqa: BLE001
            token = state.token if "state" in locals() else ""
            if token:
                self._pool.mark_failure(
                    token,
                    humanize_error(exc),
                    self._cfg.skip_failed_seconds,
                    disable_invalid=self._cfg.auto_disable_invalid_tokens,
                )
            await _safe_edit(event, f"生成失败：{humanize_error(exc)}")
            await self._log(
                ctx,
                "error",
                "ChatGPT 图片任务失败。",
                error_type=type(exc).__name__,
                error=humanize_error(exc),
                token_id=self._pool.find(token).token_id if token and self._pool.find(token) else "",
            )
            return

        elapsed = time.monotonic() - started
        chat_id = _event_chat_id(event)
        if chat_id is not None and self._cfg.remember_last_image:
            self._last_images_by_chat[int(chat_id)] = [result.data for result in results]
        await self._send_results(ctx, event, results, parsed, prompt, elapsed, len(reference_images))
        await _safe_edit(event, f"已完成：{len(results)} 张，耗时 {_format_seconds(elapsed)}。")
        await self._log(
            ctx,
            "info",
            "ChatGPT 图片任务完成。",
            model=parsed.model,
            result_count=len(results),
            elapsed_ms=int(elapsed * 1000),
        )

    async def _run_with_token(
        self,
        token: str,
        prompt: str,
        parsed: ParsedImageCommand,
        reference_images: list[bytes],
    ) -> list[ImageResult]:
        client = ChatGPTWebImageClient(
            token,
            proxy_url=self._proxy_url,
            timeout=self._cfg.timeout,
            poll_timeout=self._cfg.poll_timeout,
            poll_interval=self._cfg.poll_interval,
        )
        return await client.generate_images(
            ImageRequest(
                prompt=prompt,
                model=parsed.model,
                count=parsed.count,
                size=parsed.size,
                preferred_format=self._cfg.image_format,
                reference_images=reference_images[: self._cfg.reference_image_limit],
            )
        )

    async def _send_results(
        self,
        ctx: PluginContext,
        event: Any,
        results: list[ImageResult],
        parsed: ParsedImageCommand,
        prompt: str,
        elapsed: float,
        reference_count: int,
    ) -> None:
        if ctx.client is None:
            return
        chat_id = _event_chat_id(event)
        reply_to = _event_message_id(event)
        caption = _render_message_template(
            self._cfg.message_template,
            {
                "status": "已完成",
                "prompt": _preview(prompt, 240),
                "model": parsed.model,
                "count": str(parsed.count),
                "result_count": str(len(results)),
                "size": parsed.size,
                "style": parsed.style,
                "image_format": self._cfg.image_format,
                "output_mode": self._cfg.output_mode,
                "elapsed": _format_seconds(elapsed),
                "command": self._cfg.command,
                "edit_command": self._cfg.edit_command,
                "admin_command": self._cfg.admin_command,
                "has_reference": "是" if reference_count else "",
                "reference_count": str(reference_count),
                "proxy": self._proxy_label(),
            },
        ).strip()
        for idx, result in enumerate(results, start=1):
            ext = result.extension or (".jpg" if self._cfg.image_format == "jpeg" else f".{self._cfg.image_format}")
            file_obj = telegram_file(result.data, f"chatgpt_image_{int(time.time())}_{idx}{ext}")
            kwargs: dict[str, Any] = {
                "caption": caption if idx == 1 else None,
                "reply_to": reply_to,
            }
            if idx == 1 and caption:
                kwargs["parse_mode"] = "html"
            if self._cfg.output_mode == "file":
                kwargs["force_document"] = True
            elif self._cfg.output_mode == "image":
                kwargs["force_document"] = False
            try:
                await ctx.client.send_file(chat_id, file_obj, **kwargs)
            except Exception:
                if idx == 1 and caption and kwargs.get("parse_mode") == "html":
                    plain_kwargs = dict(kwargs)
                    plain_kwargs["caption"] = _strip_html_tags(caption)[:1024]
                    plain_kwargs.pop("parse_mode", None)
                    file_obj.seek(0)
                    try:
                        await ctx.client.send_file(chat_id, file_obj, **plain_kwargs)
                        continue
                    except Exception:
                        if self._cfg.output_mode != "auto":
                            raise
                        kwargs = plain_kwargs
                if self._cfg.output_mode == "auto":
                    file_obj.seek(0)
                    fallback_kwargs = dict(kwargs)
                    fallback_kwargs["force_document"] = True
                    await ctx.client.send_file(chat_id, file_obj, **fallback_kwargs)
                else:
                    raise

    async def _collect_reference_images(self, ctx: PluginContext, event: Any) -> list[bytes]:
        if ctx.client is None:
            return []
        try:
            replied = await event.get_reply_message()
        except Exception:
            replied = None
        self_msg = _event_message(event)
        sources = await collect_image_sources(ctx.client, replied, self_msg)
        images: list[bytes] = []
        for source in sources[: self._cfg.reference_image_limit]:
            images.append(await download_image_bytes(ctx.client, source))
        return images

    async def _cmd_admin(self, client: Any, event: Any, args: list[str], account_id: int, ctx: PluginContext) -> None:
        sub = (args[0].lower() if args else "help").strip()
        rest = args[1:]
        try:
            if sub in {"help", "-h", "--help"}:
                await _safe_edit(event, self._help_text())
            elif sub == "models":
                await _safe_edit(event, await self._models_text())
            elif sub == "ping":
                await _safe_edit(event, await self._ping_text())
            elif sub == "version":
                await _safe_edit(event, f"ChatGPT2API v{PLUGIN_VERSION}\n命令：{self._cfg.command} / {self._cfg.edit_command} / {self._cfg.admin_command}")
            elif sub == "status":
                await _safe_edit(event, self._status_text())
            elif sub == "refresh":
                await _safe_edit(event, await self._refresh_tokens(ctx))
            elif sub == "token":
                await _safe_edit(event, await self._token_command(ctx, rest))
            elif sub == "import":
                await _safe_edit(event, await self._import_command(ctx, rest))
            elif sub == "proxy":
                await _safe_edit(event, await self._proxy_command())
            else:
                await _safe_edit(event, f"未知管理子命令：{sub}\n\n{self._help_text()}")
        except Exception as exc:  # noqa: BLE001
            await _safe_edit(event, f"管理命令执行失败：{humanize_error(exc)}")
            await self._log(ctx, "error", "ChatGPT2API 管理命令失败。", subcommand=sub, error=humanize_error(exc))

    def _help_text(self) -> str:
        prefix = current_command_prefix()
        return (
            "ChatGPT2API 命令：\n"
            f"- {prefix}{self._cfg.command} [-m 模型] [-n 数量] [-s 风格] 提示词\n"
            f"- {prefix}{self._cfg.edit_command} 提示词（回复图片使用）\n"
            f"- {prefix}{self._cfg.edit_command} last 提示词\n"
            f"- {prefix}{self._cfg.admin_command} ping/models/status/version/refresh\n"
            f"- {prefix}{self._cfg.admin_command} token list/add/del\n"
            f"- {prefix}{self._cfg.admin_command} import sub2api|cpa|session\n"
            f"- {prefix}{self._cfg.admin_command} proxy test"
        )

    def _status_text(self) -> str:
        states = self._pool.states()
        lines = [
            f"ChatGPT2API v{PLUGIN_VERSION}",
            f"token 数量：{len(states)}",
            f"默认模型：{self._cfg.default_model}",
            f"代理：{self._proxy_label()}",
            "",
            "token 池：",
        ]
        if not states:
            lines.append("- 未配置 token")
        for idx, state in enumerate(states, start=1):
            quota = "未知" if state.image_quota_unknown or state.quota is None else str(state.quota)
            extra = f"，错误：{state.last_error}" if state.last_error else ""
            note = f" · 备注：{state.note}" if state.note else ""
            lines.append(
                f"{idx}. {state.masked} · {state.token_id}{note} · {state.status} · 额度 {quota} · 成功 {state.success} / 失败 {state.fail}{extra}"
            )
        return "\n".join(lines)

    async def _models_text(self) -> str:
        configured = "配置模型：\n" + "\n".join(f"- {model}" for model in self._cfg.available_models)
        if not self._pool.tokens:
            return configured + "\n\n未配置 token，无法拉取上游模型列表。"
        try:
            models = await ChatGPTWebImageClient(
                self._pool.tokens[0],
                proxy_url=self._proxy_url,
                timeout=self._cfg.timeout,
                poll_timeout=self._cfg.poll_timeout,
                poll_interval=self._cfg.poll_interval,
            ).list_models()
        except Exception as exc:  # noqa: BLE001
            return configured + f"\n\n上游模型检测失败：{humanize_error(exc)}"
        if not models:
            return configured + "\n\n上游没有返回模型列表。"
        return configured + "\n\n上游模型（前 30 个）：\n" + "\n".join(f"- {model}" for model in models[:30])

    async def _ping_text(self) -> str:
        if not self._pool.tokens:
            return "未配置 token，无法检测 ChatGPT 连通性。"
        started = time.monotonic()
        try:
            info = await ChatGPTWebImageClient(
                self._pool.tokens[0],
                proxy_url=self._proxy_url,
                timeout=self._cfg.timeout,
                poll_timeout=self._cfg.poll_timeout,
                poll_interval=self._cfg.poll_interval,
            ).get_user_info()
        except Exception as exc:  # noqa: BLE001
            return f"连通性检测失败：{humanize_error(exc)}"
        elapsed = _format_seconds(time.monotonic() - started)
        quota = "未知" if info.get("image_quota_unknown") else str(info.get("quota") or 0)
        return (
            "连通性检测成功。\n"
            f"账号：{info.get('email') or '未知'}\n"
            f"类型：{info.get('type') or '未知'}\n"
            f"图片额度：{quota}\n"
            f"耗时：{elapsed}"
        )

    async def _refresh_tokens(self, ctx: PluginContext) -> str:
        tokens = self._pool.tokens
        if not tokens:
            return "未配置 token，无法刷新额度。"
        refreshed = 0
        errors: list[str] = []
        for token in tokens:
            try:
                info = await ChatGPTWebImageClient(
                    token,
                    proxy_url=self._proxy_url,
                    timeout=self._cfg.timeout,
                    poll_timeout=self._cfg.poll_timeout,
                    poll_interval=self._cfg.poll_interval,
                ).get_user_info()
                self._pool.apply_remote_info(token, info)
                refreshed += 1
            except Exception as exc:  # noqa: BLE001
                self._pool.mark_failure(
                    token,
                    humanize_error(exc),
                    self._cfg.skip_failed_seconds,
                    disable_invalid=self._cfg.auto_disable_invalid_tokens,
                )
                state = self._pool.find(token)
                errors.append(f"{state.token_id if state else mask_token(token)}：{humanize_error(exc)}")
        await self._log(ctx, "info", "ChatGPT token 额度刷新完成。", refreshed=refreshed, errors=len(errors))
        text = f"刷新完成：成功 {refreshed} 个，失败 {len(errors)} 个。"
        if errors:
            text += "\n" + "\n".join(errors[:8])
        return text

    async def _token_command(self, ctx: PluginContext, args: list[str]) -> str:
        action = (args[0].lower() if args else "list").strip()
        if action == "list":
            return self._status_text()
        if action == "add":
            raw = " ".join(args[1:])
            session_token = extract_auth_session_access_token(raw)
            tokens = [session_token] if session_token else parse_token_lines(raw)
            if not tokens:
                return "请在 add 后面填写 token，或粘贴 chatgpt.com/api/auth/session 返回的完整 JSON。"
            source = "chatgpt.com session JSON" if session_token else "Telegram 命令添加"
            added, total = await self._merge_token_entries(ctx, [TokenEntry(token=token, note=source) for token in tokens])
            return f"已添加 {added} 个 token，当前共 {total} 个。"
        if action in {"del", "delete", "remove"}:
            if len(args) < 2:
                return "请提供要删除的 token 序号、token:id 或完整 token。"
            state = self._pool.find(args[1])
            if state is None:
                return "没有找到要删除的 token。"
            next_entries = [entry for entry in self._cfg.tokens if entry.token != state.token]
            await self._save_token_entries(ctx, next_entries)
            return f"已删除 {state.token_id}，当前共 {len(next_entries)} 个。"
        return "token 子命令支持：list / add / del。"

    async def _import_command(self, ctx: PluginContext, args: list[str]) -> str:
        source = (args[0].lower() if args else "").strip()
        importer = ImportClient(proxy_url=self._proxy_url, timeout=self._cfg.timeout)
        if source in {"session", "auth", "json"}:
            token = extract_auth_session_access_token(" ".join(args[1:]))
            if not token:
                return "没有从 session JSON 中提取到 accessToken。"
            return await self._merge_imported_tokens(ctx, [token], "chatgpt.com session JSON")
        if source == "sub2api":
            cfg = Sub2APIConfig(
                base_url=self._cfg.sub2api_base_url,
                email=self._cfg.sub2api_email,
                password=self._cfg.sub2api_password,
                api_key=self._cfg.sub2api_api_key,
                group_id=self._cfg.sub2api_group_id,
            )
            tokens = await importer.import_sub2api_tokens(cfg)
            return await self._merge_imported_tokens(ctx, tokens, "sub2api")
        if source == "cpa":
            cfg = CPAConfig(
                base_url=self._cfg.cpa_base_url,
                secret_key=self._cfg.cpa_secret_key,
                file_names=self._cfg.cpa_file_names,
            )
            if not cfg.file_names:
                files = await importer.list_cpa_files(cfg)
                if not files:
                    return "CPA 远程没有返回可导入文件。"
                return "CPA 可导入文件：\n" + "\n".join(
                    f"- {item['name']} {item.get('email') or ''}".strip() for item in files[:30]
                )
            tokens = await importer.import_cpa_tokens(cfg)
            return await self._merge_imported_tokens(ctx, tokens, "CPA")
        return "import 子命令支持：sub2api / cpa。"

    async def _merge_imported_tokens(self, ctx: PluginContext, tokens: list[str], source: str) -> str:
        if not tokens:
            return f"{source} 没有导入到 token。"
        added, total = await self._merge_token_entries(
            ctx,
            [TokenEntry(token=token, note=f"{source} 导入") for token in tokens],
        )
        await self._log(ctx, "info", f"{source} token 导入完成。", added=added, total=total)
        return f"{source} 导入完成：新增 {added} 个，当前共 {total} 个。"

    async def _merge_token_entries(self, ctx: PluginContext, entries: list[TokenEntry]) -> tuple[int, int]:
        current = list(self._cfg.tokens)
        seen = {entry.token for entry in current}
        merged = list(current)
        added = 0
        for entry in entries:
            if not entry.token or entry.token in seen:
                continue
            seen.add(entry.token)
            merged.append(entry)
            added += 1
        await self._save_token_entries(ctx, merged)
        return added, len(merged)

    async def _save_token_entries(self, ctx: PluginContext, entries: list[TokenEntry]) -> None:
        payload = format_token_entries(entries)
        await self._save_account_config(ctx, {"tokens": payload, "token": ""})
        self._cfg = _load_config({**ctx.config, "tokens": payload, "token": ""})
        self._pool.sync(self._cfg.tokens)

    async def _proxy_command(self) -> str:
        result = await ChatGPTWebImageClient(
            self._pool.tokens[0] if self._pool.tokens else "",
            proxy_url=self._proxy_url,
            timeout=self._cfg.timeout,
            poll_timeout=self._cfg.poll_timeout,
            poll_interval=self._cfg.poll_interval,
        ).test_proxy()
        status = "可用" if result.get("ok") else "不可用"
        return f"代理测试：{status}\nHTTP：{result.get('status')}\n耗时：{result.get('latency_ms')}ms\n错误：{result.get('error') or '无'}"

    async def _health_check_job(self, _job: Any) -> None:
        ctx = getattr(self, "_ctx", None)
        if ctx is None:
            return
        await self._refresh_tokens(ctx)

    def _proxy_label(self) -> str:
        return "跟随账号代理" if self._proxy_url else "跟随账号：直连"

    async def _save_account_config(self, ctx: PluginContext, updates: dict[str, Any]) -> None:
        from sqlalchemy import select

        from app.db.base import AsyncSessionLocal
        from app.db.models.feature import AccountFeature, Feature
        from app.services import feature_service

        async with AsyncSessionLocal() as db:
            feature = await db.get(Feature, ctx.feature_key)
            row = (
                await db.execute(
                    select(AccountFeature).where(
                        AccountFeature.account_id == ctx.account_id,
                        AccountFeature.feature_key == ctx.feature_key,
                    )
                )
            ).scalar_one_or_none()
            current = dict(row.config or {}) if row is not None else {}
            merged = {**current, **updates}
            schema = (feature.manifest or {}).get("config_schema") if feature is not None else None
            if schema:
                defaults = {
                    key: prop["default"]
                    for key, prop in (schema.get("properties") or {}).items()
                    if isinstance(prop, dict) and "default" in prop
                }
                validation = feature_service.validate_config_against_schema({**defaults, **merged}, schema)
                if not validation.valid:
                    raise ValueError("配置验证失败：" + "; ".join(f"{e.field}: {e.message}" for e in validation.errors))
            await feature_service.set_account_feature(
                db,
                ctx.account_id,
                ctx.feature_key,
                enabled=True,
                config=merged,
            )
        ctx.config = {**ctx.config, **updates}

    async def _log(self, ctx: PluginContext, level: str, message: str, **detail: Any) -> None:
        if ctx.log:
            await ctx.log(level, message, **detail)


__all__ = [
    "ChatGPTImageConfig",
    "ChatGPTImagePlugin",
    "PLUGIN_VERSION",
    "_load_config",
    "_parse_image_args",
]
