"""每账号 worker 子进程主入口。

设计要点：
- 子进程 entrypoint 是 ``worker_main(account_id)``；主进程 supervisor 用
  ``multiprocessing.Process(target=worker_main, args=(aid,))`` 拉起。
- worker 负责连 TG / 注册事件 / 监听 IPC / 把日志和限速事件写回 Redis stream。
- 所有 DB 写操作由主进程统一处理（消费 Redis stream）；worker 只读 DB（启动时拉一次配置）。
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select
from telethon.errors import (
    AuthKeyUnregisteredError,
    SessionRevokedError,
    UserDeactivatedError,
)

from ..crypto import decrypt_str
from ..db.base import AsyncSessionLocal
from ..db.models.account import Account, Proxy
from ..db.models.command import AccountCommandLink, CommandTemplate, LLMProvider
from ..db.models.system import SystemSetting
from ..redis_client import get_redis
from ..settings import settings as app_settings
from .command import CommandContext, make_command_handler, set_command_context
from .ipc import (
    CMD_FETCH_AVATAR,
    CMD_GET_RECENT_PEERS,
    CMD_PAUSE,
    CMD_PING,
    CMD_RELOAD_COMMANDS,
    CMD_RELOAD_CONFIG,
    CMD_RELOAD_IGNORED,
    CMD_RELOAD_PLUGIN,
    CMD_RESUME,
    CMD_STOP,
    EVT_LOGIN_REQUIRED,
    EVT_PONG,
    EVT_STATUS,
    GCMD_KILL_SWITCH,
    GCMD_RELOAD_GLOBAL,
    GLOBAL_CHANNEL,
    RUNTIME_LOG_STREAM,
    IPCMessage,
    RuntimeLogPayload,
    cmd_channel,
    event_channel,
    make_cmd,
    make_event,
)
from .tg_client import build_client

log = logging.getLogger(__name__)


async def run_worker(account_id: int) -> None:
    """worker 主协程；返回即代表退出（supervisor 决定是否重启）。"""
    redis = get_redis()

    # 启动时一次性读取账号 + 代理 + 设备伪装 profile
    async with AsyncSessionLocal() as db:
        account = (
            await db.execute(select(Account).where(Account.id == account_id))
        ).scalar_one_or_none()
        if not account:
            await _log(redis, account_id, "error", f"账号 {account_id} 不存在")
            return
        proxy = await db.get(Proxy, account.proxy_id) if account.proxy_id else None
        # 解析设备伪装：账号绑定 → 系统默认 → 硬编码兜底
        from ..services.device_profile import resolve_for_account
        device_profile = await resolve_for_account(db, account)

    # paused.is_set() == True  → 正常运行
    # paused.is_set() == False → 主动动作被暂停（被动接收照常）
    paused = asyncio.Event()
    paused.set()

    client = build_client(account, proxy, device_profile)
    make_command_handler(client, account_id)

    # 初始化命令派发上下文（含模板 + LLM provider 字典；由 IPC reload_commands 热更新）
    await _refresh_command_context(account_id)

    # ⚠ 顺序：必须先 connect，再加载插件。
    #
    # 插件的 on_startup 钩子可能要直接访问 TG（注册 event handler 之外，
    # 比如查 dialogs / 启动定时任务用的 self_id）；如果在 connect 之前调用，
    # 这些 API 会因 "not connected" 报错。把 connect 放最前面，并在 connect
    # 失败时直接返回，避免给插件留半连接的 client。
    try:
        await client.connect()
        if not await client.is_user_authorized():
            await _publish(
                redis, account_id, EVT_LOGIN_REQUIRED, message="session 失效，请重新登录"
            )
            return

        # connect 成功后再加载插件
        # D Agent 的 plugin loader 会通过 hook 接到 client 上；
        # 这里 try-import：D 没写完时不影响 worker 拉起。
        try:
            from .plugins.loader import load_plugins_for_account  # type: ignore

            await load_plugins_for_account(client, account_id, paused, redis)
        except ImportError:
            await _log(redis, account_id, "warn", "插件系统尚未就绪（D Agent 待完成）")
        except Exception as e:
            await _log(redis, account_id, "error", f"加载插件失败: {e}")

        me = await client.get_me()
        # 顺便回填 tg_user_id / tg_username（旧账号迁移 + 用户在 TG 改用户名时同步）
        try:
            new_tg_user_id = getattr(me, "id", None)
            new_tg_username = getattr(me, "username", None) or None
            async with AsyncSessionLocal() as db:
                acc = await db.get(Account, account_id)
                if acc is not None:
                    changed = False
                    if new_tg_user_id is not None and acc.tg_user_id != new_tg_user_id:
                        acc.tg_user_id = new_tg_user_id
                        changed = True
                    if acc.tg_username != new_tg_username:
                        acc.tg_username = new_tg_username
                        changed = True
                    if changed:
                        await db.commit()
        except Exception as e:  # noqa: BLE001
            # 回填失败不影响 worker 继续运行
            await _log(redis, account_id, "warn", f"同步 TG 身份失败: {type(e).__name__}: {e}")
        await _log(
            redis,
            account_id,
            "info",
            f"已上线: {me.first_name or me.username or me.id}",
        )
        await _publish(redis, account_id, EVT_STATUS, status="active")

        # 后台协程：监听 IPC 指令通道与全局通道
        ipc_task = asyncio.create_task(_listen_cmd(redis, client, account_id, paused))
        global_task = asyncio.create_task(_listen_global(redis, account_id, paused))

        try:
            # 阻塞直到 client.disconnect() 被调用
            await client.run_until_disconnected()
        finally:
            for t in (ipc_task, global_task):
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
    except (AuthKeyUnregisteredError, SessionRevokedError, UserDeactivatedError) as e:
        # session 失效类异常：通知主进程置 status=login_required
        await _publish(redis, account_id, EVT_LOGIN_REQUIRED, reason=type(e).__name__)
        await _log(redis, account_id, "error", f"session 失效: {type(e).__name__}")
    except Exception as e:
        await _log(
            redis, account_id, "error", f"worker 异常退出: {type(e).__name__}: {e}"
        )
    finally:
        try:
            if client.is_connected():
                await client.disconnect()
        except Exception:
            pass
        await _publish(redis, account_id, EVT_STATUS, status="stopped")


async def _listen_cmd(redis, client, account_id: int, paused: asyncio.Event) -> None:
    """监听 ``worker_cmd:{aid}`` 频道，处理 pause/resume/stop/ping/reload_*。"""
    pubsub = redis.pubsub()
    await pubsub.subscribe(cmd_channel(account_id))
    try:
        async for msg in pubsub.listen():
            if msg.get("type") != "message":
                continue
            try:
                cmd = IPCMessage.decode(msg["data"])
            except Exception:
                continue
            if cmd.type == CMD_PAUSE:
                paused.clear()
                await _publish(redis, account_id, EVT_STATUS, status="paused")
                await _log(redis, account_id, "info", "已暂停")
            elif cmd.type == CMD_RESUME:
                paused.set()
                await _publish(redis, account_id, EVT_STATUS, status="active")
                await _log(redis, account_id, "info", "已恢复")
            elif cmd.type == CMD_STOP:
                await _log(redis, account_id, "info", "收到 stop 指令")
                await client.disconnect()
                return
            elif cmd.type == CMD_PING:
                await _publish(redis, account_id, EVT_PONG)
            elif cmd.type == CMD_RELOAD_CONFIG:
                # 让 plugin loader 自己处理（如果存在）
                try:
                    from .plugins.loader import reload_account_config  # type: ignore

                    await reload_account_config(account_id, cmd.payload)
                except Exception:
                    pass
                await _log(redis, account_id, "info", "reload_config 完成")
            elif cmd.type == CMD_RELOAD_PLUGIN:
                try:
                    from .plugins.loader import reload_plugin  # type: ignore

                    await reload_plugin(account_id, cmd.payload.get("plugin_key"))
                except Exception as e:
                    await _log(redis, account_id, "error", f"reload_plugin 失败: {e}")
            elif cmd.type == CMD_FETCH_AVATAR:
                # 主进程懒加载头像：worker 端调用 download_profile_photo 写盘
                # path 由主进程指定（绝对路径）；失败静默，前端会走首字母 fallback
                target_path = cmd.payload.get("path")
                if not target_path:
                    continue
                try:
                    import os
                    from pathlib import Path

                    out = Path(str(target_path))
                    out.parent.mkdir(parents=True, exist_ok=True)
                    # download_profile_photo 默认拉大图；账号没头像时返回 None
                    result = await client.download_profile_photo("me", file=str(out))
                    if result is None and out.exists():
                        # Telethon 在没头像时不会写文件，但保险起见若空文件则删
                        try:
                            if os.path.getsize(str(out)) == 0:
                                out.unlink()
                        except Exception:  # noqa: BLE001
                            pass
                except Exception as e:  # noqa: BLE001
                    await _log(redis, account_id, "warn", f"fetch_avatar 失败: {type(e).__name__}: {e}")
            elif cmd.type == CMD_RELOAD_COMMANDS:
                # Sprint2 #2：账号启用/禁用模板、LLM provider 增删后通知 worker 热加载
                try:
                    await _refresh_command_context(account_id)
                except Exception as e:  # noqa: BLE001
                    await _log(
                        redis, account_id, "warn",
                        f"reload_commands 失败: {type(e).__name__}: {e}",
                    )
                else:
                    await _log(redis, account_id, "info", "reload_commands 完成")
            elif cmd.type == CMD_RELOAD_IGNORED:
                # Sprint2 #3：忽略名单变更后，让 plugin loader 从 DB 重拉 set
                try:
                    from .plugins.loader import reload_ignored_peers  # type: ignore

                    await reload_ignored_peers(account_id)
                except Exception as e:  # noqa: BLE001
                    await _log(
                        redis, account_id, "warn", f"reload_ignored 失败: {type(e).__name__}: {e}"
                    )
            elif cmd.type == CMD_GET_RECENT_PEERS:
                # Sprint2 #3 RPC：把内存里的最近活跃 peer 列表回发到 reply_to 频道
                reply_to = cmd.payload.get("reply_to")
                if not isinstance(reply_to, str) or not reply_to:
                    continue
                items: list[dict] = []
                try:
                    from .plugins.loader import get_recent_peers  # type: ignore

                    items = get_recent_peers(account_id)
                except Exception as e:  # noqa: BLE001
                    await _log(
                        redis, account_id, "warn",
                        f"get_recent_peers 失败: {type(e).__name__}: {e}",
                    )
                try:
                    await redis.publish(reply_to, make_cmd(CMD_GET_RECENT_PEERS, items=items))
                except Exception:  # noqa: BLE001
                    # 主进程超时后会自己关订阅；这里 publish 失败无所谓
                    pass
    finally:
        await pubsub.unsubscribe(cmd_channel(account_id))
        await pubsub.close()


async def _listen_global(redis, account_id: int, paused: asyncio.Event) -> None:
    """监听全局广播通道（kill switch / 全局配置 reload）。"""
    pubsub = redis.pubsub()
    await pubsub.subscribe(GLOBAL_CHANNEL)
    try:
        async for msg in pubsub.listen():
            if msg.get("type") != "message":
                continue
            try:
                cmd = IPCMessage.decode(msg["data"])
            except Exception:
                continue
            if cmd.type == GCMD_KILL_SWITCH:
                if cmd.payload.get("enabled"):
                    paused.clear()
                    await _log(redis, account_id, "warn", "全局 kill switch 已启动")
                else:
                    paused.set()
                    await _log(redis, account_id, "info", "全局 kill switch 已解除")
            elif cmd.type == GCMD_RELOAD_GLOBAL:
                # 命令前缀 / 风控模板等全局设置变更后，主进程广播这条让所有 worker 重拉
                # 当前主要刷的是 system_setting.command_prefix（写到 ctx.command_prefix）
                # 风控相关 reload 由 ratelimit 模块自己监听，不在这里处理
                try:
                    await _refresh_command_context(account_id)
                except Exception as e:  # noqa: BLE001
                    await _log(
                        redis, account_id, "warn",
                        f"reload_global 失败: {type(e).__name__}: {e}",
                    )
                else:
                    await _log(redis, account_id, "info", "reload_global 完成（命令前缀等）")
    finally:
        await pubsub.unsubscribe(GLOBAL_CHANNEL)
        await pubsub.close()


async def _publish(redis, account_id: int, type_: str, **payload):
    """向 worker_event:{aid} 发一条事件。"""
    await redis.publish(event_channel(account_id), make_event(type_, **payload))


def _build_proxy_url(
    ptype: str, host: str, port: int, username: str | None, password: str
) -> str | None:
    """把 Proxy ORM 字段拼成 httpx 接受的 URL。

    支持的类型映射（与 ``app.util.proxy._VALID_TYPES`` 对齐 + httpx 实际支持）：
    - ``socks5``        →  ``socks5://``    需 socksio（``httpx[socks]``）
    - ``http`` / ``https``  →  ``http://``  HTTP CONNECT 代理
    - ``mtproxy`` / 其它   →  None          httpx 不支持，调用方应已经过滤

    用户名密码用 ``urllib.parse.quote`` 转义；空字符串视为不设。
    """
    from urllib.parse import quote

    t = (ptype or "").lower()
    if t == "socks5":
        scheme = "socks5"
    elif t in ("http", "https"):
        scheme = "http"
    else:
        # mtproxy / unknown → 不能给 httpx 用
        return None

    auth = ""
    if username:
        auth = quote(username, safe="")
        if password:
            auth = f"{auth}:{quote(password, safe='')}"
        auth = f"{auth}@"
    return f"{scheme}://{auth}{host}:{int(port)}"


async def _refresh_command_context(account_id: int) -> None:
    """从 DB 拉本账号已启用的命令模板 + 全部 LLM provider，写入 worker-local ctx。

    用作两个时机：
    - worker 启动时一次（确保新连上 TG 就能响应 ``,模板名``）
    - 收到 IPC ``CMD_RELOAD_COMMANDS`` 时热更新

    实现细节：
    - 避免拿原 ORM 实例（脱离 session 后属性访问会报 DetachedInstanceError），转 dict
    - LLM provider 仍持有 ``api_key_enc``（Fernet token）；解密在调用前的 ``build_client`` 里做
    """
    templates: dict[str, dict] = {}
    providers: dict[int, dict] = {}
    # 命令前缀：DB 里 system_setting.command_prefix 优先，没有则用 .env 默认
    prefix: str = app_settings.command_prefix or ","
    async with AsyncSessionLocal() as db:
        # 0) 命令前缀（系统设置）
        try:
            row0 = await db.get(SystemSetting, "command_prefix")
            if row0 is not None and isinstance(row0.value, dict):
                v = str(row0.value.get("value", "") or "").strip()
                if v:
                    prefix = v
            elif row0 is not None and isinstance(row0.value, str):
                v = row0.value.strip()
                if v:
                    prefix = v
        except Exception:  # noqa: BLE001
            # DB 读不到（如迁移没跑）就退回 .env 默认；不影响其它字段加载
            pass

        # 1) 该账号启用中的命令模板
        rows = (
            await db.execute(
                select(CommandTemplate)
                .join(
                    AccountCommandLink,
                    AccountCommandLink.template_id == CommandTemplate.id,
                )
                .where(
                    AccountCommandLink.account_id == account_id,
                    AccountCommandLink.enabled.is_(True),
                )
                .order_by(CommandTemplate.id.asc())
            )
        ).scalars().all()
        for r in rows:
            payload = {
                "id": r.id,
                "name": r.name,
                "aliases": list(r.aliases or []),
                "type": r.type,
                "config": dict(r.config or {}),
                "description": r.description,
            }
            templates[r.name] = payload
            for alias in (r.aliases or []):
                templates[alias] = payload

        # 2) 全部 LLM provider（AI 命令在调用时按 provider_id 索引；不预解密 key）
        #    顺带把 proxy 信息一起拉出来，让 worker 端调 LLM 时也能走代理
        prov_rows = (
            await db.execute(select(LLMProvider))
        ).scalars().all()

        # 收集所有用到的 proxy_id 一次性查出
        proxy_ids = {p.proxy_id for p in prov_rows if p.proxy_id is not None}
        proxy_rows: dict[int, Proxy] = {}
        if proxy_ids:
            rows2 = (
                await db.execute(select(Proxy).where(Proxy.id.in_(proxy_ids)))
            ).scalars().all()
            proxy_rows = {r.id: r for r in rows2}

        for p in prov_rows:
            proxy_url: str | None = None
            if p.proxy_id is not None:
                pr = proxy_rows.get(p.proxy_id)
                if pr is not None and (pr.type or "").lower() != "mtproxy":
                    # 主进程在这里就把 password 解密 + 拼成 httpx 接受的 URL；
                    # 比把 password_enc 下发到 worker 让它再解密少一次往返，明文也只在
                    # ctx 内存里活到 LLM 调用结束（worker 进程私有，不进 Redis / 日志）
                    pwd = ""
                    if pr.password_enc:
                        try:
                            pwd = decrypt_str(pr.password_enc)
                        except Exception:  # noqa: BLE001
                            # 密码解密失败时退化为无认证连接，避免一条坏 proxy 把所有 ai 命令打死
                            pwd = ""
                    proxy_url = _build_proxy_url(
                        pr.type, pr.host, pr.port, pr.username, pwd
                    )
            providers[p.id] = {
                "id": p.id,
                "name": p.name,
                "provider": p.provider,
                "api_key_enc": p.api_key_enc,
                "base_url": p.base_url,
                "default_model": p.default_model,
                # API 协议格式：build_client 据此决定走哪条 client 实现
                "api_format": getattr(p, "api_format", None),
                # 路由元数据：worker 选 provider 时要看
                "modality": getattr(p, "modality", None) or "text",
                "tags": list(getattr(p, "tags", None) or []),
                "cost_tier": int(getattr(p, "cost_tier", None) or 2),
                "notes": getattr(p, "notes", None),
                # 出口代理 URL；None = 直连（DIRECT）
                "proxy_url": proxy_url,
                # 候选模型清单（worker 通常不直接读，但保持一致）
                "models": list(getattr(p, "models", None) or []),
            }

    set_command_context(
        CommandContext(
            account_id=account_id,
            templates=templates,
            providers=providers,
            command_prefix=prefix,
        )
    )


async def _log(
    redis, account_id: int | None, level: str, message: str, *, source: str = "system", **detail
):
    """写运行日志到 Redis stream，主进程批量消费落库。

    source 语义（前端 Logs 页 tab 区分）：
    - ``"system"``（默认） — worker 启停 / 错误 / IPC / 风控状态变化（runtime.py 几乎全是这种）
    - ``"event"``          — incoming 消息事件、plugin 命中、命令派发（业务/监控向）

    历史数据里也会出现 ``"worker"`` / ``"plugin"`` 两个旧值，API 层做了别名映射，
    前端不必关心。
    """
    payload = RuntimeLogPayload(
        account_id=account_id,
        level=level,
        source=source,
        message=message,
        detail=detail or None,
    )
    await redis.rpush(RUNTIME_LOG_STREAM, payload.encode())


def worker_main(account_id: int) -> None:
    """子进程 entrypoint。

    注意：multiprocessing 在 macOS 默认是 spawn，子进程不继承父进程的 logging handler，
    所以这里要重新初始化 logging 配置。
    """
    logging.basicConfig(
        level=logging.INFO,
        format=f"%(asctime)s [worker:{account_id}] %(levelname)s %(message)s",
    )
    asyncio.run(run_worker(account_id))
