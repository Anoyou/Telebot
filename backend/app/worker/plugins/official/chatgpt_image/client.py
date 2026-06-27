"""ChatGPT Web 图片链路的轻量异步客户端。

实现思路参考 basketikun/chatgpt2api 的图片生成链路，但只保留 TelePilot
Telegram 插件需要的能力：账号信息刷新、图片生成、图片编辑和代理测试。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import random
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any

import httpx

from .image_utils import ImageInfo, image_info

CHATGPT_BASE_URL = "https://chatgpt.com"
DEFAULT_CLIENT_VERSION = "prod-be885abbfcfe7b1f511e88b3003d9ee44757fbad"
DEFAULT_CLIENT_BUILD_NUMBER = "5955942"
DEFAULT_POW_SCRIPT = "https://chatgpt.com/backend-api/sentinel/sdk.js"
CODEX_IMAGE_MODEL = "codex-gpt-image-2"


class ChatGPTImageError(RuntimeError):
    """用户可读的 ChatGPT 图片错误。"""


class InvalidAccessTokenError(ChatGPTImageError):
    """access token 已失效或无权限。"""


class ImagePollTimeoutError(ChatGPTImageError):
    """会话结果轮询超时。"""


class UpstreamHTTPError(ChatGPTImageError):
    def __init__(self, context: str, status_code: int, body: Any) -> None:
        self.context = context
        self.status_code = status_code
        self.body = body
        super().__init__(f"{context} failed: HTTP {status_code}: {_short_body(body)}")


@dataclass(frozen=True)
class ImageRequest:
    prompt: str
    model: str
    count: int = 1
    size: str = "1:1"
    preferred_format: str = "png"
    reference_images: list[bytes] = field(default_factory=list)


@dataclass(frozen=True)
class ImageResult:
    data: bytes
    mime_type: str
    width: int
    height: int
    extension: str
    conversation_id: str = ""
    file_id: str = ""


@dataclass
class ConversationState:
    text: str = ""
    conversation_id: str = ""
    file_ids: list[str] = field(default_factory=list)
    sediment_ids: list[str] = field(default_factory=list)
    blocked: bool = False
    tool_invoked: bool | None = None
    turn_use_case: str = ""


@dataclass(frozen=True)
class ChatRequirements:
    token: str
    proof_token: str = ""
    turnstile_token: str = ""
    so_token: str = ""
    raw_finalize: dict[str, Any] | None = None


def new_uuid() -> str:
    return str(uuid.uuid4())


def build_image_prompt(prompt: str, size: str | None) -> str:
    size = str(size or "").strip()
    if not size or size == "auto":
        return prompt
    hints = {
        "1:1": "输出为 1:1 正方形构图，主体居中，适合正方形画幅。",
        "16:9": "输出为 16:9 横屏构图，适合宽画幅展示。",
        "9:16": "输出为 9:16 竖屏构图，适合竖版画幅。",
        "4:3": "输出为 4:3 比例，兼顾宽度与高度，适合展示画面细节。",
        "3:4": "输出为 3:4 比例，纵向构图，适合人物肖像或竖向场景。",
    }
    hint = hints.get(size, f"输出图片，画幅要求为：{size}。")
    return f"{prompt.strip()}\n\n{hint}"


def is_token_invalid_error(message: str) -> bool:
    text = str(message or "").lower()
    return (
        "token_invalidated" in text
        or "token_revoked" in text
        or "authentication token has been invalidated" in text
        or "invalidated oauth token" in text
        or "http 401" in text
    )


def humanize_error(exc: BaseException) -> str:
    text = str(exc or "")
    lower = text.lower()
    if isinstance(exc, InvalidAccessTokenError) or is_token_invalid_error(text):
        return "token 已失效或登录态过期，请更换 ChatGPT access token。"
    if isinstance(exc, ImagePollTimeoutError):
        return str(exc)
    if isinstance(exc, UpstreamHTTPError):
        if exc.status_code == 401:
            return "ChatGPT 返回 401，token 已失效或没有权限。"
        if exc.status_code == 403:
            return "ChatGPT 返回 403，可能触发风控或代理不可用。"
        if exc.status_code == 429:
            return "ChatGPT 返回 429，账号额度不足或临时限流。"
        if exc.status_code >= 500:
            return f"ChatGPT 服务端暂时异常（HTTP {exc.status_code}）。"
    if "proxy" in lower:
        return f"代理请求失败：{_safe_error(text)}"
    if "timeout" in lower or "timed out" in lower:
        return "请求超时，可能是上游生成排队、网络或代理较慢。"
    if "arkose" in lower:
        return "ChatGPT 要求 Arkose 校验，当前插件无法自动通过这一步。"
    return _safe_error(text) or exc.__class__.__name__


def _short_body(body: Any, limit: int = 360) -> str:
    if isinstance(body, (dict, list)):
        try:
            text = json.dumps(body, ensure_ascii=False)
        except (TypeError, ValueError):
            text = repr(body)
    else:
        text = str(body)
    return _safe_error(text, limit)


def _safe_error(text: str, limit: int = 360) -> str:
    out = str(text or "")
    out = re.sub(r"Bearer\s+[A-Za-z0-9_.\-]{8,}", "Bearer <redacted>", out, flags=re.I)
    out = re.sub(r"(?i)(token|access_token|api_key|secret|password)(\s*[=:]\s*)[^\s,;\"']{6,}", r"\1\2<redacted>", out)
    if len(out) > limit:
        out = out[:limit] + "..."
    return out


def _model_slug(model: str) -> str:
    model = str(model or "").strip()
    if not model or model == "auto":
        return "auto"
    if model == "gpt-image-2":
        return "gpt-5-3"
    if model == CODEX_IMAGE_MODEL:
        return CODEX_IMAGE_MODEL
    return model


class _ScriptSrcParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.script_sources: list[str] = []
        self.data_build = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "script":
            return
        attrs_dict = dict(attrs)
        src = attrs_dict.get("src")
        if not src:
            return
        self.script_sources.append(src)
        match = re.search(r"c/[^/]*/_", src)
        if match:
            self.data_build = match.group(0)


def parse_pow_resources(html_content: str) -> tuple[list[str], str]:
    parser = _ScriptSrcParser()
    parser.feed(html_content or "")
    scripts = parser.script_sources or [DEFAULT_POW_SCRIPT]
    data_build = parser.data_build
    if not data_build:
        match = re.search(r'<html[^>]*data-build="([^"]*)"', html_content or "")
        if match:
            data_build = match.group(1)
    return scripts, data_build


def _legacy_parse_time() -> str:
    now = datetime.now(timezone(timedelta(hours=-5)))
    return now.strftime("%a %b %d %Y %H:%M:%S") + " GMT-0500 (Eastern Standard Time)"


def _pow_config(user_agent: str, scripts: list[str], data_build: str) -> list[Any]:
    return [
        random.choice([3000, 4000, 5000]),
        _legacy_parse_time(),
        4294705152,
        0,
        user_agent,
        random.choice(scripts or [DEFAULT_POW_SCRIPT]),
        data_build,
        "zh-CN",
        "zh-CN,zh,en",
        0,
        random.choice(["webdriver−false", "vendor−Google Inc.", "language−zh-CN"]),
        random.choice(["location", "document"]),
        random.choice(["window", "self", "navigator", "performance"]),
        time.perf_counter() * 1000,
        new_uuid(),
        "",
        random.choice([8, 16, 24, 32]),
        time.time() * 1000 - (time.perf_counter() * 1000),
    ]


def _pow_generate(seed: str, difficulty: str, config: list[Any], limit: int = 500000) -> tuple[str, bool]:
    target = bytes.fromhex(difficulty)
    diff_len = len(difficulty) // 2
    seed_bytes = seed.encode()
    static_1 = (json.dumps(config[:3], separators=(",", ":"), ensure_ascii=False)[:-1] + ",").encode()
    static_2 = ("," + json.dumps(config[4:9], separators=(",", ":"), ensure_ascii=False)[1:-1] + ",").encode()
    static_3 = ("," + json.dumps(config[10:], separators=(",", ":"), ensure_ascii=False)[1:]).encode()
    for i in range(limit):
        final_json = static_1 + str(i).encode() + static_2 + str(i >> 1).encode() + static_3
        encoded = base64.b64encode(final_json)
        digest = hashlib.sha3_512(seed_bytes + encoded).digest()
        if digest[:diff_len] <= target:
            return encoded.decode(), True
    fallback = "wQ8Lk5FbGpA2NcR9dShT6gYjU7VxZ4D" + base64.b64encode(f'"{seed}"'.encode()).decode()
    return fallback, False


def build_legacy_requirements_token(user_agent: str, scripts: list[str], data_build: str) -> str:
    seed = format(random.random())
    answer, _ = _pow_generate(seed, "0fffff", _pow_config(user_agent, scripts, data_build))
    return "gAAAAAC" + answer


def build_proof_token(seed: str, difficulty: str, user_agent: str, scripts: list[str], data_build: str) -> str:
    answer, solved = _pow_generate(seed, difficulty, _pow_config(user_agent, scripts, data_build))
    if not solved:
        raise ChatGPTImageError(f"未能完成 ChatGPT PoW 校验：difficulty={difficulty}")
    return "gAAAAAB" + answer


def _xor_string(text: str, key: str) -> str:
    if not key:
        return text
    return "".join(chr(ord(ch) ^ ord(key[i % len(key)])) for i, ch in enumerate(text))


def solve_turnstile_token(dx: str, p: str) -> str:
    """极简移植 chatgpt2api 的 turnstile token 求解器。失败时返回空串。"""

    try:
        token_list = json.loads(_xor_string(base64.b64decode(dx).decode(), p))
    except Exception:
        return ""

    process_map: dict[Any, Any] = {}
    started = time.time()
    result = ""

    class OrderedMap:
        def __init__(self) -> None:
            self.keys: list[str] = []
            self.values: dict[str, Any] = {}

        def add(self, key: str, value: Any) -> None:
            if key not in self.values:
                self.keys.append(key)
            self.values[key] = value

    def to_str(value: Any) -> str:
        if value is None:
            return "undefined"
        if isinstance(value, float):
            return str(value)
        if isinstance(value, list) and all(isinstance(item, str) for item in value):
            return ",".join(value)
        special = {
            "window.Math.random": "function random() { [native code] }",
            "window.performance.now": "function () { [native code] }",
            "window.Object.create": "function create() { [native code] }",
            "window.Object.keys": "function keys() { [native code] }",
            "window.localStorage": "[object Storage]",
            "window.Reflect.set": "function set() { [native code] }",
        }
        return special.get(str(value), str(value))

    def func_1(e: float, t: float) -> None:
        process_map[e] = _xor_string(to_str(process_map[e]), to_str(process_map[t]))

    def func_2(e: float, t: Any) -> None:
        process_map[e] = t

    def func_3(e: str) -> None:
        nonlocal result
        result = base64.b64encode(e.encode()).decode()

    def func_5(e: float, t: float) -> None:
        current = process_map[e]
        incoming = process_map[t]
        if isinstance(current, (list, tuple)):
            process_map[e] = list(current) + [incoming]
        elif isinstance(current, (str, float)) or isinstance(incoming, (str, float)):
            process_map[e] = to_str(current) + to_str(incoming)
        else:
            process_map[e] = "NaN"

    def func_6(e: float, t: float, n: float) -> None:
        tv = process_map[t]
        nv = process_map[n]
        if isinstance(tv, str) and isinstance(nv, str):
            process_map[e] = "https://chatgpt.com/" if f"{tv}.{nv}" == "window.document.location" else f"{tv}.{nv}"

    def func_7(e: float, *args: float) -> None:
        target = process_map[e]
        values = [process_map[arg] for arg in args]
        if target == "window.Reflect.set":
            obj, key_name, val = values
            obj.add(str(key_name), val)
        elif callable(target):
            target(*values)

    def func_8(e: float, t: float) -> None:
        process_map[e] = process_map[t]

    def func_14(e: float, t: float) -> None:
        process_map[e] = json.loads(process_map[t])

    def func_15(e: float, t: float) -> None:
        process_map[e] = json.dumps(process_map[t])

    def func_17(e: float, t: float, *args: float) -> None:
        call_args = [process_map[arg] for arg in args]
        target = process_map[t]
        if target == "window.performance.now":
            process_map[e] = ((time.time_ns() - int(started * 1e9)) + random.random()) / 1e6
        elif target == "window.Object.create":
            process_map[e] = OrderedMap()
        elif target == "window.Object.keys":
            if call_args and call_args[0] == "window.localStorage":
                process_map[e] = ["STATSIG_LOCAL_STORAGE_STABLE_ID", "oai-did", "client-correlated-secret"]
        elif target == "window.Math.random":
            process_map[e] = random.random()
        elif callable(target):
            process_map[e] = target(*call_args)

    process_map.update({
        1: func_1,
        2: func_2,
        3: func_3,
        5: func_5,
        6: func_6,
        7: func_7,
        8: func_8,
        9: token_list,
        10: "window",
        14: func_14,
        15: func_15,
        16: p,
        17: func_17,
        18: lambda e: process_map.__setitem__(e, base64.b64decode(to_str(process_map[e])).decode()),
        19: lambda e: process_map.__setitem__(e, base64.b64encode(to_str(process_map[e]).encode()).decode()),
        20: lambda e, t, n, *args: process_map[n](*[process_map[arg] for arg in args])
        if process_map.get(e) == process_map.get(t) and callable(process_map.get(n))
        else None,
        21: lambda *_: None,
        23: lambda e, t, *args: process_map[t](*args) if process_map.get(e) is not None and callable(process_map.get(t)) else None,
        24: lambda e, t, n: process_map.__setitem__(e, f"{process_map[t]}.{process_map[n]}"),
    })

    for token in token_list:
        try:
            fn = process_map.get(token[0])
            if callable(fn):
                fn(*token[1:])
        except Exception:
            continue
    return result


class ChatGPTWebImageClient:
    def __init__(
        self,
        access_token: str,
        *,
        proxy_url: str = "",
        timeout: int = 300,
        poll_timeout: int = 180,
        poll_interval: int = 10,
    ) -> None:
        self.base_url = CHATGPT_BASE_URL
        self.access_token = access_token
        self.proxy_url = str(proxy_url or "").strip()
        self.timeout = max(30, int(timeout or 300))
        self.poll_timeout = max(30, int(poll_timeout or 180))
        self.poll_interval = max(3, int(poll_interval or 10))
        self.client_version = DEFAULT_CLIENT_VERSION
        self.client_build_number = DEFAULT_CLIENT_BUILD_NUMBER
        self.user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0"
        )
        self.device_id = new_uuid()
        self.session_id = new_uuid()
        self.pow_script_sources: list[str] = [DEFAULT_POW_SCRIPT]
        self.pow_data_build = ""

    def _client(self, *, timeout: int | float | None = None) -> httpx.AsyncClient:
        headers = {
            "User-Agent": self.user_agent,
            "Origin": self.base_url,
            "Referer": self.base_url + "/",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-US;q=0.7",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "OAI-Device-Id": self.device_id,
            "OAI-Session-Id": self.session_id,
            "OAI-Language": "zh-CN",
            "OAI-Client-Version": self.client_version,
            "OAI-Client-Build-Number": self.client_build_number,
            "Sec-Ch-Ua": '"Microsoft Edge";v="143", "Chromium";v="143", "Not A(Brand";v="24"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
        }
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        kwargs: dict[str, Any] = {
            "headers": headers,
            "timeout": httpx.Timeout(float(timeout or self.timeout)),
            "follow_redirects": True,
        }
        if self.proxy_url:
            kwargs["proxy"] = self.proxy_url
        return httpx.AsyncClient(**kwargs)

    def _headers(self, path: str, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            "X-OpenAI-Target-Path": path,
            "X-OpenAI-Target-Route": path,
        }
        if extra:
            headers.update(extra)
        return headers

    async def _ensure_ok(self, response: httpx.Response, context: str) -> None:
        if 200 <= response.status_code < 300:
            return
        body: Any = response.text
        try:
            body = response.json()
        except Exception:
            pass
        if response.status_code == 401:
            raise InvalidAccessTokenError(f"{context} failed: HTTP 401")
        raise UpstreamHTTPError(context, response.status_code, body)

    async def _bootstrap(self) -> None:
        async with self._client(timeout=30) as client:
            response = await client.get(
                self.base_url + "/",
                headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                    "User-Agent": self.user_agent,
                },
            )
            await self._ensure_ok(response, "bootstrap")
        self.pow_script_sources, self.pow_data_build = parse_pow_resources(response.text)

    async def _get_chat_requirements(self) -> ChatRequirements:
        path = "/backend-api/sentinel/chat-requirements"
        body = {
            "p": build_legacy_requirements_token(
                self.user_agent,
                self.pow_script_sources,
                self.pow_data_build,
            )
        }
        async with self._client(timeout=30) as client:
            response = await client.post(
                self.base_url + path,
                headers=self._headers(path, {"Content-Type": "application/json"}),
                json=body,
            )
            await self._ensure_ok(response, "auth_chat_requirements")
            payload = response.json()
        if (payload.get("arkose") or {}).get("required"):
            raise ChatGPTImageError("ChatGPT 要求 Arkose 校验，当前插件无法自动完成。")
        proof_token = ""
        proof_info = payload.get("proofofwork") or {}
        if proof_info.get("required"):
            proof_token = build_proof_token(
                str(proof_info.get("seed") or ""),
                str(proof_info.get("difficulty") or ""),
                self.user_agent,
                self.pow_script_sources,
                self.pow_data_build,
            )
        turnstile_token = ""
        turnstile_info = payload.get("turnstile") or {}
        if turnstile_info.get("required") and turnstile_info.get("dx"):
            turnstile_token = solve_turnstile_token(str(turnstile_info["dx"]), body["p"])
        requirements = ChatRequirements(
            token=str(payload.get("token") or ""),
            proof_token=proof_token,
            turnstile_token=turnstile_token,
            so_token=str(payload.get("so_token") or ""),
            raw_finalize=payload,
        )
        if not requirements.token:
            raise ChatGPTImageError("ChatGPT 没有返回 chat requirements token。")
        return requirements

    def _image_headers(
        self,
        path: str,
        requirements: ChatRequirements,
        conduit_token: str = "",
        accept: str = "*/*",
    ) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": accept,
            "OpenAI-Sentinel-Chat-Requirements-Token": requirements.token,
        }
        if requirements.proof_token:
            headers["OpenAI-Sentinel-Proof-Token"] = requirements.proof_token
        if requirements.turnstile_token:
            headers["OpenAI-Sentinel-Turnstile-Token"] = requirements.turnstile_token
        if requirements.so_token:
            headers["OpenAI-Sentinel-SO-Token"] = requirements.so_token
        if conduit_token:
            headers["X-Conduit-Token"] = conduit_token
        if accept == "text/event-stream":
            headers["X-Oai-Turn-Trace-Id"] = new_uuid()
        return self._headers(path, headers)

    async def get_user_info(self) -> dict[str, Any]:
        if not self.access_token:
            raise InvalidAccessTokenError("access_token is required")
        async with self._client(timeout=30) as client:
            me = await self._json_get(client, "/backend-api/me")
            init = await self._json_post(
                client,
                "/backend-api/conversation/init",
                {
                    "gizmo_id": None,
                    "requested_default_model": None,
                    "conversation_id": None,
                    "timezone_offset_min": -480,
                },
            )
            account_payload = await self._json_get(
                client,
                "/backend-api/accounts/check/v4-2023-04-27?timezone_offset_min=-480",
                route="/backend-api/accounts/check/v4-2023-04-27",
            )
        default_account = ((account_payload.get("accounts") or {}).get("default") or {}).get("account") or {}
        limits = init.get("limits_progress") if isinstance(init.get("limits_progress"), list) else []
        quota, restore_at, unknown = self._extract_quota(limits)
        plan_type = str(default_account.get("plan_type") or "free")
        return {
            "email": me.get("email"),
            "user_id": me.get("id"),
            "type": plan_type,
            "quota": quota,
            "image_quota_unknown": unknown,
            "limits_progress": limits,
            "default_model_slug": init.get("default_model_slug"),
            "restore_at": restore_at,
            "status": "正常" if unknown and plan_type.lower() != "free" else ("限流" if quota == 0 else "正常"),
        }

    async def _json_get(self, client: httpx.AsyncClient, path: str, *, route: str | None = None) -> dict[str, Any]:
        response = await client.get(self.base_url + path, headers=self._headers(route or path, {"Accept": "application/json"}))
        await self._ensure_ok(response, path)
        data = response.json()
        return data if isinstance(data, dict) else {}

    async def _json_post(self, client: httpx.AsyncClient, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = await client.post(
            self.base_url + path,
            headers=self._headers(path, {"Content-Type": "application/json", "Accept": "application/json"}),
            json=payload,
        )
        await self._ensure_ok(response, path)
        data = response.json()
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _extract_quota(limits: list[Any]) -> tuple[int, str | None, bool]:
        for item in limits:
            if isinstance(item, dict) and item.get("feature_name") == "image_gen":
                return int(item.get("remaining") or 0), str(item.get("reset_after") or "") or None, False
        return 0, None, True

    async def list_models(self) -> list[str]:
        await self._bootstrap()
        async with self._client(timeout=30) as client:
            data = await self._json_get(
                client,
                "/backend-api/models?history_and_training_disabled=false",
                route="/backend-api/models",
            )
        out: list[str] = []
        for item in data.get("models") or []:
            if isinstance(item, dict) and item.get("slug"):
                slug = str(item["slug"])
                if slug not in out:
                    out.append(slug)
        return out

    async def generate_images(self, request: ImageRequest) -> list[ImageResult]:
        results: list[ImageResult] = []
        for _ in range(max(1, request.count)):
            results.extend(await self._generate_one(request))
        return results

    async def _generate_one(self, request: ImageRequest) -> list[ImageResult]:
        if not self.access_token:
            raise InvalidAccessTokenError("access_token is required")
        prompt = build_image_prompt(request.prompt, request.size)
        references = [image_info(data, request.preferred_format) for data in request.reference_images]
        await self._bootstrap()
        requirements = await self._get_chat_requirements()
        conduit_token = await self._prepare_image_conversation(prompt, requirements, request.model)
        last_state = await self._start_image_generation(prompt, requirements, conduit_token, request.model, references)
        if last_state.blocked and last_state.text:
            raise ChatGPTImageError(last_state.text)
        file_ids = list(last_state.file_ids)
        sediment_ids = list(last_state.sediment_ids)
        if last_state.conversation_id and not file_ids and not sediment_ids:
            file_ids, sediment_ids = await self._poll_image_results(last_state.conversation_id)
        urls = await self._resolve_image_urls(last_state.conversation_id, file_ids, sediment_ids)
        if not urls and last_state.text:
            raise ChatGPTImageError(last_state.text)
        if not urls:
            raise ChatGPTImageError("ChatGPT 没有返回图片结果。")
        return await self._download_results(urls, request.preferred_format, last_state.conversation_id)

    async def _prepare_image_conversation(
        self,
        prompt: str,
        requirements: ChatRequirements,
        model: str,
    ) -> str:
        path = "/backend-api/f/conversation/prepare"
        payload = {
            "action": "next",
            "fork_from_shared_post": False,
            "parent_message_id": new_uuid(),
            "model": _model_slug(model),
            "client_prepare_state": "success",
            "timezone_offset_min": -480,
            "timezone": "Asia/Shanghai",
            "conversation_mode": {"kind": "primary_assistant"},
            "system_hints": ["picture_v2"],
            "partial_query": {
                "id": new_uuid(),
                "author": {"role": "user"},
                "content": {"content_type": "text", "parts": [prompt]},
            },
            "supports_buffering": True,
            "supported_encodings": ["v1"],
            "client_contextual_info": {"app_name": "chatgpt.com"},
        }
        async with self._client(timeout=60) as client:
            response = await client.post(
                self.base_url + path,
                headers=self._image_headers(path, requirements),
                json=payload,
            )
            await self._ensure_ok(response, path)
            data = response.json()
        return str(data.get("conduit_token") or "")

    async def _upload_image(self, client: httpx.AsyncClient, info: ImageInfo, file_name: str) -> dict[str, Any]:
        path = "/backend-api/files"
        response = await client.post(
            self.base_url + path,
            headers=self._headers(path, {"Content-Type": "application/json", "Accept": "application/json"}),
            json={
                "file_name": file_name,
                "file_size": len(info.data),
                "use_case": "multimodal",
                "width": info.width,
                "height": info.height,
            },
        )
        await self._ensure_ok(response, path)
        upload_meta = response.json()
        await asyncio.sleep(0.5)
        put_response = await client.put(
            upload_meta["upload_url"],
            headers={
                "Content-Type": info.mime_type,
                "x-ms-blob-type": "BlockBlob",
                "x-ms-version": "2020-04-08",
                "Origin": self.base_url,
                "Referer": self.base_url + "/",
                "User-Agent": self.user_agent,
                "Accept": "application/json, text/plain, */*",
            },
            content=info.data,
        )
        await self._ensure_ok(put_response, "image_upload")
        uploaded_path = f"/backend-api/files/{upload_meta['file_id']}/uploaded"
        uploaded_response = await client.post(
            self.base_url + uploaded_path,
            headers=self._headers(uploaded_path, {"Content-Type": "application/json", "Accept": "application/json"}),
            content=b"{}",
        )
        await self._ensure_ok(uploaded_response, uploaded_path)
        return {
            "file_id": upload_meta["file_id"],
            "file_name": file_name,
            "file_size": len(info.data),
            "mime_type": info.mime_type,
            "width": info.width,
            "height": info.height,
        }

    async def _start_image_generation(
        self,
        prompt: str,
        requirements: ChatRequirements,
        conduit_token: str,
        model: str,
        references: list[ImageInfo],
    ) -> ConversationState:
        path = "/backend-api/f/conversation"
        async with self._client(timeout=self.timeout) as client:
            uploaded = [
                await self._upload_image(client, ref, f"image_{idx}{ref.extension}")
                for idx, ref in enumerate(references, start=1)
            ]
            parts: list[Any] = [
                {
                    "content_type": "image_asset_pointer",
                    "asset_pointer": f"file-service://{item['file_id']}",
                    "width": item["width"],
                    "height": item["height"],
                    "size_bytes": item["file_size"],
                }
                for item in uploaded
            ]
            parts.append(prompt)
            content = {"content_type": "multimodal_text", "parts": parts} if uploaded else {
                "content_type": "text",
                "parts": [prompt],
            }
            metadata: dict[str, Any] = {
                "developer_mode_connector_ids": [],
                "selected_github_repos": [],
                "selected_all_github_repos": False,
                "system_hints": ["picture_v2"],
                "serialization_metadata": {"custom_symbol_offsets": []},
            }
            if uploaded:
                metadata["attachments"] = [
                    {
                        "id": item["file_id"],
                        "mimeType": item["mime_type"],
                        "name": item["file_name"],
                        "size": item["file_size"],
                        "width": item["width"],
                        "height": item["height"],
                    }
                    for item in uploaded
                ]
            payload = {
                "action": "next",
                "messages": [{
                    "id": new_uuid(),
                    "author": {"role": "user"},
                    "create_time": time.time(),
                    "content": content,
                    "metadata": metadata,
                }],
                "parent_message_id": new_uuid(),
                "model": _model_slug(model),
                "client_prepare_state": "sent",
                "timezone_offset_min": -480,
                "timezone": "Asia/Shanghai",
                "conversation_mode": {"kind": "primary_assistant"},
                "enable_message_followups": True,
                "system_hints": ["picture_v2"],
                "supports_buffering": True,
                "supported_encodings": ["v1"],
                "client_contextual_info": {
                    "is_dark_mode": False,
                    "time_since_loaded": 1200,
                    "page_height": 1072,
                    "page_width": 1724,
                    "pixel_ratio": 1.2,
                    "screen_height": 1440,
                    "screen_width": 2560,
                    "app_name": "chatgpt.com",
                },
                "paragen_cot_summary_display_override": "allow",
                "force_parallel_switch": "auto",
            }
            async with client.stream(
                "POST",
                self.base_url + path,
                headers=self._image_headers(path, requirements, conduit_token, "text/event-stream"),
                json=payload,
            ) as response:
                await self._ensure_ok(response, path)
                state = ConversationState()
                async for payload_text in _iter_sse_payloads(response):
                    _update_conversation_state(state, payload_text)
                return state

    async def _poll_image_results(self, conversation_id: str) -> tuple[list[str], list[str]]:
        started = time.monotonic()
        if self.poll_interval > 0:
            await asyncio.sleep(min(self.poll_interval, max(0, self.poll_timeout)))
        while time.monotonic() - started < self.poll_timeout:
            try:
                data = await self._get_conversation(conversation_id)
                file_ids, sediment_ids = _extract_image_tool_records(data)
                if file_ids or sediment_ids:
                    return file_ids, sediment_ids
            except UpstreamHTTPError as exc:
                if exc.status_code not in {429, 500, 502, 503, 504}:
                    raise
            await asyncio.sleep(self.poll_interval)
        raise ImagePollTimeoutError(f"ChatGPT 生图超时（已等待 {self.poll_timeout} 秒），可能是账号限流或生图队列拥堵。")

    async def _get_conversation(self, conversation_id: str) -> dict[str, Any]:
        path = f"/backend-api/conversation/{conversation_id}"
        async with self._client(timeout=60) as client:
            return await self._json_get(client, path)

    async def _resolve_image_urls(self, conversation_id: str, file_ids: list[str], sediment_ids: list[str]) -> list[str]:
        urls: list[str] = []
        async with self._client(timeout=60) as client:
            for file_id in file_ids:
                if not file_id or file_id == "file_upload":
                    continue
                try:
                    data = await self._json_get(client, f"/backend-api/files/{file_id}/download")
                except Exception:
                    continue
                url = str(data.get("download_url") or data.get("url") or "")
                if url:
                    urls.append(url)
            if urls or not conversation_id:
                return urls
            for sediment_id in sediment_ids:
                try:
                    data = await self._json_get(
                        client,
                        f"/backend-api/conversation/{conversation_id}/attachment/{sediment_id}/download",
                    )
                except Exception:
                    continue
                url = str(data.get("download_url") or data.get("url") or "")
                if url:
                    urls.append(url)
        return urls

    async def _download_results(
        self,
        urls: list[str],
        preferred_format: str,
        conversation_id: str,
    ) -> list[ImageResult]:
        results: list[ImageResult] = []
        async with self._client(timeout=120) as client:
            for url in urls:
                response = await client.get(url)
                await self._ensure_ok(response, "image_download")
                info = image_info(response.content, preferred_format)
                results.append(
                    ImageResult(
                        data=info.data,
                        mime_type=info.mime_type,
                        width=info.width,
                        height=info.height,
                        extension=info.extension,
                        conversation_id=conversation_id,
                        file_id=url,
                    )
                )
        return results

    async def test_proxy(self) -> dict[str, Any]:
        if not self.proxy_url:
            return {"ok": False, "status": 0, "latency_ms": 0, "error": "未配置代理地址"}
        started = time.perf_counter()
        try:
            async with self._client(timeout=15) as client:
                response = await client.get(
                    self.base_url + "/api/auth/csrf",
                    headers={"User-Agent": "Mozilla/5.0 (TelePilot chatgpt_image proxy test)"},
                )
            latency_ms = int((time.perf_counter() - started) * 1000)
            return {
                "ok": response.status_code < 500,
                "status": response.status_code,
                "latency_ms": latency_ms,
                "error": None if response.status_code < 500 else f"HTTP {response.status_code}",
            }
        except Exception as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            return {"ok": False, "status": 0, "latency_ms": latency_ms, "error": humanize_error(exc)}


async def _iter_sse_payloads(response: httpx.Response):
    async for line in response.aiter_lines():
        if not line or not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload and payload != "[DONE]":
            yield payload


def _update_conversation_state(state: ConversationState, payload: str) -> None:
    conversation_id, file_ids, sediment_ids = _extract_conversation_ids(payload)
    if conversation_id:
        state.conversation_id = conversation_id
    try:
        event = json.loads(payload)
    except json.JSONDecodeError:
        return
    if not isinstance(event, dict):
        return
    state.conversation_id = str(event.get("conversation_id") or state.conversation_id)
    value = event.get("v")
    if isinstance(value, dict):
        state.conversation_id = str(value.get("conversation_id") or state.conversation_id)
    raw = event if isinstance(event, dict) else {}
    if _is_image_context(raw, payload):
        for file_id in file_ids:
            if file_id not in state.file_ids:
                state.file_ids.append(file_id)
        for sediment_id in sediment_ids:
            if sediment_id not in state.sediment_ids:
                state.sediment_ids.append(sediment_id)
    if event.get("type") == "moderation":
        moderation = event.get("moderation_response")
        if isinstance(moderation, dict) and moderation.get("blocked") is True:
            state.blocked = True
    if event.get("type") == "server_ste_metadata":
        metadata = event.get("metadata")
        if isinstance(metadata, dict):
            if isinstance(metadata.get("tool_invoked"), bool):
                state.tool_invoked = metadata["tool_invoked"]
            state.turn_use_case = str(metadata.get("turn_use_case") or state.turn_use_case)
    text = _assistant_text(event, state.text)
    if text:
        state.text = text


def _extract_conversation_ids(payload: str) -> tuple[str, list[str], list[str]]:
    conversation_match = re.search(r'"conversation_id"\s*:\s*"([^"]+)"', payload)
    conversation_id = conversation_match.group(1) if conversation_match else ""
    file_ids = re.findall(r"(file[-_](?!service\b)[A-Za-z0-9]+)", payload)
    sediment_ids = re.findall(r"sediment://([A-Za-z0-9_-]+)", payload)
    return conversation_id, file_ids, sediment_ids


def _is_image_context(event: dict[str, Any], payload: str) -> bool:
    if "asset_pointer" in payload or "file-service://" in payload or "sediment://" in payload:
        return True
    value = event.get("v")
    message = event.get("message") or (value.get("message") if isinstance(value, dict) else None)
    if not isinstance(message, dict):
        return False
    metadata = message.get("metadata") or {}
    author = message.get("author") or {}
    return author.get("role") == "tool" and metadata.get("async_task_type") == "image_gen"


def _assistant_text(event: dict[str, Any], current_text: str = "") -> str:
    for candidate in (event, event.get("v")):
        if not isinstance(candidate, dict):
            continue
        message = candidate.get("message")
        if not isinstance(message, dict):
            continue
        role = str((message.get("author") or {}).get("role") or "")
        if role != "assistant":
            continue
        content = message.get("content") or {}
        parts = content.get("parts") or []
        text = "".join(part for part in parts if isinstance(part, str))
        if text:
            return text
    if event.get("p") == "/message/content/parts/0":
        value = str(event.get("v") or "")
        if event.get("o") == "append":
            return current_text + value
        if event.get("o") == "replace":
            return value
    return current_text


def _extract_image_tool_records(data: dict[str, Any]) -> tuple[list[str], list[str]]:
    mapping = data.get("mapping") or {}
    file_pat = re.compile(r"file-service://([A-Za-z0-9_-]+)")
    sed_pat = re.compile(r"sediment://([A-Za-z0-9_-]+)")
    records: list[tuple[float, list[str], list[str]]] = []
    for node in mapping.values():
        message = (node or {}).get("message") or {}
        author = message.get("author") or {}
        metadata = message.get("metadata") or {}
        content = message.get("content") or {}
        if author.get("role") != "tool" or content.get("content_type") != "multimodal_text":
            continue
        file_ids: list[str] = []
        sediment_ids: list[str] = []
        for part in content.get("parts") or []:
            text = part.get("asset_pointer") if isinstance(part, dict) else part
            text = str(text or "")
            for hit in file_pat.findall(text):
                if hit not in file_ids:
                    file_ids.append(hit)
            for hit in sed_pat.findall(text):
                if hit not in sediment_ids:
                    sediment_ids.append(hit)
        if metadata.get("async_task_type") != "image_gen" and not file_ids and not sediment_ids:
            continue
        records.append((float(message.get("create_time") or 0), file_ids, sediment_ids))
    records.sort(key=lambda item: item[0])
    file_ids: list[str] = []
    sediment_ids: list[str] = []
    for _, files, sediments in records:
        for file_id in files:
            if file_id not in file_ids:
                file_ids.append(file_id)
        for sediment_id in sediments:
            if sediment_id not in sediment_ids:
                sediment_ids.append(sediment_id)
    return file_ids, sediment_ids


__all__ = [
    "ChatGPTImageError",
    "ChatGPTWebImageClient",
    "ImagePollTimeoutError",
    "ImageRequest",
    "ImageResult",
    "InvalidAccessTokenError",
    "build_image_prompt",
    "humanize_error",
    "is_token_invalid_error",
]
