"""插件市场（0.113.0）：来源管理、registry 索引、批量并发更新、自动更新、健康窗口与自动回滚。

设计要点（对应 ``notes/audit-2026-10-29/architecture.md`` §4.3.7）：

- **来源**：内置官方源（插件库 ``registry.json``，``TELEPILOT_PLUGIN_MARKET=<url>|off`` 可覆盖 / 关闭），
  管理员可再添加多个自定义来源（https URL）。每个来源有信任等级 ``official / community / unreviewed``；
  条目的有效信任等级取“来源等级”和“索引自报等级”中更低的一个，自定义来源不能把自己抬成官方。
  官方源不能删除，只能停用。来源列表存 ``system_setting.plugin_market_sources``。
- **索引**：每个来源的 ``registry.json`` 在内存里缓存 6 小时；官方源拉取失败时用随宿主打包的
  ``plugin_market_builtin_registry.json`` 兜底。索引只用于展示与兼容性预判，安装 / 更新时仍克隆插件库、
  读取插件自己的 ``plugin.json`` 走与“插件仓库”完全相同的校验与安装流程（Git / ZIP / 本地导入照旧保留）。
- **批量更新并发**：多个插件更新时按仓库各拉取一次缓存、把插件目录快照到临时目录，然后用
  ``asyncio.Semaphore`` 并行替换 + 提交 + 通知 worker 重载，并发上限默认 3
  （``TELEPILOT_PLUGIN_UPDATE_CONCURRENCY`` 或管理台“更新参数”可调，1–8）。每个插件独立数据库会话，
  单个失败不影响其它，结果逐个返回。“插件仓库 → 全部更新”也走这里。
- **健康窗口与回滚**：每次通过市场 / 仓库批量更新换版本，旧版目录保留在安装根的 ``.previous/<key>``，
  并记录 ``system_setting.plugin_market_rollout:<key>``（更新前的元数据、钩子统计基线）。窗口（默认 10 分钟）内
  出现加载失败、熔断打开或钩子失败率显著升高即判定不健康：自动把旧版目录换回、恢复元数据、通知 worker 重载，
  并告警（通知 Bot ``alert`` 频道 / warning 日志 / 插件运行统计 ``@market`` 行）。窗口结束仍健康则删除旧版目录。
  插件数据目录不随版本回滚。
- **自动更新**：默认关闭。只有信任等级为 official 的来源可以打开；打开后按“远程插件检查更新”的周期
  自动更新该来源里有新版本、兼容当前宿主、没有固定版本、也没有在上次健康窗口里被回滚过的同一版本的插件。
- **固定版本**：固定后“一键全部更新”和自动更新都会跳过；单独点更新会提示先取消固定。
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import html
import json
import logging
import os
import re
import shutil
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote, urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import __version__
from ..db.base import AsyncSessionLocal
from ..db.models.feature import FEATURE_STATE_FAILED, AccountFeature, Feature
from ..db.models.plugin import InstalledPlugin
from ..db.models.plugin_repo import PluginRepo
from ..db.models.system import SystemSetting
from ..worker.plugins.update_barrier import begin_plugin_update, clear_plugin_update
from ..worker.plugins.versioning import (
    PLUGIN_API_VERSION,
    compare_versions,
    host_satisfies_min_version,
    plugin_api_satisfies,
)
from . import plugin_repo_service as repo_svc
from . import remote_plugin_service as remote_svc
from .plugin_install_history import record_plugin_install_history
from .redactor import redact_text

log = logging.getLogger(__name__)

OFFICIAL_SOURCE_ID = "official"
OFFICIAL_SOURCE_NAME = "官方插件库"
OFFICIAL_REGISTRY_URL = "https://raw.githubusercontent.com/Anoyou/telebot-plugins/main/registry.json"
MARKET_ENV = "TELEPILOT_PLUGIN_MARKET"
CONCURRENCY_ENV = "TELEPILOT_PLUGIN_UPDATE_CONCURRENCY"

TRUST_OFFICIAL = "official"
TRUST_COMMUNITY = "community"
TRUST_UNREVIEWED = "unreviewed"
TRUST_LEVELS: tuple[str, ...] = (TRUST_OFFICIAL, TRUST_COMMUNITY, TRUST_UNREVIEWED)
KINDS: tuple[str, ...] = ("interactive", "automation", "utility", "middleware", "payment_source", "ui")
# registry.json 目录版本 1（Plugin API 1.0 冻结范围，第 22 轮定死）：版本 1 的字段只增不减，见
# app.worker.plugins.stability.REGISTRY_V1_*。以后改格式发「目录版本 2」，宿主两版都读；parse_registry
# 目前不按 schema_version 拒绝任何索引（行为不变）。
REGISTRY_SCHEMA_VERSION = 1

SOURCES_KEY = "plugin_market_sources"
SETTINGS_KEY = "plugin_market_settings"
ROLLOUT_KEY_PREFIX = "plugin_market_rollout:"

REGISTRY_TTL_SECONDS = 6 * 3600
# 后台定时拉取：每个已启用来源最多每 6 小时拉一次（与缓存有效期一致），不用等有人打开市场才刷新更新角标。
REGISTRY_BACKGROUND_REFRESH_SECONDS = REGISTRY_TTL_SECONDS
REGISTRY_ERROR_RETRY_SECONDS = 300
REGISTRY_MAX_BYTES = 2 * 1024 * 1024
REGISTRY_TIMEOUT_SECONDS = 15.0
MAX_CUSTOM_SOURCES = 20

DEFAULT_CONCURRENCY = 3
MAX_CONCURRENCY = 8
DEFAULT_HEALTH_WINDOW_MINUTES = 10
MIN_HEALTH_WINDOW_MINUTES = 3
MAX_HEALTH_WINDOW_MINUTES = 60

# 健康判定：窗口内至少这么多次钩子调用才看失败率；失败率同时满足“≥30%、≥更新前 2 倍、比更新前高 20 个百分点”才算显著升高。
HEALTH_MIN_CALLS = 10
HEALTH_MIN_FAILURE_RATE = 0.3
HEALTH_RATE_MULTIPLIER = 2.0
HEALTH_RATE_DELTA = 0.2

STABLE_CHANNEL = "stable"
BETA_CHANNEL = "beta"
# 目录版本 1.1 追加字段的长度上限（展示用，超出截断）。
DESCRIPTION_MAX = 2000
USAGE_MAX = 3000
RELEASE_NOTES_MAX = 3000
MAX_SCREENSHOTS = 8

ROLLOUT_WATCHING = "watching"
ROLLOUT_HEALTHY = "healthy"
ROLLOUT_ROLLED_BACK = "rolled_back"
ROLLOUT_ROLLBACK_FAILED = "rollback_failed"

BUILTIN_REGISTRY_PATH = Path(__file__).with_name("plugin_market_builtin_registry.json")
_TRUST_RANK = {name: index for index, name in enumerate(TRUST_LEVELS)}


class MarketError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


# ─────────────────────────────────────────────────────
# system_setting 读写
# ─────────────────────────────────────────────────────
async def _get_setting(db: AsyncSession, key: str, default: Any) -> Any:
    row = await db.get(SystemSetting, key)
    if row is None:
        return copy.deepcopy(default)
    return copy.deepcopy(row.value)


async def _set_setting(db: AsyncSession, key: str, value: Any) -> None:
    row = await db.get(SystemSetting, key)
    if row is None:
        db.add(SystemSetting(key=key, value=value))
    else:
        row.value = copy.deepcopy(value)
    await db.flush()


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


# ─────────────────────────────────────────────────────
# 来源
# ─────────────────────────────────────────────────────
def official_registry_url() -> str | None:
    """官方源地址；``TELEPILOT_PLUGIN_MARKET=off`` 时返回 None（官方源不可用）。"""

    raw = (os.getenv(MARKET_ENV) or "").strip()
    if raw.lower() == "off":
        return None
    return raw or OFFICIAL_REGISTRY_URL


def normalize_trust(value: Any, *, default: str = TRUST_UNREVIEWED) -> str:
    text = str(value or "").strip().lower()
    return text if text in _TRUST_RANK else default


def lower_trust(*levels: str | None) -> str:
    """多个信任等级里取最低（unreviewed 最低）。"""

    ranked = [normalize_trust(level) for level in levels if level]
    if not ranked:
        return TRUST_UNREVIEWED
    return max(ranked, key=lambda item: _TRUST_RANK[item])


def validate_source_url(url: str) -> str:
    text = str(url or "").strip()
    parsed = urlparse(text)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise MarketError("BAD_SOURCE_URL", "来源地址必须是 https:// 开头的 registry.json 链接，且不能带账号密码")
    if len(text) > 2048:
        raise MarketError("BAD_SOURCE_URL", "来源地址过长")
    return text


def _source_id_for(url: str) -> str:
    return "src-" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]


def _normalize_custom_source(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    url = str(raw.get("url") or "").strip()
    if not url:
        return None
    trust = normalize_trust(raw.get("trust"))
    return {
        "id": str(raw.get("id") or _source_id_for(url)),
        "name": str(raw.get("name") or urlparse(url).netloc or url)[:80],
        "url": url,
        "trust": trust,
        "enabled": bool(raw.get("enabled", True)),
        "auto_update": bool(raw.get("auto_update", False)) and trust == TRUST_OFFICIAL,
        "builtin": False,
        "available": True,
    }


async def list_sources(db: AsyncSession) -> list[dict[str, Any]]:
    stored = await _get_setting(db, SOURCES_KEY, {})
    stored = stored if isinstance(stored, dict) else {}
    official = stored.get(OFFICIAL_SOURCE_ID) if isinstance(stored.get(OFFICIAL_SOURCE_ID), dict) else {}
    url = official_registry_url()
    sources = [
        {
            "id": OFFICIAL_SOURCE_ID,
            "name": OFFICIAL_SOURCE_NAME,
            "url": url or "",
            "trust": TRUST_OFFICIAL,
            "enabled": url is not None and bool(official.get("enabled", True)),
            "auto_update": bool(official.get("auto_update", False)),
            "builtin": True,
            "available": url is not None,
        }
    ]
    for raw in stored.get("custom") or []:
        item = _normalize_custom_source(raw)
        if item is not None and item["id"] != OFFICIAL_SOURCE_ID:
            sources.append(item)
    return sources


async def _save_sources(db: AsyncSession, sources: list[dict[str, Any]]) -> None:
    official = next(item for item in sources if item["id"] == OFFICIAL_SOURCE_ID)
    custom = [
        {key: item[key] for key in ("id", "name", "url", "trust", "enabled", "auto_update")}
        for item in sources
        if not item.get("builtin")
    ]
    await _set_setting(
        db,
        SOURCES_KEY,
        {
            OFFICIAL_SOURCE_ID: {
                "enabled": bool(official.get("enabled", True)),
                "auto_update": bool(official.get("auto_update", False)),
            },
            "custom": custom,
        },
    )


async def get_source(db: AsyncSession, source_id: str) -> dict[str, Any]:
    for item in await list_sources(db):
        if item["id"] == source_id:
            return item
    raise MarketError("SOURCE_NOT_FOUND", f"来源不存在: {source_id}", status=404)


async def add_source(db: AsyncSession, *, url: str, name: str | None = None, trust: str | None = None) -> dict[str, Any]:
    url = validate_source_url(url)
    sources = await list_sources(db)
    if any(item["url"].rstrip("/") == url.rstrip("/") for item in sources if item["url"]):
        raise MarketError("SOURCE_EXISTS", "这个来源已经添加过了", status=409)
    if sum(1 for item in sources if not item.get("builtin")) >= MAX_CUSTOM_SOURCES:
        raise MarketError("TOO_MANY_SOURCES", f"自定义来源最多 {MAX_CUSTOM_SOURCES} 个")
    item = _normalize_custom_source(
        {"url": url, "name": (name or "").strip() or None, "trust": normalize_trust(trust), "enabled": True}
    )
    assert item is not None
    sources.append(item)
    await _save_sources(db, sources)
    _CACHE.pop(item["id"], None)
    return item


async def update_source(db: AsyncSession, source_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    sources = await list_sources(db)
    target = next((item for item in sources if item["id"] == source_id), None)
    if target is None:
        raise MarketError("SOURCE_NOT_FOUND", f"来源不存在: {source_id}", status=404)
    if "name" in patch and patch["name"] is not None and not target.get("builtin"):
        target["name"] = str(patch["name"]).strip()[:80] or target["name"]
    if "trust" in patch and patch["trust"] is not None:
        if target.get("builtin"):
            raise MarketError("SOURCE_BUILTIN", "官方源的信任等级固定为 official")
        target["trust"] = normalize_trust(patch["trust"])
        if target["trust"] != TRUST_OFFICIAL:
            target["auto_update"] = False
    if "enabled" in patch and patch["enabled"] is not None:
        if target.get("builtin") and not target.get("available") and patch["enabled"]:
            raise MarketError("SOURCE_UNAVAILABLE", f"服务器设置了 {MARKET_ENV}=off，官方源不可用")
        target["enabled"] = bool(patch["enabled"])
    if "auto_update" in patch and patch["auto_update"] is not None:
        if patch["auto_update"] and target["trust"] != TRUST_OFFICIAL:
            raise MarketError("AUTO_UPDATE_NOT_ALLOWED", "只有信任等级为 official 的来源可以开启自动更新")
        target["auto_update"] = bool(patch["auto_update"])
    await _save_sources(db, sources)
    _CACHE.pop(source_id, None)
    return target


async def delete_source(db: AsyncSession, source_id: str) -> None:
    sources = await list_sources(db)
    target = next((item for item in sources if item["id"] == source_id), None)
    if target is None:
        raise MarketError("SOURCE_NOT_FOUND", f"来源不存在: {source_id}", status=404)
    if target.get("builtin"):
        raise MarketError("SOURCE_BUILTIN", "官方源不能删除，可以停用")
    await _save_sources(db, [item for item in sources if item["id"] != source_id])
    _CACHE.pop(source_id, None)


# ─────────────────────────────────────────────────────
# 设置（并发上限、健康窗口、固定版本）
# ─────────────────────────────────────────────────────
def default_concurrency() -> int:
    try:
        value = int(os.getenv(CONCURRENCY_ENV) or DEFAULT_CONCURRENCY)
    except ValueError:
        value = DEFAULT_CONCURRENCY
    return max(1, min(MAX_CONCURRENCY, value))


def _clamp_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))


async def load_settings(db: AsyncSession) -> dict[str, Any]:
    raw = await _get_setting(db, SETTINGS_KEY, {})
    raw = raw if isinstance(raw, dict) else {}
    pinned_raw = raw.get("pinned") if isinstance(raw.get("pinned"), dict) else {}
    return {
        "update_concurrency": _clamp_int(
            raw.get("update_concurrency"), default_concurrency(), 1, MAX_CONCURRENCY
        ),
        "health_window_minutes": _clamp_int(
            raw.get("health_window_minutes"),
            DEFAULT_HEALTH_WINDOW_MINUTES,
            MIN_HEALTH_WINDOW_MINUTES,
            MAX_HEALTH_WINDOW_MINUTES,
        ),
        "pinned": {str(key): str(value) for key, value in pinned_raw.items() if key and value},
    }


async def save_settings(db: AsyncSession, patch: dict[str, Any]) -> dict[str, Any]:
    current = await load_settings(db)
    if patch.get("update_concurrency") is not None:
        current["update_concurrency"] = _clamp_int(patch["update_concurrency"], DEFAULT_CONCURRENCY, 1, MAX_CONCURRENCY)
    if patch.get("health_window_minutes") is not None:
        current["health_window_minutes"] = _clamp_int(
            patch["health_window_minutes"],
            DEFAULT_HEALTH_WINDOW_MINUTES,
            MIN_HEALTH_WINDOW_MINUTES,
            MAX_HEALTH_WINDOW_MINUTES,
        )
    await _set_setting(db, SETTINGS_KEY, current)
    return current


async def set_pinned(db: AsyncSession, key: str, *, pinned: bool) -> str | None:
    row = await db.get(InstalledPlugin, key)
    if row is None:
        raise MarketError("PLUGIN_NOT_INSTALLED", f"插件 {key} 还没有安装", status=404)
    settings = await load_settings(db)
    if pinned:
        settings["pinned"][key] = str(row.version or "")
    else:
        settings["pinned"].pop(key, None)
    await _set_setting(db, SETTINGS_KEY, settings)
    return settings["pinned"].get(key)


# ─────────────────────────────────────────────────────
# registry 索引
# ─────────────────────────────────────────────────────
_CACHE: dict[str, dict[str, Any]] = {}
_FETCH_LOCKS: dict[str, asyncio.Lock] = {}


def reset_cache() -> None:
    _CACHE.clear()


async def fetch_registry_json(url: str) -> Any:
    """下载 registry.json（https、15 秒超时、最大 2 MB）。测试里会被替换。"""

    async with httpx.AsyncClient(timeout=REGISTRY_TIMEOUT_SECONDS, follow_redirects=True) as client:
        async with client.stream("GET", url, headers={"Accept": "application/json"}) as response:
            response.raise_for_status()
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > REGISTRY_MAX_BYTES:
                    raise ValueError("registry.json 超过 2 MB")
                chunks.append(chunk)
    return json.loads(b"".join(chunks).decode("utf-8"))


def _str_list(value: Any, *, limit: int = 64) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item)[:120] for item in value if isinstance(item, str) and item.strip()][:limit]


def _optional_str(value: Any, *, limit: int = 64) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] or None


def _https_url(value: Any) -> str | None:
    """只接受 https 链接（截图、图标、主页）；其它一律丢弃。"""

    text = str(value or "").strip()
    if not text or len(text) > 2048:
        return None
    parsed = urlparse(text)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        return None
    return text


def _additive_fields(raw: dict[str, Any], *, summary: str) -> dict[str, Any]:
    """目录版本 1.1 追加字段（全部可选，缺失时给空值；旧索引没有这些字段也照常显示）。"""

    i18n_raw = raw.get("summary_i18n") if isinstance(raw.get("summary_i18n"), dict) else {}
    summary_i18n = {
        str(lang)[:8]: str(text)[:400] for lang, text in i18n_raw.items() if isinstance(text, str) and text.strip()
    }
    summary_i18n.setdefault("zh", summary)
    channel_versions_raw = raw.get("channel_versions") if isinstance(raw.get("channel_versions"), dict) else {}
    screenshots_raw = raw.get("screenshots") if isinstance(raw.get("screenshots"), list) else []
    screenshots = [url for url in (_https_url(item) for item in screenshots_raw) if url]
    return {
        "summary_i18n": summary_i18n,
        "description": str(raw.get("description") or "")[:DESCRIPTION_MAX] or None,
        "usage": str(raw.get("usage") or "")[:USAGE_MAX] or None,
        "commands": _str_list(raw.get("commands"), limit=24),
        "release_notes": str(raw.get("release_notes") or "")[:RELEASE_NOTES_MAX] or None,
        "released_at": _optional_str(raw.get("released_at"), limit=32),
        "homepage": _https_url(raw.get("homepage")),
        "license": _optional_str(raw.get("license"), limit=40),
        "icon_url": _https_url(raw.get("icon_url")),
        "screenshots": screenshots[:MAX_SCREENSHOTS],
        "channel_versions": {
            str(name)[:32]: str(version)[:64]
            for name, version in channel_versions_raw.items()
            if isinstance(version, str) and version.strip()
        },
    }


def parse_registry(data: Any, *, source: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
    """校验并规范 registry.json；不合法的条目跳过（只记 debug），整体格式错误抛 ValueError。"""

    if not isinstance(data, dict) or not isinstance(data.get("plugins"), list):
        raise ValueError("registry.json 格式不对：缺少 plugins 列表")
    top_repository = _optional_str(data.get("repository"), limit=2048)
    top_trust = normalize_trust(data.get("trust"), default=source["trust"])
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in data["plugins"]:
        if not isinstance(raw, dict):
            continue
        key = str(raw.get("key") or raw.get("name") or "").strip()
        if not remote_svc._NAME_RE.match(key) or key in seen:
            continue
        version = _optional_str(raw.get("version"), limit=64)
        if not version:
            continue
        repository = _optional_str(raw.get("repository"), limit=2048) or top_repository
        if not repository:
            continue
        try:
            remote_svc._validate_source_url(repository)
        except remote_svc.RemotePluginError:
            log.debug("registry 条目 %s 的仓库地址不合法：%s", key, repository)
            continue
        raw_name = raw.get("name")
        if isinstance(raw_name, dict):
            name_zh = str(raw_name.get("zh") or raw_name.get("en") or key)
            name_en = str(raw_name.get("en") or raw_name.get("zh") or key)
        else:
            name_zh = name_en = str(raw_name or raw.get("display_name") or key)
        kind = str(raw.get("kind") or "utility")
        channels_raw = raw.get("channels") if isinstance(raw.get("channels"), dict) else {}
        path = str(raw.get("path") or key).strip().strip("/")
        if ".." in path.split("/") or path.startswith("."):
            continue
        seen.add(key)
        summary = str(raw.get("summary") or raw.get("description") or "")[:400]
        entries.append(
            {
                "key": key,
                "name": {"zh": name_zh[:80], "en": name_en[:80]},
                "kind": kind if kind in KINDS else "utility",
                "icon": str(raw.get("icon") or "")[:16] or None,
                "summary": summary,
                "version": version,
                "author": str(raw.get("author") or "")[:80],
                "min_telepilot_version": _optional_str(raw.get("min_telepilot_version")),
                "plugin_api": _optional_str(raw.get("plugin_api")),
                "permissions": _str_list(raw.get("permissions")),
                "reads": _str_list(raw.get("reads")),
                "tags": _str_list(raw.get("tags"), limit=24),
                "channels": {
                    str(name)[:32]: str(ref)[:128] for name, ref in channels_raw.items() if isinstance(ref, str)
                },
                "path": path,
                "repository": repository,
                "trust": lower_trust(source["trust"], normalize_trust(raw.get("trust"), default=top_trust)),
                **_additive_fields(raw, summary=summary),
            }
        )
    return entries, top_repository


def _load_builtin_registry() -> Any:
    return json.loads(BUILTIN_REGISTRY_PATH.read_text(encoding="utf-8"))


async def source_entries(source: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
    """返回来源的索引状态（带缓存）：``entries / fetched_at / error / fallback``。"""

    source_id = source["id"]
    url = source.get("url") or ""
    lock = _FETCH_LOCKS.setdefault(source_id, asyncio.Lock())
    async with lock:
        cached = _CACHE.get(source_id)
        now = time.time()
        if cached is not None and cached.get("url") == url and not force:
            age_limit = REGISTRY_ERROR_RETRY_SECONDS if cached.get("error") else REGISTRY_TTL_SECONDS
            if now - float(cached.get("checked_at") or 0) < age_limit:
                return cached
        state: dict[str, Any]
        try:
            data = await fetch_registry_json(url)
            entries, repository = parse_registry(data, source=source)
            state = {
                "url": url,
                "entries": entries,
                "repository": repository,
                "fetched_at": now,
                "checked_at": now,
                "error": None,
                "fallback": False,
            }
        except Exception as exc:  # noqa: BLE001
            error = redact_text(f"{type(exc).__name__}: {exc}")[:300]
            log.warning("插件市场来源 %s 拉取失败：%s", source_id, error)
            if cached is not None and cached.get("url") == url and cached.get("entries"):
                state = {**cached, "error": error, "checked_at": now}
            elif source.get("builtin"):
                try:
                    entries, repository = parse_registry(_load_builtin_registry(), source=source)
                except Exception:  # noqa: BLE001
                    entries, repository = [], None
                state = {
                    "url": url,
                    "entries": entries,
                    "repository": repository,
                    "fetched_at": None,
                    "checked_at": now,
                    "error": error,
                    "fallback": True,
                }
            else:
                state = {
                    "url": url,
                    "entries": [],
                    "repository": None,
                    "fetched_at": None,
                    "checked_at": now,
                    "error": error,
                    "fallback": False,
                }
        _CACHE[source_id] = state
        return state


# ─────────────────────────────────────────────────────
# 目录（catalog）
# ─────────────────────────────────────────────────────
def compatibility(entry: dict[str, Any]) -> tuple[bool, str | None]:
    minimum = entry.get("min_telepilot_version")
    if minimum and not host_satisfies_min_version(__version__, minimum):
        return False, f"需要 TelePilot {minimum} 或更新（当前 {__version__}）"
    api = entry.get("plugin_api")
    if api:
        try:
            ok = plugin_api_satisfies(api, PLUGIN_API_VERSION)
        except Exception:  # noqa: BLE001
            ok = False
        if not ok:
            return False, f"需要 Plugin API {api}（当前 {PLUGIN_API_VERSION}）"
    return True, None


def _rollout_public(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if not record:
        return None
    return {
        "key": record.get("key"),
        "status": record.get("status"),
        "trigger": record.get("trigger"),
        "from_version": record.get("from_version"),
        "to_version": record.get("to_version"),
        "started_at": record.get("started_at"),
        "deadline": record.get("deadline"),
        "finished_at": record.get("finished_at"),
        "reason": record.get("reason"),
    }


_CHANNEL_REF_RE = re.compile(r"^[A-Za-z0-9._][A-Za-z0-9._/-]{0,127}$")


def channel_url(entry: dict[str, Any], channel: str) -> str | None:
    """通道对应的安装地址：稳定通道就是索引里的 ``repository``（与 0.113.0 起的安装记录一致）；
    其它通道把分支写成 GitHub ``tree/<ref>`` 链接（宿主现有的分支安装格式）。非 GitHub 仓库只支持稳定通道。"""

    repository = entry["repository"]
    if channel == STABLE_CHANNEL:
        return repository
    ref = (entry.get("channels") or {}).get(channel)
    if not ref or not _CHANNEL_REF_RE.match(ref) or ".." in ref.split("/"):
        return None
    try:
        source = remote_svc.normalize_git_source_url(repository)
    except remote_svc.RemotePluginError:
        return None
    parsed = urlparse(source.clone_url)
    if parsed.hostname not in {"github.com", "www.github.com"}:
        return None
    stable_ref = source.ref or (entry.get("channels") or {}).get(STABLE_CHANNEL)
    if ref == stable_ref:
        return None  # 与稳定通道是同一个分支，不算单独的通道
    path = parsed.path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    return f"https://github.com/{path}/tree/{quote(ref, safe='/')}"


def available_channels(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """条目可选的通道（稳定通道永远在第一个）；``version`` 来自 ``channel_versions``，稳定通道缺省用 ``version``。"""

    versions = entry.get("channel_versions") or {}
    channels = entry.get("channels") or {}
    out = [
        {
            "name": STABLE_CHANNEL,
            "ref": channels.get(STABLE_CHANNEL),
            "version": versions.get(STABLE_CHANNEL) or entry["version"],
            "url": entry["repository"],
        }
    ]
    for name in sorted(channels):
        if name == STABLE_CHANNEL:
            continue
        url = channel_url(entry, name)
        if url is None:
            continue
        out.append({"name": name, "ref": channels[name], "version": versions.get(name), "url": url})
    return out


def parse_install_spec(text: str) -> tuple[str, str | None, str | None]:
    """解析安装串 ``[registry:]<key>[@version|#channel]`` → ``(key, version, channel)``。"""

    raw = str(text or "").strip()
    if raw.startswith("registry:"):
        raw = raw[len("registry:"):]
    version = channel = None
    if "#" in raw:
        raw, channel = raw.split("#", 1)
        channel = channel.strip() or None
    elif "@" in raw:
        raw, version = raw.split("@", 1)
        version = version.strip() or None
    return raw.strip(), version, channel


def _catalog_item(
    entry: dict[str, Any],
    source: dict[str, Any],
    row: InstalledPlugin | None,
    settings: dict[str, Any],
    rollout: dict[str, Any] | None,
) -> dict[str, Any]:
    ok, reason = compatibility(entry)
    channels = available_channels(entry)
    installed = None
    installed_channel: str | None = None
    update_version: str | None = None
    update_available = False
    if row is not None:
        current = next((ch for ch in channels if repo_svc._same_repo_source(row.source_url, ch["url"])), None)
        installed_channel = current["name"] if current else None
        installed = {
            "version": row.version,
            "enabled": bool(row.enabled),
            "source": row.source,
            "source_url": row.source_url,
            "same_source": current is not None,
        }
        if current is not None:
            update_version = current["version"]
            update_available = bool(
                ok and update_version and compare_versions(update_version, row.version) > 0
            )
    pinned_version = settings["pinned"].get(entry["key"])
    return {
        "id": f"{source['id']}:{entry['key']}",
        **{key: entry[key] for key in (
            "key", "name", "kind", "icon", "summary", "version", "author", "min_telepilot_version",
            "plugin_api", "permissions", "reads", "tags", "channels", "path", "repository", "trust",
        )},
        **{key: entry.get(key) for key in (
            "summary_i18n", "description", "usage", "commands", "release_notes", "released_at",
            "homepage", "license", "icon_url", "screenshots", "channel_versions",
        )},
        "source_id": source["id"],
        "source_name": source["name"],
        "compatible": ok,
        "incompatible_reason": reason,
        "installed": installed,
        "update_available": update_available,
        "update_version": update_version if update_available else None,
        "channel": installed_channel,
        "available_channels": [{k: ch[k] for k in ("name", "ref", "version")} for ch in channels],
        "pinned_version": pinned_version,
        "auto_update": bool(
            source.get("auto_update")
            and entry["trust"] == TRUST_OFFICIAL
            and not pinned_version
            and installed_channel in (None, STABLE_CHANNEL)
        ),
        "rollout": _rollout_public(rollout),
    }


async def build_catalog(db: AsyncSession, *, refresh: bool = False) -> dict[str, Any]:
    sources = await list_sources(db)
    settings = await load_settings(db)
    rows = {row.key: row for row in (await db.execute(select(InstalledPlugin))).scalars().all()}
    rollouts = await load_rollouts(db)
    enabled = [source for source in sources if source["enabled"]]
    states = await asyncio.gather(*(source_entries(source, force=refresh) for source in enabled))
    state_by_id = {source["id"]: state for source, state in zip(enabled, states, strict=True)}
    items: list[dict[str, Any]] = []
    source_views: list[dict[str, Any]] = []
    for source in sources:
        state = state_by_id.get(source["id"])
        view = {
            **source,
            "status": "disabled",
            "plugin_count": 0,
            "fetched_at": None,
            "error": None,
            "fallback": False,
        }
        if state is not None:
            fetched = state.get("fetched_at")
            view.update(
                {
                    "status": "error" if state.get("error") and not state.get("entries") else (
                        "fallback" if state.get("fallback") else ("stale" if state.get("error") else "ok")
                    ),
                    "plugin_count": len(state.get("entries") or []),
                    "fetched_at": _iso(datetime.fromtimestamp(fetched, UTC)) if fetched else None,
                    "error": state.get("error"),
                    "fallback": bool(state.get("fallback")),
                }
            )
            for entry in state.get("entries") or []:
                items.append(_catalog_item(entry, source, rows.get(entry["key"]), settings, rollouts.get(entry["key"])))
        source_views.append(view)
    update_keys = {item["key"] for item in items if item["update_available"] and not item["pinned_version"]}
    return {
        "server_version": __version__,
        "plugin_api": PLUGIN_API_VERSION,
        "registry_refresh_hours": REGISTRY_BACKGROUND_REFRESH_SECONDS // 3600,
        "sources": source_views,
        "items": items,
        "updates_available": len(update_keys),
        "settings": settings,
        "rollouts": [_rollout_public(item) for item in rollouts.values()],
    }


# ─────────────────────────────────────────────────────
# 安装
# ─────────────────────────────────────────────────────
async def _repo_row_for(db: AsyncSession, url: str, name: str) -> Any:
    """复用已保存的同地址插件仓库（含私有凭证）；没有就用一个不落库的临时仓库对象。"""

    for row in (await db.execute(select(PluginRepo))).scalars().all():
        if repo_svc._same_repo_source(row.url, url):
            return row
    return SimpleNamespace(id=0, name=name, url=url, credential_enc=None)


def _repo_token(row: Any) -> str | None:
    return repo_svc._repo_credential(row) if isinstance(row, PluginRepo) else None


async def _find_entry(source: dict[str, Any], key: str) -> dict[str, Any]:
    state = await source_entries(source)
    for entry in state.get("entries") or []:
        if entry["key"] == key:
            return entry
    raise MarketError("PLUGIN_NOT_IN_MARKET", f"来源「{source['name']}」里没有插件 {key}", status=404)


def _channel_or_error(entry: dict[str, Any], channel: str | None) -> dict[str, Any]:
    name = (channel or STABLE_CHANNEL).strip() or STABLE_CHANNEL
    for item in available_channels(entry):
        if item["name"] == name:
            return item
    raise MarketError("CHANNEL_NOT_FOUND", f"插件 {entry['key']} 没有「{name}」通道", status=404)


async def _repo_for_url(db: AsyncSession, entry: dict[str, Any], url: str, name: str) -> tuple[Any, str | None]:
    """安装用的仓库对象（``url`` 写进安装记录）与凭证：凭证复用同仓库已保存的插件仓库（含私有凭证）。"""

    base_row = await _repo_row_for(db, entry["repository"], name)
    token = _repo_token(base_row)
    if url == entry["repository"]:
        return base_row, token
    return SimpleNamespace(id=0, name=name, url=url, credential_enc=None), token


async def install(
    db: AsyncSession,
    *,
    source_id: str,
    key: str,
    default_enabled: bool = False,
    triggered_by_user_id: int | None = None,
    channel: str | None = None,
    version: str | None = None,
) -> remote_svc.RemotePluginView:
    """从市场安装。``key`` 也可以写成安装串 ``registry:<key>[@version|#channel]``。"""

    spec_key, spec_version, spec_channel = parse_install_spec(key)
    key = spec_key
    channel = channel or spec_channel
    version = version or spec_version
    source = await get_source(db, source_id)
    if not source["enabled"]:
        raise MarketError("SOURCE_DISABLED", f"来源「{source['name']}」已停用")
    entry = await _find_entry(source, key)
    ok, reason = compatibility(entry)
    if not ok:
        raise MarketError("PLUGIN_INCOMPATIBLE", reason or "与当前服务器不兼容", status=409)
    target = _channel_or_error(entry, channel)
    if version and target["version"] and compare_versions(version, target["version"]) != 0:
        raise MarketError(
            "VERSION_NOT_AVAILABLE",
            f"市场里只有 {key} 的当前版本 {target['version']}（{target['name']} 通道），不能安装指定的 {version}",
            status=409,
        )
    url = target["url"]
    repo_row, token = await _repo_for_url(db, entry, url, source["name"])
    async with repo_svc._repo_cache_lock(url):
        repo_dir = await repo_svc._ensure_repo_cached_unlocked(url, force_refresh=True, token=token)
        return await repo_svc._install_plugin_from_cached_repo(
            db,
            repo_row,
            repo_dir,
            key,
            default_enabled=default_enabled,
            replace_existing=False,
            triggered_by_user_id=triggered_by_user_id,
        )


# ─────────────────────────────────────────────────────
# 批量并发更新
# ─────────────────────────────────────────────────────
@dataclass(slots=True)
class UpdateTarget:
    key: str
    repo_url: str
    token: str | None = None
    to_version: str | None = None
    # 切换通道：允许从同一仓库的另一个分支替换（版本可能更低），并照常进入健康观察窗口。
    switch_channel: bool = False


@dataclass(slots=True)
class UpdateItemResult:
    key: str
    display_name: str = ""
    from_version: str | None = None
    to_version: str | None = None
    status: str = "failed"  # updated / skipped / failed / pinned
    message: str = ""
    health_check: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class UpdateRunResult:
    concurrency: int
    items: list[UpdateItemResult] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "concurrency": self.concurrency,
            "total": len(self.items),
            "updated": sum(1 for item in self.items if item.status == "updated"),
            "skipped": sum(1 for item in self.items if item.status in {"skipped", "pinned"}),
            "failed": sum(1 for item in self.items if item.status == "failed"),
            "items": [item.as_dict() for item in self.items],
        }


_PLUGIN_LOCKS: dict[str, asyncio.Lock] = {}


def _plugin_lock(key: str) -> asyncio.Lock:
    return _PLUGIN_LOCKS.setdefault(key, asyncio.Lock())


def _previous_dir_for(install_path: Path, key: str) -> Path:
    return install_path.parent / ".previous" / key


async def _snapshot_targets(
    targets: list[UpdateTarget],
    tmp_root: Path,
) -> tuple[dict[str, Path], dict[str, UpdateItemResult]]:
    """每个仓库只拉取一次缓存，把要更新的插件目录复制到临时目录，之后的并行替换不再碰缓存。"""

    snapshots: dict[str, Path] = {}
    failures: dict[str, UpdateItemResult] = {}
    groups: dict[str, list[UpdateTarget]] = {}
    for target in targets:
        group_key = next((url for url in groups if repo_svc._same_repo_source(url, target.repo_url)), target.repo_url)
        groups.setdefault(group_key, []).append(target)
    for url, group in groups.items():
        try:
            async with repo_svc._repo_cache_lock(url):
                repo_dir = await repo_svc._ensure_repo_cached_unlocked(
                    url, force_refresh=True, token=group[0].token
                )
                found: dict[str, Path] = {}
                for default_name, plugin_dir in repo_svc._scan_plugins(repo_dir):
                    try:
                        meta = remote_svc._read_plugin_metadata(plugin_dir, fallback_name=default_name)
                    except remote_svc.InvalidPluginMetadata:
                        continue
                    found[meta.name] = plugin_dir
                for target in group:
                    source_dir = found.get(target.key)
                    if source_dir is None:
                        failures[target.key] = UpdateItemResult(
                            key=target.key, to_version=target.to_version, message="仓库里没有找到这个插件"
                        )
                        continue
                    destination = tmp_root / target.key
                    shutil.copytree(
                        source_dir,
                        destination,
                        ignore=shutil.ignore_patterns(".git", ".gitignore", "__pycache__"),
                        symlinks=True,
                    )
                    snapshots[target.key] = destination
        except Exception as exc:  # noqa: BLE001
            message = getattr(exc, "message", None) or f"{type(exc).__name__}: {exc}"
            for target in group:
                failures.setdefault(
                    target.key,
                    UpdateItemResult(
                        key=target.key,
                        to_version=target.to_version,
                        message=redact_text(f"拉取仓库失败：{message}")[:300],
                    ),
                )
    return snapshots, failures


def _row_snapshot(row: InstalledPlugin) -> dict[str, Any]:
    return {
        "version": row.version,
        "manifest_json": copy.deepcopy(row.manifest_json or {}),
        "source": row.source,
        "source_url": row.source_url,
        "source_label": row.source_label,
        "trust_tier": row.trust_tier,
        "lint_warnings": list(row.lint_warnings or []),
        "installed_path": row.installed_path,
    }


def _feature_snapshot(feature: Feature | None) -> dict[str, Any] | None:
    if feature is None:
        return None
    return {
        "display_name": feature.display_name,
        "version": feature.version,
        "manifest": copy.deepcopy(feature.manifest),
    }


def _same_clone(left: str | None, right: str | None) -> bool:
    """同一个 Git 仓库（不看分支）：切换通道时用。"""

    if not left or not right:
        return False
    try:
        left_url = remote_svc.normalize_git_source_url(left).clone_url
        right_url = remote_svc.normalize_git_source_url(right).clone_url
    except remote_svc.RemotePluginError:
        return False

    def _identity(value: str) -> str:
        value = value.rstrip("/")
        return value[:-4] if value.endswith(".git") else value

    return _identity(left_url) == _identity(right_url)


async def _apply_one(
    target: UpdateTarget,
    snapshot: Path,
    *,
    trigger: str,
    triggered_by_user_id: int | None,
    health_window_minutes: int,
) -> UpdateItemResult:
    result = UpdateItemResult(key=target.key, to_version=target.to_version)
    async with _plugin_lock(target.key):
        async with AsyncSessionLocal() as db:
            installed = await db.get(InstalledPlugin, target.key)
            if installed is None:
                result.status = "skipped"
                result.message = "插件没有安装"
                return result
            result.display_name = str((installed.manifest_json or {}).get("display_name") or target.key)
            result.from_version = installed.version
            if target.switch_channel:
                if not _same_clone(installed.source_url, target.repo_url):
                    result.status = "skipped"
                    result.message = "已安装的插件来自别的仓库，不能切换通道"
                    return result
            elif not repo_svc._same_repo_source(installed.source_url, target.repo_url):
                result.status = "skipped"
                result.message = "已安装的插件来自别的仓库，没有替换"
                return result
            meta = remote_svc._read_plugin_metadata(snapshot, fallback_name=target.key)
            result.to_version = meta.version
            order = compare_versions(meta.version, installed.version)
            metadata_only = (
                not target.switch_channel
                and order == 0
                and repo_svc._installed_plugin_metadata_needs_refresh(installed, meta)
            )
            if not target.switch_channel and (order < 0 or (order == 0 and not metadata_only)):
                result.status = "skipped"
                result.message = "已经是最新版本"
                return result
            install_path = Path(installed.installed_path or remote_svc._existing_plugin_dir(target.key))
            previous_dir = None if metadata_only else _previous_dir_for(install_path, target.key)
            row_before = _row_snapshot(installed)
            feature = (await db.execute(select(Feature).where(Feature.key == target.key))).scalar_one_or_none()
            feature_before = _feature_snapshot(feature)
            baseline = await _stats_totals(target.key)
            failed_before = await _failed_accounts(db, target.key)
            await repo_svc._replace_installed_plugin_from_repo_dir(
                db,
                repo_url=target.repo_url,
                plugin_dir=snapshot,
                meta=meta,
                installed=installed,
                triggered_by_user_id=triggered_by_user_id,
                keep_previous_dir=previous_dir,
            )
            record_plugin_install_history(
                db,
                row=installed,
                event_type="updated",
                previous_version=row_before["version"],
                detail=f"market:{trigger}",
            )
            if previous_dir is not None and previous_dir.exists():
                started = _now()
                await _save_rollout(
                    db,
                    target.key,
                    {
                        "key": target.key,
                        "status": ROLLOUT_WATCHING,
                        "trigger": trigger,
                        "from_version": row_before["version"],
                        "to_version": meta.version,
                        "started_at": _iso(started),
                        "deadline": _iso(started + timedelta(minutes=health_window_minutes)),
                        "finished_at": None,
                        "reason": None,
                        "previous_dir": str(previous_dir),
                        "install_path": str(install_path),
                        "baseline": baseline,
                        "failed_before": failed_before,
                        "previous": row_before,
                        "previous_feature": feature_before,
                    },
                )
                result.health_check = True
            await db.commit()
        result.status = "updated"
        result.message = "已同步元数据" if metadata_only else ("已切换通道" if target.switch_channel else "已更新")
        try:
            async with AsyncSessionLocal() as db:
                await remote_svc.trigger_reload(db, target.key)
        except remote_svc.RemotePluginError as exc:
            result.message += f"；{exc.message}"
    return result


async def run_updates(
    targets: list[UpdateTarget],
    *,
    concurrency: int,
    trigger: str,
    health_window_minutes: int = DEFAULT_HEALTH_WINDOW_MINUTES,
    triggered_by_user_id: int | None = None,
    preset: list[UpdateItemResult] | None = None,
) -> UpdateRunResult:
    """并行更新多个插件；单个失败不影响其它，结果按输入顺序逐个返回。"""

    concurrency = max(1, min(MAX_CONCURRENCY, int(concurrency or 1)))
    run = UpdateRunResult(concurrency=concurrency, items=list(preset or []))
    if not targets:
        return run
    tmp_root = Path(tempfile.mkdtemp(prefix="telepilot-market-"))
    try:
        snapshots, failures = await _snapshot_targets(targets, tmp_root)
        semaphore = asyncio.Semaphore(concurrency)

        async def _one(target: UpdateTarget) -> UpdateItemResult:
            if target.key in failures:
                return failures[target.key]
            async with semaphore:
                try:
                    return await _apply_one(
                        target,
                        snapshots[target.key],
                        trigger=trigger,
                        triggered_by_user_id=triggered_by_user_id,
                        health_window_minutes=health_window_minutes,
                    )
                except Exception as exc:  # noqa: BLE001
                    message = getattr(exc, "message", None) or str(exc) or type(exc).__name__
                    log.warning("插件 %s 更新失败", target.key, exc_info=True)
                    return UpdateItemResult(
                        key=target.key,
                        to_version=target.to_version,
                        status="failed",
                        message=redact_text(str(message))[:300],
                    )

        run.items.extend(await asyncio.gather(*(_one(target) for target in targets)))
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
    return run


async def update_from_market(
    db: AsyncSession,
    *,
    keys: list[str] | None,
    triggered_by_user_id: int | None = None,
    trigger: str = "manual",
) -> UpdateRunResult:
    """市场“更新”/“一键全部更新”：``keys=None`` 表示全部有更新且未固定的插件。"""

    catalog = await build_catalog(db)
    settings = catalog["settings"]
    by_key: dict[str, dict[str, Any]] = {}
    for item in catalog["items"]:
        if not item["update_available"]:
            continue
        current = by_key.get(item["key"])
        if current is None or _TRUST_RANK[item["trust"]] < _TRUST_RANK[current["trust"]]:
            by_key[item["key"]] = item
    preset: list[UpdateItemResult] = []
    targets: list[UpdateTarget] = []
    wanted = list(dict.fromkeys(keys)) if keys else sorted(by_key)
    repo_rows: dict[str, Any] = {}
    for key in wanted:
        item = by_key.get(key)
        if item is None:
            row = await db.get(InstalledPlugin, key)
            preset.append(
                UpdateItemResult(
                    key=key,
                    from_version=row.version if row else None,
                    status="skipped",
                    message="没有可用的新版本" if row else "插件没有安装",
                )
            )
            continue
        if item["pinned_version"]:
            if keys is None:
                continue
            preset.append(
                UpdateItemResult(
                    key=key,
                    display_name=item["name"]["zh"],
                    from_version=(item["installed"] or {}).get("version"),
                    to_version=item["version"],
                    status="pinned",
                    message=f"已固定在 {item['pinned_version']}，先取消固定再更新",
                )
            )
            continue
        repo_row = repo_rows.get(item["repository"])
        if repo_row is None:
            repo_row = await _repo_row_for(db, item["repository"], item["source_name"])
            repo_rows[item["repository"]] = repo_row
        repo_url = channel_url(item, item.get("channel") or STABLE_CHANNEL) or item["repository"]
        targets.append(
            UpdateTarget(
                key=key,
                repo_url=repo_url,
                token=_repo_token(repo_row),
                to_version=item.get("update_version") or item["version"],
            )
        )
    await db.rollback()
    return await run_updates(
        targets,
        concurrency=settings["update_concurrency"],
        trigger=trigger,
        health_window_minutes=settings["health_window_minutes"],
        triggered_by_user_id=triggered_by_user_id,
        preset=preset,
    )


async def switch_channel(
    db: AsyncSession,
    *,
    key: str,
    channel: str,
    source_id: str | None = None,
    triggered_by_user_id: int | None = None,
) -> UpdateRunResult:
    """把已安装插件切到另一个通道（稳定 ↔ 测试）。走与更新相同的替换流程：保留旧版目录、健康观察窗口、
    不健康自动回滚。从测试切回稳定可能是降级；插件数据目录不随版本变化。"""

    catalog = await build_catalog(db)
    candidates = [
        item for item in catalog["items"]
        if item["key"] == key and (source_id is None or item["source_id"] == source_id)
    ]
    item = next((c for c in candidates if (c.get("installed") or {}).get("same_source")), None)
    if item is None:
        if not candidates:
            raise MarketError("PLUGIN_NOT_IN_MARKET", f"市场里没有插件 {key}", status=404)
        if not any(c.get("installed") for c in candidates):
            raise MarketError("PLUGIN_NOT_INSTALLED", f"插件 {key} 还没有安装", status=404)
        raise MarketError("NOT_SAME_SOURCE", "已安装的版本来自别的仓库，不能在市场里切换通道", status=409)
    if item["pinned_version"]:
        raise MarketError("PLUGIN_PINNED", f"已固定在 {item['pinned_version']}，先取消固定再切换通道", status=409)
    target = _channel_or_error(item, channel)
    if item.get("channel") == target["name"]:
        raise MarketError("ALREADY_ON_CHANNEL", f"已经在「{target['name']}」通道", status=409)
    if not item["compatible"]:
        raise MarketError("PLUGIN_INCOMPATIBLE", item.get("incompatible_reason") or "与当前服务器不兼容", status=409)
    _row, token = await _repo_for_url(db, item, target["url"], item["source_name"])
    settings = catalog["settings"]
    await db.rollback()
    return await run_updates(
        [
            UpdateTarget(
                key=key,
                repo_url=target["url"],
                token=token,
                to_version=target["version"],
                switch_channel=True,
            )
        ],
        concurrency=1,
        trigger="channel",
        health_window_minutes=settings["health_window_minutes"],
        triggered_by_user_id=triggered_by_user_id,
    )


async def update_repo_installed(
    db: AsyncSession,
    repo_id: int,
    *,
    triggered_by_user_id: int | None = None,
) -> dict[str, Any]:
    """“插件仓库 → 全部更新”：并行更新该仓库里版本更高（或元数据过期）的已安装插件。

    返回 ``PluginRepoBulkUpdateResult`` 形状；与旧版的区别是不再一个个顺序更新，单个失败 / 重载未确认
    只体现在该插件的结果里，不再让整次请求报错。
    """

    row = await repo_svc._get_repo(db, repo_id)
    repo_url = row.url
    token = repo_svc._repo_credential(row)
    settings = await load_settings(db)
    async with repo_svc._repo_cache_lock(repo_url):
        repo_dir = await repo_svc._ensure_repo_cached_unlocked(repo_url, force_refresh=True, token=token)
        scanned: list[tuple[str, Any | None, str | None]] = []
        for default_name, plugin_dir in repo_svc._scan_plugins(repo_dir):
            try:
                meta = remote_svc._read_plugin_metadata(plugin_dir, fallback_name=default_name)
            except remote_svc.InvalidPluginMetadata as exc:
                scanned.append((default_name, None, exc.message))
                continue
            scanned.append((meta.name, meta, None))
    checked = 0
    update_available = 0
    preset: list[UpdateItemResult] = []
    targets: list[UpdateTarget] = []
    for name, meta, error in scanned:
        if meta is None:
            preset.append(UpdateItemResult(key=name, status="failed", message=error or "plugin.json 无效"))
            continue
        installed = await db.get(InstalledPlugin, meta.name)
        if installed is None or not repo_svc._same_repo_source(installed.source_url, repo_url):
            continue
        checked += 1
        order = compare_versions(meta.version, installed.version)
        if order > 0 or (order == 0 and repo_svc._installed_plugin_metadata_needs_refresh(installed, meta)):
            if order > 0:
                update_available += 1
            targets.append(UpdateTarget(key=meta.name, repo_url=repo_url, token=token, to_version=meta.version))
            continue
        preset.append(
            UpdateItemResult(
                key=meta.name,
                display_name=meta.display_name or meta.name,
                from_version=installed.version,
                to_version=meta.version,
                status="skipped",
                message="本地已是该仓库中的最新版本",
            )
        )
    repo_name = row.name
    await db.rollback()
    run = await run_updates(
        targets,
        concurrency=settings["update_concurrency"],
        trigger="repo",
        health_window_minutes=settings["health_window_minutes"],
        triggered_by_user_id=triggered_by_user_id,
        preset=preset,
    )
    items = [
        {
            "name": item.key,
            "display_name": item.display_name or item.key,
            "from_version": item.from_version,
            "to_version": item.to_version,
            "status": "skipped" if item.status == "pinned" else item.status,
            "message": item.message,
        }
        for item in run.items
    ]
    return {
        "repo_id": repo_id,
        "repo_name": repo_name,
        "checked": checked,
        "update_available": update_available,
        "updated": sum(1 for item in items if item["status"] == "updated"),
        "skipped": sum(1 for item in items if item["status"] == "skipped"),
        "failed": sum(1 for item in items if item["status"] == "failed"),
        "concurrency": run.concurrency,
        "items": items,
    }


# ─────────────────────────────────────────────────────
# 健康窗口与回滚
# ─────────────────────────────────────────────────────
async def _save_rollout(db: AsyncSession, key: str, record: dict[str, Any]) -> None:
    await _set_setting(db, ROLLOUT_KEY_PREFIX + key, record)


async def load_rollouts(db: AsyncSession) -> dict[str, dict[str, Any]]:
    rows = (
        await db.execute(select(SystemSetting).where(SystemSetting.key.like(ROLLOUT_KEY_PREFIX + "%")))
    ).scalars().all()
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if isinstance(row.value, dict):
            result[row.key[len(ROLLOUT_KEY_PREFIX):]] = copy.deepcopy(row.value)
    return result


async def _stats_totals(key: str) -> dict[str, int] | None:
    """读插件钩子统计合计（不含 ``@`` 开头的宿主记录行）；Redis 不可用返回 None。"""

    try:
        from ..redis_client import get_redis
        from ..worker.plugins import plugin_stats

        data = await plugin_stats.read_plugin_stats(get_redis(), key)
    except Exception:  # noqa: BLE001
        return None
    totals = {"calls": 0, "fail": 0, "timeout": 0, "circuit_open": 0}
    for hook in data.get("hooks") or []:
        if str(hook.get("hook") or "").startswith("@"):
            continue
        for name in totals:
            totals[name] += int(hook.get(name) or 0)
    return totals


async def _failed_accounts(db: AsyncSession, key: str) -> dict[str, str]:
    rows = (
        await db.execute(
            select(AccountFeature).where(
                AccountFeature.feature_key == key,
                AccountFeature.enabled.is_(True),
                AccountFeature.state == FEATURE_STATE_FAILED,
            )
        )
    ).scalars().all()
    return {str(row.account_id): str(row.last_error or "加载失败")[:200] for row in rows}


def evaluate_health(
    *,
    baseline: dict[str, int] | None,
    current: dict[str, int] | None,
    new_failures: dict[str, str],
    now: datetime,
    deadline: datetime,
) -> tuple[str, str | None]:
    """判定健康窗口状态：``("unhealthy", 原因) / ("healthy", None) / ("watching", None)``。"""

    if new_failures:
        account_id, error = next(iter(sorted(new_failures.items())))
        return "unhealthy", f"加载失败（账号 {account_id}）：{error}"
    if baseline is not None and current is not None:
        base = dict(baseline)
        if any(int(current.get(name, 0)) < int(base.get(name, 0)) for name in ("calls", "fail", "timeout", "circuit_open")):
            base = {"calls": 0, "fail": 0, "timeout": 0, "circuit_open": 0}  # 统计被清零过
        circuit = int(current.get("circuit_open", 0)) - int(base.get("circuit_open", 0))
        if circuit > 0:
            return "unhealthy", f"熔断打开（{circuit} 次调用被熔断拦截）"
        calls = int(current.get("calls", 0)) - int(base.get("calls", 0))
        failed = (int(current.get("fail", 0)) + int(current.get("timeout", 0))) - (
            int(base.get("fail", 0)) + int(base.get("timeout", 0))
        )
        base_calls = int(base.get("calls", 0))
        base_rate = (int(base.get("fail", 0)) + int(base.get("timeout", 0))) / base_calls if base_calls else 0.0
        if calls >= HEALTH_MIN_CALLS:
            rate = failed / calls
            if (
                rate >= HEALTH_MIN_FAILURE_RATE
                and rate >= base_rate * HEALTH_RATE_MULTIPLIER
                and rate - base_rate >= HEALTH_RATE_DELTA
            ):
                return "unhealthy", f"钩子失败率 {rate:.0%}（更新前 {base_rate:.0%}，窗口内 {calls} 次调用）"
    if now >= deadline:
        return "healthy", None
    return "watching", None


async def _alert(key: str, text: str, reason: str) -> None:
    log.warning(text)
    try:
        from ..redis_client import get_redis
        from ..worker.plugins import plugin_stats

        plugin_stats.record_hook(
            account_id=0,
            plugin_key=key,
            hook="@market",
            event_type="rollback",
            outcome=plugin_stats.OUTCOME_FAIL,
            error=reason,
        )
        await plugin_stats.get_recorder().flush(get_redis())
    except Exception:  # noqa: BLE001
        log.debug("记录插件回滚统计失败", exc_info=True)
    try:
        from . import notify_service

        await notify_service.send("alert", html.escape(text))
    except Exception:  # noqa: BLE001
        log.debug("插件回滚告警发送失败", exc_info=True)


async def _finish_healthy(key: str, record: dict[str, Any]) -> None:
    async with AsyncSessionLocal() as db:
        record["status"] = ROLLOUT_HEALTHY
        record["finished_at"] = _iso(_now())
        record.pop("previous", None)
        record.pop("previous_feature", None)
        await _save_rollout(db, key, record)
        await db.commit()
    shutil.rmtree(record.get("previous_dir") or "", ignore_errors=True)
    log.info("插件 %s 更新到 %s 后健康窗口结束，已删除旧版目录", key, record.get("to_version"))


async def rollback(key: str, *, reason: str, automatic: bool) -> dict[str, Any]:
    """把插件换回健康窗口开始前的旧版本（目录 + 元数据），通知 worker 重载并告警。"""

    async with _plugin_lock(key):
        async with AsyncSessionLocal() as db:
            record = await _get_setting(db, ROLLOUT_KEY_PREFIX + key, None)
            if not isinstance(record, dict) or record.get("status") != ROLLOUT_WATCHING:
                raise MarketError("NO_ACTIVE_ROLLOUT", f"插件 {key} 当前不在健康观察期，无法回滚", status=409)
            previous_dir = Path(str(record.get("previous_dir") or ""))
            install_path = Path(str(record.get("install_path") or ""))
            row = await db.get(InstalledPlugin, key)
            previous = record.get("previous") if isinstance(record.get("previous"), dict) else None
            if row is None or previous is None or not previous_dir.is_dir():
                record.update(
                    {"status": ROLLOUT_ROLLBACK_FAILED, "finished_at": _iso(_now()), "reason": f"{reason}；旧版目录已不存在，无法回滚"}
                )
                await _save_rollout(db, key, record)
                await db.commit()
                await _alert(key, f"插件 {key} 更新到 {record.get('to_version')} 后不健康，但旧版已不存在，无法自动回滚：{reason}", reason)
                return _rollout_public(record) or {}
            failed_dir = previous_dir.parent / f"{key}.failed-{int(time.time())}"
            begin_plugin_update(install_path.parent, key, target_version=str(record.get("from_version") or ""))
            swapped = False
            try:
                sqlite_names = remote_svc._relocate_legacy_plugin_sqlite(install_path, key)
                json_names = remote_svc._relocate_legacy_plugin_json(install_path, key)
                if install_path.exists():
                    install_path.rename(failed_dir)
                previous_dir.rename(install_path)
                swapped = True
                remote_svc._attach_legacy_plugin_sqlite_links(install_path, key, sqlite_names)
                remote_svc._attach_legacy_plugin_json_links(install_path, key, json_names)
                row.version = previous["version"]
                row.manifest_json = copy.deepcopy(previous.get("manifest_json") or {})
                row.source = previous.get("source") or row.source
                row.source_url = previous.get("source_url")
                row.source_label = previous.get("source_label")
                row.trust_tier = previous.get("trust_tier") or row.trust_tier
                row.lint_warnings = list(previous.get("lint_warnings") or [])
                row.installed_path = previous.get("installed_path") or str(install_path)
                feature_before = record.get("previous_feature")
                if isinstance(feature_before, dict):
                    feature = (await db.execute(select(Feature).where(Feature.key == key))).scalar_one_or_none()
                    if feature is not None:
                        feature.display_name = feature_before.get("display_name") or feature.display_name
                        feature.version = feature_before.get("version") or feature.version
                        feature.manifest = copy.deepcopy(feature_before.get("manifest"))
                record_plugin_install_history(
                    db,
                    row=row,
                    event_type="rolled_back",
                    previous_version=str(record.get("to_version") or ""),
                    detail=("自动回滚：" if automatic else "手动回滚：") + reason,
                )
                record.update(
                    {
                        "status": ROLLOUT_ROLLED_BACK,
                        "finished_at": _iso(_now()),
                        "reason": reason,
                        "automatic": automatic,
                    }
                )
                record.pop("previous", None)
                record.pop("previous_feature", None)
                await _save_rollout(db, key, record)
                await db.commit()
            except Exception:
                await db.rollback()
                if swapped and install_path.exists():
                    install_path.rename(previous_dir)
                if failed_dir.exists() and not install_path.exists():
                    failed_dir.rename(install_path)
                clear_plugin_update(install_path.parent, key)
                raise
        shutil.rmtree(failed_dir, ignore_errors=True)
        try:
            async with AsyncSessionLocal() as db:
                await remote_svc.trigger_reload(db, key)
        except remote_svc.RemotePluginError as exc:
            log.warning("插件 %s 回滚后重载未全部确认：%s", key, exc.message)
    prefix = "自动回滚" if automatic else "已手动回滚"
    await _alert(
        key,
        f"插件 {key} {prefix}：{record.get('to_version')} → {record.get('from_version')}。原因：{reason}",
        reason,
    )
    return _rollout_public(record) or {}


async def accept_rollout(key: str) -> dict[str, Any]:
    """提前确认新版本健康：结束观察期并删除旧版目录。"""

    async with _plugin_lock(key):
        async with AsyncSessionLocal() as db:
            record = await _get_setting(db, ROLLOUT_KEY_PREFIX + key, None)
        if not isinstance(record, dict) or record.get("status") != ROLLOUT_WATCHING:
            raise MarketError("NO_ACTIVE_ROLLOUT", f"插件 {key} 当前不在健康观察期", status=409)
        record["reason"] = "管理员确认新版本正常"
        await _finish_healthy(key, record)
    return _rollout_public(record) or {}


async def check_rollouts(*, now: datetime | None = None) -> list[dict[str, Any]]:
    """巡检所有观察中的更新；返回本轮状态有变化的记录。"""

    async with AsyncSessionLocal() as db:
        rollouts = await load_rollouts(db)
        failures = {
            key: await _failed_accounts(db, key)
            for key, record in rollouts.items()
            if record.get("status") == ROLLOUT_WATCHING
        }
    changed: list[dict[str, Any]] = []
    current_time = now or _now()
    for key, record in rollouts.items():
        if record.get("status") != ROLLOUT_WATCHING:
            continue
        deadline = _parse_iso(record.get("deadline")) or current_time
        before = record.get("failed_before") if isinstance(record.get("failed_before"), dict) else {}
        new_failures = {acc: err for acc, err in failures.get(key, {}).items() if acc not in before}
        status, reason = evaluate_health(
            baseline=record.get("baseline") if isinstance(record.get("baseline"), dict) else None,
            current=await _stats_totals(key),
            new_failures=new_failures,
            now=current_time,
            deadline=deadline,
        )
        try:
            if status == "unhealthy":
                changed.append(await rollback(key, reason=reason or "不健康", automatic=True))
            elif status == "healthy":
                async with _plugin_lock(key):
                    await _finish_healthy(key, record)
                changed.append(_rollout_public(record) or {})
        except MarketError:
            continue
        except Exception:  # noqa: BLE001
            log.warning("插件 %s 健康窗口处理失败", key, exc_info=True)
    return changed


# ─────────────────────────────────────────────────────
# 自动更新
# ─────────────────────────────────────────────────────
def auto_update_candidates(catalog: dict[str, Any], rollouts: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    sources = {source["id"]: source for source in catalog["sources"]}
    picked: dict[str, dict[str, Any]] = {}
    for item in catalog["items"]:
        source = sources.get(item["source_id"]) or {}
        if not (item["update_available"] and item["auto_update"] and source.get("auto_update")):
            continue
        if item["trust"] != TRUST_OFFICIAL or item["pinned_version"]:
            continue
        if (item.get("channel") or STABLE_CHANNEL) != STABLE_CHANNEL:
            continue  # 测试通道只手动更新
        record = rollouts.get(item["key"]) or {}
        if record.get("status") == ROLLOUT_WATCHING:
            continue
        if record.get("status") in {ROLLOUT_ROLLED_BACK, ROLLOUT_ROLLBACK_FAILED} and record.get("to_version") == item["version"]:
            continue  # 这个版本上次在健康窗口里被回滚过，等下一个版本
        picked.setdefault(item["key"], item)
    return list(picked.values())


async def auto_update_tick() -> UpdateRunResult | None:
    async with AsyncSessionLocal() as db:
        sources = await list_sources(db)
        if not any(source["enabled"] and source["auto_update"] for source in sources):
            return None
        catalog = await build_catalog(db)
        rollouts = await load_rollouts(db)
        keys = [item["key"] for item in auto_update_candidates(catalog, rollouts)]
        if not keys:
            return None
        log.info("插件市场自动更新：%s", ", ".join(keys))
        return await update_from_market(db, keys=keys, trigger="auto")


async def registry_refresh_tick() -> int:
    """后台定时拉取所有已启用来源的 registry.json（缓存未过期的来源不会重复下载）；返回来源数。

    官方源拉取失败时照旧用随服务器打包的索引副本兜底；``TELEPILOT_PLUGIN_MARKET=off`` 时官方源不拉取。
    """

    async with AsyncSessionLocal() as db:
        sources = [source for source in await list_sources(db) if source["enabled"] and source.get("url")]
    for source in sources:
        try:
            await source_entries(source)
        except Exception:  # noqa: BLE001
            log.warning("插件市场来源 %s 定时拉取失败", source["id"], exc_info=True)
    return len(sources)


async def market_loop() -> None:
    """后台：每 30 秒巡检健康窗口；每 6 小时（最多）拉一次已启用来源的索引，让更新角标不用等打开市场；
    按“远程插件检查更新”的周期执行官方源自动更新（默认关闭）。"""

    await asyncio.sleep(20)
    last_auto: datetime | None = None
    last_registry: datetime | None = None
    while True:
        try:
            await check_rollouts()
        except Exception:  # noqa: BLE001
            log.warning("插件更新健康窗口巡检失败", exc_info=True)
        now = _now()
        if last_registry is None or (now - last_registry).total_seconds() >= REGISTRY_BACKGROUND_REFRESH_SECONDS:
            last_registry = now
            try:
                await registry_refresh_tick()
            except Exception:  # noqa: BLE001
                log.warning("插件市场索引定时拉取失败", exc_info=True)
        try:
            enabled, interval = await remote_svc._load_update_check_setting()
            now = _now()
            if enabled and (last_auto is None or (now - last_auto).total_seconds() >= interval * 60):
                last_auto = now
                await auto_update_tick()
        except Exception:  # noqa: BLE001
            log.warning("插件市场自动更新失败", exc_info=True)
        await asyncio.sleep(30)


__all__ = [
    "MarketError",
    "UpdateItemResult",
    "UpdateRunResult",
    "UpdateTarget",
    "accept_rollout",
    "add_source",
    "auto_update_candidates",
    "auto_update_tick",
    "build_catalog",
    "check_rollouts",
    "compatibility",
    "delete_source",
    "evaluate_health",
    "install",
    "list_sources",
    "load_rollouts",
    "load_settings",
    "market_loop",
    "available_channels",
    "channel_url",
    "parse_install_spec",
    "parse_registry",
    "registry_refresh_tick",
    "rollback",
    "run_updates",
    "save_settings",
    "set_pinned",
    "switch_channel",
    "update_from_market",
    "update_repo_installed",
    "update_source",
]
