"""运行树按账号口径、真实接入方式与「关不掉」原因（第 19 轮）。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api import platform_tree as tree_api
from app.db.models.account import Account
from app.db.models.feature import AccountFeature, Feature
from app.db.models.plugin import InstalledPlugin
from app.schemas.platform_capabilities import PlatformCapabilitiesOut
from app.schemas.platform_tree import PlatformTreeOut
from app.services import platform_capabilities as caps
from app.services import platform_tree_service as tree_service
from app.services import platform_tree_shape, runtime_profile_service
from app.tests.test_platform_tree import _Result, _TreeDB

LEGACY_LEAF_KEYS = {"key", "attachment", "enabled", "requires", "warnings", "source_missing"}
LEGACY_BRANCH_KEYS = {"state", "desired", "forced_off", "demanded_by", "can_turn_off"}


class _ScopedDB(_TreeDB):
    def __init__(self, *, features=None, accounts=None, **kwargs) -> None:  # noqa: ANN001
        super().__init__(**kwargs)
        self.features = list(features or [])
        self.accounts = list(accounts or [])

    async def get(self, model, key):  # noqa: ANN001
        if model is Account:
            return next((row for row in self.accounts if row.id == key), None)
        return await super().get(model, key)

    async def execute(self, stmt):  # noqa: ANN001
        entity = stmt.column_descriptions[0].get("entity")
        if entity is Feature:
            return _Result(self.features)
        if entity is Account:
            return _Result(self.accounts)
        return await super().execute(stmt)


def _plugin(path: Path, *, requires: list[str], extra: dict | None = None, code: str = "") -> None:
    path.mkdir(parents=True, exist_ok=True)
    data = {"key": path.name, "requires_platform_capabilities": requires, **(extra or {})}
    (path / "plugin.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    (path / "plugin.py").write_text(code or "class P:\n    pass\n", encoding="utf-8")


@pytest.fixture(autouse=True)
def _reset():
    caps._reset_for_tests()
    runtime_profile_service._reset_for_tests()
    platform_tree_shape._reset_for_tests()
    yield
    platform_tree_shape._reset_for_tests()
    runtime_profile_service._reset_for_tests()
    caps._reset_for_tests()


def _patch_runtime(monkeypatch, profile: dict | None = None) -> None:  # noqa: ANN001
    monkeypatch.setattr(tree_service, "list_builtin_capability_requirements", lambda: [])
    monkeypatch.setattr(tree_service, "get_worker_runtime_snapshot", lambda: [])
    monkeypatch.setattr(
        tree_service.runtime_profile_service,
        "get_status",
        AsyncMock(return_value=profile or {"current_profile": "production"}),
    )
    monkeypatch.setattr(tree_service.kill_switch_service, "get_enabled", AsyncMock(return_value=False))


def _settings(**enabled: bool) -> dict[str, object]:
    base = {"ai": True, "interaction_bot": True, "webhooks": True, "ledger": True, "dispatch_debug": True}
    base.update(enabled)
    return {f"{key}_enabled": {"enabled": value, "generation": 0} for key, value in base.items()}


def _scenario(tmp_path: Path) -> _ScopedDB:
    # ai_helper（需要 AI）只在账号 2 启用；game（交互 + 台账）在账号 1、2 启用；
    # cron_only 只有定时任务；cmd 只有命令；fast 直通；hook 收 Webhook
    _plugin(tmp_path / "ai_helper", requires=["ai"], code="class P:\n    async def on_command(self, ctx, cmd, args, event):\n        return True\n")
    _plugin(
        tmp_path / "game",
        requires=["interaction_bot", "ledger"],
        extra={"interaction_entries": [{"key": "start"}], "event_subscriptions": [{"events": ["callback_query"], "source": ["interaction_bot"]}]},
        code="class P:\n    async def on_interaction(self, ctx, k, p):\n        return []\n",
    )
    _plugin(
        tmp_path / "cron_only",
        requires=[],
        code="class P:\n    async def on_startup(self, ctx):\n        ctx.scheduler.register('x', cron='* * * * *')\n",
    )
    _plugin(tmp_path / "cmd", requires=[], code="class P:\n    async def on_message(self, ctx, event):\n        pass\n")
    _plugin(tmp_path / "fast", requires=[], extra={"capabilities": {"telegram_direct_passthrough": {"enabled": True}}})
    _plugin(
        tmp_path / "hook",
        requires=["webhooks"],
        extra={"event_subscriptions": [{"events": ["webhook"], "source": ["webhook"]}], "middleware": [{"phase": "inbound"}]},
    )
    keys = ["ai_helper", "game", "cron_only", "cmd", "fast", "hook"]
    installed = [
        InstalledPlugin(key=key, source="local", installed_path=str(tmp_path / key), version="1", enabled=True)
        for key in keys
    ]
    account_features = [
        AccountFeature(account_id=2, feature_key="ai_helper", enabled=True, state="active"),
        AccountFeature(account_id=1, feature_key="game", enabled=True, state="active"),
        AccountFeature(account_id=2, feature_key="game", enabled=True, state="active"),
        AccountFeature(account_id=1, feature_key="cmd", enabled=False, state="failed", last_error="boom"),
        AccountFeature(account_id=1, feature_key="fast", enabled=True, state="active"),
    ]
    return _ScopedDB(
        installed=installed,
        account_features=account_features,
        settings=_settings(webhooks=True),
        features=[SimpleNamespace(key="ai_helper", display_name="AI 助手"), SimpleNamespace(key="game", display_name="小游戏")],
        accounts=[SimpleNamespace(id=1, display_name="主号"), SimpleNamespace(id=2, display_name="")],
    )


@pytest.mark.asyncio
async def test_server_scope_is_backward_compatible(tmp_path: Path, monkeypatch) -> None:
    db = _scenario(tmp_path)
    _patch_runtime(monkeypatch)
    tree = PlatformTreeOut.model_validate(await tree_service.build_platform_tree(db)).model_dump()  # type: ignore[arg-type]

    assert tree["title"] == "运行树"
    assert tree["scope"] == {"kind": "server", "account_id": None, "account_name": None}
    leaves = {leaf["key"]: leaf for leaf in tree["leaves"]}
    for leaf in leaves.values():
        assert LEGACY_LEAF_KEYS <= set(leaf)
        assert leaf["account_state"] is None
    for branch in tree["branches"].values():
        assert LEGACY_BRANCH_KEYS <= set(branch)
        assert branch["demanded_by"] == branch["demanded_by_global"]
    # 全局口径：任一账号启用即算
    assert leaves["ai_helper"]["enabled"] is True
    assert leaves["cmd"]["enabled"] is False
    assert tree["branches"]["ai"]["demanded_by"] == ["ai_helper"]
    assert tree["branches"]["ai"]["can_turn_off"] is False
    assert tree["branches"]["ai"]["blocked_code"] == "required_by_plugins"
    assert "AI 助手（#2）" in tree["branches"]["ai"]["blocked_reason"]
    assert tree["branches"]["webhooks"]["can_turn_off"] is True
    assert tree["branches"]["webhooks"]["blocked_code"] is None
    assert tree["branches"]["webhooks"]["blocked_reason"] is None


@pytest.mark.asyncio
async def test_channel_and_entry_kinds_follow_real_declarations(tmp_path: Path, monkeypatch) -> None:
    db = _scenario(tmp_path)
    _patch_runtime(monkeypatch)
    tree = await tree_service.build_platform_tree(db)  # type: ignore[arg-type]
    leaves = {leaf["key"]: leaf for leaf in tree["leaves"]}

    # 旧字段不变：除直通 / 交互外仍然是「命令」
    assert {key: leaf["attachment"] for key, leaf in leaves.items()} == {
        "ai_helper": "命令",
        "game": "交互",
        "cron_only": "命令",
        "cmd": "命令",
        "fast": "直通",
        "hook": "命令",
    }
    assert leaves["cron_only"]["channel"] == "main"
    assert leaves["cron_only"]["channel_label"] == "主通道"
    assert leaves["cron_only"]["entry_kinds"] == ["schedule"]
    assert leaves["cron_only"]["entry_labels"] == ["定时任务"]
    assert leaves["ai_helper"]["entry_kinds"] == ["command"]
    assert leaves["cmd"]["entry_kinds"] == ["message"]
    assert leaves["fast"]["channel"] == "passthrough"
    assert leaves["fast"]["entry_kinds"] == ["passthrough"]
    assert leaves["game"]["channel"] == "interaction"
    assert leaves["game"]["channel_label"] == "交互 Bot"
    assert leaves["game"]["entry_kinds"] == ["event", "interaction"]
    assert leaves["hook"]["entry_kinds"] == ["webhook", "middleware"]
    assert leaves["game"]["display_name"] == "小游戏"
    assert leaves["cmd"]["display_name"] == "cmd"


@pytest.mark.asyncio
async def test_account_scope_explains_why_module_cannot_turn_off(tmp_path: Path, monkeypatch) -> None:
    db = _scenario(tmp_path)
    _patch_runtime(monkeypatch)
    tree = await tree_service.build_platform_tree(db, account_id=1, account_name="主号")  # type: ignore[arg-type]

    assert tree["scope"] == {"kind": "account", "account_id": 1, "account_name": "主号"}
    leaves = {leaf["key"]: leaf for leaf in tree["leaves"]}
    assert leaves["ai_helper"]["enabled"] is False  # 只在账号 2 启用
    assert leaves["ai_helper"]["enabled_global"] is True
    assert leaves["ai_helper"]["enabled_account_ids"] == [2]
    assert leaves["ai_helper"]["account_state"] == "disabled"
    assert leaves["cmd"]["account_state"] == "failed"
    assert leaves["cmd"]["account_error"] == "boom"
    assert leaves["fast"]["enabled"] is True

    ai = tree["branches"]["ai"]
    assert ai["demanded_by"] == []  # 本账号没人用
    assert ai["demanded_by_global"] == ["ai_helper"]
    assert ai["demanders"] == [{"key": "ai_helper", "display_name": "AI 助手", "account_ids": [2]}]
    assert ai["can_turn_off"] is False  # 模块全服共用，仍关不掉
    assert ai["blocked_code"] == "required_by_other_accounts"
    assert "其他账号" in ai["blocked_reason"] and "AI 助手（#2）" in ai["blocked_reason"]

    ledger = tree["branches"]["ledger"]
    assert ledger["demanded_by"] == ["game"]
    assert ledger["blocked_code"] == "required_by_this_account"
    assert "账号 #2" in ledger["blocked_reason"]

    # 账号 2 的视角
    tree2 = await tree_service.build_platform_tree(db, account_id=2)  # type: ignore[arg-type]
    assert tree2["branches"]["ai"]["demanded_by"] == ["ai_helper"]
    assert tree2["branches"]["ai"]["blocked_code"] == "required_by_this_account"
    assert "账号 #1（主号）" in tree2["branches"]["ledger"]["blocked_reason"]


@pytest.mark.asyncio
async def test_forced_off_module_reports_reason(tmp_path: Path, monkeypatch) -> None:
    db = _scenario(tmp_path)
    _patch_runtime(monkeypatch, {"current_profile": "safe_watch", "active_profile": "safe_watch"})
    monkeypatch.setattr(caps, "get_snapshot", lambda: SimpleNamespace(
        cache_ready=True,
        runtime={},
        desired={"ai": False, "interaction_bot": True, "webhooks": True, "ledger": True, "dispatch_debug": True},
        forced_off={"ai": True},
    ))
    tree = await tree_service.build_platform_tree(db, account_id=1)  # type: ignore[arg-type]
    ai = tree["branches"]["ai"]
    assert ai["blocked_code"] == "already_off"
    assert ai["forced_off_reason"] and "值守" in ai["forced_off_reason"]
    assert "值守" in ai["blocked_reason"]


@pytest.mark.asyncio
async def test_api_rejects_unknown_account(tmp_path: Path, monkeypatch) -> None:
    db = _scenario(tmp_path)
    _patch_runtime(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        await tree_api.get_platform_tree(db, None, account_id=99)  # type: ignore[arg-type]
    assert exc.value.status_code == 404
    out = await tree_api.get_platform_tree(db, None, account_id=1)  # type: ignore[arg-type]
    assert out.scope.kind == "account" and out.scope.account_name == "主号"
    legacy = await tree_api.get_platform_tree(db, None, account_id=None)  # type: ignore[arg-type]
    assert legacy.scope.kind == "server"


def test_capabilities_expose_probe_flag() -> None:
    out = PlatformCapabilitiesOut.model_validate({"modules": [], "channels": []})
    assert out.features["platform_tree_account_scope"] is True


def test_shape_cache_refreshes_when_source_changes(tmp_path: Path) -> None:
    from app.services.plugin_capability_requirements import read_plugin_capability_requirement

    root = tmp_path / "p"
    _plugin(root, requires=[])
    record = read_plugin_capability_requirement("p", root, source="local")
    assert platform_tree_shape.plugin_shape(record, "命令").entry_kinds == ()
    (root / "plugin.py").write_text(
        "class P:\n    async def on_command(self, ctx, cmd, args, event):\n        return True\n# changed\n",
        encoding="utf-8",
    )
    assert platform_tree_shape.plugin_shape(record, "命令").entry_kinds == ("command",)
