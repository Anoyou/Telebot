"""Cross-process barrier that blocks stale plugin instances during code replacement."""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path

_SAFE_PLUGIN_KEY = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")

# marker 负缓存：只缓存"无更新进行中"的结果，键为 (root, plugin_key, account_id)，
# 值为 time.monotonic() 检查时刻。正结果（更新进行中）永不缓存。
_MARKER_CACHE_TTL_SECONDS = 2.0
_MARKER_NEGATIVE_CACHE: dict[tuple[str, str, int], float] = {}


def _safe_key(plugin_key: str) -> str:
    key = str(plugin_key or "").strip()
    if not _SAFE_PLUGIN_KEY.fullmatch(key):
        raise ValueError(f"invalid plugin key: {plugin_key!r}")
    return key


def _marker_path(root: Path, plugin_key: str) -> Path:
    return root / f".{_safe_key(plugin_key)}.update.json"


def _ack_path(root: Path, plugin_key: str, account_id: int) -> Path:
    return root / f".{_safe_key(plugin_key)}.update.{int(account_id)}.ack"


def _read_update_id(root: Path, plugin_key: str) -> str | None:
    marker = _marker_path(root, plugin_key)
    if not marker.is_file():
        return None
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    update_id = str(payload.get("update_id") or "").strip() if isinstance(payload, dict) else ""
    return update_id or None


def begin_plugin_update(root: Path, plugin_key: str, *, target_version: str = "") -> str:
    """Publish the barrier before replacing code and invalidate prior worker ACKs."""

    root.mkdir(parents=True, exist_ok=True)
    key = _safe_key(plugin_key)
    # 更新开始：本进程内立即失效负缓存，保证后续调用穿透检查（跨进程靠 TTL 兜底）。
    invalidate_update_marker_cache(root, key)
    update_id = uuid.uuid4().hex
    for ack in root.glob(f".{key}.update.*.ack"):
        ack.unlink(missing_ok=True)
    marker = _marker_path(root, key)
    temporary = root / f".{key}.update.{uuid.uuid4().hex}.tmp"
    payload = {
        "update_id": update_id,
        "plugin_key": key,
        "target_version": str(target_version or ""),
        "started_at": time.time(),
        "pid": os.getpid(),
    }
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")
        temporary.replace(marker)
    finally:
        temporary.unlink(missing_ok=True)
    return update_id


def plugin_update_token(root: Path, plugin_key: str) -> str | None:
    """Return the current update token captured before one worker reloads code."""

    return _read_update_id(root, plugin_key)


def acknowledge_plugin_update(
    root: Path,
    plugin_key: str,
    account_id: int,
    update_id: str | None,
) -> None:
    """Allow one account only after its worker has activated the replacement."""

    token = str(update_id or "").strip()
    if not token or _read_update_id(root, plugin_key) != token:
        return
    # worker 完成新代码激活：失效该插件的负缓存（本进程内立刻生效）。
    invalidate_update_marker_cache(root, plugin_key)
    ack = _ack_path(root, plugin_key, account_id)
    temporary = ack.with_name(f"{ack.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(token, encoding="ascii")
        temporary.replace(ack)
    finally:
        temporary.unlink(missing_ok=True)


def plugin_update_in_progress(root: Path, plugin_key: str, account_id: int) -> bool:
    """Return True while this account still owns an old in-memory instance.

    热路径优化（loader 每次插件调用都会走到这里）：连续调用共享最多
    ``_MARKER_CACHE_TTL_SECONDS`` 秒的进程内负缓存，避免每次两趟文件 IO。
    语义方向是"宁可多拦一次调用也不能漏拦"：
    - 只缓存"无更新进行中"（False）的结果，且 TTL 很短；
    - 一旦真实检查发现更新进行中，立即丢弃缓存，后续调用必须穿透检查；
    - ``begin_plugin_update`` / ``acknowledge_plugin_update`` / ``clear_plugin_update``
      在本进程内执行时会主动失效对应缓存（跨进程场景靠 TTL 兜底）。
    """

    root_text = str(root)
    key = _safe_key(plugin_key)
    account = int(account_id)
    cache_key = (root_text, key, account)
    cached_at = _MARKER_NEGATIVE_CACHE.get(cache_key)
    now = time.monotonic()
    if cached_at is not None and now - cached_at < _MARKER_CACHE_TTL_SECONDS:
        return False
    in_progress = _plugin_update_in_progress_uncached(root, key, account)
    if in_progress:
        # 发现更新进行中：不缓存正结果，后续调用必须真实穿透检查。
        _MARKER_NEGATIVE_CACHE.pop(cache_key, None)
    else:
        _MARKER_NEGATIVE_CACHE[cache_key] = now
    return in_progress


def _plugin_update_in_progress_uncached(root: Path, plugin_key: str, account_id: int) -> bool:
    marker = _marker_path(root, plugin_key)
    if not marker.is_file():
        return False
    update_id = _read_update_id(root, plugin_key)
    if update_id is None:
        return True
    try:
        acknowledged_id = _ack_path(root, plugin_key, account_id).read_text(encoding="ascii").strip()
    except OSError:
        return True
    return acknowledged_id != update_id


def invalidate_update_marker_cache(
    root: Path | None = None,
    plugin_key: str | None = None,
) -> None:
    """显式失效 marker 负缓存。

    不带参数清空全部；带 ``root`` / ``plugin_key`` 时只清匹配条目。
    ``begin_plugin_update`` / ``acknowledge_plugin_update`` / ``clear_plugin_update``
    已内置调用；loader 或测试需要强制穿透检查时可显式调用。
    """

    if root is None and plugin_key is None:
        _MARKER_NEGATIVE_CACHE.clear()
        return
    root_text = str(root) if root is not None else None
    key_text = _safe_key(plugin_key) if plugin_key else None
    for cache_key in list(_MARKER_NEGATIVE_CACHE):
        cached_root, cached_key, _account = cache_key
        if root_text is not None and cached_root != root_text:
            continue
        if key_text is not None and cached_key != key_text:
            continue
        _MARKER_NEGATIVE_CACHE.pop(cache_key, None)


def plugin_update_active(root: Path, plugin_key: str) -> bool:
    """Return whether any replacement barrier currently exists for this plugin."""

    return _marker_path(root, plugin_key).is_file()


def clear_plugin_update(root: Path, plugin_key: str) -> None:
    """Remove the shared barrier after every targeted worker confirmed reload."""

    key = _safe_key(plugin_key)
    # 更新结束：失效负缓存，让下一次调用重新确认磁盘状态。
    invalidate_update_marker_cache(root, key)
    _marker_path(root, key).unlink(missing_ok=True)
    for ack in root.glob(f".{key}.update.*.ack"):
        ack.unlink(missing_ok=True)
