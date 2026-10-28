"""LLM 上游 HTTP 客户端复用（第 11 轮审计 B5）。

旧实现每次模型调用都 ``async with httpx.AsyncClient(...)``：新建连接池、重新做
TCP/TLS（走代理时再加一次代理握手），每次多几十毫秒到 1 秒。这里按
``(事件循环, proxy, trust_env)`` 缓存长期存活的 ``AsyncClient``，keep-alive 复用连接；
每次调用的超时通过 per-request ``timeout=`` 传入，所以不同 Provider / 调用方的超时
互不影响。

用法保持 ``async with`` 形状不变，调用点几乎零改动::

    async with lease_llm_http_client(timeout=..., proxy=...) as cli:
        resp = await cli.post(url, json=body)          # 自动带上本次 timeout
        async with cli.stream("POST", url, json=body) as resp: ...

退出 ``async with`` 不会关闭共享客户端；进程停机时调用 :func:`close_llm_http_clients`。
客户端绑定创建它的事件循环（httpx 连接不能跨 loop），换 loop（测试、子进程）时自动重建。
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from typing import Any

import httpx

log = logging.getLogger(__name__)

# 每个共享客户端的连接上限：并发模型调用（含流式长连接）够用，keep-alive 少量即可。
LLM_HTTP_LIMITS = httpx.Limits(
    max_connections=64,
    max_keepalive_connections=16,
    keepalive_expiry=45.0,
)
_MAX_CLIENTS = 16

_clients: OrderedDict[tuple[Any, ...], tuple[asyncio.AbstractEventLoop, httpx.AsyncClient]] = OrderedDict()
stats = {"created": 0, "reused": 0}


def _client_key(loop: asyncio.AbstractEventLoop, kwargs: dict[str, Any]) -> tuple[Any, ...]:
    return (
        id(loop),
        str(kwargs.get("proxy") or ""),
        bool(kwargs.get("trust_env", True)),
        bool(kwargs.get("http2", False)),
        repr(kwargs.get("verify", True)),
    )


def _get_shared_client(kwargs: dict[str, Any]) -> httpx.AsyncClient:
    loop = asyncio.get_running_loop()
    key = _client_key(loop, kwargs)
    cached = _clients.get(key)
    if cached is not None:
        cached_loop, client = cached
        if cached_loop is loop and not client.is_closed and not loop.is_closed():
            _clients.move_to_end(key)
            stats["reused"] += 1
            return client
        _clients.pop(key, None)
    build_kwargs = {k: v for k, v in kwargs.items() if k not in {"timeout", "limits"}}
    client = httpx.AsyncClient(limits=LLM_HTTP_LIMITS, **build_kwargs)
    _clients[key] = (loop, client)
    stats["created"] += 1
    while len(_clients) > _MAX_CLIENTS:
        _old_key, (old_loop, old_client) = _clients.popitem(last=False)
        if old_loop is loop and not old_client.is_closed:
            loop.create_task(old_client.aclose())
    return client


class _TimeoutBoundClient:
    """把本次调用的 timeout 作为默认值注入每个请求的薄包装。"""

    __slots__ = ("_client", "_timeout")

    def __init__(self, client: httpx.AsyncClient, timeout: Any) -> None:
        self._client = client
        self._timeout = timeout

    def _with_timeout(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        if self._timeout is not None and "timeout" not in kwargs:
            kwargs["timeout"] = self._timeout
        return kwargs

    async def post(self, url: Any, **kwargs: Any) -> httpx.Response:
        return await self._client.post(url, **self._with_timeout(kwargs))

    async def get(self, url: Any, **kwargs: Any) -> httpx.Response:
        return await self._client.get(url, **self._with_timeout(kwargs))

    async def request(self, method: str, url: Any, **kwargs: Any) -> httpx.Response:
        return await self._client.request(method, url, **self._with_timeout(kwargs))

    def stream(self, method: str, url: Any, **kwargs: Any) -> Any:
        return self._client.stream(method, url, **self._with_timeout(kwargs))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


class _Lease:
    __slots__ = ("_kwargs",)

    def __init__(self, kwargs: dict[str, Any]) -> None:
        self._kwargs = kwargs

    async def __aenter__(self) -> _TimeoutBoundClient:
        client = _get_shared_client(self._kwargs)
        return _TimeoutBoundClient(client, self._kwargs.get("timeout"))

    async def __aexit__(self, *_exc: Any) -> None:
        return None


def lease_llm_http_client(**kwargs: Any) -> _Lease:
    """返回 ``async with`` 可用的共享客户端租约；kwargs 与 ``httpx.AsyncClient`` 相同。"""

    return _Lease(dict(kwargs))


async def close_llm_http_clients() -> None:
    """停机时关闭当前事件循环创建的共享客户端；其它 loop 的条目直接丢弃。"""

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    items = list(_clients.items())
    _clients.clear()
    for _key, (client_loop, client) in items:
        if client_loop is loop and not client.is_closed:
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001
                log.debug("close llm http client failed", exc_info=True)


__all__ = ["LLM_HTTP_LIMITS", "close_llm_http_clients", "lease_llm_http_client", "stats"]
