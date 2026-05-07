"""账号级插件加载器：连接 Telethon、实例化每个启用的 [账号 × feature] 插件，并维护其生命周期。

使用流程：
1. ``run_worker`` 在 ``client.connect()`` 前调 ``load_plugins_for_account``，本模块会：
   - 触发内置插件 import（``@register`` 写入全局注册表）
   - 在 ``client`` 上挂全局 ``NewMessage`` 派发器（incoming + outgoing），按各插件的 ``message_channels`` 声明过滤
   - 实例化该账号当前 ``account_feature.enabled=True`` 的所有插件，并把状态写回为 active
2. 主进程通过 IPC ``CMD_RELOAD_CONFIG`` 触发 ``reload_account_config`` 实现热更新（拉新 rules / config，
   并对新增 / 移除的 feature 做差量加载与卸载）
3. ``CMD_RELOAD_PLUGIN`` 调 ``reload_plugin``：``importlib.reload`` 单个内置插件模块并重新激活

模块化后插件以"目录"形式存在：
- 内置：``backend/app/worker/plugins/builtin/<key>/{__init__.py, manifest.py, plugin.py}``
- 第三方：``data/plugins/installed/<key>/{__init__.py, manifest.py, plugin.py}``（阶段 B 引入）
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
import time
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from telethon import TelegramClient, events

from ...db.base import AsyncSessionLocal
from ...db.models.account import Account, HumanizeConfig
from ...db.models.feature import (
    FEATURE_STATE_ACTIVE,
    FEATURE_STATE_DISABLED,
    FEATURE_STATE_FAILED,
    AccountFeature,
)
from ...db.models.ignored_peer import IgnoredPeer
from ...db.models.rule import Rule
from ...redis_client import get_redis
from ...services.rate_limit_service import get_effective
from ..command import register_plugin_command
from ..ipc import RUNTIME_LOG_STREAM, RuntimeLogPayload
from ..ratelimit.engine import RateLimitEngine
from ..ratelimit.humanize import HumanizeOpts
from .base import Plugin, PluginContext, all_plugins, get_plugin
from .manifest import Manifest

log = logging.getLogger(__name__)


# worker 内存里维护的最近活跃 peer 数量上限（超过则按 LRU 丢弃最旧）
RECENT_PEERS_LIMIT = 50


# 内置插件根目录：``backend/app/worker/plugins/builtin``
_BUILTIN_DIR: Path = Path(__file__).parent / "builtin"


def _installed_dir() -> Path:
    """解析第三方插件安装目录：阶段 B 引入，由 ``settings.plugins_installed_dir`` 配置。

    每次调用都重新解析，便于测试通过 monkeypatch settings 实现隔离；
    生产环境下值是稳定的。
    """
    try:
        from ...settings import settings  # 延迟 import 避免循环

        return Path(settings.plugins_installed_dir).resolve()
    except Exception:  # noqa: BLE001
        # settings 加载失败时退化到默认相对路径
        return Path("./data/plugins/installed").resolve()


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
        # discover_plugins 同时扫描 builtin + installed，并把 _manifest / _source
        # 挂到 plugin 类上；这里只关心其副作用。
        discover_plugins()
    except Exception:  # noqa: BLE001
        log.exception("discover_plugins 失败")


def _load_dir(path: Path, source: str) -> dict[str, type[Plugin]]:
    """从单个插件目录加载 ``PLUGIN_CLASS`` 与 ``MANIFEST``；失败返回 {} 并写日志。

    - ``source="builtin"``：走正常的 ``importlib.import_module`` 路径，包名是
      ``app.worker.plugins.builtin.<key>``，能享受 Python 的 import 缓存。
    - ``source="installed"``：第三方插件解压在 ``data/plugins/installed/<key>/``，
      不属于 ``app.*`` 包；用 ``spec_from_file_location`` + ``submodule_search_locations``
      手工创建模块对象再执行，使其能 ``from .plugin import ...`` 等相对 import。

    无论哪种来源，最终都把 ``Manifest`` 与 ``source`` 写到 plugin 类的 ``_manifest`` /
    ``_source`` 属性上，方便后续运行期、API 层直接读取。
    """
    init_file = path / "__init__.py"
    if not init_file.exists():
        log.warning("插件目录 %s 缺少 __init__.py，跳过", path)
        return {}

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

            mod_name = f"_telebot_installed_plugin_{path.name}"
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
            except Exception:
                _sys.modules.pop(mod_name, None)
                raise
    except Exception:  # noqa: BLE001
        log.exception("加载插件目录 %s 失败", path)
        return {}

    cls = getattr(mod, "PLUGIN_CLASS", None)
    manifest = getattr(mod, "MANIFEST", None)
    if cls is None or manifest is None:
        log.warning("插件 %s 缺少 PLUGIN_CLASS 或 MANIFEST，跳过", path)
        return {}
    if not isinstance(manifest, Manifest):
        log.warning(
            "插件 %s 的 MANIFEST 不是 Manifest 实例 (got %s)，跳过",
            path,
            type(manifest).__name__,
        )
        return {}

    # 把 manifest / source 挂到 plugin 类上，方便 API 层暴露给前端
    cls._manifest = manifest
    cls._source = source

    # 防御性写入注册表：plugin.py 里若有 @register 已经写过；此处再写一次幂等
    # （主要是为了第三方插件——它们的 plugin.py 也应当 @register，但兜底一下）
    from .base import _REGISTRY  # 延迟 import 避免循环

    _REGISTRY[manifest.key] = cls
    return {manifest.key: cls}


def discover_plugins() -> dict[str, type[Plugin]]:
    """按目录扫描 builtin + installed 两个根，返回 ``{key -> Plugin 子类}``。

    - 同名时 ``installed`` 覆盖 ``builtin``（第三方插件可"覆盖升级"内置实现）。
    - 单个插件失败只记日志，不影响其它插件。
    - 不存在 ``data/plugins/installed`` 目录时直接跳过该源。
    """
    out: dict[str, type[Plugin]] = {}
    for sub in _scan_builtin_dirs():
        out.update(_load_dir(sub, source="builtin"))
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
        self.engine: RateLimitEngine | None = None
        self.client: TelegramClient | None = None
        self.redis: Any = None  # redis.asyncio.Redis
        self.contexts: dict[str, PluginContext] = {}  # feature_key -> ctx
        self.instances: dict[str, Plugin] = {}  # feature_key -> Plugin 实例
        # paused 由 runtime 创建并传入；is_set() == True 表示正常运行
        self.paused: asyncio.Event | None = None
        # Sprint2 #3：忽略 peer 名单（int set），从 ignored_peer 表加载，IPC 触发热更
        self.ignored_peers: set[int] = set()
        # Sprint2 #3：最近活跃 peer 的 LRU（peer_id -> {peer_kind, peer_label, ts}）
        # 仅 worker 内存维护；重启后清空。前端不能假设它持久。
        self.recent_peers: collections.OrderedDict[int, dict[str, Any]] = collections.OrderedDict()


# 进程级状态字典（一个 worker 进程通常只服务一个账号；用 dict 是为了灵活）
_STATES: dict[int, _AccountState] = {}


# ─────────────────────────────────────────────────────
# 主入口：load_plugins_for_account
# ─────────────────────────────────────────────────────
async def load_plugins_for_account(
    client: TelegramClient,
    account_id: int,
    paused: asyncio.Event,
    redis: Any,
) -> None:
    """runtime 在 ``client.connect()`` 之前调一次。

    步骤：
      1. import 全部内置插件（首次会触发注册）
      2. 构造 ``RateLimitEngine``（依赖 humanize 配置 + service 层 ``get_effective``）
      3. 在 client 上注册全局 NewMessage 派发，把每条消息按 instances 顺序广播
      4. 加载该账号已启用的 features → ``_activate``
    """
    _import_builtins()

    state = _AccountState(account_id)
    state.client = client
    state.paused = paused
    state.redis = redis
    _STATES[account_id] = state

    # ── 1) 拉取拟人化 + 账号信息构造 engine ──
    async with AsyncSessionLocal() as db:
        acc = await db.get(Account, account_id)
        humanize_row = await db.get(HumanizeConfig, account_id)
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

    # ── 1.5) 拉取忽略 peer 名单 ──
    await _load_ignored_peers(state)

    # ── 2) 全局事件派发 ──
    def _make_dispatcher(direction: str):  # "incoming" or "outgoing"
        """创建消息派发闭包。direction 对应 Plugin.message_channels 的值。"""
        kwargs = {"incoming": True} if direction == "incoming" else {"outgoing": True}

        @client.on(events.NewMessage(**kwargs))
        async def _dispatch(event):  # noqa: ANN001
            if state.paused is not None and not state.paused.is_set():
                return

            # incoming 消息需要 ignored_peer 检查和 LRU 维护
            if direction == "incoming":
                pid = event.chat_id
                if pid is not None:
                    await _record_recent_peer(state, event)
                    if pid in state.ignored_peers:
                        log.debug("[ignored] account=%s chat_id=%s", account_id, pid)
                        return
                # 调试日志：每条 incoming 消息记一行
                try:
                    peer_kind = (
                        "private" if event.is_private
                        else "channel" if event.is_channel
                        else "group" if event.is_group
                        else "?"
                    )
                    text_preview = (event.raw_text or "")[:80]
                    await _log(
                        redis,
                        account_id,
                        "info",
                        f"[event] {peer_kind} chat_id={event.chat_id} | {text_preview!r}",
                    )
                except Exception:  # noqa: BLE001
                    pass

            for fkey, inst in list(state.instances.items()):
                if direction not in inst.message_channels:
                    continue
                ctx = state.contexts.get(fkey)
                if ctx is None:
                    continue
                try:
                    await inst.on_message(ctx, event)
                except Exception as exc:  # noqa: BLE001
                    await _log(
                        redis,
                        account_id,
                        "error",
                        f"插件 {fkey} on_message({direction}) 异常: {type(exc).__name__}: {exc}",
                        source="system",
                    )

        return _dispatch

    _make_dispatcher("incoming")
    _make_dispatcher("outgoing")

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
# 单 feature 激活
# ─────────────────────────────────────────────────────
async def _activate(db, state: _AccountState, af: AccountFeature, redis: Any) -> None:
    """根据 ``account_feature`` 行实例化对应插件，调 ``on_startup``，写状态。"""
    cls = get_plugin(af.feature_key)
    if cls is None:
        await _log(
            redis,
            state.account_id,
            "warn",
            f"feature {af.feature_key} 已启用但未找到插件实现",
        )
        await db.execute(
            update(AccountFeature)
            .where(
                AccountFeature.account_id == state.account_id,
                AccountFeature.feature_key == af.feature_key,
            )
            .values(state=FEATURE_STATE_FAILED, last_error="plugin not found")
        )
        await db.commit()
        return

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

    ctx = PluginContext(
        account_id=state.account_id,
        feature_key=af.feature_key,
        config=dict(af.config or {}),
        rules=list(rules),
        client=plugin_client,
        engine=state.engine,
        redis=state.redis or redis,
        log=_make_logger(redis, state.account_id),
    )

    try:
        await inst.on_startup(ctx)
    except Exception as exc:  # noqa: BLE001
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
    # 优先读实例属性（on_startup 可能动态设置），回退到类属性
    cmds = getattr(inst, "commands", None) or cls.commands or {}
    for cname, fn in cmds.items():
        register_plugin_command(cname, _wrap_cmd(fn, ctx))

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
        await fn(client, event, args, account_id, ctx)

    return w


def _make_logger(redis: Any, account_id: int):
    """构造一个 ctx.log 协程，写到 ``runtime_log_stream``。"""

    async def _writer(level: str, message: str, **detail: Any) -> None:
        await _log(redis, account_id, level, message, **detail)

    return _writer


# ─────────────────────────────────────────────────────
# 配置热更新：reload_account_config
# ─────────────────────────────────────────────────────
async def reload_account_config(account_id: int, payload: dict | None = None) -> None:
    """收到 IPC ``reload_config`` 时调用：

    - **先刷新 BUILTIN_FEATURES**：动态重扫 builtin 目录，让新增插件立即可见
    - **再重新扫描插件目录**：``discover_plugins()`` 把新发现的插件类注册进 ``_REGISTRY``
    - 已实例化的 feature：刷新 ``ctx.config`` / ``ctx.rules``；若该 feature 已被禁用则 shutdown
    - 数据库新增的 enabled feature：调 ``_activate`` 加载

    任何异常都吞掉，热更新失败不应让 worker 崩溃。
    """
    state = _STATES.get(account_id)
    if state is None:
        return
    redis = state.redis or get_redis()

    # 刷新动态发现的 BUILTIN_FEATURES，让新增 builtin 插件目录立即可见
    try:
        from ...db.models.feature import BUILTIN_FEATURES  # noqa: PLC0415
        BUILTIN_FEATURES.refresh()
    except Exception:  # noqa: BLE001
        log.exception("reload_account_config 时刷新 BUILTIN_FEATURES 失败")

    # 阶段 B：先扫描一次目录，把新装的第三方插件注册进来；存量 builtin 走 import 缓存几乎零开销
    try:
        discover_plugins()
    except Exception:  # noqa: BLE001
        log.exception("reload_account_config 时 discover_plugins 失败")

    async with AsyncSessionLocal() as db:
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
            if af is None or not af.enabled:
                ctx = state.contexts.get(fkey)
                if ctx is not None:
                    try:
                        await inst.on_shutdown(ctx)
                    except Exception:  # noqa: BLE001
                        log.exception("on_shutdown 失败 feature=%s", fkey)
                state.instances.pop(fkey, None)
                state.contexts.pop(fkey, None)
                # 同时写状态为 disabled，便于前端展示
                await db.execute(
                    update(AccountFeature)
                    .where(
                        AccountFeature.account_id == account_id,
                        AccountFeature.feature_key == fkey,
                    )
                    .values(state=FEATURE_STATE_DISABLED)
                )
                await db.commit()
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
            ctx.config = dict(af.config or {})
            ctx.rules = list(rules)

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

    await _log(redis, account_id, "info", "插件配置已热更新")


# ─────────────────────────────────────────────────────
# 单插件热重载：reload_plugin
# ─────────────────────────────────────────────────────
async def reload_plugin(account_id: int, plugin_key: str | None) -> None:
    """``importlib.reload`` 一个内置插件模块，并重新激活。

    仅支持内置插件；第三方插件目前不在 MVP 范围。
    """
    if not plugin_key:
        return
    state = _STATES.get(account_id)
    if state is None:
        return
    redis = state.redis or get_redis()

    # 1) 先 shutdown 旧实例
    if plugin_key in state.instances:
        try:
            await state.instances[plugin_key].on_shutdown(state.contexts[plugin_key])
        except Exception:  # noqa: BLE001
            log.exception("on_shutdown 失败 feature=%s", plugin_key)
        state.instances.pop(plugin_key, None)
        state.contexts.pop(plugin_key, None)

    # 2) reload 模块（仅内置）
    if plugin_key not in _BUILTIN_MODULES:
        await _log(redis, account_id, "warn", f"reload_plugin 仅支持内置插件: {plugin_key}")
        return
    try:
        # 模块化后每个 builtin 插件是子包：``manifest.py`` + ``plugin.py`` + ``__init__.py``。
        # 按"manifest → plugin → 子包入口"顺序 reload，确保 @register 重新触发，
        # MANIFEST / PLUGIN_CLASS 取到最新版本。
        for sub in ("manifest", "plugin"):
            try:
                m = importlib.import_module(
                    f".builtin.{plugin_key}.{sub}", package=__package__
                )
                importlib.reload(m)
            except ModuleNotFoundError:
                # 旧式单文件 builtin（理论上重构后已不存在），忽略
                pass
        pkg_mod = importlib.import_module(
            f".builtin.{plugin_key}", package=__package__
        )
        importlib.reload(pkg_mod)
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
# Sprint2 #3：忽略 peer + 最近活跃 peer
# ─────────────────────────────────────────────────────
async def _load_ignored_peers(state: _AccountState) -> None:
    """从 ``ignored_peer`` 表把当前账号的所有 peer_id 装进内存 set。

    任何异常都吞掉——失败时退化为"空名单"，等价于不忽略，业务侧不至于挂。
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
        log.exception("加载忽略名单失败 account=%s", state.account_id)
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
        f"忽略名单已热更新（共 {len(state.ignored_peers)} 个 peer）",
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


__all__ = [
    "RECENT_PEERS_LIMIT",
    "discover_plugins",
    "get_recent_peers",
    "load_plugins_for_account",
    "registered_plugins",
    "reload_account_config",
    "reload_ignored_peers",
    "reload_plugin",
]
