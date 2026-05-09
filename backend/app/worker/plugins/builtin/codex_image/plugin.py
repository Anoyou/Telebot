"""Codex 图片生成插件 — 通过 Codex API 调用 GPT 图片生成模型。

功能：
  - 纯文本生成图片：,cximg 提示词
  - 参考图+提示词生成：回复图片后 ,cximg 提示词
  - Token 管理：,cximg token <token> 保存 / ,cximg token 查看

配置存储：
  - access_token / model / max_wait_seconds 存储在 account_feature.config
  - 前端可通过 ConfigDialog（模式 C）或专属页面（模式 B）管理

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
import json
import time
from typing import Any

import httpx

from app.worker.plugins.base import Plugin, PluginContext, register

# ─── 常量 ───────────────────────────────────────────────

CODEX_URL = "https://chatgpt.com/backend-api/codex/responses"
DEFAULT_MODEL = "gpt-5.4"
DEFAULT_MAX_WAIT = 600  # 10 分钟

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


async def _update_account_config(ctx: PluginContext, key: str, value: Any) -> None:
    """更新 account_feature.config 中的某个字段并持久化。"""
    from ....db.base import AsyncSessionLocal
    from ....db.models.feature import AccountFeature
    from sqlalchemy import select

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
    reference_image: dict[str, str] | None = None,
    update_status: Any | None = None,
    max_wait: int = DEFAULT_MAX_WAIT,
) -> dict[str, str | None]:
    """调用 Codex 图片生成 API。

    Args:
        prompt: 生成提示词
        token: Bearer token
        model: 模型名
        reference_image: 参考图 {base64, mime_type}，可选
        update_status: 异步状态回调 async (text) -> None
        max_wait: 最大等待秒数

    Returns:
        {image_base64, revised_prompt, status, response_id}
    """
    deadline = time.monotonic() + max_wait

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

    payload = {
        "model": model,
        "instructions": "You are a helpful assistant. Use tools when available.",
        "input": [{"role": "user", "content": content}],
        "store": False,
        "tools": [{"type": "image_generation"}],
        "reasoning": {"effort": "low"},
        "include": [],
        "tool_choice": "auto",
        "parallel_tool_calls": True,
        "prompt_cache_key": None,
        "stream": True,
    }

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    result: dict[str, str | None] = {
        "image_base64": None,
        "revised_prompt": None,
        "status": None,
        "response_id": None,
    }

    # ── 流式读取 SSE ──────────────────────────────────
    remaining_timeout = max(1.0, deadline - time.monotonic())
    async with httpx.AsyncClient(timeout=httpx.Timeout(remaining_timeout)) as client:
        async with client.stream("POST", CODEX_URL, json=payload, headers=headers) as resp:
            if resp.status_code != 200:
                body = await resp.aread()
                raise CodexApiError(resp.status_code, body.decode("utf-8", errors="replace")[:500])

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
                        if event_type == "response.created":
                            result["response_id"] = (obj.get("response") or {}).get("id") or result["response_id"]
                            result["status"] = (obj.get("response") or {}).get("status") or result["status"]
                        elif event_type == "response.image_generation_call.partial_image":
                            result["image_base64"] = obj.get("partial_image_b64") or result["image_base64"]
                            result["revised_prompt"] = obj.get("revised_prompt") or result["revised_prompt"]
                            result["status"] = obj.get("status") or result["status"]
                        elif event_type == "response.completed":
                            resp_obj = obj.get("response", {})
                            result["status"] = resp_obj.get("status") or result["status"]
                            result["response_id"] = resp_obj.get("id") or result["response_id"]

    # 如果流式结束后已经有图片，直接返回
    if result["image_base64"]:
        return result

    # 如果没有 response_id 或状态不是 in_progress，直接返回
    if not result["response_id"] or result["status"] != "in_progress":
        return result

    # ── 轮询补全 ──────────────────────────────────────
    attempt = 0
    while True:
        attempt += 1
        now = time.monotonic()
        if now >= deadline:
            raise TimeoutError("生成超时，已强制停止（超过10分钟）")

        await asyncio.sleep(min(20.0, max(1.0, deadline - now)))

        if time.monotonic() >= deadline:
            raise TimeoutError("生成超时，已强制停止（超过10分钟）")

        if update_status:
            await update_status(f"⏳ 正在等待 Codex 返回结果...（第 {attempt} 次检查）")

        polled = await _poll_codex_response(client_ref=None, token=token, response_id=result["response_id"], deadline=deadline)
        if polled is None:
            continue
        if polled.get("image_base64"):
            return {**result, **polled}
        if polled.get("status") and polled["status"] != "in_progress":
            return {
                **result,
                **polled,
                "image_base64": polled.get("image_base64") or result.get("image_base64"),
                "revised_prompt": polled.get("revised_prompt") or result.get("revised_prompt"),
            }

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
        "Authorization": f"Bearer {token}",
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(remaining_timeout)) as client:
            resp = await client.get(f"{CODEX_URL}/{response_id}", headers=headers)
            data = resp.json().get("response", resp.json())
            if not data or not isinstance(data, dict):
                return None

            image_base64: str | None = None
            revised_prompt: str | None = None

            def visit(value: Any) -> None:
                nonlocal image_base64, revised_prompt
                if not value or not isinstance(value, (dict, list)):
                    return
                if isinstance(value, list):
                    for item in value:
                        visit(item)
                    return
                # dict
                if isinstance(value.get("partial_image_b64"), str) and value["partial_image_b64"]:
                    image_base64 = value["partial_image_b64"]
                if isinstance(value.get("revised_prompt"), str) and value["revised_prompt"]:
                    revised_prompt = value["revised_prompt"]
                for v in value.values():
                    if isinstance(v, (dict, list)):
                        visit(v)

            visit(data)
            return {
                "image_base64": image_base64,
                "revised_prompt": revised_prompt,
                "status": data.get("status") if isinstance(data.get("status"), str) else None,
                "response_id": data.get("id") if isinstance(data.get("id"), str) else response_id,
            }
    except Exception:
        return None


# ─── 异常 ───────────────────────────────────────────────


class CodexApiError(Exception):
    """Codex API 调用失败。"""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"Codex API 错误 ({status_code}): {detail}")


# ─── 帮助文本 ───────────────────────────────────────────

HELP_TEXT = """<b>Codex 图片生成插件</b>

通过 Codex API 调用 GPT 图片生成模型。

<b>用法：</b>
<code>,cximg 提示词</code> — 纯文本生成图片
回复图片后发送 <code>,cximg 提示词</code> — 参考图生成
<code>,cximg token 你的 access token</code> — 保存 Token
<code>,cximg token</code> — 查看当前 Token

<b>Token 获取：</b>
通常在 <code>.codex/auth.json</code> 文件中找到 access_token"""


# ─── 插件主类 ───────────────────────────────────────────


@register
class CodexImagePlugin(Plugin):
    key = "codex_image"
    display_name = "Codex 图片生成"
    description = HELP_TEXT
    message_channels = {"incoming", "outgoing"}

    # ── 命令入口 ──────────────────────────────────────

    async def on_command(
        self, ctx: PluginContext, cmd: str, args: list[str], event
    ) -> bool:
        if cmd != "cximg":
            return False
        try:
            await self._dispatch(ctx, args, event)
        except Exception as exc:
            try:
                await event.edit(f"❌ 操作失败: {_html_escape(str(exc))}")
            except Exception:
                pass
        return True

    # ── 命令分发 ──────────────────────────────────────

    async def _dispatch(
        self, ctx: PluginContext, args: list[str], event
    ) -> None:
        sub = args[0].lower() if args else ""

        if sub == "token":
            await self._cmd_token(ctx, args[1:], event)
            return

        prompt = " ".join(args).strip()
        if not prompt:
            await event.edit(
                f"❌ 请输入提示词，例如：<code>,cximg 一只戴墨镜的柴犬坐在跑车里</code>\n"
                f"• 设置 Token：<code>,cximg token 你的codex access token</code>"
            )
            return

        await self._cmd_generate(ctx, prompt, event)

    # ── Token 管理 ────────────────────────────────────

    async def _cmd_token(
        self, ctx: PluginContext, args: list[str], event
    ) -> None:
        token_value = " ".join(args).strip()
        current_token = _get_config_value(ctx, "access_token", "")

        if not token_value:
            await event.edit(
                f"🔐 当前 Token：{_mask_token(current_token)}\n"
                f"• 设置方式：<code>,cximg token 你的codex access token（通常在 .codex/auth.json）</code>"
            )
            return

        await _update_account_config(ctx, "access_token", token_value)
        # 更新运行时 config
        ctx.config["access_token"] = token_value
        await event.edit("✅ 已保存 Codex Access Token")

    # ── 图片生成 ──────────────────────────────────────

    async def _cmd_generate(
        self, ctx: PluginContext, prompt: str, event
    ) -> None:
        # 获取 token
        token = _get_config_value(ctx, "access_token", "")
        if not token:
            await event.edit(
                "❌ 缺少鉴权，请先使用 <code>,cximg token 你的codex access token（通常在 .codex/auth.json）</code> 保存 Token"
            )
            return

        model = _get_config_value(ctx, "model", DEFAULT_MODEL)
        max_wait = int(_get_config_value(ctx, "max_wait_seconds", DEFAULT_MAX_WAIT))

        # 检查参考图
        reference_image = None
        reply_msg = await event.get_reply_message()
        if reply_msg and reply_msg.media:
            try:
                reference_image = await self._download_reference_image(ctx, reply_msg)
            except Exception as exc:
                await event.edit(f"❌ 参考图下载失败：{_html_escape(str(exc))}")
                return

        # 发送初始状态
        initial_status = (
            "🖼️ 已检测到参考图，正在生成图片..."
            if reference_image
            else "🎨 正在根据提示词生成图片..."
        )
        await event.edit(initial_status)

        # 生成图片
        started_at = time.monotonic()
        last_status_at = 0.0
        current_phase = initial_status

        async def update_status(phase: str) -> None:
            nonlocal last_status_at, current_phase
            current_phase = phase
            now = time.monotonic()
            if now - last_status_at < 1.5:
                return
            last_status_at = now
            elapsed = _format_duration((now - started_at) * 1000)
            try:
                await event.edit(f"{phase}\n⏱️ 已耗时：{elapsed}")
            except Exception:
                pass

        # 心跳更新
        heartbeat_stop = False

        async def heartbeat() -> None:
            nonlocal heartbeat_stop
            while not heartbeat_stop:
                await asyncio.sleep(20)
                if heartbeat_stop:
                    break
                await update_status(current_phase)

        hb_task = asyncio.create_task(heartbeat())

        try:
            result = await _call_codex_image(
                prompt=prompt,
                token=token,
                model=model,
                reference_image=reference_image,
                update_status=update_status,
                max_wait=max_wait,
            )
        except CodexApiError as exc:
            heartbeat_stop = True
            hb_task.cancel()
            elapsed = _format_duration((time.monotonic() - started_at) * 1000)
            await event.edit(
                f"❌ Codex 请求失败 ({exc.status_code})：{_html_escape(exc.detail)}\n⏱️ 耗时：{elapsed}"
            )
            return
        except TimeoutError as exc:
            heartbeat_stop = True
            hb_task.cancel()
            elapsed = _format_duration((time.monotonic() - started_at) * 1000)
            await event.edit(f"❌ {_html_escape(str(exc))}\n⏱️ 耗时：{elapsed}")
            return
        except Exception as exc:
            heartbeat_stop = True
            hb_task.cancel()
            elapsed = _format_duration((time.monotonic() - started_at) * 1000)
            await event.edit(f"❌ 生成失败：{_html_escape(str(exc))}\n⏱️ 耗时：{elapsed}")
            return

        heartbeat_stop = True
        hb_task.cancel()
        elapsed = _format_duration((time.monotonic() - started_at) * 1000)

        if not result.get("image_base64"):
            status_info = result.get("status", "")
            status_text = f"（status: {_html_escape(status_info)}）" if status_info else ""
            await event.edit(f"❌ 未收到生成图片{status_text}\n⏱️ 耗时：{elapsed}")
            return

        # 发送图片
        try:
            image_bytes = base64.b64decode(result["image_base64"])
            caption_parts = [
                f"<b>提示词:</b> {_html_escape(prompt)}",
                f"<b>耗时:</b> {_html_escape(elapsed)}",
            ]
            if result.get("revised_prompt"):
                caption_parts.append(f"<b>修订提示词:</b> {_html_escape(result['revised_prompt'])}")

            client = ctx.client
            if not client:
                await event.edit("❌ 客户端未初始化")
                return

            await client.send_file(
                event.chat_id,
                image_bytes,
                caption="\n".join(caption_parts),
                parse_mode="html",
                reply_to=reply_msg.id if reply_msg else event.id,
            )

            # 删除原命令消息
            try:
                await event.delete()
            except Exception:
                await event.edit("✅ 图片生成完成")

        except Exception as exc:
            await event.edit(f"❌ 图片发送失败：{_html_escape(str(exc))}")

    # ── 参考图下载 ────────────────────────────────────

    async def _download_reference_image(
        self, ctx: PluginContext, reply_msg: Any
    ) -> dict[str, str]:
        """从回复消息中下载参考图，返回 {base64, mime_type}。"""
        from ....media import _download_with_retry

        client = ctx.client
        if not client:
            raise RuntimeError("客户端未初始化")

        # 下载媒体（带 file_reference 过期重试）
        media_bytes = await _download_with_retry(client, reply_msg)
        if not media_bytes:
            raise RuntimeError("未能获取参考图数据")

        # 推断 MIME 类型
        mime_type = "image/png"
        if hasattr(reply_msg, "media") and reply_msg.media:
            doc = getattr(reply_msg.media, "document", None)
            if doc:
                doc_mime = getattr(doc, "mime_type", None)
                if doc_mime and doc_mime.startswith("image/"):
                    mime_type = doc_mime
            elif hasattr(reply_msg.media, "photo"):
                mime_type = "image/jpeg"

        b64 = base64.b64encode(media_bytes).decode("utf-8")
        return {"base64": b64, "mime_type": mime_type}


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


__all__ = [
    "CodexImagePlugin",
    "PLUGIN_CLASS",
    "_dry_run_match",
]
