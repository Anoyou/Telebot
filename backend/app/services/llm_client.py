"""LLM provider 抽象 —— OpenAI / Anthropic / (占位) Ollama。

设计要点：
- 每个 provider 实现 ``LLMClient`` 接口；``complete`` 返 ``LLMResult``
- ``build_client`` 根据 ``LLMProvider`` ORM 行解密 api_key 并装配具体实现
- **安全红线**：解密后的 api_key 仅留在 client 实例内；不打 log，不 audit；
  错误路径用 ``_safe_error_message`` 兜底剥离任何含 sk-/secret-/Bearer 字样
- 视觉支持：``complete(images=[...])`` 接 PNG/JPEG 等字节，由各实现按各自厂商
  vision 协议封装到 multipart content。``images`` 留空 = 纯文本（向后兼容）

调用入口在 worker 进程 (``worker/command.py:_run_ai``)，所以这里 httpx 调用是 async。

V1 仅实现 openai/anthropic 两类常用接口；ollama 走 OpenAI-compatible 端点（``/v1/chat/completions``）由 OpenAIClient 复用。
"""

from __future__ import annotations

import base64
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import httpx

from ..crypto import decrypt_str
from ..db.models.command import (
    LLM_API_FORMAT_ANTHROPIC_MESSAGES,
    LLM_API_FORMAT_CHAT_COMPLETIONS,
    LLM_API_FORMAT_RESPONSES,
    LLM_PROVIDER_OLLAMA,
    LLMProvider,
    default_api_format_for,
)
from .llm_dto import LLMProviderDTO

# 默认调用超时；prompt 较长 / TG 端用户体验角度都不宜过长
_HTTP_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
# 本地桥接（如 grok-bridge）需要等待浏览器 JS 执行 + LLM 生成，超时更长
_LOCAL_TIMEOUT = httpx.Timeout(180.0, connect=10.0)

# ── Retry 策略常量 ──────────────────────────────────────────
# 最大重试次数（不含首次调用）
_MAX_RETRIES = 3
# 重试睡票基数（秒），指数退避
_RETRY_BASE_DELAY = 1.0
# 最大退避时间（秒）
_RETRY_MAX_DELAY = 30.0


@dataclass
class LLMResult:
    """LLM 调用的统一结果。"""

    text: str           # 模型回答正文
    model: str          # 实际使用的模型名（便于 TG 内回显）
    input_tokens: int   # 入 tokens；若供应商不返就给 0
    output_tokens: int  # 出 tokens；若供应商不返就给 0
    image_urls: list = field(default_factory=list)  # LLM 生成的图片 URL（如 Grok 文生图）
    image_data: list = field(default_factory=list)  # LLM 生成的图片 base64 data URI（如 Grok 文生图）


def _sniff_image_mime(data: bytes) -> str:
    """根据 magic bytes 判断图片 MIME 类型。

    支持 JPEG / PNG / WebP / GIF；其它一律返回 ``image/jpeg``（绝大多数 vision 模型
    会按 jpeg 兜底解码，比报错稳）。

    与 OpenAI/Anthropic 对 ``image/...`` 的接受集对齐。
    """
    if len(data) < 12:
        return "image/jpeg"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return "image/jpeg"


def _to_data_url(data: bytes) -> str:
    """把图片字节编码为 ``data:image/...;base64,...`` data URL。

    OpenAI Chat Completions Vision / mimo / GLM-4V 等都接受这种 inline 形式，
    省去托管图床的麻烦。"""
    mime = _sniff_image_mime(data)
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}"


class LLMClient(ABC):
    """provider-agnostic 调用接口。"""

    @abstractmethod
    async def complete(
        self,
        system: str,
        user: str,
        max_tokens: int = 512,
        images: list[bytes] | None = None,
    ) -> LLMResult:
        """以 system + user 拼 prompt（可附图），返回回答与 token 统计。

        ``images`` 留空 = 纯文本路径（向后兼容老调用）；
        非空时各实现按自己的 vision 协议把图片塞进 user message 的 content 块里。
        """
        raise NotImplementedError

    async def transcribe(self, audio: bytes, model: str) -> str:
        """语音转写：把音频字节喂给 ``/audio/transcriptions`` 之类的 STT 端点。

        默认抛 ``NotImplementedError``——需要每个具体 client 自己实现（Anthropic 暂无）。

        ``model`` 由调用方指定（一般是 ``whisper-1``）；不复用 ``self._model``
        因为聊天模型与 STT 模型几乎总是不同的（gpt-4o-mini vs whisper-1）。
        """
        raise NotImplementedError(
            "本 provider 不支持语音转写（仅 OpenAI 兼容 /audio/transcriptions 端点支持）"
        )


# ────────────────────────────────────────────────────────────
# OpenAI / OpenAI 兼容（含 Ollama）
# ────────────────────────────────────────────────────────────


class OpenAIClient(LLMClient):
    """OpenAI Chat Completions 兼容协议。

    用 ``/v1/chat/completions`` 端点；Ollama (``/v1/chat/completions`` since 0.1.20+) 也走这里。
    """

    def __init__(
        self,
        api_key: str,
        base_url: str | None,
        model: str,
        proxy_url: str | None = None,
    ):
        self._api_key = api_key
        self._base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        self._model = model
        self._proxy_url = proxy_url

    async def complete(
        self,
        system: str,
        user: str,
        max_tokens: int = 512,
        images: list[bytes] | None = None,
    ) -> LLMResult:
        url = f"{self._base_url}/chat/completions"
        # Ollama 部署可能不需要 api_key；为空时不下发 Authorization 头
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        # 视觉路径：content 改成数组，先 text 再 image_url（OpenAI / mimo / GLM-4V 均如此）
        if images:
            user_content: object = [
                {"type": "text", "text": user},
                *[
                    {"type": "image_url", "image_url": {"url": _to_data_url(img)}}
                    for img in images
                ],
            ]
        else:
            user_content = user
        body = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
            "max_tokens": max_tokens,
        }
        # httpx 0.28+ 用 proxy=<str> 单参数；socks5 需要 httpx[socks] 安装的 socksio
        # 当 proxy_url 为空时，显式 trust_env=False 避免 httpx 读取环境变量中的
        # HTTP_PROXY / NO_PROXY（NO_PROXY 含 ::1 会导致 httpx InvalidURL 崩溃）
        # 本地桥接（grok-bridge 等 localhost 服务）需要更长超时：浏览器 JS 执行 +
        # LLM 生成 + 图片 XHR 下载，整个过程可能超过 30 秒
        _is_local = "127.0.0.1" in self._base_url or "localhost" in self._base_url
        client_kwargs: dict[str, object] = {"timeout": _LOCAL_TIMEOUT if _is_local else _HTTP_TIMEOUT}
        if self._proxy_url:
            client_kwargs["proxy"] = self._proxy_url
        else:
            client_kwargs["trust_env"] = False
        try:
            async with httpx.AsyncClient(**client_kwargs) as cli:
                resp = await cli.post(url, headers=headers, json=body)
        except httpx.HTTPError as exc:
            # 很多 httpx 异常 str() 是空（典型 SSL 握手 / ConnectError("")）；
            # 把异常类名 + 目标 host 也透出来，否则用户只看到 "网络异常: " 没法排查
            raise LLMError(
                _safe_error_message(
                    _describe_http_error(exc, self._base_url),
                    self._api_key,
                )
            ) from None
        if resp.status_code >= 400:
            # 不要把 api_key 回显到错误里；构造前先剥离
            raise LLMError(
                _safe_error_message(
                    f"OpenAI 接口返回 {resp.status_code}: {resp.text[:200]}{_hint_for_status(resp.status_code)}",
                    self._api_key,
                )
            )
        try:
            data = resp.json()
        except json.JSONDecodeError as exc:
            raise LLMError(f"OpenAI 返回非 JSON: {exc}") from None

        # 标准 OpenAI 形态：choices[0].message.content
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"OpenAI 返回结构异常: {exc}") from None

        usage = data.get("usage") or {}
        raw_images = data.get("images") or []
        if not isinstance(raw_images, list):
            raw_images = []
        # 兼容两种格式：
        #   旧: ["url1", "url2"]（纯 URL 列表）
        #   新: [{"url": "url1", "data": "base64..."}, ...]（带 base64 数据）
        image_urls = []
        image_data = []
        for item in raw_images:
            if isinstance(item, dict):
                if item.get("url"):
                    image_urls.append(item["url"])
                if item.get("data"):
                    image_data.append(item["data"])
            elif isinstance(item, str):
                image_urls.append(item)
        return LLMResult(
            text=text.strip(),
            model=str(data.get("model", self._model)),
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            image_urls=image_urls,
            image_data=image_data,
        )

    async def transcribe(self, audio: bytes, model: str) -> str:
        """OpenAI / 兼容厂商的 ``POST /audio/transcriptions``（Whisper 协议）。

        multipart/form-data 上传：``file=<bytes>``、``model=<id>``、可选 ``response_format=json``。
        返回 JSON ``{"text": "..."}``。
        """
        if not audio:
            raise LLMError("音频字节为空")
        if not model:
            raise LLMError("transcribe() 必须指定 model（如 'whisper-1'）")
        url = f"{self._base_url}/audio/transcriptions"
        headers = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        # 文件名给个通用后缀，让上游按二进制 audio 流处理
        files = {
            "file": ("audio.ogg", audio, "audio/ogg"),
        }
        data = {"model": model, "response_format": "json"}
        _is_local = "127.0.0.1" in self._base_url or "localhost" in self._base_url
        client_kwargs: dict[str, object] = {"timeout": _LOCAL_TIMEOUT if _is_local else _HTTP_TIMEOUT}
        if self._proxy_url:
            client_kwargs["proxy"] = self._proxy_url
        else:
            client_kwargs["trust_env"] = False
        try:
            async with httpx.AsyncClient(**client_kwargs) as cli:
                resp = await cli.post(url, headers=headers, files=files, data=data)
        except httpx.HTTPError as exc:
            raise LLMError(
                _safe_error_message(
                    _describe_http_error(exc, self._base_url),
                    self._api_key,
                )
            ) from None
        if resp.status_code >= 400:
            raise LLMError(
                _safe_error_message(
                    f"STT 接口返回 {resp.status_code}: {resp.text[:200]}{_hint_for_status(resp.status_code)}",
                    self._api_key,
                )
            )
        try:
            payload = resp.json()
        except json.JSONDecodeError as exc:
            raise LLMError(f"STT 返回非 JSON: {exc}") from None
        text = payload.get("text") if isinstance(payload, dict) else None
        if not isinstance(text, str):
            raise LLMError(f"STT 返回缺少 text 字段：{str(payload)[:200]}")
        return text.strip()


# ────────────────────────────────────────────────────────────
# Anthropic Messages API
# ────────────────────────────────────────────────────────────


class AnthropicClient(LLMClient):
    """Anthropic ``/v1/messages`` 协议（Claude 系列）。"""

    # 文档要求的版本头；新版本兼容旧调用
    _ANTHROPIC_VERSION = "2023-06-01"

    def __init__(
        self,
        api_key: str,
        base_url: str | None,
        model: str,
        proxy_url: str | None = None,
    ):
        self._api_key = api_key
        self._base_url = (base_url or "https://api.anthropic.com/v1").rstrip("/")
        self._model = model
        self._proxy_url = proxy_url

    async def complete(
        self,
        system: str,
        user: str,
        max_tokens: int = 512,
        images: list[bytes] | None = None,
    ) -> LLMResult:
        url = f"{self._base_url}/messages"
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": self._ANTHROPIC_VERSION,
            "Content-Type": "application/json",
            # 模拟 Claude 客户端的关键 headers
            # Anyrouter 等反代强制校验 anthropic-beta 含 context-1m-2025-08-07
            "anthropic-beta": "claude-code-20250219,context-1m-2025-08-07,interleaved-thinking-2025-05-14,effort-2025-11-24",
            "anthropic-dangerous-direct-browser-access": "true",
            "x-app": "cli",
        }
        # 视觉路径：Anthropic 用 {"type":"image","source":{"type":"base64",...}}
        # 与 OpenAI 的 image_url 协议**不一样**，要分别构造
        if images:
            user_content: object = [
                *[
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": _sniff_image_mime(img),
                            "data": base64.b64encode(img).decode("ascii"),
                        },
                    }
                    for img in images
                ],
                {"type": "text", "text": user},
            ]
        else:
            user_content = user
        body = {
            "model": self._model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user_content}],
            # 使用流式（SSE）模式；Anyrouter 等 Claude Code 反代依赖流式协议分发
            "stream": True,
        }
        _is_local = "127.0.0.1" in self._base_url or "localhost" in self._base_url
        client_kwargs: dict[str, object] = {"timeout": _LOCAL_TIMEOUT if _is_local else _HTTP_TIMEOUT}
        if self._proxy_url:
            client_kwargs["proxy"] = self._proxy_url
        else:
            client_kwargs["trust_env"] = False

        # ── SSE 流式响应解析 ──────────────────────────────
        # 事件流生命周期：
        #   message_start → content_block_start → content_block_delta(×N)
        #   → content_block_stop → message_delta → message_stop
        #
        # 我们只需要：
        #   - message_start.message.model / .usage  → 模型名 + input_tokens
        #   - content_block_delta.delta.text         → 文本增量
        #   - message_delta.usage.output_tokens      → output_tokens
        text_parts: list[str] = []
        model_name = self._model
        input_tokens = 0
        output_tokens = 0

        try:
            async with httpx.AsyncClient(**client_kwargs) as cli:
                async with cli.stream("POST", url, headers=headers, json=body) as resp:
                    if resp.status_code >= 400:
                        # 流式模式下，错误仍然可能作为普通 JSON 返回
                        error_body = ""
                        async for chunk in resp.aiter_text():
                            error_body += chunk
                            if len(error_body) > 500:
                                break
                        raise LLMError(
                            _safe_error_message(
                                f"Anthropic 接口返回 {resp.status_code}: {error_body[:200]}{_hint_for_status(resp.status_code)}",
                                self._api_key,
                            )
                        )
                    # 逐行解析 SSE 事件
                    current_event = ""
                    async for line in resp.aiter_lines():
                        line = line.rstrip("\r\n")
                        if line.startswith("event: "):
                            current_event = line[7:].strip()
                            continue
                        if line.startswith("data: "):
                            raw = line[6:]
                            try:
                                payload = json.loads(raw)
                            except json.JSONDecodeError:
                                continue
                            if current_event == "message_start":
                                msg = payload.get("message") or {}
                                model_name = str(msg.get("model", self._model))
                                usage = msg.get("usage") or {}
                                input_tokens = int(usage.get("input_tokens") or 0)
                            elif current_event == "content_block_delta":
                                delta = payload.get("delta") or {}
                                if delta.get("type") == "text_delta":
                                    text_parts.append(delta.get("text", ""))
                            elif current_event == "message_delta":
                                usage = payload.get("usage") or {}
                                output_tokens = int(usage.get("output_tokens") or 0)
                            # message_stop / content_block_start / content_block_stop → 忽略
                            continue
                        # 空行 = 事件分隔符（SSE 规范）
                        if not line:
                            current_event = ""
        except LLMError:
            raise
        except httpx.HTTPError as exc:
            raise LLMError(
                _safe_error_message(
                    _describe_http_error(exc, self._base_url),
                    self._api_key,
                )
            ) from None

        text = "".join(text_parts).strip()
        if not text:
            raise LLMError("Anthropic 返回空内容（SSE 流中未收到 text_delta 事件）")

        return LLMResult(
            text=text,
            model=model_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )


# ────────────────────────────────────────────────────────────
# OpenAI Responses API（POST /responses，2024 出的新协议）
# ────────────────────────────────────────────────────────────


class ResponsesClient(LLMClient):
    """OpenAI Responses API（POST ``/responses``）。

    与 chat/completions 的差异：
    - 入参 ``input=[{role, content}]`` + ``instructions`` + ``model`` + ``max_output_tokens``
    - 出参 ``output=[{type:"message", content:[{type:"output_text", text:"..."}]}]``
      也可能直接给 ``output_text`` 顶层字符串（不同实现略有差异，都做兼容）
    - usage 字段是 ``input_tokens`` / ``output_tokens``（不是 prompt_tokens / completion_tokens）

    很多国内 OpenAI 兼容反代（如 anyrouter）只接 ``/responses`` 不接 ``/chat/completions``，
    所以这条 client 是必须的。
    """

    def __init__(
        self,
        api_key: str,
        base_url: str | None,
        model: str,
        proxy_url: str | None = None,
    ):
        self._api_key = api_key
        self._base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        self._model = model
        self._proxy_url = proxy_url

    async def complete(
        self,
        system: str,
        user: str,
        max_tokens: int = 512,
        images: list[bytes] | None = None,
    ) -> LLMResult:
        url = f"{self._base_url}/responses"
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        # 视觉路径：Responses API 的 content 是 [{"type":"input_text"}, {"type":"input_image"}]
        # （注意：不是 chat/completions 的 image_url 名字；OpenAI 把这两套协议命名拆开了）
        if images:
            input_content: object = [
                {"type": "input_text", "text": user},
                *[
                    {"type": "input_image", "image_url": _to_data_url(img)}
                    for img in images
                ],
            ]
        else:
            input_content = user
        body = {
            "model": self._model,
            # 用 instructions 字段传 system；input 列表按 role/content 给 user 输入
            "instructions": system,
            "input": [
                {"role": "user", "content": input_content},
            ],
            # Responses API 用 max_output_tokens（不是 max_tokens）
            "max_output_tokens": max_tokens,
        }

        _is_local = "127.0.0.1" in self._base_url or "localhost" in self._base_url
        client_kwargs: dict[str, object] = {"timeout": _LOCAL_TIMEOUT if _is_local else _HTTP_TIMEOUT}
        if self._proxy_url:
            client_kwargs["proxy"] = self._proxy_url
        else:
            client_kwargs["trust_env"] = False
        try:
            async with httpx.AsyncClient(**client_kwargs) as cli:
                resp = await cli.post(url, headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise LLMError(
                _safe_error_message(
                    _describe_http_error(exc, self._base_url),
                    self._api_key,
                )
            ) from None

        if resp.status_code >= 400:
            raise LLMError(
                _safe_error_message(
                    f"Responses 接口返回 {resp.status_code}: {resp.text[:200]}{_hint_for_status(resp.status_code)}",
                    self._api_key,
                )
            )

        try:
            data = resp.json()
        except json.JSONDecodeError as exc:
            raise LLMError(f"Responses 返回非 JSON: {exc}") from None

        # 解析 output：兼容多种形态
        # 形态 1：data["output_text"] = "..."（部分实现的便利字段）
        # 形态 2：data["output"] = [{"type":"message","content":[{"type":"output_text","text":"..."}]}]
        text = ""
        ot = data.get("output_text")
        if isinstance(ot, str):
            text = ot
        else:
            output_list = data.get("output") or []
            text_parts: list[str] = []
            for item in output_list if isinstance(output_list, list) else []:
                if not isinstance(item, dict):
                    continue
                content = item.get("content") or []
                if isinstance(content, list):
                    for c in content:
                        if not isinstance(c, dict):
                            continue
                        t = c.get("text")
                        # type 通常是 output_text；保险起见全收
                        if isinstance(t, str):
                            text_parts.append(t)
            text = "".join(text_parts)

        # usage：input_tokens / output_tokens
        usage = data.get("usage") or {}
        return LLMResult(
            text=text.strip(),
            model=str(data.get("model", self._model)),
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
        )

    async def transcribe(self, audio: bytes, model: str) -> str:
        """OpenAI Responses 协议厂商一般也在同一个 base_url 下挂 ``/audio/transcriptions``——
        直接复用 Whisper 协议（与 ``OpenAIClient.transcribe`` 同实现）。"""
        # 复用 OpenAIClient 的 transcribe；二者只差 chat/responses 那条主 endpoint
        return await OpenAIClient.transcribe(self, audio, model)  # type: ignore[arg-type]


# ────────────────────────────────────────────────────────────
# 工厂 & 安全工具
# ────────────────────────────────────────────────────────────


class LLMError(Exception):
    """LLM 调用层统一异常；message 已脱敏。"""

    def __init__(self, message: str, *, retryable: bool = False, fallback: bool = False):
        super().__init__(message)
        self.retryable = retryable  # 是否可重试（timeout/429/5xx/网络错误）
        self.fallback = fallback     # 是否可 fallback（网络错误/非认证错误）


class LLMCallFailed(Exception):
    """LLM 调用失败（捕获后用于 fallback 决策）。"""

    def __init__(
        self,
        message: str,
        provider_id: int | None = None,
        provider_name: str | None = None,
        error_type: str | None = None,
        status_code: int | None = None,
        retryable: bool = False,
    ):
        super().__init__(message)
        self.provider_id = provider_id
        self.provider_name = provider_name
        self.error_type = error_type  # "timeout" / "network" / "rate_limit" / "auth" / "server_error"
        self.status_code = status_code
        self.retryable = retryable


def _is_retryable_error(exc: Exception, status_code: int | None = None) -> bool:
    """判断错误是否可重试。

    可重试：timeout / ConnectError / 网络错误 / 429 / 5xx
    不可重试：400 / 401 / 403 / 404（认证/配置错误，重试无意义）
    """
    if status_code is not None:
        # 4xx 客户端错误中，只有 429 限流可重试
        if status_code == 429:
            return True
        # 5xx 服务端错误可重试
        if 500 <= status_code < 600:
            return True
        # 400/401/403/404 等不可重试
        return False

    # 无 status_code 时，判断异常类型
    exc_name = type(exc).__name__
    retryable_types = {
        "TimeoutException",
        "ConnectTimeout",
        "ReadTimeout",
        "WriteTimeout",
        "PoolTimeout",
        "ConnectError",
        "ReadError",
        "WriteError",
        "ProxyError",
        "SSLError",
        "ProtocolError",
        "HTTPError",  # httpx 基础异常，包含各种网络问题
    }
    return exc_name in retryable_types


def _classify_error(exc: Exception, status_code: int | None = None) -> str:
    """分类错误类型（用于日志和用户提示）。"""
    if status_code is not None:
        if status_code == 429:
            return "rate_limit"
        if status_code == 401 or status_code == 403:
            return "auth"
        if status_code == 404:
            return "not_found"
        if 500 <= status_code < 600:
            return "server_error"
        if 400 <= status_code < 500:
            return "client_error"
        return "unknown"

    exc_name = type(exc).__name__
    if "Timeout" in exc_name:
        return "timeout"
    if "Connect" in exc_name or "Proxy" in exc_name or "SSL" in exc_name:
        return "network"
    if "HTTP" in exc_name:
        return "network"
    return "unknown"


def _compute_retry_delay(attempt: int, base: float = _RETRY_BASE_DELAY, max_delay: float = _RETRY_MAX_DELAY) -> float:
    """计算指数退避延迟：base * 2^(attempt-1)，加抖动后限制在 max_delay 内。"""
    import random
    delay = base * (2 ** (attempt - 1))
    # 加 ±25% 抖动
    jitter = delay * 0.25 * (2 * random.random() - 1)
    return min(delay + jitter, max_delay)


def _safe_error_message(msg: str, api_key: str | None) -> str:
    """把可能含敏感信息的错误文本脱敏。

    - 若 api_key 出现在 msg 中，整段替换为 ``<redacted>``
    - 兜底过滤 ``sk-...`` / ``Bearer ...`` 形态
    - 过滤其他常见 token 格式
    """
    import re

    if not msg:
        return ""
    out = msg
    if api_key:
        out = out.replace(api_key, "<redacted>")
    # 统一截断，避免长串敏感数据透出
    if len(out) > 400:
        out = out[:400] + "..."
    # 正则过滤常见 token 格式（独立于 api_key 变量）
    # sk- 开头的 key
    out = re.sub(r"sk-[A-Za-z0-9_-]{8,}", "<sk>", out)
    # Bearer token
    out = re.sub(r"Bearer\s+[A-Za-z0-9_.\-]{8,}", "Bearer <token>", out)
    # 常见的其他 key 格式
    out = re.sub(r"(?i)(api[_-]?key|secret|token)\s*[=:]\s*['\"]?[A-Za-z0-9_.\-]{8,}['\"]?", r"\1=<redacted>", out)
    return out


# Cloudflare 5xx 错误码的人话翻译（用户最常碰到 520，且不是应用问题）
_CF_5XX_HINTS: dict[int, str] = {
    520: "上游返回异常（Cloudflare 520 = 反代连不上目标 / 上游崩了；不是本项目代码问题）",
    521: "上游服务器拒绝连接（Cloudflare 521）",
    522: "上游连接超时（Cloudflare 522）",
    523: "上游不可达（Cloudflare 523）",
    524: "上游处理超时（Cloudflare 524；常见于慢模型 + 反代严格超时）",
    525: "SSL 握手失败（Cloudflare 525）",
    526: "SSL 证书无效（Cloudflare 526）",
}


def _hint_for_status(status: int) -> str:
    """根据 HTTP 状态码给一句人话提示，便于用户区分"我配错了"还是"反代/上游挂了"。"""
    if status in _CF_5XX_HINTS:
        return f"  ↳ {_CF_5XX_HINTS[status]}"
    if status == 401 or status == 403:
        return "  ↳ api_key 无效 / 权限不够"
    if status == 404:
        return "  ↳ model 名不对 / 端点不存在；试试 Fetch 模型列表选一条已支持的"
    if status == 429:
        return "  ↳ 限流，等会儿再试 / 或换一条不那么紧的反代"
    if 500 <= status < 600:
        return "  ↳ 服务器侧错误（不是 api_key / model 问题）"
    return ""


def _describe_http_error(exc: BaseException, base_url: str | None) -> str:
    """把 httpx 异常翻译成"用户能看懂的报错"。

    httpx 很多异常的 ``str(exc)`` 是空字符串（``ConnectError("")`` / SSL 握手错），
    单纯透 ``f"网络异常: {exc}"`` 会变成 "网络异常: " 难以排查。这里：

    - 总带上异常类名：``ConnectError`` / ``ReadTimeout`` / ``ProxyError`` / ``SSLError`` 等
    - 总带上目标 host（不带路径）：让用户一眼看出是 anthropic.com 还是 openai.com 不通
    - 细节为空时给一个建议性提示（"可能是 SSL/DNS/代理"）
    """
    name = type(exc).__name__
    detail = str(exc).strip()
    host = ""
    if base_url:
        try:
            from urllib.parse import urlparse

            host = urlparse(base_url).netloc or base_url
        except Exception:  # noqa: BLE001
            host = base_url

    parts = [f"网络异常 {name}"]
    if host:
        parts.append(f"→ {host}")
    if detail:
        parts.append(f": {detail}")
    else:
        parts.append("（无详情；常见原因：连不到目标域名 / SSL 握手失败 / 代理未生效）")
    return " ".join(parts)


def build_client(
    provider_row: LLMProvider,
    override_model: str | None = None,
    proxy_url: str | None = None,
) -> LLMClient:
    """根据 ORM 行装配具体 LLMClient。

    协议路由（以 ``api_format`` 为准；老数据没这字段时按 ``provider`` 厂商兜底）：
    - ``chat_completions``     → ``OpenAIClient``        ``POST /chat/completions``
    - ``responses``            → ``ResponsesClient``     ``POST /responses``
    - ``anthropic_messages``   → ``AnthropicClient``     ``POST /messages``

    - 解密 api_key（若该 provider 行没有 key 字段则 client 拿空串）
    - ``override_model`` 优先于 provider.default_model
    - ``proxy_url`` 给 None 表示直连；socks5/http/https 都接受 httpx URL
    """
    api_key = ""
    if provider_row.api_key_enc:
        api_key = decrypt_str(provider_row.api_key_enc)
    model = (override_model or provider_row.default_model or "").strip()
    if not model:
        raise ValueError("LLM provider 没配 default_model，且当次调用也未提供 model 覆盖")

    # api_format 优先；老数据兼容（无字段时按 provider 厂商兜底）
    fmt = (
        getattr(provider_row, "api_format", None)
        or default_api_format_for(provider_row.provider)
    )

    if fmt == LLM_API_FORMAT_CHAT_COMPLETIONS:
        # ollama 兜底 base_url（chat_completions 也兼容）
        base = provider_row.base_url
        if not base and provider_row.provider == LLM_PROVIDER_OLLAMA:
            base = "http://localhost:11434/v1"
        return OpenAIClient(
            api_key="" if provider_row.provider == LLM_PROVIDER_OLLAMA else api_key,
            base_url=base,
            model=model,
            proxy_url=proxy_url,
        )
    if fmt == LLM_API_FORMAT_RESPONSES:
        return ResponsesClient(
            api_key=api_key, base_url=provider_row.base_url, model=model, proxy_url=proxy_url
        )
    if fmt == LLM_API_FORMAT_ANTHROPIC_MESSAGES:
        return AnthropicClient(
            api_key=api_key, base_url=provider_row.base_url, model=model, proxy_url=proxy_url
        )
    raise ValueError(f"未知 api_format: {fmt}")


def build_client_from_dto(
    dto: LLMProviderDTO,
    override_model: str | None = None,
    proxy_url: str | None = None,
) -> LLMClient:
    """根据 LLMProviderDTO 装配具体 LLMClient。

    与 build_client 等效，但输入是 DTO 而非 ORM 行。
    proxy_url 以参数传入优先，其次用 dto.proxy_url。

    Args:
        dto: LLMProviderDTO 对象
        override_model: 覆盖模型名（优先于 dto.default_model）
        proxy_url: 代理 URL（优先于 dto.proxy_url）
    """
    api_key = ""
    if dto.api_key_enc:
        api_key = decrypt_str(dto.api_key_enc)
    model = (override_model or dto.default_model or "").strip()
    if not model:
        raise ValueError("LLM provider 没配 default_model，且当次调用也未提供 model 覆盖")

    # api_format 优先；兜底按 provider 厂商
    fmt = dto.api_format or default_api_format_for(dto.provider)

    # proxy 合并：参数传入 > dto 内置
    final_proxy = proxy_url if proxy_url else dto.proxy_url

    if fmt == LLM_API_FORMAT_CHAT_COMPLETIONS:
        base = dto.base_url
        if not base and dto.is_ollama:
            base = "http://localhost:11434/v1"
        return OpenAIClient(
            api_key="" if dto.is_ollama else api_key,
            base_url=base,
            model=model,
            proxy_url=final_proxy,
        )
    if fmt == LLM_API_FORMAT_RESPONSES:
        return ResponsesClient(
            api_key=api_key, base_url=dto.base_url, model=model, proxy_url=final_proxy
        )
    if fmt == LLM_API_FORMAT_ANTHROPIC_MESSAGES:
        return AnthropicClient(
            api_key=api_key, base_url=dto.base_url, model=model, proxy_url=final_proxy
        )
    raise ValueError(f"未知 api_format: {fmt}")


__all__ = [
    "AnthropicClient",
    "LLMCallFailed",
    "LLMClient",
    "LLMError",
    "LLMResult",
    "OpenAIClient",
    "ResponsesClient",
    "build_client",
    "build_client_from_dto",
]
