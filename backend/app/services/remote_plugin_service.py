"""远程 Git 仓库插件管理服务（阶段 D：tpm-style 远程插件）。

职责：
- ``install``：``git clone`` 到 ``plugins/installed/<name>/`` → 解析 ``plugin.json`` 或
  ``manifest.py`` → 写 ``remote_plugin`` 表 → 触发 worker 热加载（``reload_plugin``）
- ``uninstall``：删 DB 行 + 删插件目录
- ``enable`` / ``disable``：翻转 ``enabled`` 标志，并向 worker 广播热加载
- ``update``：``git pull`` → 重读 manifest → 写新版本号 → 触发热加载

设计要点：
- 与现有 ``loader.py`` 集成靠两条路：
  1. 直接 ``import`` 现有 ``reload_plugin`` 函数（同进程调用）
  2. 通过 Redis IPC ``CMD_RELOAD_PLUGIN`` 广播到所有 worker 进程
- 不修改现有 ``loader.py`` / ``base.py`` / 内置插件文件
- 不依赖 GitPython，统一走 ``asyncio`` 子进程跑 ``git``，只需要环境里有 ``git`` 即可
- 名字（``name``）三重身份：DB 唯一键 / 文件目录名 / loader 注册的 plugin key
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.account import Account
from ..db.models.remote_plugin import RemotePlugin
from ..settings import settings
from ..worker.ipc import CMD_RELOAD_PLUGIN, cmd_channel, make_cmd

# 直接复用现有 loader 的 reload_plugin —— 不修改 loader.py，仅 import 调用
from ..worker.plugins.loader import reload_plugin

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────
# 错误类型
# ─────────────────────────────────────────────────────
class RemotePluginError(Exception):
    """远程插件操作的基类异常。``code`` 用于 API 层映射 HTTP 状态。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class RemotePluginNotFound(RemotePluginError):
    """根据 name 查不到 ``remote_plugin`` 行。"""


class DuplicatePluginName(RemotePluginError):
    """安装时同名插件已存在。"""


class GitOperationFailed(RemotePluginError):
    """``git clone`` / ``git pull`` 等子命令非 0 退出。"""


class InvalidPluginMetadata(RemotePluginError):
    """``plugin.json`` / ``manifest.py`` 缺失或解析失败。"""


# ─────────────────────────────────────────────────────
# 元数据容器
# ─────────────────────────────────────────────────────
@dataclass
class PluginMetadata:
    """从 ``plugin.json`` 或 ``manifest.py`` 解析出来的统一形态。"""

    name: str
    display_name: str = ""
    description: str = ""
    author: str = ""
    version: str = "0.0.0"


# ─────────────────────────────────────────────────────
# 内部工具：路径与 Git
# ─────────────────────────────────────────────────────
_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")


def _installed_root() -> Path:
    """安装根目录（与 worker loader 的 ``_installed_dir`` 同源）。"""
    return Path(settings.plugins_installed_dir).resolve()


def _plugin_dir(name: str) -> Path:
    """计算单个插件的安装目录，含路径穿越防御。"""
    root = _installed_root()
    target = (root / name).resolve()
    # 名字必须能作合法目录名，且最终路径必须落在 installed_root 之下
    if not _NAME_RE.match(name):
        raise RemotePluginError(
            "BAD_PLUGIN_NAME",
            f"插件名仅允许字母/数字/._- 字符，得到 {name!r}",
        )
    if root != target and root not in target.parents:
        raise RemotePluginError(
            "BAD_PLUGIN_NAME",
            f"插件名派生路径越界: {name!r}",
        )
    return target


def _derive_name_from_url(url: str) -> str:
    """从 ``source_url`` 的最后一段推导插件名：
    - ``https://github.com/foo/bar.git`` → ``bar``
    - ``git@github.com:foo/bar`` → ``bar``
    - ``./local/path`` → ``path``
    """
    cleaned = url.rstrip("/").strip()
    if cleaned.endswith(".git"):
        cleaned = cleaned[:-4]
    # 同时支持 ``/`` 与 ``:`` 作分隔（scp-like git URL）
    last = re.split(r"[/:]", cleaned)[-1] if cleaned else ""
    if not last:
        raise RemotePluginError(
            "BAD_SOURCE_URL", f"无法从 source_url 推导插件名: {url!r}"
        )
    return last


async def _run_git(*args: str, cwd: str | Path | None = None) -> str:
    """以子进程跑 ``git <args>``；失败抛 ``GitOperationFailed``。返回 stdout（已解码）。"""
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        msg = (stderr or b"").decode("utf-8", errors="replace").strip()
        raise GitOperationFailed(
            "GIT_FAILED",
            f"git {' '.join(args)} 失败 (rc={proc.returncode}): {msg}",
        )
    return (stdout or b"").decode("utf-8", errors="replace")


# ─────────────────────────────────────────────────────
# 元数据读取：plugin.json 优先，回退 manifest.py
# ─────────────────────────────────────────────────────
def _read_plugin_metadata(plugin_dir: Path, *, fallback_name: str) -> PluginMetadata:
    """从插件目录读元数据。先尝试 ``plugin.json``，再回退 ``manifest.py``。

    ``plugin.json`` 字段约定（与 manifest.Manifest 字段名保持一致）：
        {
          "name" / "key": str,
          "display_name": str,
          "description": str,
          "author": str,
          "version": str
        }

    ``manifest.py`` 必须导出顶层 ``MANIFEST: Manifest``（与 zip 安装路径一致），
    本函数只读取，不会 import 进 ``app.*`` 命名空间。
    """
    # ── 1) plugin.json ──
    pj = plugin_dir / "plugin.json"
    if pj.is_file():
        try:
            data = json.loads(pj.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise InvalidPluginMetadata(
                "BAD_PLUGIN_JSON", f"plugin.json 解析失败: {exc}"
            ) from exc
        return PluginMetadata(
            name=str(data.get("name") or data.get("key") or fallback_name),
            display_name=str(data.get("display_name") or ""),
            description=str(data.get("description") or ""),
            author=str(data.get("author") or ""),
            version=str(data.get("version") or "0.0.0"),
        )

    # ── 2) manifest.py ──
    mp = plugin_dir / "manifest.py"
    if mp.is_file():
        spec_name = f"_telebot_remote_manifest_{plugin_dir.name}_{id(mp)}"
        spec = importlib.util.spec_from_file_location(spec_name, mp)
        if spec is None or spec.loader is None:
            raise InvalidPluginMetadata(
                "MANIFEST_LOAD_FAIL", f"无法构造 manifest.py 的 spec: {mp}"
            )
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec_name] = mod
        try:
            try:
                spec.loader.exec_module(mod)
            except Exception as exc:  # noqa: BLE001
                raise InvalidPluginMetadata(
                    "MANIFEST_EXEC_FAIL", f"manifest.py 执行失败: {exc}"
                ) from exc
        finally:
            sys.modules.pop(spec_name, None)
        manifest = getattr(mod, "MANIFEST", None)
        if manifest is None:
            raise InvalidPluginMetadata(
                "MANIFEST_MISSING_CONST",
                "manifest.py 必须导出顶层常量 MANIFEST",
            )
        # 兼容 Manifest dataclass / 普通 object：通过 getattr 取字段
        return PluginMetadata(
            name=str(getattr(manifest, "key", fallback_name) or fallback_name),
            display_name=str(getattr(manifest, "display_name", "") or ""),
            description=str(getattr(manifest, "description", "") or ""),
            author=str(getattr(manifest, "author", "") or ""),
            version=str(getattr(manifest, "version", "0.0.0") or "0.0.0"),
        )

    raise InvalidPluginMetadata(
        "MANIFEST_NOT_FOUND",
        f"插件目录 {plugin_dir} 缺少 plugin.json 或 manifest.py",
    )


# ─────────────────────────────────────────────────────
# 触发 worker 热加载
# ─────────────────────────────────────────────────────
async def _trigger_reload(db: AsyncSession, name: str) -> None:
    """通知 worker 重新加载该插件。

    两条路径并行：
    - 通过 Redis IPC ``CMD_RELOAD_PLUGIN`` 广播到所有账号 worker
    - 直接调本进程的 ``reload_plugin``（在 worker 进程内才会有效，主进程为 no-op）

    任何环节失败都吞掉——热加载失败不应阻塞 install/update 这条主流程；
    DB 已经写好，下次 worker 启动时也会自动扫描到新插件。
    """
    aids: list[int] = []
    try:
        rows = (await db.execute(select(Account.id))).scalars().all()
        aids = [int(a) for a in rows]
    except Exception:  # noqa: BLE001
        log.exception("拉取 account 列表失败，跳过 reload 广播")

    # 1) Redis IPC 广播
    try:
        from ..redis_client import get_redis  # 延迟 import 防循环

        redis = get_redis()
        for aid in aids:
            try:
                await redis.publish(
                    cmd_channel(aid),
                    make_cmd(CMD_RELOAD_PLUGIN, plugin_key=name),
                )
            except Exception:  # noqa: BLE001
                log.debug("redis 广播 reload_plugin 失败 aid=%s", aid, exc_info=True)
    except Exception:  # noqa: BLE001
        log.debug("redis 不可用，跳过 IPC 广播", exc_info=True)

    # 2) 进程内直接调用（worker 进程才有效；主进程内 _STATES 为空，函数会早返回）
    for aid in aids:
        try:
            await reload_plugin(aid, name)
        except Exception:  # noqa: BLE001
            log.debug("inproc reload_plugin 失败 aid=%s name=%s", aid, name, exc_info=True)


# ─────────────────────────────────────────────────────
# 核心动作
# ─────────────────────────────────────────────────────
async def install(
    db: AsyncSession,
    source_url: str,
    *,
    name: str | None = None,
    enable: bool = False,
) -> RemotePlugin:
    """从 Git 仓库克隆并安装一个远程插件。

    步骤：
      1. 推导 / 校验 ``name``
      2. 拒绝重名：DB 已有同名行或目录已存在 → ``DuplicatePluginName``
      3. ``git clone <source_url> plugins/installed/<name>``
      4. 读 ``plugin.json`` / ``manifest.py``
      5. 写 ``remote_plugin`` 行
      6. 触发 ``reload_plugin`` 广播

    任何中间步骤失败：已克隆的目录会被清理；DB 不会留下脏行（由调用方
    在事务里 commit / rollback 即可，本函数只 ``flush``）。
    """
    if not source_url or not source_url.strip():
        raise RemotePluginError("BAD_SOURCE_URL", "source_url 不能为空")
    final_name = name or _derive_name_from_url(source_url)
    target = _plugin_dir(final_name)

    # 重名拦截：先查 DB，再查目录
    existing = (
        await db.execute(select(RemotePlugin).where(RemotePlugin.name == final_name))
    ).scalar_one_or_none()
    if existing is not None:
        raise DuplicatePluginName(
            "PLUGIN_EXISTS", f"插件 {final_name!r} 已安装"
        )
    if target.exists():
        raise DuplicatePluginName(
            "DIR_EXISTS", f"目录已存在但 DB 无记录: {target}（请先手动清理）"
        )

    # 确保父目录存在
    target.parent.mkdir(parents=True, exist_ok=True)

    # git clone
    try:
        await _run_git("clone", "--depth", "1", source_url, str(target))
    except GitOperationFailed:
        # 失败时清理可能产生的部分目录
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        raise

    try:
        meta = _read_plugin_metadata(target, fallback_name=final_name)
        row = RemotePlugin(
            name=final_name,
            display_name=meta.display_name or final_name,
            description=meta.description,
            author=meta.author,
            source_url=source_url,
            version=meta.version,
            enabled=bool(enable),
        )
        db.add(row)
        await db.flush()
    except Exception:
        # 元数据/写库失败 → 回滚文件系统的 clone
        shutil.rmtree(target, ignore_errors=True)
        raise

    # 触发 worker 热加载（失败已在 _trigger_reload 内吞掉）
    await _trigger_reload(db, final_name)
    return row


async def uninstall(db: AsyncSession, name: str) -> bool:
    """卸载远程插件：删 DB 行 + 删插件目录。

    返回 ``True`` 表示真删了一行。``name`` 不存在时返回 ``False``，不抛异常。
    """
    row = (
        await db.execute(select(RemotePlugin).where(RemotePlugin.name == name))
    ).scalar_one_or_none()
    if row is None:
        return False

    await db.delete(row)
    await db.flush()

    # 文件系统清理：失败仅记日志，不阻塞 DB
    try:
        target = _plugin_dir(name)
        if target.exists():
            shutil.rmtree(target)
    except Exception:  # noqa: BLE001
        log.exception("卸载 %s 时删除目录失败", name)

    # 通知 worker 重新加载（让其知晓该插件已被移除）
    try:
        await _trigger_reload(db, name)
    except Exception:  # noqa: BLE001
        log.debug("uninstall 后 reload 广播失败 name=%s", name, exc_info=True)
    return True


async def set_enabled(
    db: AsyncSession, name: str, *, enabled: bool
) -> RemotePlugin:
    """翻转 ``enabled`` 标志。``name`` 不存在抛 ``RemotePluginNotFound``。"""
    row = (
        await db.execute(select(RemotePlugin).where(RemotePlugin.name == name))
    ).scalar_one_or_none()
    if row is None:
        raise RemotePluginNotFound("PLUGIN_NOT_FOUND", f"插件不存在: {name}")
    row.enabled = bool(enabled)
    await db.flush()
    await _trigger_reload(db, name)
    return row


async def enable(db: AsyncSession, name: str) -> RemotePlugin:
    """启用插件 = ``set_enabled(..., enabled=True)``。"""
    return await set_enabled(db, name, enabled=True)


async def disable(db: AsyncSession, name: str) -> RemotePlugin:
    """禁用插件 = ``set_enabled(..., enabled=False)``。"""
    return await set_enabled(db, name, enabled=False)


async def update(db: AsyncSession, name: str) -> RemotePlugin:
    """从远程仓库拉取最新版本（``git pull``）+ 重读 manifest + 写新版本号。"""
    row = (
        await db.execute(select(RemotePlugin).where(RemotePlugin.name == name))
    ).scalar_one_or_none()
    if row is None:
        raise RemotePluginNotFound("PLUGIN_NOT_FOUND", f"插件不存在: {name}")

    target = _plugin_dir(name)
    if not target.exists():
        raise RemotePluginError(
            "DIR_MISSING",
            f"插件目录已丢失: {target}（请先 uninstall 再 install）",
        )

    # git pull —— 保持简单，直接 pull 当前分支；若仓库无 upstream 会抛错
    await _run_git("pull", "--ff-only", cwd=target)

    meta = _read_plugin_metadata(target, fallback_name=name)
    if meta.display_name:
        row.display_name = meta.display_name
    row.description = meta.description
    row.author = meta.author or row.author
    row.version = meta.version or row.version
    await db.flush()

    await _trigger_reload(db, name)
    return row


async def list_installed(db: AsyncSession) -> list[RemotePlugin]:
    """按 name 字典序列出所有远程插件。"""
    rows = (
        await db.execute(select(RemotePlugin).order_by(RemotePlugin.name))
    ).scalars().all()
    return list(rows)


async def get_by_name(db: AsyncSession, name: str) -> RemotePlugin | None:
    """按 name 查单个插件；不存在返回 None（不抛异常，调用方自决）。"""
    return (
        await db.execute(select(RemotePlugin).where(RemotePlugin.name == name))
    ).scalar_one_or_none()


__all__ = [
    "DuplicatePluginName",
    "GitOperationFailed",
    "InvalidPluginMetadata",
    "PluginMetadata",
    "RemotePluginError",
    "RemotePluginNotFound",
    "disable",
    "enable",
    "get_by_name",
    "install",
    "list_installed",
    "set_enabled",
    "uninstall",
    "update",
]
