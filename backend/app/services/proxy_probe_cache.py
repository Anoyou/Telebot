"""代理出口探测缓存——把 ``POST /api/proxies/{id}/test`` 的结果存 Redis。

设计目标：
- 让"账号卡显示真实出口"不必每次刷新页面都 2~5 秒探测一次
- 让前端 ProxySummary 能直接带上 last-probed 国家 / IP，零额外 RTT
- 缓存 30 min；用户主动点"刷新"时调 ``test_proxy`` 写入新值，自动失效旧值

**安全注意**：缓存里只放出口元信息（IP / 国家 / 城市 / 延迟），**绝不**写代理凭据
（host:port:user:pass）——那些都在 Postgres 里 Fernet 加密了。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from ..redis_client import get_redis

log = logging.getLogger(__name__)

# Redis key 模板：每个代理一条
_KEY_FMT = "proxy:probe:{pid}"
# TTL：30 min。住宅代理出口偶尔会变，但不会一秒一变；半小时是个合理窗口。
_TTL_SECONDS = 30 * 60


def _key(pid: int) -> str:
    return _KEY_FMT.format(pid=pid)


async def get_probe(pid: int) -> dict[str, Any] | None:
    """读取缓存；不存在或 Redis 故障返 None（调用方继续不抛）。"""
    try:
        redis = get_redis()
    except Exception:  # noqa: BLE001
        log.warning("[proxy-probe-cache] get_probe pid=%s: redis client failed", pid)
        return None
    try:
        raw = await redis.get(_key(pid))
    except Exception:  # noqa: BLE001
        log.warning("[proxy-probe-cache] redis get failed pid=%s", pid, exc_info=True)
        return None
    if not raw:
        log.info("[proxy-probe-cache] miss pid=%s", pid)
        return None
    try:
        data = json.loads(raw)
        log.info("[proxy-probe-cache] hit pid=%s country=%s", pid, data.get("country"))
        return data
    except Exception:  # noqa: BLE001
        log.warning("[proxy-probe-cache] bad JSON in cache pid=%s", pid)
        return None


async def get_probes_bulk(pids: list[int]) -> dict[int, dict[str, Any]]:
    """批量读——给 ``list_accounts`` 这种一次查 N 条的场景用，减少 Redis 来回。"""
    if not pids:
        return {}
    try:
        redis = get_redis()
    except Exception:  # noqa: BLE001
        log.warning("[proxy-probe-cache] bulk: redis client failed")
        return {}
    keys = [_key(pid) for pid in pids]
    try:
        raws = await redis.mget(keys)
    except Exception:  # noqa: BLE001
        log.warning("[proxy-probe-cache] mget failed", exc_info=True)
        return {}
    out: dict[int, dict[str, Any]] = {}
    for pid, raw in zip(pids, raws, strict=True):
        if not raw:
            continue
        try:
            out[pid] = json.loads(raw)
        except Exception:  # noqa: BLE001
            continue
    log.info("[proxy-probe-cache] bulk hits=%d/%d for pids=%s", len(out), len(pids), pids)
    return out


async def set_probe(
    pid: int,
    *,
    ok: bool,
    exit_ip: str | None,
    country: str | None,
    region: str | None,
    city: str | None,
    latency_ms: int | None,
    error: str | None = None,
) -> None:
    """写入缓存——``test_proxy`` 成功 / 失败都该写一份，让前端反映"上次测的是什么状态"。"""
    payload = {
        "ok": ok,
        "exit_ip": exit_ip,
        "country": country,
        "region": region,
        "city": city,
        "latency_ms": latency_ms,
        "error": error,
        "probed_at": int(time.time()),
    }
    try:
        redis = get_redis()
    except Exception:  # noqa: BLE001
        log.warning("[proxy-probe-cache] set_probe pid=%s: redis client failed", pid)
        return
    try:
        await redis.set(_key(pid), json.dumps(payload), ex=_TTL_SECONDS)
        log.info(
            "[proxy-probe-cache] wrote pid=%s ok=%s country=%s ip=%s",
            pid, ok, country, exit_ip,
        )
    except Exception:  # noqa: BLE001
        log.warning("[proxy-probe-cache] redis set failed pid=%s", pid, exc_info=True)


async def clear_probe(pid: int) -> None:
    """删代理时连着清缓存。"""
    try:
        redis = get_redis()
    except Exception:  # noqa: BLE001
        return
    try:
        await redis.delete(_key(pid))
    except Exception:  # noqa: BLE001
        pass


__all__ = [
    "clear_probe",
    "get_probe",
    "get_probes_bulk",
    "set_probe",
]
