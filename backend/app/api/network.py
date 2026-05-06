"""网络环境探测 API。

提供：
  - ``GET /api/system/network``  返回当前后端进程出口 IP + 国家/地区
  - ``GET /api/system/network/refresh``  强制刷新（绕过缓存）

结果缓存 5 分钟（避免每次请求都打 ipinfo.io）。前端 TopBar 用此显示当前环境。
"""

from __future__ import annotations

import asyncio
import time as _time

import httpx
from fastapi import APIRouter
from pydantic import BaseModel

from ..deps import CurrentUser

router = APIRouter(prefix="/api/system", tags=["system"])


class NetworkInfo(BaseModel):
    ip: str | None = None
    country: str | None = None      # ISO 国家/地区代码（CN / US / JP / HK 等）
    region: str | None = None
    city: str | None = None
    org: str | None = None          # ISP / ASN
    cached_at: float = 0.0          # 客户端可用以判断是否过期
    fresh: bool = True              # 本次是否新拉到（false=用缓存）
    error: str | None = None


_TTL_SECONDS = 5 * 60
_CACHE: dict[str, NetworkInfo] = {}
_LOCK = asyncio.Lock()


async def _fetch() -> NetworkInfo:
    """实际调外部 API 拿出口 IP 信息。优先 ip-api.com（HTTP，免费 45/min），失败回退 ipinfo.io。"""
    # 主：ip-api.com（HTTP，无 token，字段全）
    try:
        async with httpx.AsyncClient(timeout=8.0) as cli:
            r = await cli.get("http://ip-api.com/json/")
            r.raise_for_status()
            d = r.json()
            if d.get("status") == "success":
                return NetworkInfo(
                    ip=d.get("query"),
                    country=d.get("countryCode"),
                    region=d.get("regionName") or d.get("region"),
                    city=d.get("city"),
                    org=d.get("isp") or d.get("org"),
                    cached_at=_time.time(),
                    fresh=True,
                )
    except Exception:
        pass

    # 备：ipinfo.io（HTTPS，限流后会 429）
    try:
        async with httpx.AsyncClient(timeout=8.0) as cli:
            r = await cli.get("https://ipinfo.io/json")
            r.raise_for_status()
            d = r.json()
            return NetworkInfo(
                ip=d.get("ip"),
                country=d.get("country"),
                region=d.get("region"),
                city=d.get("city"),
                org=d.get("org"),
                cached_at=_time.time(),
                fresh=True,
            )
    except Exception as e:
        return NetworkInfo(
            cached_at=_time.time(),
            fresh=True,
            error=f"{type(e).__name__}: {e}",
        )


async def _get_or_fetch(force: bool = False) -> NetworkInfo:
    async with _LOCK:
        cached = _CACHE.get("v1")
        now = _time.time()
        if not force and cached and (now - cached.cached_at) < _TTL_SECONDS:
            return cached.model_copy(update={"fresh": False})
        info = await _fetch()
        _CACHE["v1"] = info
        return info


@router.get("/network", response_model=NetworkInfo)
async def get_network(_user: CurrentUser) -> NetworkInfo:
    return await _get_or_fetch(force=False)


@router.post("/network/refresh", response_model=NetworkInfo)
async def refresh_network(_user: CurrentUser) -> NetworkInfo:
    return await _get_or_fetch(force=True)
