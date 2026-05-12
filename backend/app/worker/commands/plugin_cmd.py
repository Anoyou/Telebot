"""远程插件管理 Bot 命令（,plugin）。

支持子命令：
  ,plugin list                    - 列出已安装远程插件
  ,plugin install <url> [--default]- 从 Git URL 安装（--default 默认为所有账号启用）
  ,plugin remove <name>           - 卸载
  ,plugin enable <name>           - 启用
  ,plugin disable <name>          - 禁用
  ,plugin update <name>           - 从远程 git pull 更新
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

HELP_TEXT = """📦 远程插件管理

,plugin list                 列出已安装远程插件
,plugin install <url>        从 Git 仓库安装插件
  --default                  安装后默认为所有账号启用
,plugin remove <name>        卸载指定插件
,plugin enable <name>        启用指定插件
,plugin disable <name>       禁用指定插件
,plugin update <name>        从远程更新插件（git pull）
"""


async def handle_plugin_cmd(client, event, args: list, account_id: int) -> bool:
    """处理 ,plugin 命令派发。

    Returns True 表示命令已消费，False 表示不处理（供 command.py 跳过）。
    """
    if not args:
        await event.edit(HELP_TEXT)
        return True

    sub = args[0].lower()

    if sub == "list":
        await _subcmd_list(event)
        return True

    if sub == "install":
        if len(args) < 2:
            await event.edit("用法：,plugin install <git-url> [--default]")
            return True
        url = args[1]
        default_enabled = "--default" in args[2:]
        await _subcmd_install(event, url, default_enabled=default_enabled)
        return True

    if sub in ("remove", "uninstall"):
        if len(args) < 2:
            await event.edit(f"用法：,plugin {sub} <name>")
            return True
        name = args[1]
        await _subcmd_remove(event, name)
        return True

    if sub == "enable":
        if len(args) < 2:
            await event.edit("用法：,plugin enable <name>")
            return True
        name = args[1]
        await _subcmd_enable(event, name)
        return True

    if sub == "disable":
        if len(args) < 2:
            await event.edit("用法：,plugin disable <name>")
            return True
        name = args[1]
        await _subcmd_disable(event, name)
        return True

    if sub == "update":
        if len(args) < 2:
            await event.edit("用法：,plugin update <name>")
            return True
        name = args[1]
        await _subcmd_update(event, name)
        return True

    await event.edit(HELP_TEXT)
    return True


# ─────────────────────────────────────────────────────────────────────
# 子命令实现
# ─────────────────────────────────────────────────────────────────────

async def _subcmd_list(event) -> None:
    from ...db.base import AsyncSessionLocal
    from ...services import remote_plugin_service as svc

    async with AsyncSessionLocal() as db:
        rows = await svc.list_installed(db)

    if not rows:
        await event.edit("📦 暂无已安装远程插件")
        return

    lines = ["📦 已安装远程插件："]
    for r in rows:
        status = "✅" if r.enabled else "⏸️"
        lines.append(f"{status} {r.name}  v{r.version}  （{r.author or '未知作者'}）")
        if r.description:
            lines.append(f"   {r.description[:60]}{'…' if len(r.description) > 60 else ''}")
    await event.edit("\n".join(lines))


async def _subcmd_install(event, url: str, *, default_enabled: bool = False) -> None:
    from ...db.base import AsyncSessionLocal
    from ...services import remote_plugin_service as svc
    from ...services.remote_plugin_service import (
        DuplicatePluginName,
        GitOperationFailed,
        InvalidPluginMetadata,
        RemotePluginError,
    )

    await event.edit(f"⏳ 正在安装 {url} …")
    try:
        async with AsyncSessionLocal() as db:
            row = await svc.install(db, url, default_enabled=default_enabled)
            plugin_name = row.name
            version = row.version
            author = row.author
            enabled = row.enabled
            await db.commit()
            await svc.trigger_reload(db, plugin_name)
        status = "已启用" if enabled else "已禁用（需 ,plugin enable 启用）"
        if default_enabled:
            status = "已为所有账号启用"
        await event.edit(
            f"✅ 安装成功\n"
            f"名称: {plugin_name}\n"
            f"版本: {version}\n"
            f"作者: {author or '未知'}\n"
            f"状态: {status}"
        )
    except DuplicatePluginName as e:
        await event.edit(f"❌ 安装失败：插件已存在\n{e.message}")
    except GitOperationFailed as e:
        await event.edit(f"❌ 安装失败：Git 操作出错\n{e.message}")
    except InvalidPluginMetadata as e:
        await event.edit(f"❌ 安装失败：插件元数据无效\n{e.message}")
    except RemotePluginError as e:
        await event.edit(f"❌ 安装失败：{e.message}")
    except Exception as e:  # noqa: BLE001
        log.exception("plugin install 失败 url=%s", url)
        await event.edit(f"❌ 安装失败：{type(e).__name__}: {str(e)[:100]}")


async def _subcmd_remove(event, name: str) -> None:
    from ...db.base import AsyncSessionLocal
    from ...services import remote_plugin_service as svc

    await event.edit(f"⏳ 正在卸载 {name} …")
    try:
        async with AsyncSessionLocal() as db:
            found = await svc.uninstall(db, name)
            await db.commit()
            if found:
                await svc.trigger_reload(db, name)
        if found:
            await event.edit(f"✅ 已卸载：{name}")
        else:
            await event.edit(f"❌ 插件不存在：{name}")
    except Exception as e:  # noqa: BLE001
        log.exception("plugin remove 失败 name=%s", name)
        await event.edit(f"❌ 卸载失败：{type(e).__name__}: {str(e)[:100]}")


async def _subcmd_enable(event, name: str) -> None:
    from ...db.base import AsyncSessionLocal
    from ...services import remote_plugin_service as svc
    from ...services.remote_plugin_service import RemotePluginNotFound

    try:
        async with AsyncSessionLocal() as db:
            row = await svc.enable(db, name, bootstrap_accounts=True)
            plugin_name = row.name
            await db.commit()
            await svc.trigger_reload(db, plugin_name)
        await event.edit(f"✅ 已启用：{plugin_name}")
    except RemotePluginNotFound:
        await event.edit(f"❌ 插件不存在：{name}")
    except Exception as e:  # noqa: BLE001
        log.exception("plugin enable 失败 name=%s", name)
        await event.edit(f"❌ 启用失败：{type(e).__name__}: {str(e)[:100]}")


async def _subcmd_disable(event, name: str) -> None:
    from ...db.base import AsyncSessionLocal
    from ...services import remote_plugin_service as svc
    from ...services.remote_plugin_service import RemotePluginNotFound

    try:
        async with AsyncSessionLocal() as db:
            row = await svc.disable(db, name)
            plugin_name = row.name
            await db.commit()
            await svc.trigger_reload(db, plugin_name)
        await event.edit(f"⏸️ 已禁用：{plugin_name}")
    except RemotePluginNotFound:
        await event.edit(f"❌ 插件不存在：{name}")
    except Exception as e:  # noqa: BLE001
        log.exception("plugin disable 失败 name=%s", name)
        await event.edit(f"❌ 禁用失败：{type(e).__name__}: {str(e)[:100]}")


async def _subcmd_update(event, name: str) -> None:
    from ...db.base import AsyncSessionLocal
    from ...services import remote_plugin_service as svc
    from ...services.remote_plugin_service import (
        GitOperationFailed,
        InvalidPluginMetadata,
        RemotePluginError,
        RemotePluginNotFound,
    )

    await event.edit(f"⏳ 正在更新 {name} …")
    try:
        async with AsyncSessionLocal() as db:
            row = await svc.update(db, name)
            plugin_name = row.name
            version = row.version
            await db.commit()
            await svc.trigger_reload(db, plugin_name)
        await event.edit(f"✅ 已更新：{plugin_name}  →  v{version}")
    except RemotePluginNotFound:
        await event.edit(f"❌ 插件不存在：{name}")
    except GitOperationFailed as e:
        await event.edit(f"❌ 更新失败：Git 操作出错\n{e.message}")
    except InvalidPluginMetadata as e:
        await event.edit(f"❌ 更新失败：插件元数据无效\n{e.message}")
    except RemotePluginError as e:
        await event.edit(f"❌ 更新失败：{e.message}")
    except Exception as e:  # noqa: BLE001
        log.exception("plugin update 失败 name=%s", name)
        await event.edit(f"❌ 更新失败：{type(e).__name__}: {str(e)[:100]}")
