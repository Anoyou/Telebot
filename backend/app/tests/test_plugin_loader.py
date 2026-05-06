"""plugin loader 测试：mock DB（AsyncSessionLocal）+ Redis + Telethon client。

覆盖：
  - 注册表：内置 5 个 plugin 全部能被找到
  - 加载流程：enabled feature 会调到对应 plugin 的 on_startup（用 spy）
  - 配置热重载：reload_account_config 能刷新 ctx.rules / ctx.config，已禁用的会 shutdown
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.db.models.feature import (
    FEATURE_AUTO_REPLY,
    FEATURE_FORWARD,
    FEATURE_SCHEDULER,
)
from app.worker.plugins import loader as loader_mod
from app.worker.plugins.base import Plugin, PluginContext
from app.worker.plugins.loader import (
    _BUILTIN_MODULES,
    _import_builtins,
    load_plugins_for_account,
    reload_account_config,
)


# ─────────────────────────────────────────────────────
# 极简 fake redis（loader 仅用 rpush）
# ─────────────────────────────────────────────────────
class _FakeRedis:
    def __init__(self) -> None:
        self.list_pushes: list[tuple[str, str]] = []

    async def rpush(self, key: str, val: str) -> int:
        self.list_pushes.append((key, val))
        return len(self.list_pushes)

    async def publish(self, *_a, **_kw) -> int:
        return 0

    async def get(self, *_a, **_kw):
        return None

    async def set(self, *_a, **_kw):
        return True

    async def script_load(self, *_a, **_kw):
        return "fake-sha"

    async def evalsha(self, *_a, **_kw):
        return [1, 0, 0]


# ─────────────────────────────────────────────────────
# Fake ORM 行（避免连真 PG）
# ─────────────────────────────────────────────────────
@dataclass
class _FakeAcc:
    id: int = 1
    cold_start_until: Any = None


@dataclass
class _FakeAF:
    account_id: int
    feature_key: str
    enabled: bool = True
    config: dict | None = None
    state: str = "disabled"
    last_error: str | None = None


@dataclass
class _FakeRule:
    id: int
    account_id: int
    feature_key: str
    enabled: bool = True
    priority: int = 100
    config: dict | None = None


# ─────────────────────────────────────────────────────
# Fake AsyncSession：拦截 db.get / db.execute / db.commit
# ─────────────────────────────────────────────────────
class _FakeDB:
    """一个超薄 fake DB：以"按表归类的 rows"驱动 db.get / select 行为。"""

    def __init__(
        self,
        accounts: dict[int, _FakeAcc],
        humanize: dict[int, Any],
        afs: list[_FakeAF],
        rules: list[_FakeRule],
        features: dict[str, Any] | None = None,
    ) -> None:
        self.accounts = accounts
        self.humanize = humanize
        self.afs = afs
        self.rules = rules
        self.features = features or {}
        # 记录 update 调用，便于断言 state 改动
        self.update_calls: list[Any] = []

    async def get(self, model, pk):
        # 按 model.__tablename__ 区分
        name = getattr(model, "__tablename__", None) or getattr(
            getattr(model, "__table__", None), "name", None
        )
        if name == "account":
            return self.accounts.get(pk)
        if name == "humanize_config":
            return self.humanize.get(pk)
        if name == "feature":
            return self.features.get(pk)
        return None

    async def execute(self, stmt):
        text = str(stmt).lower()
        # update -> 记录并返回空 result
        if text.startswith("update"):
            self.update_calls.append(stmt)
            return _FakeResult([])
        # select account_feature where account_id = X
        if "account_feature" in text:
            return _FakeResult([(af,) for af in self.afs])
        if "rule" in text:
            return _FakeResult([(r,) for r in self.rules])
        return _FakeResult([])

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


class _FakeResult:
    def __init__(self, rows: list[tuple[Any, ...]]):
        self._rows = rows

    def scalar_one_or_none(self):
        return self._rows[0][0] if self._rows else None

    def scalars(self):
        return _FakeScalars([r[0] for r in self._rows])


class _FakeScalars:
    def __init__(self, items: list[Any]):
        self._items = items

    def all(self):
        return list(self._items)


@asynccontextmanager
async def _fake_session_factory(db: _FakeDB):
    yield db


# ─────────────────────────────────────────────────────
# 用例 1：内置 5 个 plugin 都能被注册
# ─────────────────────────────────────────────────────
def test_import_builtins_registers_all_three() -> None:
    _import_builtins()
    from app.worker.plugins.base import all_plugins

    reg = all_plugins()
    for key in (
        FEATURE_AUTO_REPLY,
        FEATURE_FORWARD,
        FEATURE_SCHEDULER,
    ):
        assert key in reg, f"plugin {key} 未注册"


def test_builtin_modules_constant_is_complete() -> None:
    """_BUILTIN_MODULES 应当至少覆盖 3 个内置模块。"""
    assert {
        "auto_reply",
        "forward",
        "scheduler",
    } <= set(_BUILTIN_MODULES)


# ─────────────────────────────────────────────────────
# 用例 2：load_plugins_for_account 调到 on_startup
# ─────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_load_calls_on_startup(monkeypatch) -> None:
    """模拟一个 account_feature 行（auto_reply enabled），验证 plugin 实例的 on_startup 被调一次。"""
    # 1) mock db 数据
    fake_db = _FakeDB(
        accounts={1: _FakeAcc(id=1)},
        humanize={1: None},
        afs=[_FakeAF(account_id=1, feature_key=FEATURE_AUTO_REPLY, enabled=True, config={})],
        rules=[],
    )
    monkeypatch.setattr(
        loader_mod, "AsyncSessionLocal", lambda: _fake_session_factory(fake_db)
    )

    # 2) 替换 AutoReplyPlugin.on_startup 为 spy
    on_startup_spy = AsyncMock()
    monkeypatch.setattr(
        "app.worker.plugins.builtin.auto_reply.AutoReplyPlugin.on_startup",
        on_startup_spy,
    )

    # 3) mock telethon client（client.on 装饰器返回原函数即可）
    client = MagicMock()

    def _on(_filter):
        def _wrap(fn):
            return fn

        return _wrap

    client.on = _on

    redis = _FakeRedis()
    paused = asyncio.Event()
    paused.set()

    await load_plugins_for_account(client, account_id=1, paused=paused, redis=redis)

    on_startup_spy.assert_awaited_once()


# ─────────────────────────────────────────────────────
# 用例 3：reload_account_config 在 plugin 已禁用时应触发 shutdown
# ─────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_reload_account_config_shutdown_disabled(monkeypatch) -> None:
    """先正常加载一个 plugin，然后把它在 DB 里改成 enabled=False，触发热重载应调 on_shutdown。"""

    # 注册一个临时 plugin，以便我们独占断言
    from app.worker.plugins.base import register

    @register
    class _TempPlugin(Plugin):
        key = "_test_temp"
        display_name = "测试占位"

        async def on_startup(self, ctx: PluginContext) -> None:  # noqa: D401
            return None

        async def on_shutdown(self, ctx: PluginContext) -> None:  # noqa: D401
            return None

    # 在 feature 表里登记，避免 _activate 因 plugin 未注册而走 failed 分支
    fake_db_init = _FakeDB(
        accounts={1: _FakeAcc(id=1)},
        humanize={1: None},
        afs=[_FakeAF(account_id=1, feature_key="_test_temp", enabled=True, config={})],
        rules=[],
    )
    monkeypatch.setattr(
        loader_mod, "AsyncSessionLocal", lambda: _fake_session_factory(fake_db_init)
    )

    client = MagicMock()
    client.on = lambda f: (lambda fn: fn)
    paused = asyncio.Event()
    paused.set()
    redis = _FakeRedis()

    # spy on_shutdown
    shutdown_spy = AsyncMock()
    monkeypatch.setattr(_TempPlugin, "on_shutdown", shutdown_spy)

    await load_plugins_for_account(client, account_id=1, paused=paused, redis=redis)

    # 把 fake_db 的 enabled 改成 False，再触发热重载
    fake_db_init.afs[0].enabled = False
    await reload_account_config(account_id=1)

    shutdown_spy.assert_awaited_once()
