"""远程 Git 仓库插件管理服务（阶段 D：tpm-style 远程插件）。

职责：
- ``install``：``git clone`` 到 ``plugins/installed/<name>/`` → 解析 ``plugin.json`` → 写 ``remote_plugin`` 表 → 触发 worker 热加载（``reload_config``）
- ``uninstall``：删 DB 行 + 删插件目录
- ``enable`` / ``disable``：翻转 ``enabled`` 标志，并向 worker 广播热加载
- ``update``：``git pull`` → 重读 plugin.json → 写新版本号 → 触发热加载

安全设计（阶段 E 修复）：
- 安装阶段**绝对禁止执行任何 Python 代码**（manifest.py 在安装时不被解析/执行）
- 只允许静态解析 ``plugin.json``
- source_url 只允许 https:// 和 git+ssh://
- git clone 强制 timeout

设计要点：
- 与现有 ``loader.py`` 集成靠两条路：
  1. 直接 ``import`` 现有 ``reload_account_config`` 函数（同进程调用）
  2. 通过 Redis IPC ``CMD_RELOAD_CONFIG`` 广播到所有 worker 进程
- 不依赖 GitPython，统一走 ``asyncio`` 子进程跑 ``git``，只需要环境里有 ``git`` 即可
- 名字（``name``）三重身份：DB 唯一键 / 文件目录名 / loader 注册的 plugin key
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.account import Account
from ..db.models.feature import FEATURE_STATE_DISABLED, AccountFeature, Feature
from ..db.models.remote_plugin import RemotePlugin
from ..settings import settings
from ..worker.ipc import CMD_RELOAD_CONFIG, publish_cmd_with_ack

# 直接复用现有 loader 的配置热更新路径；installed 插件在 loader 里按 DB 双开关按需加载
from ..worker.plugins.loader import reload_account_config

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────
# 安全常量：source_url 允许的 scheme
# ─────────────────────────────────────────────────────
_ALLOWED_URL_SCHEMES: frozenset[str] = frozenset({"https", "git+ssh"})


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
    """``plugin.json`` 缺失或解析失败。"""


class InvalidSourceUrl(RemotePluginError):
    """source_url 不符合安全要求。"""


# ─────────────────────────────────────────────────────
# 元数据模型（用于校验 plugin.json）
# ─────────────────────────────────────────────────────
class PluginMetadataSchema(BaseModel):
    """plugin.json 的 Pydantic 校验模型。

    只允许静态解析，不执行任何 Python 代码。
    所有字段在通过校验后才返回 PluginMetadata。
    """

    # 允许 name/key 任一字段，优先用 name
    name: str | None = None
    key: str | None = None

    display_name: str = ""
    description: str = ""
    author: str = ""
    version: str = "0.0.0"
    # entry 是可选的，默认为 plugin.py
    entry: str = "plugin.py"
    # permissions 和 config_schema 是可选扩展字段
    permissions: list[str] = field(default_factory=list)
    config_schema: dict[str, Any] | None = None

    @field_validator("name", "key")
    @classmethod
    def _validate_key(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = str(v).strip()
        if not v:
            return None
        # 路径穿越防御：禁止 . / \ 以及不可见字符
        if not re.match(r"^[A-Za-z0-9_][A-Za-z0-9_-]*$", v):
            raise ValueError(
                f"插件名仅允许字母/数字/_/-，得到 {v!r}"
            )
        return v

    @field_validator("version")
    @classmethod
    def _validate_version(cls, v: str) -> str:
        # 简单校验：必须是类似 x.y.z 的格式
        v = str(v).strip()
        if not re.match(r"^\d+\.\d+\.\d+", v):
            raise ValueError(f"版本号格式不正确: {v!r}")
        return v

    @field_validator("author")
    @classmethod
    def _validate_author(cls, v: str) -> str:
        # 防止注入
        v = str(v).strip()
        if len(v) > 255:
            raise ValueError("author 字段过长（最大 255 字符）")
        return v

    @model_validator(mode="after")
    def _fill_name_from_key(self) -> PluginMetadataSchema:
        if self.name is None and self.key is not None:
            self.name = self.key
        return self


@dataclass
class PluginMetadata:
    """从 ``plugin.json`` 解析出来的统一形态。"""

    name: str
    display_name: str = ""
    description: str = ""
    author: str = ""
    version: str = "0.0.0"
    entry: str = "plugin.py"
    permissions: list[str] = field(default_factory=list)
    config_schema: dict[str, Any] | None = None


def _feature_manifest_from_meta(meta: PluginMetadata) -> dict[str, Any] | None:
    manifest: dict[str, Any] = {}
    if meta.config_schema:
        manifest["config_schema"] = meta.config_schema
    if meta.permissions:
        manifest["permissions"] = list(meta.permissions)
    return manifest or None


# ─────────────────────────────────────────────────────
# 内部工具：路径与 Git
# ─────────────────────────────────────────────────────
_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]*$")  # no dots to prevent .. traversal


def _installed_root() -> Path:
    """安装根目录（与 worker loader 的 ``_installed_dir`` 同源）。"""
    return settings.plugins_installed_path


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


def _legacy_plugin_dir(name: str) -> Path:
    """旧版本在 backend/ 工作目录下运行时可能写到 backend/plugins/installed。"""
    backend_root = Path(__file__).resolve().parents[2]
    return (backend_root / "plugins" / "installed" / name).resolve()


def _existing_plugin_dir(name: str) -> Path:
    """返回当前插件实际目录；兼容已安装到旧 backend/plugins/installed 的插件。"""
    target = _plugin_dir(name)
    if target.exists():
        return target
    legacy = _legacy_plugin_dir(name)
    if legacy.exists():
        return legacy
    return target


def _validate_source_url(url: str) -> None:
    """校验 source_url 只允许 https:// 或 git+ssh://，防止本地文件/恶意 URL 攻击。

    Args:
        url: 待校验的 source_url

    Raises:
        InvalidSourceUrl: scheme 不在白名单中
    """
    if not url or not url.strip():
        raise InvalidSourceUrl("BAD_SOURCE_URL", "source_url 不能为空")

    url = url.strip()

    # 解析 scheme
    if url.startswith("git+ssh://"):
        scheme = "git+ssh"
    elif "://" in url:
        scheme = url.split("://", 1)[0].lower()
    elif ":" in url and "@" in url:
        # scp-like 格式 git@github.com:foo/bar
        scheme = "ssh"
    else:
        raise InvalidSourceUrl(
            "BAD_SOURCE_URL",
            f"source_url 缺少合法 scheme: {url!r}",
        )

    # 特殊处理 scp-like SSH URL
    if scheme == "ssh" and url.startswith("git@"):
        # git@github.com:foo/bar.git 格式是允许的
        allowed_ssh_pattern = re.compile(r"^git@[a-zA-Z0-9.\-]+:[^:]+$")
        if not allowed_ssh_pattern.match(url):
            raise InvalidSourceUrl(
                "BAD_SOURCE_URL",
                f"SSH URL 格式不正确: {url!r}",
            )
        return

    if scheme not in _ALLOWED_URL_SCHEMES:
        raise InvalidSourceUrl(
            "BAD_SOURCE_URL",
            f"source_url 只允许 https:// 或 git+ssh:// scheme，得到 {scheme!r}",
        )


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


async def _run_git(*args: str, cwd: str | Path | None = None, timeout: float = 120.0) -> str:
    """以子进程跑 ``git <args>``；失败抛 ``GitOperationFailed``。返回 stdout（已解码）。

    Args:
        timeout: git 操作超时秒数，默认 120s。clone 超时会导致半目录被清理。
    """
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise GitOperationFailed(
            "GIT_TIMEOUT",
            f"git {' '.join(args)} 超时（{timeout}s）",
        ) from None
    if proc.returncode != 0:
        msg = (stderr or b"").decode("utf-8", errors="replace").strip()
        raise GitOperationFailed(
            "GIT_FAILED",
            f"git {' '.join(args)} 失败 (rc={proc.returncode}): {msg}",
        )
    return (stdout or b"").decode("utf-8", errors="replace")


# ─────────────────────────────────────────────────────
# 元数据读取：只支持 plugin.json，禁止执行 manifest.py
# ─────────────────────────────────────────────────────
def _read_plugin_metadata(plugin_dir: Path, *, fallback_name: str) -> PluginMetadata:
    """从插件目录读元数据。**安全设计：只解析 plugin.json，绝对不执行 manifest.py**。

    plugin.json 字段约定：
        {
          "name" / "key": str (必填其一，name 优先),
          "display_name": str,
          "description": str,
          "author": str,
          "version": str,
          "entry": str (可选，默认 plugin.py),
          "permissions": list[str] (可选),
          "config_schema": dict (可选)
        }

    Args:
        plugin_dir: 插件目录路径
        fallback_name: plugin.json 解析失败时的回退名称

    Returns:
        PluginMetadata 实例

    Raises:
        InvalidPluginMetadata: plugin.json 不存在或解析失败
    """
    # ── 只允许 plugin.json ──
    pj = plugin_dir / "plugin.json"
    if not pj.is_file():
        raise InvalidPluginMetadata(
            "PLUGIN_JSON_NOT_FOUND",
            f"插件目录 {plugin_dir} 必须包含 plugin.json（manifest.py 在安装阶段禁止执行）",
        )

    try:
        raw_data = json.loads(pj.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InvalidPluginMetadata(
            "BAD_PLUGIN_JSON", f"plugin.json 解析失败: {exc}"
        ) from exc

    # Pydantic 校验
    try:
        validated = PluginMetadataSchema(**raw_data)
    except Exception as exc:
        raise InvalidPluginMetadata(
            "BAD_PLUGIN_JSON",
            f"plugin.json 字段校验失败: {exc}",
        ) from exc

    # 提取 name（优先 name，key 作备选）
    name = validated.name or validated.key or fallback_name

    # 再次路径穿越防御（防御 plugin.json 中可能的恶意 name）
    if not _NAME_RE.match(name):
        raise InvalidPluginMetadata(
            "BAD_PLUGIN_NAME",
            f"plugin.json 中的 name/key 非法: {name!r}",
        )

    return PluginMetadata(
        name=name,
        display_name=str(validated.display_name or ""),
        description=str(validated.description or ""),
        author=str(validated.author or ""),
        version=str(validated.version or "0.0.0"),
        entry=str(validated.entry or "plugin.py"),
        permissions=list(validated.permissions or []),
        config_schema=validated.config_schema,
    )


def _validate_runtime_plugin_shape(plugin_dir: Path, meta: PluginMetadata) -> None:
    """校验远程插件运行期结构，避免安装后 worker 找不到实现。

    安装阶段仍不执行 Python，只检查必要文件存在。远程插件必须按新版文档
    提供完整包结构：plugin.json + manifest.py + plugin.py + __init__.py。
    """
    missing: list[str] = []
    for filename in ("manifest.py", "plugin.py", "__init__.py"):
        if not (plugin_dir / filename).is_file():
            missing.append(filename)

    entry = str(meta.entry or "plugin.py")
    if "/" in entry or "\\" in entry or not entry.endswith(".py"):
        raise InvalidPluginMetadata(
            "BAD_PLUGIN_ENTRY",
            f"plugin.json entry 必须是当前插件目录下的 .py 文件，得到 {entry!r}。请按 docs/REMOTE-PLUGIN-GUIDE.md 更新插件结构。",
        )
    if not (plugin_dir / entry).is_file():
        missing.append(entry)

    if missing:
        unique = sorted(set(missing))
        raise InvalidPluginMetadata(
            "PLUGIN_RUNTIME_FILES_MISSING",
            "远程插件缺少运行期文件："
            + ", ".join(unique)
            + "。新版远程插件必须包含 plugin.json、manifest.py、plugin.py、__init__.py；请按 docs/REMOTE-PLUGIN-GUIDE.md 更新插件后再安装。",
        )


# ─────────────────────────────────────────────────────
# 触发 worker 热加载
# ─────────────────────────────────────────────────────
async def _trigger_reload(db: AsyncSession, name: str) -> None:
    """通知 worker 重新加载该插件。

    两条路径并行：
    - 通过 Redis IPC ``CMD_RELOAD_CONFIG`` 广播到所有账号 worker
    - 直接调本进程的 ``reload_account_config``（在 worker 进程内才会有效，主进程为 no-op）

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
                ok = await publish_cmd_with_ack(redis, aid, CMD_RELOAD_CONFIG, plugin_key=name)
                if not ok:
                    log.debug("worker reload_config 未确认 aid=%s plugin=%s，将由周期 reconcile 收敛", aid, name)
            except Exception:  # noqa: BLE001
                log.debug("redis 广播 reload_config 失败 aid=%s", aid, exc_info=True)
    except Exception:  # noqa: BLE001
        log.debug("redis 不可用，跳过 IPC 广播", exc_info=True)

    # 2) 进程内直接调用（worker 进程才有效；主进程内 _STATES 为空，函数会早返回）
    for aid in aids:
        try:
            await reload_account_config(aid, {"plugin_key": name})
        except Exception:  # noqa: BLE001
            log.debug("inproc reload_config 失败 aid=%s name=%s", aid, name, exc_info=True)


async def trigger_reload(db: AsyncSession, name: str) -> None:
    """提交事务后通知 worker 热加载远程插件。"""
    await _trigger_reload(db, name)


async def _enable_for_all_accounts_if_unclaimed(db: AsyncSession, name: str) -> int:
    """首次全局启用远程插件时，为现有账号创建账号级启用行。

    已经存在任何 account_feature 行时保留用户的账号级选择；这避免后续全局开关
    反复开关时，把用户手动关闭的账号重新打开。
    """
    existing = (
        await db.execute(select(AccountFeature).where(AccountFeature.feature_key == name))
    ).scalars().all()
    if existing:
        return 0

    aids = (await db.execute(select(Account.id))).scalars().all()
    for aid in aids:
        db.add(
            AccountFeature(
                account_id=int(aid),
                feature_key=name,
                enabled=True,
                state=FEATURE_STATE_DISABLED,
            )
        )
    await db.flush()
    return len(aids)


# ─────────────────────────────────────────────────────
# 核心动作
# ─────────────────────────────────────────────────────
async def install(
    db: AsyncSession,
    source_url: str,
    *,
    name: str | None = None,
    enable: bool = False,
    default_enabled: bool = False,
) -> RemotePlugin:
    """从 Git 仓库克隆并安装一个远程插件。

    **安全要求**：
    - source_url 必须通过 ``_validate_source_url``（只允许 https:// 或 git+ssh://）
    - 安装阶段绝对不执行任何 Python 代码（只解析 plugin.json）

    步骤：
      1. 推导 / 校验 ``name``
      2. 校验 ``source_url`` scheme
      3. 拒绝重名：DB 已有同名行或目录已存在 → ``DuplicatePluginName``
      4. ``git clone <source_url> plugins/installed/<name>`` (带 timeout)
      5. 读 ``plugin.json``（不执行 manifest.py）
      6. 写 ``remote_plugin`` 行
      7. 注册到 ``feature`` 表（is_builtin=False），使功能矩阵可见
      8. 若 ``default_enabled=True``，为所有已有账号创建 ``AccountFeature`` 行
      9. 触发 ``reload_config`` 广播

    任何中间步骤失败：已克隆的目录会被清理；DB 不会留下脏行（由调用方
    在事务里 commit / rollback 即可，本函数只 ``flush``）。
    """
    # 1. 安全校验 source_url
    _validate_source_url(source_url)

    final_name = name or _derive_name_from_url(source_url)
    target = _plugin_dir(final_name)

    # 2. 重名拦截：先查 DB，再查目录
    existing = (
        await db.execute(select(RemotePlugin).where(RemotePlugin.name == final_name))
    ).scalar_one_or_none()
    if existing is not None:
        raise DuplicatePluginName(
            "PLUGIN_EXISTS", f"插件 {final_name!r} 已安装"
        )
    legacy_target = _legacy_plugin_dir(final_name)
    if target.exists() or legacy_target.exists():
        raise DuplicatePluginName(
            "DIR_EXISTS", f"目录已存在但 DB 无记录: {target}（请先手动清理）"
        )

    # 3. 确保父目录存在
    target.parent.mkdir(parents=True, exist_ok=True)

    # 4. git clone（带 timeout，防止挂起）
    try:
        await _run_git("clone", "--depth", "1", source_url, str(target), timeout=180.0)
    except GitOperationFailed:
        # 失败时清理可能产生的部分目录
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        raise

    try:
        meta = _read_plugin_metadata(target, fallback_name=final_name)
        _validate_runtime_plugin_shape(target, meta)
        row = RemotePlugin(
            name=final_name,
            display_name=meta.display_name or final_name,
            description=meta.description,
            author=meta.author,
            source_url=source_url,
            version=meta.version,
            enabled=bool(enable or default_enabled),
            default_enabled=default_enabled,
        )
        db.add(row)

        # 注册到 feature 表（使功能矩阵可见）
        feat = (
            await db.execute(select(Feature).where(Feature.key == final_name))
        ).scalar_one_or_none()
        if feat is None:
            db.add(Feature(
                key=final_name,
                display_name=meta.display_name or final_name,
                is_builtin=False,
                version=meta.version,
                manifest=_feature_manifest_from_meta(meta),
            ))
        else:
            # 已存在则校正 display_name 和 version
            feat.display_name = meta.display_name or final_name
            feat.version = meta.version
            feat.is_builtin = False
            feat.manifest = _feature_manifest_from_meta(meta)

        await db.flush()

        # 如果 default_enabled=True，为所有已有账号启用
        if default_enabled:
            aids = (await db.execute(select(Account.id))).scalars().all()
            for aid in aids:
                af = (
                    await db.execute(
                        select(AccountFeature).where(
                            AccountFeature.account_id == int(aid),
                            AccountFeature.feature_key == final_name,
                        )
                    )
                ).scalar_one_or_none()
                if af is None:
                    db.add(AccountFeature(
                        account_id=int(aid),
                        feature_key=final_name,
                        enabled=True,
                        state=FEATURE_STATE_DISABLED,
                    ))
            await db.flush()

    except Exception:
        # 元数据/写库失败 → 回滚文件系统的 clone
        shutil.rmtree(target, ignore_errors=True)
        raise

    return row


async def uninstall(db: AsyncSession, name: str) -> bool:
    """卸载远程插件：删 DB 行 + 删插件目录 + 清理 Feature/AccountFeature 行。

    返回 ``True`` 表示真删了一行。``name`` 不存在时返回 ``False``，不抛异常。
    """
    row = (
        await db.execute(select(RemotePlugin).where(RemotePlugin.name == name))
    ).scalar_one_or_none()
    if row is None:
        return False

    # 清理 AccountFeature 行
    afs = (
        await db.execute(
            select(AccountFeature).where(AccountFeature.feature_key == name)
        )
    ).scalars().all()
    for af in afs:
        await db.delete(af)

    # 清理 Feature 行
    feat = (
        await db.execute(select(Feature).where(Feature.key == name))
    ).scalar_one_or_none()
    if feat is not None:
        await db.delete(feat)

    await db.delete(row)
    await db.flush()

    # 文件系统清理：失败仅记日志，不阻塞 DB
    try:
        target = _existing_plugin_dir(name)
        if target.exists():
            shutil.rmtree(target)
    except Exception:  # noqa: BLE001
        log.exception("卸载 %s 时删除目录失败", name)

    return True


async def set_enabled(
    db: AsyncSession, name: str, *, enabled: bool, bootstrap_accounts: bool = False
) -> RemotePlugin:
    """翻转 ``enabled`` 标志。``name`` 不存在抛 ``RemotePluginNotFound``。"""
    row = (
        await db.execute(select(RemotePlugin).where(RemotePlugin.name == name))
    ).scalar_one_or_none()
    if row is None:
        raise RemotePluginNotFound("PLUGIN_NOT_FOUND", f"插件不存在: {name}")
    row.enabled = bool(enabled)
    if row.enabled and bootstrap_accounts:
        await _enable_for_all_accounts_if_unclaimed(db, name)
    await db.flush()
    return row


async def enable(
    db: AsyncSession, name: str, *, bootstrap_accounts: bool = False
) -> RemotePlugin:
    """启用插件 = ``set_enabled(..., enabled=True)``。"""
    return await set_enabled(db, name, enabled=True, bootstrap_accounts=bootstrap_accounts)


async def disable(db: AsyncSession, name: str) -> RemotePlugin:
    """禁用插件 = ``set_enabled(..., enabled=False)``。"""
    return await set_enabled(db, name, enabled=False)


async def update(db: AsyncSession, name: str) -> RemotePlugin:
    """从远程仓库拉取最新版本（``git pull``）+ 重读 plugin.json + 写新版本号。

    注意：manifest.py 不会被执行，只解析 plugin.json。
    """
    row = (
        await db.execute(select(RemotePlugin).where(RemotePlugin.name == name))
    ).scalar_one_or_none()
    if row is None:
        raise RemotePluginNotFound("PLUGIN_NOT_FOUND", f"插件不存在: {name}")

    target = _existing_plugin_dir(name)
    if not target.exists():
        raise RemotePluginError(
            "DIR_MISSING",
            f"插件目录已丢失: {target}（请先 uninstall 再 install）",
        )

    # git pull（带 timeout）。如果插件是从多插件仓库子目录复制安装的，
    # 安装目录没有 .git，此时临时 clone source_url 后按 plugin.json.name 定位子目录覆盖。
    if (target / ".git").exists():
        await _run_git("pull", "--ff-only", cwd=target, timeout=60.0)
    else:
        with tempfile.TemporaryDirectory(prefix="telebot-plugin-update-") as tmp:
            repo_dir = Path(tmp) / "repo"
            await _run_git("clone", "--depth", "1", row.source_url, str(repo_dir), timeout=180.0)

            candidates = [repo_dir]
            candidates.extend([p for p in repo_dir.iterdir() if p.is_dir() and not p.name.startswith(".")])
            source_dir: Path | None = None
            for candidate in candidates:
                if not (candidate / "plugin.json").is_file():
                    continue
                try:
                    candidate_meta = _read_plugin_metadata(candidate, fallback_name=candidate.name)
                except InvalidPluginMetadata:
                    continue
                if candidate_meta.name == name:
                    source_dir = candidate
                    break
            if source_dir is None:
                raise RemotePluginError(
                    "PLUGIN_NOT_IN_REPO",
                    f"仓库 {row.source_url!r} 内未找到插件 {name!r}",
                )

            backup = target.with_name(f"{target.name}.bak-update")
            if backup.exists():
                shutil.rmtree(backup, ignore_errors=True)
            target.rename(backup)
            try:
                shutil.copytree(
                    source_dir,
                    target,
                    ignore=shutil.ignore_patterns(".git", ".gitignore", "__pycache__"),
                )
            except Exception:
                if target.exists():
                    shutil.rmtree(target, ignore_errors=True)
                backup.rename(target)
                raise
            finally:
                if backup.exists():
                    shutil.rmtree(backup, ignore_errors=True)

    meta = _read_plugin_metadata(target, fallback_name=name)
    _validate_runtime_plugin_shape(target, meta)
    if meta.display_name:
        row.display_name = meta.display_name
    row.description = meta.description
    row.author = meta.author or row.author
    row.version = meta.version or row.version
    feat = (
        await db.execute(select(Feature).where(Feature.key == name))
    ).scalar_one_or_none()
    if feat is not None:
        feat.display_name = meta.display_name or name
        feat.version = meta.version or feat.version
        feat.is_builtin = False
        feat.manifest = _feature_manifest_from_meta(meta)
    await db.flush()

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
    "InvalidSourceUrl",
    "PluginMetadata",
    "PluginMetadataSchema",
    "RemotePluginError",
    "RemotePluginNotFound",
    "disable",
    "enable",
    "get_by_name",
    "install",
    "list_installed",
    "set_enabled",
    "trigger_reload",
    "uninstall",
    "update",
]
