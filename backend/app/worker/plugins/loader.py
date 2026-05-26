"""账号级插件加载器：连接 Telethon、实例化每个启用的 [账号 × feature] 插件，并维护其生命周期。

安全设计（阶段 E）：
- installed 插件统一授权：安装记录全局开关 / 签名状态 / AccountFeature.enabled 都通过才加载
- 插件命令注册表：追踪 owner/plugin_key/generation，插件 reload/disable 时自动注销
- on_shutdown 保证调用（幂等设计）

使用流程：
1. ``run_worker`` 在 ``client.connect()`` 前调 ``load_plugins_for_account``，本模块会：
   - 触发内置插件 import（``@register`` 写入全局注册表）
   - 在 ``client`` 上挂全局消息派发器（incoming + outgoing），按各插件的 ``message_channels`` 声明过滤
   - 实例化该账号当前通过授权检查的 enabled 插件，并把状态写回为 active
2. 主进程通过 IPC ``CMD_RELOAD_CONFIG`` 触发 ``reload_account_config`` 实现热更新（拉新 rules / config，
   并对新增 / 移除的 feature 做差量加载与卸载）
3. ``CMD_RELOAD_PLUGIN`` 调 ``reload_plugin``：builtin 走 ``importlib.reload``，installed 走按需重载

模块化后插件以"目录"形式存在：
- 内置：``backend/app/worker/plugins/builtin/<key>/{__init__.py, manifest.py, plugin.py}``
- 第三方：``plugins/installed/<key>/{__init__.py, manifest.py, plugin.py}``（阶段 B 引入）
每个插件目录的 ``__init__.py`` 必须暴露 ``PLUGIN_CLASS``（Plugin 子类）与 ``MANIFEST``
（``Manifest`` 实例）两个常量；``discover_plugins()`` 扫描时按目录读取这两个常量装载。

任何插件抛出的异常都不会让整个 worker 崩溃；该 plugin 会被标记为 ``failed`` 状态。
"""

from __future__ import annotations

import asyncio
import collections
import importlib
import importlib.util
import logging
import re
import shutil
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from telethon import TelegramClient, events

from ... import __version__ as TELEPILOT_VERSION
from ...db.base import AsyncSessionLocal
from ...db.models.account import Account, HumanizeConfig, SudoUser
from ...db.models.feature import (
    FEATURE_SCHEDULER,
    FEATURE_STATE_ACTIVE,
    FEATURE_STATE_DISABLED,
    FEATURE_STATE_FAILED,
    AccountFeature,
)
from ...db.models.ignored_peer import IgnoredPeer
from ...db.models.plugin import PLUGIN_TRUST_ORPHAN, InstalledPlugin
from ...db.models.rule import Rule
from ...db.models.system import SystemSetting
from ...redis_client import get_redis
from ...services import interaction_bot_service
from ...services.rate_limit_service import get_effective
from ...settings import settings as app_settings
from ...util.sudo_permissions import sudo_chat_allowed
from ..command import register_plugin_command, unregister_plugin_command
from ..ipc import RUNTIME_LOG_STREAM, RuntimeLogPayload
from ..ratelimit.engine import RateLimitEngine
from ..ratelimit.humanize import HumanizeOpts
from .base import Plugin, PluginContext, all_plugins, get_plugin
from .manifest import Manifest

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class _InstalledPluginAuthorization:
    """运行期 installed 插件加载授权结果。"""

    allowed: bool
    state: str = FEATURE_STATE_ACTIVE
    last_error: str | None = None
    log_level: str = "info"
    log_message: str = ""


# 不在每次消息都查 DB；启动 + reload 时刷新一次，足够快
async def _load_log_incoming_messages_setting() -> bool:
    """从 ``system_setting`` 读取「是否记录每条 incoming 消息」开关。

    支持两种存储格式（兼容前端不同 toggle 实现）：
      - ``{"enabled": true}``
      - ``{"value": true}``
      - ``true`` / ``false`` 直接 JSON 布尔
    缺失或异常一律按 ``app_settings.log_incoming_messages_default`` 处理（默认 False）。
    """
    try:
        async with AsyncSessionLocal() as db:
            row = await db.get(SystemSetting, "log_incoming_messages")
        if row is None:
            return bool(app_settings.log_incoming_messages_default)
        v = row.value
        if isinstance(v, dict):
            return bool(v.get("enabled", v.get("value", app_settings.log_incoming_messages_default)))
        if isinstance(v, bool):
            return v
        return bool(app_settings.log_incoming_messages_default)
    except Exception:  # noqa: BLE001
        return bool(app_settings.log_incoming_messages_default)


# worker 内存里维护的最近活跃 peer 数量上限（超过则按 LRU 丢弃最旧）
RECENT_PEERS_LIMIT = 50


# 内置插件根目录：``backend/app/worker/plugins/builtin``
_BUILTIN_DIR: Path = Path(__file__).parent / "builtin"
_BACKEND_DIR: Path = Path(__file__).resolve().parents[3]


def _installed_dir() -> Path:
    """解析第三方插件安装目录：阶段 B 引入，由 ``settings.plugins_installed_dir`` 配置。

    每次调用都重新解析，便于测试通过 monkeypatch settings 实现隔离；
    生产环境下值是稳定的。
    """
    try:
        from ...settings import settings  # 延迟 import 避免循环

        return settings.plugins_installed_path
    except Exception:  # noqa: BLE001
        # settings 加载失败时退化到默认相对路径
        return Path("./plugins/installed").resolve()


def _scan_builtin_dirs() -> list[Path]:
    """扫描 builtin 子目录（仅取目录，跳过 ``__pycache__`` 等下划线开头的私有目录）。

    返回值的顺序按文件名字典序，便于测试稳定。
    """
    if not _BUILTIN_DIR.exists():
        return []
    return sorted(
        [p for p in _BUILTIN_DIR.iterdir() if p.is_dir() and not p.name.startswith("_")],
        key=lambda p: p.name,
    )


# 内置插件模块名清单（运行期由扫描得出，保留 tuple 类型以兼容现有测试）
# 每次 import loader 时刷新一次；新增 builtin 子目录无需改这里。
_BUILTIN_MODULES: tuple[str, ...] = tuple(p.name for p in _scan_builtin_dirs())
# installed 插件模块名清单：用来避免每次清理都全量扫 sys.modules。
_INSTALLED_MODULE_NAMES: set[str] = set()

_SUPPORTED_FACADE_PERMISSIONS: set[str] = {
    "external_http",
    "external_http_bypass_proxy",
    "ai_text",
}
_RESERVED_UNSUPPORTED_FACADE_PERMISSIONS: set[str] = {
    "ai_vision",
    "ai_image",
    "ai_stt",
}


def _builtin_plugin_path(plugin_key: str) -> Path | None:
    if not _is_safe_plugin_key(plugin_key):
        return None
    path = (_BUILTIN_DIR / plugin_key).resolve()
    try:
        path.relative_to(_BUILTIN_DIR.resolve())
    except ValueError:
        return None
    return path if path.is_dir() else None


def _missing_plugin_error(feature_key: str) -> tuple[str, str]:
    """为缺失插件提供统一错误码与可读日志，便于前端/运维识别。"""
    if feature_key == "codex_image":
        return (
            "builtin plugin codex_image not found",
            (
                "feature codex_image 已启用但未找到内置模块实现。"
                "请确认当前镜像包含 backend/app/worker/plugins/builtin/codex_image；"
                "已跳过加载并保持 worker 运行。"
            ),
        )
    return ("plugin not found", f"feature {feature_key} 已启用但未找到插件实现")


def _import_builtins() -> None:
    """import 内置插件包，触发各模块的 ``@register`` 装饰器写入注册表。

    模块化重构后此函数等价于"调 ``discover_plugins()`` + 跳过返回值"——
    保留是因为现有调用方（runtime / 测试）仍以这个名字为入口；
    返回值忽略，单纯靠副作用（``@register`` + ``_manifest`` 注入）来工作。
    任意单个插件失败仅记日志，不影响其它插件加载。
    """
    try:
        from . import builtin  # noqa: F401  builtin/__init__.py 也会 re-export
    except Exception:  # noqa: BLE001
        log.exception("import plugins.builtin 失败")
    try:
        # 只扫描 builtin。第三方 installed 插件必须等 DB 双开关检查通过后
        # 再按需加载，避免 worker 启动/配置刷新时执行未启用插件代码。
        discover_plugins()
    except Exception:  # noqa: BLE001
        log.exception("discover_plugins 失败")


def _installed_module_name(plugin_key: str) -> str:
    return f"_telepilot_installed_plugin_{plugin_key}"


def _clear_installed_module_cache(plugin_key: str) -> None:
    """清掉第三方插件包、子模块和注册表旧类，保证热加载读到磁盘最新代码。"""
    import importlib as _importlib
    import sys as _sys

    mod_name = _installed_module_name(plugin_key)
    if _INSTALLED_MODULE_NAMES:
        tracked_names = [
            name
            for name in _INSTALLED_MODULE_NAMES
            if name == mod_name or name.startswith(f"{mod_name}.")
        ]
        for name in tracked_names:
            _sys.modules.pop(name, None)
            _INSTALLED_MODULE_NAMES.discard(name)
    else:
        for name in list(_sys.modules):
            if name == mod_name or name.startswith(f"{mod_name}."):
                _sys.modules.pop(name, None)
    _importlib.invalidate_caches()
    try:
        from .base import _REGISTRY

        cls = _REGISTRY.get(plugin_key)
        if cls is not None and getattr(cls, "_source", None) == "installed":
            _REGISTRY.pop(plugin_key, None)
    except Exception:  # noqa: BLE001
        log.debug("清理 installed 插件注册表失败 plugin=%s", plugin_key, exc_info=True)
    try:
        root = _installed_dir().resolve()
        path = (root / plugin_key).resolve()
        path.relative_to(root)
        if path.is_dir():
            for cache_dir in path.rglob("__pycache__"):
                shutil.rmtree(cache_dir, ignore_errors=True)
    except Exception:  # noqa: BLE001
        log.debug("清理 installed 插件 pycache 失败 plugin=%s", plugin_key, exc_info=True)


def _clear_builtin_module_cache(plugin_key: str) -> None:
    """清掉 builtin 插件包和子模块，保证目录型插件热重载能读到辅助模块变更。"""
    import sys as _sys

    mod_name = f"{__package__}.builtin.{plugin_key}"
    for name in list(_sys.modules):
        if name == mod_name or name.startswith(f"{mod_name}."):
            _sys.modules.pop(name, None)
    importlib.invalidate_caches()
    try:
        from .base import _REGISTRY

        cls = _REGISTRY.get(plugin_key)
        if cls is not None and getattr(cls, "_source", "builtin") == "builtin":
            _REGISTRY.pop(plugin_key, None)
    except Exception:  # noqa: BLE001
        log.debug("清理 builtin 插件注册表失败 plugin=%s", plugin_key, exc_info=True)
    try:
        path = _builtin_plugin_path(plugin_key)
        if path is not None:
            for cache_dir in path.rglob("__pycache__"):
                shutil.rmtree(cache_dir, ignore_errors=True)
    except Exception:  # noqa: BLE001
        log.debug("清理 builtin 插件 pycache 失败 plugin=%s", plugin_key, exc_info=True)


def _is_safe_plugin_key(plugin_key: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_.-]+", plugin_key or ""))


def _version_tuple(v: str | None) -> tuple[int, ...]:
    """把 ``0.9.6`` / ``v0.9.6-beta`` 转成可比较 tuple。"""
    if not v:
        return ()
    parts = [int(p) for p in re.findall(r"\d+", str(v))]
    return tuple(parts[:3])


def _manifest_compatible(manifest: Manifest) -> tuple[bool, str | None]:
    """检查 manifest 的版本和插件依赖声明。"""
    min_version = (
        getattr(manifest, "min_telepilot_version", None)
        or getattr(manifest, "min_telebot_version", None)
    )
    if min_version and _version_tuple(TELEPILOT_VERSION) < _version_tuple(min_version):
        return False, f"需要 TelePilot >= {min_version}，当前 {TELEPILOT_VERSION}"

    missing = [
        key for key in list(getattr(manifest, "requires_features", None) or [])
        if key not in all_plugins()
    ]
    if missing:
        return False, f"缺少依赖插件: {', '.join(missing)}"

    return True, None


def _restore_registry_after_failed_load(
    snapshot: dict[str, type[Plugin]],
    *,
    path_key: str,
    cls: Any = None,
    manifest: Manifest | None = None,
) -> None:
    """Rollback registry side effects from a plugin import that failed validation."""

    from .base import _REGISTRY

    candidate_keys = {path_key}
    for value in (getattr(cls, "key", None), getattr(manifest, "key", None)):
        if value:
            candidate_keys.add(str(value))
    for key in candidate_keys:
        previous = snapshot.get(key)
        if previous is None:
            _REGISTRY.pop(key, None)
        else:
            _REGISTRY[key] = previous


def _restore_full_registry_snapshot(snapshot: dict[str, type[Plugin]]) -> None:
    from .base import _REGISTRY

    _REGISTRY.clear()
    _REGISTRY.update(snapshot)


def _validate_plugin_identity(path: Path, cls: Any, manifest: Manifest) -> tuple[bool, str | None]:
    """Require directory name, manifest key and Plugin.key to describe one plugin."""

    if not isinstance(cls, type) or not issubclass(cls, Plugin):
        return False, "PLUGIN_CLASS 必须是 Plugin 子类"
    path_key = path.name
    class_key = str(getattr(cls, "key", "") or "")
    manifest_key = str(getattr(manifest, "key", "") or "")
    if not manifest_key:
        return False, "MANIFEST.key 不能为空"
    if manifest_key != path_key:
        return False, f"MANIFEST.key={manifest_key!r} 与目录名 {path_key!r} 不一致"
    if class_key != manifest_key:
        return False, f"Plugin.key={class_key!r} 与 MANIFEST.key={manifest_key!r} 不一致"
    return True, None


def _load_dir(path: Path, source: str) -> dict[str, type[Plugin]]:
    """从单个插件目录加载 ``PLUGIN_CLASS`` 与 ``MANIFEST``；失败返回 {} 并写日志。

    - ``source="builtin"``：走正常的 ``importlib.import_module`` 路径，包名是
      ``app.worker.plugins.builtin.<key>``，能享受 Python 的 import 缓存。
    - ``source="installed"``：第三方插件解压在 ``plugins/installed/<key>/``，
      不属于 ``app.*`` 包；用 ``spec_from_file_location`` + ``submodule_search_locations``
      手工创建模块对象再执行，使其能 ``from .plugin import ...`` 等相对 import。

    无论哪种来源，最终都把 ``Manifest`` 与 ``source`` 写到 plugin 类的 ``_manifest`` /
    ``_source`` 属性上，方便后续运行期、API 层直接读取。
    """
    init_file = path / "__init__.py"
    if not init_file.exists():
        log.warning("插件目录 %s 缺少 __init__.py，跳过", path)
        return {}
    from .base import _REGISTRY  # 延迟 import 避免循环

    registry_snapshot = dict(_REGISTRY)

    try:
        if source == "builtin":
            mod = importlib.import_module(
                f".builtin.{path.name}", package=__package__
            )
        else:
            # 第三方插件：构造一个独立的模块对象，避免污染 app 包命名空间。
            # 关键：必须把 mod 注册到 sys.modules，否则 ``from .plugin import X``
            # 这种相对 import 会找不到父包。
            import sys as _sys

            mod_name = _installed_module_name(path.name)
            _clear_installed_module_cache(path.name)
            spec = importlib.util.spec_from_file_location(
                mod_name,
                init_file,
                submodule_search_locations=[str(path)],
            )
            if spec is None or spec.loader is None:
                log.warning("无法为插件 %s 构造 spec", path)
                return {}
            mod = importlib.util.module_from_spec(spec)
            _sys.modules[mod_name] = mod
            try:
                spec.loader.exec_module(mod)
                _INSTALLED_MODULE_NAMES.update(
                    name
                    for name in _sys.modules
                    if name == mod_name or name.startswith(f"{mod_name}.")
                )
            except Exception:
                _sys.modules.pop(mod_name, None)
                raise
    except Exception:  # noqa: BLE001
        log.exception("加载插件目录 %s 失败", path)
        _restore_full_registry_snapshot(registry_snapshot)
        if source == "installed":
            _clear_installed_module_cache(path.name)
            _restore_full_registry_snapshot(registry_snapshot)
        return {}

    cls = getattr(mod, "PLUGIN_CLASS", None)
    manifest = getattr(mod, "MANIFEST", None)
    if cls is None or manifest is None:
        log.warning("插件 %s 缺少 PLUGIN_CLASS 或 MANIFEST，跳过", path)
        if source == "installed":
            _clear_installed_module_cache(path.name)
        _restore_registry_after_failed_load(registry_snapshot, path_key=path.name, cls=cls)
        return {}
    if not isinstance(manifest, Manifest):
        log.warning(
            "插件 %s 的 MANIFEST 不是 Manifest 实例 (got %s)，跳过",
            path,
            type(manifest).__name__,
        )
        if source == "installed":
            _clear_installed_module_cache(path.name)
        _restore_registry_after_failed_load(registry_snapshot, path_key=path.name, cls=cls)
        return {}
    identity_ok, identity_reason = _validate_plugin_identity(path, cls, manifest)
    if not identity_ok:
        log.warning("插件 %s 身份不一致，跳过: %s", path, identity_reason)
        if source == "installed":
            _clear_installed_module_cache(path.name)
        _restore_registry_after_failed_load(
            registry_snapshot,
            path_key=path.name,
            cls=cls,
            manifest=manifest,
        )
        return {}
    ok, reason = _manifest_compatible(manifest)
    if not ok:
        log.warning("插件 %s manifest 不兼容，跳过: %s", manifest.key, reason)
        if source == "installed":
            _clear_installed_module_cache(path.name)
        _restore_registry_after_failed_load(
            registry_snapshot,
            path_key=path.name,
            cls=cls,
            manifest=manifest,
        )
        return {}

    # 把 manifest / source 挂到 plugin 类上，方便 API 层暴露给前端
    cls._manifest = manifest
    cls._source = source

    # 防御性写入注册表：plugin.py 里若有 @register 已经写过；此处再写一次幂等
    # （主要是为了第三方插件——它们的 plugin.py 也应当 @register，但兜底一下）
    _REGISTRY[manifest.key] = cls
    return {manifest.key: cls}


def _load_installed_plugin(plugin_key: str) -> dict[str, type[Plugin]]:
    """按 key 加载单个 installed 插件。

    调用方必须先完成 DB 层授权检查；此函数只负责路径约束和 import。
    """
    if not _is_safe_plugin_key(plugin_key):
        log.warning("installed 插件 key 非法，拒绝加载: %r", plugin_key)
        return {}

    root = _installed_dir().resolve()
    path = (root / plugin_key).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        log.warning("installed 插件路径越界，拒绝加载: %s", path)
        return {}
    if not path.is_dir():
        legacy_path = (_BACKEND_DIR / "plugins" / "installed" / plugin_key).resolve()
        try:
            legacy_path.relative_to((_BACKEND_DIR / "plugins" / "installed").resolve())
        except ValueError:
            return {}
        if not legacy_path.is_dir():
            return {}
        log.warning(
            "installed 插件 %s 位于旧路径 %s；建议移动到 %s",
            plugin_key,
            legacy_path,
            path,
        )
        path = legacy_path
    return _load_dir(path, source="installed")


def _installed_plugin_exists(plugin_key: str) -> bool:
    """判断 installed 插件目录是否存在；仅用于兼容旧 account_feature 行。"""
    if not _is_safe_plugin_key(plugin_key):
        return False
    root = _installed_dir().resolve()
    path = (root / plugin_key).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return False
    if path.is_dir():
        return True
    legacy_root = (_BACKEND_DIR / "plugins" / "installed").resolve()
    legacy_path = (legacy_root / plugin_key).resolve()
    try:
        legacy_path.relative_to(legacy_root)
    except ValueError:
        return False
    return legacy_path.is_dir()


async def _authorize_via_installed_plugin(
    db: Any, plugin_key: str
) -> _InstalledPluginAuthorization:
    """按 installed_plugin 表做 installed 插件授权判断。"""

    installed_plugin = await db.get(InstalledPlugin, plugin_key)
    if installed_plugin is None:
        return _InstalledPluginAuthorization(
            allowed=False,
            state=FEATURE_STATE_FAILED,
            last_error="PLUGIN_LOAD_ORPHAN: installed_plugin missing",
            log_level="warn",
            log_message=f"installed 插件 {plugin_key} 缺少 installed_plugin 记录，已拒绝加载",
        )

    if not bool(getattr(installed_plugin, "enabled", False)):
        return _InstalledPluginAuthorization(
            allowed=False,
            state=FEATURE_STATE_DISABLED,
            last_error="PLUGIN_DISABLED: installed_plugin.enabled=False",
            log_level="info",
            log_message=f"installed 插件 {plugin_key} 的 installed_plugin.enabled=False，跳过加载",
        )

    if str(getattr(installed_plugin, "trust_tier", "")) == PLUGIN_TRUST_ORPHAN:
        return _InstalledPluginAuthorization(
            allowed=False,
            state=FEATURE_STATE_FAILED,
            last_error="PLUGIN_LOAD_ORPHAN: installed_plugin.trust_tier=orphan",
            log_level="warn",
            log_message=f"installed 插件 {plugin_key} trust_tier=orphan，已拒绝加载",
        )

    signature_ok = getattr(installed_plugin, "signature_ok", None)
    if signature_ok is False:
        return _InstalledPluginAuthorization(
            allowed=False,
            state=FEATURE_STATE_FAILED,
            last_error="PLUGIN_SIGNATURE_FAILED: installed_plugin.signature_ok=False",
            log_level="warn",
            log_message=f"installed 插件 {plugin_key} 的 installed_plugin.signature_ok=False，已拒绝加载",
        )
    if signature_ok is None and not bool(app_settings.plugin_allow_legacy_unsigned_plugins):
        return _InstalledPluginAuthorization(
            allowed=False,
            state=FEATURE_STATE_FAILED,
            last_error="PLUGIN_SIGNATURE_UNKNOWN: installed_plugin.signature_ok is null",
            log_level="warn",
            log_message=(
                f"installed 插件 {plugin_key} 的 installed_plugin.signature_ok=NULL，"
                "且 plugin_allow_legacy_unsigned_plugins=False，已拒绝加载"
            ),
        )

    last_install_error = str(getattr(installed_plugin, "last_install_error", "") or "").strip()
    if last_install_error:
        return _InstalledPluginAuthorization(
            allowed=False,
            state=FEATURE_STATE_FAILED,
            last_error=f"PLUGIN_INSTALL_FAILED: {last_install_error}",
            log_level="warn",
            log_message=(
                f"installed 插件 {plugin_key} 的 installed_plugin.last_install_error 非空，"
                "已拒绝加载"
            ),
        )

    return _InstalledPluginAuthorization(allowed=True)


async def _authorize_installed_plugin(
    db: Any,
    plugin_key: str,
    *,
    redis: Any | None = None,
    account_id: int | None = None,
) -> _InstalledPluginAuthorization:
    """统一判断 installed 插件是否允许被 worker 加载。

    第三方插件目录可能来自 zip、Git 远程仓库、历史本地残留或手工拷贝。worker 只认
    installed_plugin 里的权威安装状态；磁盘上有目录但缺少 installed_plugin 记录时，
    一律视为 orphan 并拒绝加载。``redis`` / ``account_id`` 保留在签名中，供调用方
    统一传参和未来扩展 runtime_log 使用。
    """

    return await _authorize_via_installed_plugin(db, plugin_key)


async def _write_account_feature_load_state(
    db: Any,
    account_id: int,
    feature_key: str,
    *,
    state: str,
    last_error: str | None,
) -> None:
    """写回插件加载状态，供前端模块中心展示。"""

    await db.execute(
        update(AccountFeature)
        .where(
            AccountFeature.account_id == account_id,
            AccountFeature.feature_key == feature_key,
        )
        .values(state=state, last_error=last_error)
    )
    await db.commit()


async def _deny_installed_plugin_load(
    db: Any,
    redis: Any,
    account_id: int,
    plugin_key: str,
    auth: _InstalledPluginAuthorization,
) -> None:
    """记录 installed 插件授权失败并同步账号功能状态。"""

    await _log(
        redis,
        account_id,
        auth.log_level,
        auth.log_message or f"installed 插件 {plugin_key} 未通过加载授权",
        source="system",
        plugin_key=plugin_key,
    )
    await _write_account_feature_load_state(
        db,
        account_id,
        plugin_key,
        state=auth.state,
        last_error=auth.last_error,
    )


def _load_builtin_plugin(plugin_key: str) -> dict[str, type[Plugin]]:
    """按 key 加载单个 builtin 插件；worker 启动时只为启用项付内存成本。"""

    path = _builtin_plugin_path(plugin_key)
    if path is None:
        return {}
    return _load_dir(path, source="builtin")


def discover_plugins(*, include_installed: bool = False) -> dict[str, type[Plugin]]:
    """按目录扫描插件根，返回 ``{key -> Plugin 子类}``。

    - 默认只扫描 builtin，避免无条件执行 installed 插件代码。
    - ``include_installed=True`` 仅保留给受控测试/迁移脚本；运行路径不要使用。
    - 单个插件失败只记日志，不影响其它插件。
    - 不存在 ``plugins/installed`` 目录时直接跳过该源。
    """
    out: dict[str, type[Plugin]] = {}
    for sub in _scan_builtin_dirs():
        out.update(_load_dir(sub, source="builtin"))
    if not include_installed:
        return out
    installed_dir = _installed_dir()
    if installed_dir.exists():
        for sub in sorted(installed_dir.iterdir(), key=lambda p: p.name):
            if not sub.is_dir() or sub.name.startswith("_"):
                continue
            out.update(_load_dir(sub, source="installed"))
    return out


# ─────────────────────────────────────────────────────
# 每账号一份运行态（worker 进程内单例）
# ─────────────────────────────────────────────────────
class _AccountState:
    """单账号 worker 的插件运行态，包含 engine、client、各插件实例与 ctx。"""

    def __init__(self, account_id: int) -> None:
        self.account_id = account_id
        self.generation: int = 1
        self.engine: RateLimitEngine | None = None
        self.client: TelegramClient | None = None
        self.redis: Any = None  # redis.asyncio.Redis
        self.scheduler: Any = None  # PlatformScheduler
        self.contexts: dict[str, PluginContext] = {}  # feature_key -> ctx
        self.instances: dict[str, Plugin] = {}  # feature_key -> Plugin 实例
        # paused 由 runtime 创建并传入；is_set() == True 表示正常运行
        self.paused: asyncio.Event | None = None
        # Sprint2 #3：忽略 peer 名单（int set），从 ignored_peer 表加载，IPC 触发热更
        self.ignored_peers: set[int] = set()
        self.owner_tg_user_id: int | None = None
        self.sudo_users: dict[int, dict[str, Any]] = {}
        # Sprint2 #3：最近活跃 peer 的 LRU（peer_id -> {peer_kind, peer_label, ts}）
        # 仅 worker 内存维护；重启后清空。前端不能假设它持久。
        self.recent_peers: collections.OrderedDict[int, dict[str, Any]] = collections.OrderedDict()
        # 是否对每条 incoming 消息都额外写一行可见性 runtime_log。
        # 默认 False（小机器场景能省大量 Redis stream + DB 写入）。
        # 通过 system_setting key=``log_incoming_messages`` 全局打开，账号
        # 启动 / reload_config 时同步。命令派发、插件错误、业务事件不受影响。
        self.log_incoming_messages: bool = False
        self.account_proxy_url: str | None = None


# 进程级状态字典（一个 worker 进程通常只服务一个账号；用 dict 是为了灵活）
_STATES: dict[int, _AccountState] = {}


async def _event_sender_id(event: Any) -> int | None:
    sender = getattr(event, "sender", None)
    sender_id = getattr(sender, "id", None)
    if sender_id is not None:
        return int(sender_id)
    try:
        sender = await event.get_sender()
        sender_id = getattr(sender, "id", None)
        return int(sender_id) if sender_id is not None else None
    except Exception:  # noqa: BLE001
        return None


async def _event_allowed_for_owner_only(state: _AccountState, event: Any) -> bool:
    """owner_only 插件的统一消息门禁：账号本人或授权 sudo 用户才可触发。"""
    if bool(getattr(event, "outgoing", False)):
        return True
    sender_id = await _event_sender_id(event)
    if sender_id is None:
        return False
    if state.owner_tg_user_id is not None and sender_id == state.owner_tg_user_id:
        return True
    sudo_cfg = state.sudo_users.get(sender_id)
    if sudo_cfg is None:
        return False
    allowed_chats = sudo_cfg.get("allowed_chat_ids") or []
    if not sudo_chat_allowed(allowed_chats, getattr(event, "chat_id", None)):
        return False
    return True


def _rule_chat_matches_for_interaction_guard(rule: dict[str, Any], chat_id: int | None) -> bool:
    if chat_id is None:
        return False
    chat_ids = rule.get("chat_ids")
    if isinstance(chat_ids, list) and chat_ids:
        try:
            return int(chat_id) in {int(item) for item in chat_ids}
        except (TypeError, ValueError):
            return False
    return True


def _text_equals_any(text: str, values: Any) -> bool:
    if not isinstance(values, list):
        return False
    clean = str(text or "").strip()
    return bool(clean and any(clean == str(item or "").strip() for item in values if str(item or "").strip()))


async def _interaction_bot_owns_incoming_text(state: _AccountState, event: Any) -> bool:
    """交互 Bot 规则接管的关键词不再交给普通插件 on_message 自行开局。"""

    text = str(getattr(event, "raw_text", "") or "").strip()
    if not text:
        return False
    try:
        async with AsyncSessionLocal() as db:
            row = await db.get(SystemSetting, interaction_bot_service.transfer_notice_setting_key(state.account_id))
        cfg = interaction_bot_service.normalize_transfer_notice_config(row.value if row is not None else None)
    except Exception:  # noqa: BLE001
        log.debug("读取交互 Bot 规则失败 account=%s", state.account_id, exc_info=True)
        return False
    if not cfg.get("enabled"):
        return False
    chat_id = getattr(event, "chat_id", None)
    for rule in cfg.get("rules") or []:
        if not isinstance(rule, dict) or not bool(rule.get("enabled", True)):
            continue
        if not _rule_chat_matches_for_interaction_guard(rule, chat_id):
            continue
        if _text_equals_any(text, rule.get("open_commands")) or _text_equals_any(text, rule.get("close_commands")):
            return True
        if _text_equals_any(text, rule.get("module_start_keywords")):
            return True
    return False


# ─────────────────────────────────────────────────────
# 主入口：load_plugins_for_account
# ─────────────────────────────────────────────────────
async def load_plugins_for_account(
    client: TelegramClient,
    account_id: int,
    paused: asyncio.Event,
    redis: Any,
    scheduler: Any | None = None,
    account_proxy_url: str | None = None,
) -> None:
    """runtime 在 ``client.connect()`` 之前调一次。

    步骤：
      1. 构造账号插件运行态
      2. 构造 ``RateLimitEngine``（依赖 humanize 配置 + service 层 ``get_effective``）
      3. 在 client 上注册全局消息派发，把每条消息按 instances 顺序广播
      4. 加载该账号已启用的 features → ``_activate`` 按需导入对应插件
    """
    state = _AccountState(account_id)
    state.client = client
    state.paused = paused
    state.redis = redis
    state.scheduler = scheduler
    state.account_proxy_url = account_proxy_url
    state.log_incoming_messages = await _load_log_incoming_messages_setting()
    _STATES[account_id] = state

    # ── 1) 拉取拟人化 + 账号信息构造 engine ──
    async with AsyncSessionLocal() as db:
        acc = await db.get(Account, account_id)
        humanize_row = await db.get(HumanizeConfig, account_id)
        sudo_rows = (
            await db.execute(select(SudoUser).where(SudoUser.account_id == account_id))
        ).scalars().all()
    owner_id = getattr(acc, "tg_user_id", None)
    state.owner_tg_user_id = int(owner_id) if owner_id else None
    state.sudo_users = {
        int(r.tg_user_id): {
            "allowed_chat_ids": list(r.allowed_chat_ids or []),
            "allowed_commands": list(r.allowed_commands or []),
        }
        for r in sudo_rows
    }
    opts = HumanizeOpts(
        jitter_pct=humanize_row.jitter_pct if humanize_row else 15,
        typing_simulate=bool(humanize_row.typing_simulate) if humanize_row else True,
        typing_min_ms=humanize_row.typing_min_ms if humanize_row else 1000,
        typing_max_ms=humanize_row.typing_max_ms if humanize_row else 3000,
        typing_probability=humanize_row.typing_probability if humanize_row else 80,
        read_before_reply=bool(humanize_row.read_before_reply) if humanize_row else True,
        active_window_start=humanize_row.active_window_start if humanize_row else None,
        active_window_end=humanize_row.active_window_end if humanize_row else None,
        cold_start_days=humanize_row.cold_start_days if humanize_row else 7,
        cold_start_until=acc.cold_start_until if acc else None,
    )

    async def _get_eff(aid: int, action: str):
        """engine 用的 get_effective 工厂闭包：每次新开 session，避免共享。"""
        async with AsyncSessionLocal() as db:
            return await get_effective(db, aid, action)

    state.engine = RateLimitEngine(account_id, opts, _get_eff, redis=redis)
    if scheduler is not None:
        try:
            scheduler.attach_engine(state.engine)
        except Exception:  # noqa: BLE001
            log.exception("注入平台调度器 engine 失败 account=%s", account_id)

    # ── 1.5) 拉取允许 peer 名单（沿用 ignored_peer 表存储） ──
    await _load_ignored_peers(state)

    # ── 2) 全局事件派发 ──
    def _make_dispatcher(direction: str, *, edited: bool = False):  # "incoming" or "outgoing"
        """创建消息派发闭包。direction 对应 Plugin.message_channels 的值。"""
        kwargs = {"incoming": True} if direction == "incoming" else {"outgoing": True}
        handler_name = "on_message_edited" if edited else "on_message"
        event_label = f"{direction}_edited" if edited else direction
        event_builder = events.MessageEdited if edited else events.NewMessage

        @client.on(event_builder(**kwargs))
        async def _dispatch(event):  # noqa: ANN001
            if state.paused is not None and not state.paused.is_set():
                return

            # incoming 消息需要允许名单检查和 LRU 维护
            if direction == "incoming":
                pid = event.chat_id
                if pid is not None:
                    await _record_recent_peer(state, event)
                    # 白名单模式：配置为空 = 放行全部；非空 = 仅放行名单内会话
                    if state.ignored_peers and pid not in state.ignored_peers:
                        log.debug("[allowed] drop account=%s chat_id=%s", account_id, pid)
                        return
                # 调试日志：每条 incoming 消息记一行；
                # 默认关闭，small VPS 上活跃账号能产生数百条/分钟。
                # 在 system_setting.log_incoming_messages = true 时打开。
                if state.log_incoming_messages:
                    try:
                        peer_kind = (
                            "private" if event.is_private
                            else "channel" if event.is_channel
                            else "group" if event.is_group
                            else "?"
                        )
                        text_preview = (event.raw_text or "")[:80]
                        edit_label = "编辑" if edited else ""
                        await _log(
                            redis,
                            account_id,
                            "info",
                            (
                                f"收到一条{peer_kind}{edit_label}消息：聊天 ID={event.chat_id}，"
                                f"内容预览={text_preview!r}。已进入插件分发流程。"
                            ),
                            source="event",
                            chat_id=event.chat_id,
                            peer_kind=peer_kind,
                            message_preview=text_preview,
                            edited=edited,
                        )
                    except Exception:  # noqa: BLE001
                        pass
                if not edited and await _interaction_bot_owns_incoming_text(state, event):
                    return

            for fkey, inst in list(state.instances.items()):
                if direction not in inst.message_channels:
                    continue
                if getattr(inst, "owner_only", True):
                    allowed = await _event_allowed_for_owner_only(state, event)
                    if not allowed:
                        continue
                ctx = state.contexts.get(fkey)
                if ctx is None:
                    continue
                if ctx.generation != state.generation:
                    continue
                handler = getattr(inst, handler_name, None)
                if handler is None:
                    continue
                if edited and getattr(type(inst), handler_name, None) is getattr(Plugin, handler_name, None):
                    continue
                plugin_event = _wrap_event_for_context(event, ctx)
                try:
                    await handler(ctx, plugin_event)
                except Exception as exc:  # noqa: BLE001
                    await _log(
                        redis,
                        account_id,
                        "error",
                        (
                            f"插件 {fkey} 处理{event_label}消息时出错："
                            f"{type(exc).__name__}: {exc}。"
                            "这条消息已跳过，其他插件和 worker 会继续运行。"
                        ),
                        source="plugin",
                        plugin_key=fkey,
                        direction=event_label,
                        chat_id=getattr(event, "chat_id", None),
                        sender_id=getattr(event, "sender_id", None),
                        message_preview=(getattr(event, "raw_text", "") or "")[:200],
                        traceback=traceback.format_exc(limit=8),
                    )

        return _dispatch

    _make_dispatcher("outgoing", edited=True)
    _make_dispatcher("incoming", edited=True)
    _make_dispatcher("outgoing")
    _make_dispatcher("incoming")

    # ── 3) 加载该账号所有已启用 feature ──
    async with AsyncSessionLocal() as db:
        afs = (
            await db.execute(
                select(AccountFeature).where(
                    AccountFeature.account_id == account_id,
                    AccountFeature.enabled.is_(True),
                )
            )
        ).scalars().all()
        for af in afs:
            await _activate(db, state, af, redis)


# ─────────────────────────────────────────────────────
# 单 feature 激活（安全：双开关检查）
# ─────────────────────────────────────────────────────
async def _activate(db, state: _AccountState, af: AccountFeature, redis: Any) -> None:
    """根据 ``account_feature`` 行实例化对应插件，调 ``on_startup``，写状态。

    **安全：加载授权检查**
    - builtin 插件只看 AccountFeature.enabled
    - installed 插件必须有 installed_plugin 安装记录
    - installed 插件的全局开关、签名状态、账号开关都允许时才加载

    Args:
        db: AsyncSession
        state: _AccountState 实例
        af: AccountFeature 行
        redis: Redis 客户端
    """
    if af.feature_key == FEATURE_SCHEDULER:
        # scheduler 已是 worker 级平台基础能力，不再作为普通插件实例加载。
        # 保留 account_feature 行仅用于历史兼容和前端配置入口。
        await db.execute(
            update(AccountFeature)
            .where(
                AccountFeature.account_id == state.account_id,
                AccountFeature.feature_key == af.feature_key,
            )
            .values(state=FEATURE_STATE_ACTIVE, last_error=None)
        )
        await db.commit()
        return

    cls = get_plugin(af.feature_key)
    if cls is None and _builtin_plugin_path(af.feature_key) is not None:
        _load_builtin_plugin(af.feature_key)
        cls = get_plugin(af.feature_key)
    if cls is None:
        if _installed_plugin_exists(af.feature_key):
            auth = await _authorize_installed_plugin(
                db,
                af.feature_key,
                redis=redis,
                account_id=state.account_id,
            )
            if not auth.allowed:
                await _deny_installed_plugin_load(
                    db,
                    redis,
                    state.account_id,
                    af.feature_key,
                    auth,
                )
                return
            _load_installed_plugin(af.feature_key)
            cls = get_plugin(af.feature_key)
    if cls is None:
        last_error, log_message = _missing_plugin_error(af.feature_key)
        await _log(
            redis,
            state.account_id,
            "warn",
            log_message,
        )
        await _write_account_feature_load_state(
            db,
            state.account_id,
            af.feature_key,
            state=FEATURE_STATE_FAILED,
            last_error=last_error,
        )
        return

    # ── 安全：installed 插件统一授权检查 ──
    plugin_source = getattr(cls, "_source", "builtin")
    if plugin_source == "installed":
        auth = await _authorize_installed_plugin(
            db,
            af.feature_key,
            redis=redis,
            account_id=state.account_id,
        )
        if not auth.allowed:
            await _deny_installed_plugin_load(
                db,
                redis,
                state.account_id,
                af.feature_key,
                auth,
            )
            return
    # ── installed 授权检查结束 ──

    # 拉规则（按 priority 倒序：值越大越先匹配）
    rules = (
        await db.execute(
            select(Rule)
            .where(
                Rule.account_id == state.account_id,
                Rule.feature_key == af.feature_key,
                Rule.enabled.is_(True),
            )
            .order_by(Rule.priority.desc())
        )
    ).scalars().all()

    inst = cls()
    # 阶段 C：第三方插件 (source="installed") 拿到的 client 走沙箱包装；
    # builtin 仍直接拿真 client（避免对原代码做改动）。
    plugin_client: Any = state.client
    plugin_source = getattr(cls, "_source", "builtin")
    plugin_manifest = getattr(cls, "_manifest", None)
    if plugin_source == "installed" and state.client is not None:
        from .sandbox import SandboxClient  # 延迟 import 避免循环

        perms = list(plugin_manifest.permissions) if plugin_manifest else []
        plugin_client = SandboxClient(
            state.client, perms, plugin_key=af.feature_key
        )

    effective_config = await _merge_plugin_config(
        db, state.account_id, af.feature_key, dict(af.config or {})
    )
    plugin_permissions = set(plugin_manifest.permissions or []) if plugin_manifest is not None else set()
    plugin_http: Any = None
    if plugin_manifest is not None and "external_http" in plugin_permissions:
        allowed_hosts = list(getattr(plugin_manifest, "allowed_hosts", None) or [])
        if allowed_hosts:
            from .http_facade import PluginHTTP  # 延迟 import，避免未用 HTTP 的插件增加依赖面

            plugin_http = PluginHTTP.from_context(
                PluginContext(
                    account_id=state.account_id,
                    feature_key=af.feature_key,
                    config=effective_config,
                    account_proxy_url=state.account_proxy_url,
                ),
                allowed_hosts=allowed_hosts,
                manifest_http=getattr(plugin_manifest, "http", None),
            )
    plugin_ai: Any = None
    if plugin_manifest is not None and "ai_text" in plugin_permissions:
        from .ai_facade import PluginAI  # 延迟 import，避免未用 AI 的插件增加依赖面

        plugin_ai = PluginAI.from_context(
            PluginContext(
                account_id=state.account_id,
                feature_key=af.feature_key,
            )
        )
    declared_facade_permissions = plugin_permissions & (
        _SUPPORTED_FACADE_PERMISSIONS | _RESERVED_UNSUPPORTED_FACADE_PERMISSIONS
    )
    unsupported_facade_permissions = (
        declared_facade_permissions & _RESERVED_UNSUPPORTED_FACADE_PERMISSIONS
    )
    for perm in sorted(unsupported_facade_permissions):
        await _log(
            redis,
            state.account_id,
            "warn",
            f"manifest 声明权限 {perm} 但当前平台未提供对应 facade，将被忽略",
            source="system",
            plugin_key=af.feature_key,
        )

    ctx = PluginContext(
        account_id=state.account_id,
        feature_key=af.feature_key,
        config=effective_config,
        rules=list(rules),
        client=plugin_client,
        engine=state.engine if plugin_source != "installed" else None,
        redis=(state.redis or redis) if plugin_source != "installed" else None,
        log=_make_logger(redis, state.account_id, af.feature_key),
        scheduler=(
            state.scheduler.for_plugin(af.feature_key, state.generation)
            if state.scheduler is not None else None
        ),
        http=plugin_http,
        ai=plugin_ai,
        generation=state.generation,
        account_proxy_url=state.account_proxy_url,
    )

    try:
        await inst.on_startup(ctx)
    except Exception as exc:  # noqa: BLE001
        if state.scheduler is not None:
            state.scheduler.unregister_owner(af.feature_key)
        await db.execute(
            update(AccountFeature)
            .where(
                AccountFeature.account_id == state.account_id,
                AccountFeature.feature_key == af.feature_key,
            )
            .values(state=FEATURE_STATE_FAILED, last_error=str(exc))
        )
        await db.commit()
        await _log(
            redis,
            state.account_id,
            "error",
            f"插件 {af.feature_key} startup 失败: {exc}",
        )
        return

    state.instances[af.feature_key] = inst
    state.contexts[af.feature_key] = ctx

    # 暴露插件命令到 TG 命令分发表
    # 安全：传入 generation 和 plugin_key，以便 reload/disable 时能追踪并注销旧命令
    cmds = getattr(inst, "commands", None) or cls.commands or {}
    for cname, fn in cmds.items():
        register_plugin_command(
            cname,
            _wrap_cmd(fn, ctx),
            owner_plugin_key=af.feature_key,
            generation=state.generation,
        )

    await db.execute(
        update(AccountFeature)
        .where(
            AccountFeature.account_id == state.account_id,
            AccountFeature.feature_key == af.feature_key,
        )
        .values(state=FEATURE_STATE_ACTIVE, last_error=None)
    )
    await db.commit()


def _wrap_cmd(fn, ctx: PluginContext):
    """把插件 ``commands`` 里登记的 5 参数 handler 包成命令分发期望的 4 参数签名。"""

    async def w(client, event, args, account_id):  # noqa: ANN001
        plugin_client = ctx.client if ctx.client is not None else client
        plugin_event = _wrap_event_for_context(event, ctx)
        await fn(plugin_client, plugin_event, args, account_id, ctx)

    return w


def _wrap_event_for_context(event: Any, ctx: PluginContext) -> Any:
    """Installed plugins receive a SandboxEvent so event helpers honor permissions."""

    client = ctx.client
    if client is None:
        return event
    try:
        from .sandbox import SandboxEvent
    except Exception:  # noqa: BLE001
        return event
    if bool(getattr(client, "_is_sandboxed", False)) or bool(getattr(client, "is_sandbox_client", False)):
        return SandboxEvent(event, client, plugin_key=ctx.feature_key)
    return event


def _make_logger(redis: Any, account_id: int, plugin_key: str):
    """构造一个 ctx.log 协程，写到 ``runtime_log_stream``。"""

    async def _writer(level: str, message: str, **detail: Any) -> None:
        source = str(detail.pop("source", "plugin"))
        await _log(
            redis,
            account_id,
            level,
            message,
            source=source,
            plugin_key=plugin_key,
            **detail,
        )

    return _writer


# ─────────────────────────────────────────────────────
# 配置合并：_merge_plugin_config
# ─────────────────────────────────────────────────────
async def _merge_plugin_config(
    db: AsyncSessionLocal,
    account_id: int,
    feature_key: str,
    account_config: dict[str, Any],
) -> dict[str, Any]:
    """合并插件配置。

    合并顺序：schema defaults < global config < account config

    - global config 存储在 Feature.manifest["global_config"] 中
    - 合并时只取 account_config 中非 global 字段
    """
    from ...db.models.feature import Feature

    # 获取 feature manifest
    feature = await db.get(Feature, feature_key)
    if feature is None:
        return account_config

    manifest = feature.manifest or {}
    config_schema = manifest.get("config_schema", {})
    global_config = manifest.get("global_config", {})

    # 提取 schema defaults
    defaults: dict[str, Any] = {}
    properties = config_schema.get("properties", {})
    for prop_name, prop_def in properties.items():
        if isinstance(prop_def, dict) and "default" in prop_def:
            defaults[prop_name] = prop_def["default"]

    # 提取 global 字段名
    global_fields = {
        k for k, v in properties.items()
        if isinstance(v, dict) and v.get("level") == "global"
    }

    # 提取 account 专属字段（排除 global 字段）
    account_only_config = {k: v for k, v in account_config.items() if k not in global_fields}

    # 合并：defaults < global < account_only
    result = {**defaults}
    for k, v in global_config.items():
        if k in global_fields:
            result[k] = v
    result.update(account_only_config)

    return result


# ─────────────────────────────────────────────────────
# 配置热更新：reload_account_config
# ─────────────────────────────────────────────────────
async def reload_account_config(account_id: int, payload: dict | None = None) -> None:
    """收到 IPC ``reload_config`` 时调用：

    - **先刷新 BUILTIN_FEATURES**：动态重扫 builtin 目录，让新增插件立即可见
    - builtin / installed 插件都在 ``_activate`` 中按需加载，避免每次热更新导入全部实现
    - 已实例化的 feature：刷新 ``ctx.config`` / ``ctx.rules``；若该 feature 已被禁用则 shutdown
    - 数据库新增的 enabled feature：调 ``_activate`` 加载

    任何异常都吞掉，热更新失败不应让 worker 崩溃。
    """
    state = _STATES.get(account_id)
    if state is None:
        return
    next_generation = state.generation + 1
    redis = state.redis or get_redis()

    # 同步全局开关：让 reload_config 也能让 incoming-message 可见性日志即时生效
    state.log_incoming_messages = await _load_log_incoming_messages_setting()

    # 刷新动态发现的 BUILTIN_FEATURES，让新增 builtin 插件目录立即可见
    try:
        from ...db.models.feature import BUILTIN_FEATURES  # noqa: PLC0415
        BUILTIN_FEATURES.refresh()
    except Exception:  # noqa: BLE001
        log.exception("reload_account_config 时刷新 BUILTIN_FEATURES 失败")

    reload_plugin_key = None
    if isinstance(payload, dict):
        raw_key = payload.get("plugin_key")
        if isinstance(raw_key, str) and raw_key:
            reload_plugin_key = raw_key
            _clear_installed_module_cache(raw_key)

    async with AsyncSessionLocal() as db:
        acc = await db.get(Account, account_id)
        owner_id = getattr(acc, "tg_user_id", None)
        state.owner_tg_user_id = int(owner_id) if owner_id else None
        sudo_rows = (
            await db.execute(select(SudoUser).where(SudoUser.account_id == account_id))
        ).scalars().all()
        state.sudo_users = {
            int(r.tg_user_id): {
                "allowed_chat_ids": list(r.allowed_chat_ids or []),
                "allowed_commands": list(r.allowed_commands or []),
            }
            for r in sudo_rows
        }

        # 1) 现有实例：刷新或卸载
        for fkey, inst in list(state.instances.items()):
            af = (
                await db.execute(
                    select(AccountFeature).where(
                        AccountFeature.account_id == account_id,
                        AccountFeature.feature_key == fkey,
                    )
                )
            ).scalar_one_or_none()
            cls = get_plugin(fkey)
            plugin_source = getattr(cls, "_source", "builtin") if cls is not None else "builtin"
            auth_denied: _InstalledPluginAuthorization | None = None
            if plugin_source == "installed":
                auth = await _authorize_installed_plugin(
                    db,
                    fkey,
                    redis=redis,
                    account_id=account_id,
                )
                if not auth.allowed:
                    auth_denied = auth

            force_reload = reload_plugin_key == fkey
            if af is None or not af.enabled or auth_denied is not None or force_reload:
                ctx = state.contexts.get(fkey)
                inst = state.instances.get(fkey)

                # ── 安全：先注销该插件的所有命令 ──
                if inst is not None:
                    if cls is not None:
                        cmds = getattr(inst, "commands", None) or cls.commands or {}
                        for cname in cmds.keys():
                            unregister_plugin_command(cname, owner_plugin_key=fkey)
                    if state.scheduler is not None:
                        state.scheduler.unregister_owner(fkey)

                # 调用 shutdown（幂等设计）
                if ctx is not None and inst is not None:
                    try:
                        await inst.on_shutdown(ctx)
                    except Exception:  # noqa: BLE001
                        log.exception("on_shutdown 失败 feature=%s", fkey)

                state.instances.pop(fkey, None)
                state.contexts.pop(fkey, None)
                if af is not None and not af.enabled:
                    # 同时写状态为 disabled，便于前端展示
                    await _write_account_feature_load_state(
                        db,
                        account_id,
                        fkey,
                        state=FEATURE_STATE_DISABLED,
                        last_error=None,
                    )
                elif af is not None and auth_denied is not None:
                    await _deny_installed_plugin_load(
                        db,
                        redis,
                        account_id,
                        fkey,
                        auth_denied,
                    )
                continue
            # 仍启用：刷新 rules + config
            rules = (
                await db.execute(
                    select(Rule)
                    .where(
                        Rule.account_id == account_id,
                        Rule.feature_key == fkey,
                        Rule.enabled.is_(True),
                    )
                    .order_by(Rule.priority.desc())
                )
            ).scalars().all()
            ctx = state.contexts[fkey]

            # 合并配置：schema defaults < global config < account config
            old_config = dict(ctx.config or {})
            new_config = await _merge_plugin_config(db, account_id, fkey, dict(af.config or {}))
            command_config_keys = set(getattr(inst, "command_config_keys", set()) or set())
            command_config_changed = any(
                old_config.get(k) != new_config.get(k) for k in command_config_keys
            )
            if command_config_changed:
                cmds = getattr(inst, "commands", None) or cls.commands or {}
                for cname in cmds.keys():
                    unregister_plugin_command(cname, owner_plugin_key=fkey)
                if state.scheduler is not None:
                    state.scheduler.unregister_owner(fkey)
                try:
                    await inst.on_shutdown(ctx)
                except Exception:  # noqa: BLE001
                    log.exception("命令配置变化后 on_shutdown 失败 feature=%s", fkey)
                state.instances.pop(fkey, None)
                state.contexts.pop(fkey, None)
                await _activate(db, state, af, redis)
                continue

            ctx.config = new_config
            ctx.rules = list(rules)
            ctx.generation = state.generation
            ctx.scheduler = (
                state.scheduler.for_plugin(fkey, state.generation)
                if state.scheduler is not None else None
            )

        # 2) 处理新增的 enabled feature
        afs = (
            await db.execute(
                select(AccountFeature).where(
                    AccountFeature.account_id == account_id,
                    AccountFeature.enabled.is_(True),
                )
            )
        ).scalars().all()
        for af in afs:
            if af.feature_key not in state.instances:
                await _activate(db, state, af, redis)

    state.generation = next_generation
    for fkey, ctx in state.contexts.items():
        ctx.generation = next_generation
        ctx.scheduler = (
            state.scheduler.for_plugin(fkey, next_generation)
            if state.scheduler is not None else None
        )

    await _log(redis, account_id, "info", "插件配置已热更新")


# ─────────────────────────────────────────────────────
# 单插件热重载：reload_plugin
# ─────────────────────────────────────────────────────
async def reload_plugin(account_id: int, plugin_key: str | None) -> None:
    """热重载单个插件并重新激活。

    builtin 走 importlib.reload；installed 先清模块缓存，再让 _activate 在 DB 双开关
    通过后重新加载。
    """
    if not plugin_key:
        return
    state = _STATES.get(account_id)
    if state is None:
        return
    state.generation += 1
    redis = state.redis or get_redis()

    # 1) 先注销旧插件命令（如果有）
    if plugin_key in state.instances:
        inst = state.instances[plugin_key]
        cls = get_plugin(plugin_key)
        if cls is not None:
            cmds = getattr(inst, "commands", None) or cls.commands or {}
            for cname in cmds.keys():
                unregister_plugin_command(cname, owner_plugin_key=plugin_key)
        if state.scheduler is not None:
            state.scheduler.unregister_owner(plugin_key)

    # 2) shutdown 旧实例（幂等设计）
    if plugin_key in state.instances:
        try:
            await state.instances[plugin_key].on_shutdown(state.contexts[plugin_key])
        except Exception:  # noqa: BLE001
            log.exception("on_shutdown 失败 feature=%s", plugin_key)
        state.instances.pop(plugin_key, None)
        state.contexts.pop(plugin_key, None)

    # 2) reload 模块
    builtin_path = _builtin_plugin_path(plugin_key)
    if builtin_path is None:
        _clear_installed_module_cache(plugin_key)
    else:
        try:
            # 目录型 builtin 可能有 client/importers/token_pool 等辅助模块。
            # 直接清掉整个包再重新加载，避免 plugin.py 引到旧的子模块对象。
            _clear_builtin_module_cache(plugin_key)
            loaded = _load_dir(builtin_path, source="builtin")
            if plugin_key not in loaded:
                raise RuntimeError(f"builtin plugin {plugin_key} reload returned no plugin class")
        except Exception as exc:  # noqa: BLE001
            await _log(redis, account_id, "error", f"reload {plugin_key} 失败: {exc}")
            return

    # 3) 重新激活
    async with AsyncSessionLocal() as db:
        af = (
            await db.execute(
                select(AccountFeature).where(
                    AccountFeature.account_id == account_id,
                    AccountFeature.feature_key == plugin_key,
                )
            )
        ).scalar_one_or_none()
        if af is not None and af.enabled:
            await _activate(db, state, af, redis)
    await _log(redis, account_id, "info", f"插件 {plugin_key} 已重载")


# ─────────────────────────────────────────────────────
# 写运行日志的便利函数
# ─────────────────────────────────────────────────────
async def _log(
    redis: Any,
    account_id: int | None,
    level: str,
    message: str,
    *,
    source: str = "event",
    **detail: Any,
) -> None:
    """写入 ``runtime_log_stream``，主进程批量消费落库。任何异常吞掉。

    source 语义（前端 Logs 页 tab 区分）：
    - ``"event"``（loader 默认）  — incoming 消息事件 / plugin 命中 / 命令派发
    - ``"system"``                — plugin 内部错误 / 加载失败等技术异常应显式传

    历史数据里也会出现 ``"plugin"`` 旧值，API 层做了别名映射。
    """
    try:
        payload = RuntimeLogPayload(
            account_id=account_id,
            level=level,  # type: ignore[arg-type]
            source=source,
            message=message,
            detail=detail or None,
        )
        await redis.rpush(RUNTIME_LOG_STREAM, payload.encode())
    except Exception:  # noqa: BLE001
        log.exception("写 runtime_log_stream 失败 account=%s", account_id)


# 测试与外部需要时可用：列出当前所有已注册的 plugin 类
def registered_plugins() -> dict[str, type[Plugin]]:
    """便于测试 / 调试：返回当前注册表副本。"""
    return all_plugins()


# ─────────────────────────────────────────────────────
# Sprint2 #3：允许 peer（沿用 ignored_peer 表）+ 最近活跃 peer
# ─────────────────────────────────────────────────────
async def _load_ignored_peers(state: _AccountState) -> None:
    """从 ``ignored_peer`` 表把当前账号的所有 peer_id 装进内存 set（允许名单语义）。

    任何异常都吞掉——失败时退化为"空名单"，等价于允许全部，业务侧不至于挂。
    """
    try:
        async with AsyncSessionLocal() as db:
            rows = (
                await db.execute(
                    select(IgnoredPeer.peer_id).where(
                        IgnoredPeer.account_id == state.account_id
                    )
                )
            ).scalars().all()
        state.ignored_peers = {int(pid) for pid in rows}
    except Exception:  # noqa: BLE001
        log.exception("加载允许名单失败 account=%s", state.account_id)
        state.ignored_peers = set()


def _classify_peer(event: Any) -> str:
    """把 Telethon event 的会话类型归一化为 ``private/group/supergroup/channel``。

    supergroup 与 channel 都属于 ``is_channel``；通过 chat_id 的 -100 前缀区分
    （Telegram 协议约定，supergroup 与 channel 的 chat_id 都以 -100 开头，
    我们这里粗略把"是 group 又是 channel 的"当作 supergroup）。
    """
    try:
        if event.is_private:
            return "private"
        if event.is_channel and not event.is_group:
            return "channel"
        if event.is_group and event.is_channel:
            return "supergroup"
        return "group"
    except Exception:  # noqa: BLE001
        return "private"


async def _record_recent_peer(state: _AccountState, event: Any) -> None:
    """把当前 event 的 peer 写入 LRU；超出上限则丢最旧。

    会尝试调 ``event.get_chat()`` 拿群名/用户名作为 ``peer_label``，失败则用 chat_id 字符串兜底。
    异常一律吞掉——这条 LRU 只是 UI 辅助，不能影响主流程。
    """
    pid = event.chat_id
    if pid is None:
        return
    try:
        kind = _classify_peer(event)
        label: str | None
        try:
            chat = await event.get_chat()
            label = (
                getattr(chat, "title", None)
                or getattr(chat, "username", None)
                or getattr(chat, "first_name", None)
                or str(pid)
            )
        except Exception:  # noqa: BLE001
            label = str(pid)
        state.recent_peers[pid] = {
            "peer_kind": kind,
            "peer_label": label,
            "ts": time.time(),
        }
        # OrderedDict.move_to_end 把"最近一次写入的 peer"挪到末尾，实现 LRU
        state.recent_peers.move_to_end(pid)
        while len(state.recent_peers) > RECENT_PEERS_LIMIT:
            state.recent_peers.popitem(last=False)
    except Exception:  # noqa: BLE001
        log.exception("维护 recent_peers 失败 account=%s pid=%s", state.account_id, pid)


async def reload_ignored_peers(account_id: int) -> None:
    """IPC ``reload_ignored`` 入口：从 DB 重新拉一遍名单。

    若该账号在本进程没有运行态（worker 未起 / 已退出），静默忽略。
    """
    state = _STATES.get(account_id)
    if state is None:
        return
    await _load_ignored_peers(state)
    redis = state.redis or get_redis()
    await _log(
        redis,
        account_id,
        "info",
        f"允许群组名单已热更新（共 {len(state.ignored_peers)} 个 peer）",
    )


def get_recent_peers(account_id: int) -> list[dict[str, Any]]:
    """IPC ``get_recent_peers`` 应答：返回当前账号最近活跃 peer 列表。

    顺序：最新 → 最旧（OrderedDict 末尾是最近写入的，所以反向遍历）。
    若该账号在本进程没有运行态，返回空列表。
    """
    state = _STATES.get(account_id)
    if state is None:
        return []
    out: list[dict[str, Any]] = []
    for pid, info in reversed(state.recent_peers.items()):
        out.append(
            {
                "peer_id": int(pid),
                "peer_kind": info.get("peer_kind") or "private",
                "peer_label": info.get("peer_label"),
                "ts": float(info.get("ts") or 0.0),
            }
        )
    return out


async def invoke_interaction_entry(
    account_id: int,
    *,
    plugin_key: str,
    entry_key: str,
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    """调用已加载插件的交互 Bot 入口，返回平台标准动作。"""

    state = _STATES.get(account_id)
    if state is None:
        raise RuntimeError("账号 worker 尚未运行")
    plugin_key = str(plugin_key or "").strip()
    entry_key = str(entry_key or "").strip()
    if not plugin_key or not entry_key:
        raise ValueError("缺少 plugin_key 或 entry_key")
    inst = state.instances.get(plugin_key)
    ctx = state.contexts.get(plugin_key)
    if inst is None or ctx is None:
        raise RuntimeError(f"模块未加载或未启用：{plugin_key}")
    actions = await inst.on_interaction(ctx, entry_key, dict(payload or {}))
    if actions is None:
        raise RuntimeError(f"模块尚未实现交互入口：{plugin_key}.{entry_key}")
    if not isinstance(actions, list) or not all(isinstance(item, dict) for item in actions):
        raise TypeError("交互入口必须返回 list[dict] 标准动作")
    return actions


__all__ = [
    "RECENT_PEERS_LIMIT",
    "discover_plugins",
    "get_recent_peers",
    "invoke_interaction_entry",
    "load_plugins_for_account",
    "registered_plugins",
    "reload_account_config",
    "reload_ignored_peers",
    "reload_plugin",
]
