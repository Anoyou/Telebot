"""插件仓库（plugin_repo）服务：浏览 Git 仓库内可装插件 + 选择性安装。

与 ``remote_plugin_service`` 的分工：
- 本服务管理“仓库列表”（plugin_repo 表）：CRUD + 列出仓库内插件 + 触发安装
- ``remote_plugin_service`` 才是真正的“已安装插件”落地逻辑（写 remote_plugin 表 +
  落盘 plugins/installed/<name>/ + 注册 Feature + 热加载）

安装路径分两种：
1) 仓库根目录就是单个插件（含 plugin.json）→ 直接复用
   ``remote_plugin_service.install(repo_url)`` 走 git clone 流程
2) 仓库下多个子目录、每个子目录一个插件 → 不能简单 git clone 整个仓库到
   ``plugins/installed/<plugin>/``（会把无关插件也带进去）；本服务从本地缓存
   的对应子目录复制到 ``plugins/installed/<plugin>/``，写 remote_plugin 行 +
   注册 Feature + 触发热加载，整套流程与 ``remote_plugin_service.install`` 一致

安全设计：
- 同样禁止执行 ``manifest.py``，只静态解析 ``plugin.json``
- ``url`` 走与 ``remote_plugin_service._validate_source_url`` 同套校验
- 缓存目录隔离：每个 URL 对应一个 sha256 摘要目录，避免文件名冲突 / 路径穿越
"""

from __future__ import annotations

import hashlib
import logging
import re
import shutil
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.account import Account
from ..db.models.feature import AccountFeature, Feature
from ..db.models.plugin_repo import PluginRepo
from ..db.models.remote_plugin import RemotePlugin
from ..schemas.plugin_repo import PluginRepoPlugin
from ..settings import settings
from . import remote_plugin_service as rps
from .remote_plugin_service import (
    DuplicatePluginName,
    InvalidPluginMetadata,
    RemotePluginError,
    _derive_name_from_url,
    _feature_manifest_from_meta,
    _plugin_dir,
    _read_plugin_metadata,
    _run_git,
    _trigger_reload,
    _validate_runtime_plugin_shape,
    _validate_source_url,
)

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────
# 错误类型
# ─────────────────────────────────────────────────────
class PluginRepoError(Exception):
    """plugin_repo 操作基类异常。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class PluginRepoNotFound(PluginRepoError):
    """根据 id 查不到 plugin_repo 行。"""


class DuplicatePluginRepo(PluginRepoError):
    """同 URL 已经保存过。"""


class PluginNotInRepo(PluginRepoError):
    """仓库内找不到指定名字的插件。"""


# ─────────────────────────────────────────────────────
# 缓存目录管理
# ─────────────────────────────────────────────────────
def _cache_root() -> Path:
    """所有仓库克隆的缓存根目录。"""
    root = settings.resolve_project_path(settings.plugin_repos_cache_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _cache_dir_for(url: str) -> Path:
    """单个仓库 URL 对应的缓存目录。

    用 sha256(url) 当目录名，既避免文件系统非法字符也防止路径穿越攻击。
    """
    digest = hashlib.sha256(url.strip().encode("utf-8")).hexdigest()[:32]
    return _cache_root() / digest


async def _ensure_repo_cached(url: str, *, force_refresh: bool = False) -> Path:
    """确保仓库已克隆到本地缓存；返回缓存目录路径。

    - 首次：``git clone <url>``
    - 已存在：``git fetch + git reset --hard origin/HEAD``，保证内容最新
    - 失败：清理半完成的克隆目录，向上抛 ``GitOperationFailed``
    """
    _validate_source_url(url)
    target = _cache_dir_for(url)

    if not target.exists() or force_refresh and target.exists() and not (target / ".git").exists():
        # 第一种：根本不存在 → clone
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            await _run_git("clone", url, str(target), timeout=180.0)
        except Exception:
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            raise
        return target

    # 缓存命中：尝试 git fetch + reset 到 origin/HEAD，失败时不至于阻塞读取列表
    try:
        await _run_git("fetch", "--all", "--prune", cwd=target, timeout=60.0)
        # 用 origin 的默认分支做硬重置；--ff-only 在分支变更时会失败，硬重置更鲁棒
        head = (await _run_git(
            "symbolic-ref", "refs/remotes/origin/HEAD", cwd=target, timeout=10.0,
        )).strip() or "refs/remotes/origin/HEAD"
        # symbolic-ref 输出形如 "refs/remotes/origin/main"
        await _run_git("reset", "--hard", head, cwd=target, timeout=30.0)
    except Exception:  # noqa: BLE001
        log.warning("刷新仓库缓存失败，继续使用旧副本: %s", url, exc_info=True)
    return target


# ─────────────────────────────────────────────────────
# 浏览仓库：找出可装插件清单
# ─────────────────────────────────────────────────────
def _name_from_url(url: str) -> str:
    """从 URL 派生默认显示名（与 remote_plugin_service 中的派生一致）。"""
    try:
        return _derive_name_from_url(url)
    except RemotePluginError:
        return url


def _scan_plugins(repo_dir: Path) -> list[tuple[str, Path]]:
    """扫描仓库目录，找出所有插件子目录。

    返回 ``[(默认名, 绝对路径), ...]``：
    - 仓库根有 plugin.json → 整个仓库是一个插件，默认名 = 目录名
    - 否则扫描一级子目录，凡含 plugin.json 的都算一个插件，默认名 = 子目录名

    只识别含 ``plugin.json`` 的目录；没有 plugin.json 的目录无法解析元数据、
    也无法走安全安装流程，故不在列表中暴露。
    """
    results: list[tuple[str, Path]] = []
    if (repo_dir / "plugin.json").is_file():
        results.append((repo_dir.name, repo_dir))
        return results

    for child in sorted(repo_dir.iterdir()):
        if not child.is_dir():
            continue
        # 跳过 .git / 隐藏目录
        if child.name.startswith("."):
            continue
        if (child / "plugin.json").is_file():
            results.append((child.name, child))
    return results


def _version_tuple(raw: str | None) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", str(raw or ""))[:3])


async def list_plugins_in_repo(
    db: AsyncSession, repo_id: int
) -> list[PluginRepoPlugin]:
    """列出 ``plugin_repo[id]`` 仓库内所有可装插件。

    步骤：
      1. 取仓库行
      2. ``_ensure_repo_cached`` 拉到最新副本
      3. 扫描根 / 一级子目录里的 plugin.json
      4. 用 ``_read_plugin_metadata`` 静态解析元数据
      5. 与 ``remote_plugin.name`` 做差集，标记 ``installed`` 字段
    """
    row = await _get_repo(db, repo_id)

    repo_dir = await _ensure_repo_cached(row.url)
    raw = _scan_plugins(repo_dir)

    installed_rows = (
        await db.execute(select(RemotePlugin.name, RemotePlugin.version))
    ).all()
    installed_versions = {str(name): str(version or "") for name, version in installed_rows}

    out: list[PluginRepoPlugin] = []
    for default_name, plugin_dir in raw:
        try:
            meta = _read_plugin_metadata(plugin_dir, fallback_name=default_name)
        except InvalidPluginMetadata:
            log.warning("跳过仓库 %s 内非法插件目录: %s", row.url, plugin_dir)
            continue
        subdir = (
            ""
            if plugin_dir == repo_dir
            else str(plugin_dir.relative_to(repo_dir))
        )
        installed_version = installed_versions.get(meta.name)
        out.append(
            PluginRepoPlugin(
                name=meta.name,
                display_name=meta.display_name or meta.name,
                description=meta.description,
                author=meta.author,
                version=meta.version,
                installed=installed_version is not None,
                installed_version=installed_version,
                update_available=(
                    installed_version is not None
                    and _version_tuple(meta.version) > _version_tuple(installed_version)
                ),
                subdir=subdir,
            )
        )
    # 已装的排到后面，便于用户先看到可装的
    out.sort(key=lambda p: (p.installed, p.name))
    return out


# ─────────────────────────────────────────────────────
# CRUD
# ─────────────────────────────────────────────────────
async def list_repos(db: AsyncSession) -> list[PluginRepo]:
    """按 ``added_at`` 倒序列出所有仓库。"""
    rows = (
        await db.execute(select(PluginRepo).order_by(PluginRepo.added_at.desc()))
    ).scalars().all()
    return list(rows)


async def _get_repo(db: AsyncSession, repo_id: int) -> PluginRepo:
    row = (
        await db.execute(select(PluginRepo).where(PluginRepo.id == repo_id))
    ).scalar_one_or_none()
    if row is None:
        raise PluginRepoNotFound("REPO_NOT_FOUND", f"仓库不存在: id={repo_id}")
    return row


async def get_repo(db: AsyncSession, repo_id: int) -> PluginRepo:
    """对外的 get；不存在抛 ``PluginRepoNotFound``。"""
    return await _get_repo(db, repo_id)


async def create_repo(
    db: AsyncSession,
    url: str,
    *,
    name: str | None = None,
    description: str | None = None,
) -> PluginRepo:
    """新增仓库行。

    - URL 必须通过 ``_validate_source_url``
    - URL 全局唯一；重复时抛 ``DuplicatePluginRepo``
    - ``name`` 为空时从 URL 派生
    """
    _validate_source_url(url)
    url = url.strip()

    existing = (
        await db.execute(select(PluginRepo).where(PluginRepo.url == url))
    ).scalar_one_or_none()
    if existing is not None:
        raise DuplicatePluginRepo("REPO_EXISTS", f"仓库已保存: {url}")

    row = PluginRepo(
        url=url,
        name=(name or "").strip() or _name_from_url(url),
        description=(description or "").strip(),
    )
    db.add(row)
    await db.flush()
    return row


async def delete_repo(db: AsyncSession, repo_id: int) -> bool:
    """删除仓库行；同时清理本地缓存目录。

    ``False`` 表示行不存在。**不**联动卸载已安装的插件——仓库只是“目录”，
    用户卸载具体插件应走插件管理页面（remote_plugin DELETE 接口）。
    """
    row = (
        await db.execute(select(PluginRepo).where(PluginRepo.id == repo_id))
    ).scalar_one_or_none()
    if row is None:
        return False

    cache = _cache_dir_for(row.url)
    await db.delete(row)
    await db.flush()

    # DB 已提交逻辑由调用方负责；这里只清理缓存目录
    try:
        if cache.exists():
            shutil.rmtree(cache, ignore_errors=True)
    except Exception:  # noqa: BLE001
        log.exception("清理仓库缓存目录失败: %s", cache)
    return True


# ─────────────────────────────────────────────────────
# 选择性安装
# ─────────────────────────────────────────────────────
async def install_plugin_from_repo(
    db: AsyncSession,
    repo_id: int,
    plugin_name: str,
    *,
    default_enabled: bool = False,
) -> RemotePlugin:
    """从仓库中安装指定名字的插件。

    步骤：
      1. 取仓库行 → 确保缓存最新（git clone / pull）
      2. 在缓存里定位含 plugin.json 的插件目录
      3. 校验目标安装目录 ``plugins/installed/<plugin_name>/`` 不存在且 DB 无同名行
      4. ``copytree`` 把插件目录拷过去（不含 .git）
      5. 写 ``remote_plugin`` 行（source_url 用仓库 URL，便于追溯）
      6. 注册到 ``feature`` 表 + 按 ``default_enabled`` 批量启用账号
      7. 触发 worker 热加载
    """
    row = await _get_repo(db, repo_id)
    repo_dir = await _ensure_repo_cached(row.url)

    # 扫描定位插件子目录
    raw = _scan_plugins(repo_dir)
    target_dir: Path | None = None
    for default_name, plugin_dir in raw:
        try:
            meta = _read_plugin_metadata(plugin_dir, fallback_name=default_name)
        except InvalidPluginMetadata:
            continue
        if meta.name == plugin_name:
            target_dir = plugin_dir
            break
    if target_dir is None:
        raise PluginNotInRepo(
            "PLUGIN_NOT_IN_REPO",
            f"仓库 {row.url!r} 内未找到插件 {plugin_name!r}",
        )

    # 重读元数据（已校验通过；再读一次以拿到完整字段）
    meta = _read_plugin_metadata(target_dir, fallback_name=plugin_name)
    _validate_runtime_plugin_shape(target_dir, meta)
    final_name = meta.name

    # 单插件仓库（target_dir == repo_dir）走 remote_plugin_service.install 复用 clone
    # 路径，行为与“直接粘 URL 装”完全等价
    if target_dir == repo_dir:
        return await rps.install(
            db, row.url, default_enabled=default_enabled,
        )

    # 多插件仓库子目录 → 复制 target_dir 到 plugins/installed/<final_name>/
    install_path = _plugin_dir(final_name)

    # 重名检查：DB 行 + 目录都不能存在
    existing = (
        await db.execute(select(RemotePlugin).where(RemotePlugin.name == final_name))
    ).scalar_one_or_none()
    if existing is not None:
        raise DuplicatePluginName(
            "PLUGIN_EXISTS", f"插件 {final_name!r} 已安装"
        )
    if install_path.exists():
        raise DuplicatePluginName(
            "DIR_EXISTS",
            f"目录已存在但 DB 无记录: {install_path}（请先手动清理）",
        )

    install_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(
            target_dir,
            install_path,
            ignore=shutil.ignore_patterns(".git", ".gitignore", "__pycache__"),
        )
    except Exception as exc:
        if install_path.exists():
            shutil.rmtree(install_path, ignore_errors=True)
        raise PluginRepoError(
            "COPY_FAILED", f"复制插件目录失败: {exc}"
        ) from exc

    try:
        rp_row = RemotePlugin(
            name=final_name,
            display_name=meta.display_name or final_name,
            description=meta.description,
            author=meta.author,
            source_url=row.url,
            version=meta.version,
            enabled=False,
            default_enabled=default_enabled,
        )
        db.add(rp_row)

        feat = (
            await db.execute(select(Feature).where(Feature.key == final_name))
        ).scalar_one_or_none()
        if feat is None:
            db.add(
                Feature(
                    key=final_name,
                    display_name=meta.display_name or final_name,
                    is_builtin=False,
                    version=meta.version,
                    manifest=_feature_manifest_from_meta(meta),
                )
            )
        else:
            feat.display_name = meta.display_name or final_name
            feat.version = meta.version
            feat.is_builtin = False
            feat.manifest = _feature_manifest_from_meta(meta)

        await db.flush()

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
                    db.add(
                        AccountFeature(
                            account_id=int(aid),
                            feature_key=final_name,
                            enabled=True,
                            state="active",
                        )
                    )
            await db.flush()
    except Exception:
        # 写库 / 元数据失败 → 回滚已复制的目录
        shutil.rmtree(install_path, ignore_errors=True)
        raise

    await _trigger_reload(db, final_name)
    return rp_row


__all__ = [
    "DuplicatePluginRepo",
    "PluginNotInRepo",
    "PluginRepoError",
    "PluginRepoNotFound",
    "create_repo",
    "delete_repo",
    "get_repo",
    "install_plugin_from_repo",
    "list_plugins_in_repo",
    "list_repos",
]
