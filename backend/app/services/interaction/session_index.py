"""Reverse index: account+chat → interaction session Redis keys.

Hot path replaces dual SCAN with SMEMBERS + GET. Index is best-effort and
rebuilt from SCAN when missing/stale.
"""

from __future__ import annotations

import logging
import time
from typing import Any

log = logging.getLogger(__name__)

SESSION_KEY_PREFIX = "account_bot:interaction_session:"
CHAT_INDEX_PREFIX = "account_bot:interaction_session_chat:"
CHAT_INDEX_TTL_SECONDS = 7 * 24 * 3600
# 账号级索引：account → 全部会话 key。交互 Bot 每轮需要「有活跃会话的 chat」
# 集合，旧实现对整个 Redis 键空间 SCAN 再逐个 GET（N+1）；改为 SMEMBERS + MGET。
ACCOUNT_INDEX_PREFIX = "account_bot:interaction_session_account:"
# 对账标记：存在时信任账号索引（即使为空集合）；过期后读取方用 SCAN 重建一次，
# 修正写入方漏记或升级前遗留的会话（定期对账）。
ACCOUNT_INDEX_BUILT_PREFIX = "account_bot:interaction_session_account_built:"
ACCOUNT_INDEX_RECONCILE_SECONDS = 600


def chat_index_key(account_id: int, chat_id: int) -> str:
    return f"{CHAT_INDEX_PREFIX}{int(account_id)}:{int(chat_id)}"


def account_index_key(account_id: int) -> str:
    return f"{ACCOUNT_INDEX_PREFIX}{int(account_id)}"


def account_index_built_key(account_id: int) -> str:
    return f"{ACCOUNT_INDEX_BUILT_PREFIX}{int(account_id)}"


async def index_session_key(
    redis: Any,
    *,
    account_id: int,
    chat_id: int | None,
    session_key: str,
    ttl_seconds: int | None = None,
) -> None:
    if redis is None or chat_id is None or not session_key:
        return
    key = chat_index_key(account_id, int(chat_id))
    try:
        await redis.sadd(key, session_key)
        # 索引应比单会话更长寿：会话 TTL 往往只有数分钟，索引过早过期会迫使热路径
        # 反复 SCAN。取 max(会话 TTL, 默认 7 天)；失效成员由读取路径 unindex 清理。
        session_ttl = int(ttl_seconds) if ttl_seconds is not None else 0
        ex = max(session_ttl, CHAT_INDEX_TTL_SECONDS) if session_ttl > 0 else CHAT_INDEX_TTL_SECONDS
        if ex > 0:
            await redis.expire(key, ex)
        account_key = account_index_key(account_id)
        await redis.sadd(account_key, session_key)
        await redis.expire(account_key, CHAT_INDEX_TTL_SECONDS)
    except Exception:  # noqa: BLE001
        log.debug(
            "index session key failed account=%s chat=%s key=%s",
            account_id,
            chat_id,
            session_key,
            exc_info=True,
        )


async def unindex_session_key(
    redis: Any,
    *,
    account_id: int,
    chat_id: int | None,
    session_key: str,
) -> None:
    if redis is None or not session_key:
        return
    try:
        if chat_id is not None:
            await redis.srem(chat_index_key(account_id, int(chat_id)), session_key)
        await redis.srem(account_index_key(account_id), session_key)
    except Exception:  # noqa: BLE001
        log.debug(
            "unindex session key failed account=%s chat=%s key=%s",
            account_id,
            chat_id,
            session_key,
            exc_info=True,
        )


async def list_indexed_session_keys(
    redis: Any,
    *,
    account_id: int,
    chat_id: int,
) -> list[str] | None:
    """Return indexed keys, or ``None`` if the index key is missing (caller may rebuild)."""

    if redis is None:
        return None
    key = chat_index_key(account_id, chat_id)
    try:
        exists = await redis.exists(key)
        if not int(exists or 0):
            return None
        members = await redis.smembers(key)
    except Exception:  # noqa: BLE001
        log.debug("list indexed session keys failed account=%s chat=%s", account_id, chat_id, exc_info=True)
        return None
    out: list[str] = []
    for raw in members or []:
        text = raw.decode("utf-8", errors="ignore") if isinstance(raw, bytes) else str(raw)
        if text:
            out.append(text)
    return out


async def rebuild_chat_index_from_scan(
    redis: Any,
    *,
    account_id: int,
    chat_id: int,
    scan_keys: list[str],
) -> list[str]:
    """Merge SCAN results into the chat index without deleting concurrent adds.

    旧实现先 DELETE 再 SADD：若新会话在 SCAN 之后、pipeline 之前写入索引，
    DELETE 会抹掉它且扫描结果又不含它，热路径此后不再 SCAN，导致最长 TTL
    内丢会话。重建只做并集写入；失效成员由读取路径 unindex 清理。
    """

    if redis is None:
        return list(scan_keys)
    key = chat_index_key(account_id, chat_id)
    try:
        if scan_keys:
            await redis.sadd(key, *scan_keys)
        # 即使 scan 为空也确保 key 存在，避免热路径反复把「空集合」当成「索引缺失」。
        # 用 expire 刷新 TTL；若 key 仍不存在（无任何 member），sadd 一个占位再删掉不划算，
        # 空结果允许返回 None 路径——调用方已 hydrate 完成。
        exists = await redis.exists(key)
        if int(exists or 0):
            await redis.expire(key, CHAT_INDEX_TTL_SECONDS)
        elif not scan_keys:
            # 标记「已重建且为空」：写入短暂空集合哨兵，避免下一请求再全量 SCAN。
            # 用空 set + expire：sadd 需要 member，改为 set 一个标记 key 不合适（smembers 语义）。
            # 这里保持「不存在」= 可重建；空 chat 会再次慢路径，频率低可接受。
            pass
    except Exception:  # noqa: BLE001
        log.debug("rebuild chat index failed account=%s chat=%s", account_id, chat_id, exc_info=True)
    # 返回并集视图（扫描结果 ∪ 当前索引），便于调用方 hydrate。
    try:
        members = await redis.smembers(key)
        out: list[str] = []
        seen: set[str] = set()
        for raw in list(scan_keys) + list(members or []):
            text = raw.decode("utf-8", errors="ignore") if isinstance(raw, bytes) else str(raw)
            if text and text not in seen:
                seen.add(text)
                out.append(text)
        return out
    except Exception:  # noqa: BLE001
        return list(scan_keys)


def _decode_members(members: Any) -> list[str]:
    out: list[str] = []
    for raw in members or []:
        text = raw.decode("utf-8", errors="ignore") if isinstance(raw, bytes) else str(raw)
        if text:
            out.append(text)
    return out


async def list_account_session_keys(redis: Any, *, account_id: int) -> list[str] | None:
    """账号下全部会话 key；对账标记缺失（需要 SCAN 重建）或读取失败时返回 ``None``。"""

    if redis is None:
        return None
    try:
        built = await redis.exists(account_index_built_key(account_id))
        if not int(built or 0):
            return None
        return _decode_members(await redis.smembers(account_index_key(account_id)))
    except Exception:  # noqa: BLE001
        log.debug("list account session keys failed account=%s", account_id, exc_info=True)
        return None


async def rebuild_account_index_from_scan(
    redis: Any,
    *,
    account_id: int,
    scan_keys: list[str],
) -> list[str]:
    """把 SCAN 结果并入账号索引（只做并集，不删并发新增），并写对账标记。"""

    if redis is None:
        return list(scan_keys)
    key = account_index_key(account_id)
    try:
        if scan_keys:
            await redis.sadd(key, *scan_keys)
            await redis.expire(key, CHAT_INDEX_TTL_SECONDS)
        await redis.set(account_index_built_key(account_id), "1", ex=ACCOUNT_INDEX_RECONCILE_SECONDS)
        members = _decode_members(await redis.smembers(key))
    except Exception:  # noqa: BLE001
        log.debug("rebuild account session index failed account=%s", account_id, exc_info=True)
        return list(scan_keys)
    seen: set[str] = set()
    out: list[str] = []
    for item in list(scan_keys) + members:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


async def drop_account_session_keys(redis: Any, *, account_id: int, session_keys: list[str]) -> None:
    """读取方发现会话 key 已不存在（过期）时，从账号索引里移除。"""

    if redis is None or not session_keys:
        return
    try:
        await redis.srem(account_index_key(account_id), *session_keys)
    except Exception:  # noqa: BLE001
        log.debug("drop account session keys failed account=%s", account_id, exc_info=True)


def session_still_active(session: dict[str, Any], *, now: float | None = None) -> bool:
    if not isinstance(session, dict) or not session:
        return False
    expires_at = session.get("expires_at")
    try:
        if expires_at is not None and float(expires_at) <= (now if now is not None else time.time()):
            return False
    except (TypeError, ValueError):
        return False
    return True


__all__ = [
    "ACCOUNT_INDEX_BUILT_PREFIX",
    "ACCOUNT_INDEX_PREFIX",
    "CHAT_INDEX_PREFIX",
    "SESSION_KEY_PREFIX",
    "account_index_built_key",
    "account_index_key",
    "chat_index_key",
    "drop_account_session_keys",
    "index_session_key",
    "list_account_session_keys",
    "list_indexed_session_keys",
    "rebuild_account_index_from_scan",
    "rebuild_chat_index_from_scan",
    "session_still_active",
    "unindex_session_key",
]
