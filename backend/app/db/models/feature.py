"""功能（feature/plugin）与账号-功能关联。"""

from __future__ import annotations

import importlib
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base

log = logging.getLogger(__name__)

# ── 历史功能常量（各处 import 用；不再有新增必要，以后新 builtin 直接建目录即可）──
FEATURE_AUTO_REPLY = "auto_reply"
FEATURE_FORWARD = "forward"
FEATURE_SCHEDULER = "scheduler"
FEATURE_GAME24 = "game24"
FEATURE_AUTOREPEAT = "autorepeat"
FEATURE_CODEX_IMAGE = "codex_image"

# 历史功能 key —— 已在 v0.4.0 砍掉对应 builtin 目录与前端页面，
# 但保留常量用于迁移期间识别 / 清理 DB 旧行（迁移 0014 会清空对应 account_feature 行）
FEATURE_LEGACY_KEYS: tuple[str, ...] = ("group_admin", "monitor")

# ── builtin 插件目录：backend/app/worker/plugins/builtin/ ──
_BUILTIN_PLUGIN_DIR: Path = (
    Path(__file__).parent.parent.parent  # models → db → app
    / "worker" / "plugins" / "builtin"
)


def scan_builtin_manifests() -> dict[str, str]:
    """动态扫描 builtin 目录，返回 {plugin_key: display_name}。

    - 以文件系统为权威来源：只要在 builtin/ 下新建一个包含正确 manifest.py 的目录，
      就会自动出现在结果里，不需要手动维护任何常量。
    - 解析方式：直接 import ``app.worker.plugins.builtin.<key>.manifest`` 模块，
      读取其 ``MANIFEST.display_name``；import 失败的目录写 warn 后跳过，不影响其它插件。
    - 任何异常均吞掉，最坏情况返回空 dict，上层 seed 逻辑有幂等保护不会误删已有行。
    """
    result: dict[str, str] = {}
    if not _BUILTIN_PLUGIN_DIR.exists():
        log.warning("builtin 插件目录不存在: %s", _BUILTIN_PLUGIN_DIR)
        return result

    for sub in sorted(_BUILTIN_PLUGIN_DIR.iterdir()):
        if not sub.is_dir() or sub.name.startswith("_"):
            continue
        manifest_file = sub / "manifest.py"
        if not manifest_file.exists():
            continue
        try:
            mod = importlib.import_module(
                f"app.worker.plugins.builtin.{sub.name}.manifest"
            )
            m = getattr(mod, "MANIFEST", None)
            if m is None:
                log.warning("builtin 插件 %s 的 manifest.py 没有 MANIFEST 对象，跳过", sub.name)
                continue
            key: str = getattr(m, "key", sub.name)
            display_name: str = getattr(m, "display_name", key)
            result[key] = display_name
        except Exception:  # noqa: BLE001
            log.warning("扫描 builtin 插件 %s 失败，跳过", sub.name, exc_info=True)
    return result


# ── 向后兼容的 BUILTIN_FEATURES 包装 ──────────────────────────────────────────
# 为避免循环 import 与模块级副作用，这里改为「惰性字典」：
# - 仍然是 dict 类型，可以 `key in BUILTIN_FEATURES`、`BUILTIN_FEATURES.items()` 等
# - 第一次被访问时才执行扫描，之后结果缓存在自身（_LazyBuiltinFeatures._cache）
# - 调用 BUILTIN_FEATURES.refresh() 可强制刷新（reload_account_config 里用到）
class _LazyBuiltinFeatures(dict):
    """惰性填充、可刷新的内置功能字典。

    继承 dict 以保持与现有 ``key in BUILTIN_FEATURES`` / ``BUILTIN_FEATURES.items()``
    等用法完全兼容；第一次实际访问时填充，之后可通过 ``refresh()`` 强制重扫。
    """

    _loaded: bool = False

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.refresh()

    def refresh(self) -> None:
        """重新扫描 builtin 目录并更新自身内容。线程 / 协程安全：操作是同步的，
        在单线程 asyncio worker 里没有竞争；主进程 FastAPI 多请求并发最坏结果是多扫一次，
        结果一致。
        """
        found = scan_builtin_manifests()
        self.clear()
        self.update(found)
        self._loaded = True
        log.debug("BUILTIN_FEATURES 已刷新: %s", list(self.keys()))

    # ── 重载 dict 各访问入口以触发惰性加载 ──
    def __contains__(self, item: object) -> bool:
        self._ensure_loaded()
        return super().__contains__(item)

    def __iter__(self):
        self._ensure_loaded()
        return super().__iter__()

    def __len__(self) -> int:
        self._ensure_loaded()
        return super().__len__()

    def keys(self):
        self._ensure_loaded()
        return super().keys()

    def values(self):
        self._ensure_loaded()
        return super().values()

    def items(self):
        self._ensure_loaded()
        return super().items()

    def get(self, key, default=None):
        self._ensure_loaded()
        return super().get(key, default)

    def __getitem__(self, key):
        self._ensure_loaded()
        return super().__getitem__(key)


BUILTIN_FEATURES: _LazyBuiltinFeatures = _LazyBuiltinFeatures()


# AccountFeature.state
FEATURE_STATE_ACTIVE = "active"
FEATURE_STATE_FAILED = "failed"
FEATURE_STATE_DISABLED = "disabled"


class Feature(Base):
    """功能 / 插件登记表。第三方插件通过 plugin_repo 同步后写入。"""

    __tablename__ = "feature"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[str | None] = mapped_column(String, nullable=True)
    manifest: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class AccountFeature(Base):
    """[账号 × 功能] 矩阵的某个格子。"""

    __tablename__ = "account_feature"

    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("account.id", ondelete="CASCADE"), primary_key=True
    )
    feature_key: Mapped[str] = mapped_column(
        String, ForeignKey("feature.key"), primary_key=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    state: Mapped[str] = mapped_column(String, default=FEATURE_STATE_DISABLED)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    installed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
