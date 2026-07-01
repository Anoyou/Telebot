from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.db.models.log import PluginConfigActionJob, RuntimeLog
from app.schemas.feature import FeatureInfo
from app.services import plugin_config_action_jobs, plugin_config_actions
from app.services.plugin_config_action_jobs import create_plugin_config_action_job
from app.services.plugin_config_actions import declared_config_actions, run_plugin_config_action
from app.worker.plugins.base import Plugin, PluginContext


class DemoConfigActionPlugin(Plugin):
    key = "demo_action"
    display_name = "Demo Action"

    async def on_config_action(
        self,
        ctx: PluginContext,
        action_key: str,
        payload: dict,
    ) -> dict:
        assert action_key == "make_item"
        assert ctx.config["count"] == 3
        assert ctx.config["api_token"] == "real-token"
        return {
            "message": "已生成",
            "config_patch": {
                "items": [
                    {
                        "enabled": True,
                        "name": payload["input"]["name"],
                        "count": ctx.config["count"],
                    }
                ]
            },
        }


class LoggingConfigActionPlugin(Plugin):
    key = "logging_action"
    display_name = "Logging Action"

    async def on_config_action(
        self,
        ctx: PluginContext,
        action_key: str,
        payload: dict,
    ) -> dict:
        assert action_key == "make_item"
        if ctx.log:
            await ctx.log("info", "动作进度", step="demo")
        return {"message": "已完成", "config_patch": {"done": True}}


class FakeDB:
    def __init__(self) -> None:
        self.added = []
        self.commits = 0
        self.flushed = False
        self.refreshed = []

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        self.flushed = True

    async def commit(self):
        self.commits += 1

    async def refresh(self, value):
        self.refreshed.append(value)

    async def get(self, *_args, **_kwargs):
        return None


def test_declared_config_actions_reads_schema_metadata() -> None:
    feature = SimpleNamespace(
        manifest={
            "config_schema": {
                "x-config-actions": [
                    {"key": "make_item", "title": "生成"},
                    {"title": "缺少 key"},
                ]
            }
        }
    )

    actions = declared_config_actions(feature)

    assert actions == [{"key": "make_item", "title": "生成"}]


def test_declared_config_actions_reads_installed_manifest_metadata() -> None:
    feature = SimpleNamespace(
        manifest={
            "config_schema": {"type": "object"},
        }
    )
    installed = SimpleNamespace(
        manifest_json={
            "config_actions": [
                {"key": "generate_knowledge_base", "title": "获取并整理为题库"},
                {"title": "缺少 key"},
            ]
        }
    )

    actions = declared_config_actions(feature, installed_plugin=installed)

    assert actions == [
        {"key": "generate_knowledge_base", "title": "获取并整理为题库"}
    ]


def test_feature_info_reads_installed_manifest_config_actions() -> None:
    feature = SimpleNamespace(
        key="quick_qa",
        display_name="快问快答",
        is_builtin=False,
        version="1.2.0",
        manifest={
            "config_schema": {"type": "object"},
        },
    )
    installed = SimpleNamespace(
        source="repo",
        source_url="https://github.com/Anoyou/telebot-plugins/tree/0.33.x",
        source_label="Plugin Repo",
        signature_ok=None,
        manifest_json={
            "config_actions": [
                {
                    "key": "generate_knowledge_base",
                    "title": "获取并整理为题库",
                }
            ]
        },
        lint_warnings=[],
    )

    info = FeatureInfo.from_feature(feature, installed_plugin=installed)

    assert info.config_actions == [
        {
            "key": "generate_knowledge_base",
            "title": "获取并整理为题库",
        }
    ]


@pytest.mark.asyncio
async def test_run_plugin_config_action_merges_form_config_and_returns_patch(monkeypatch) -> None:
    feature = SimpleNamespace(
        key="demo_action",
        manifest={
            "permissions": [],
            "config_actions": [{"key": "make_item", "title": "生成"}],
        },
    )
    account = SimpleNamespace(id=7, proxy_id=None)
    installed = SimpleNamespace(manifest_json={})
    monkeypatch.setattr(plugin_config_actions, "get_plugin", lambda key: DemoConfigActionPlugin)

    result = await run_plugin_config_action(
        FakeDB(),
        account=account,
        feature=feature,
        action_key="make_item",
        effective_config={"count": 1, "api_token": "real-token"},
        current_config={"count": 3, "api_token": "••••••••••••••••"},
        action_input={"name": "第一组"},
        installed_plugin=installed,
    )

    assert result["message"] == "已生成"
    assert result["config_patch"]["items"] == [
        {"enabled": True, "name": "第一组", "count": 3}
    ]


@pytest.mark.asyncio
async def test_run_plugin_config_action_accepts_installed_manifest_action(monkeypatch) -> None:
    feature = SimpleNamespace(
        key="demo_action",
        manifest={
            "permissions": [],
            "config_schema": {"type": "object"},
        },
    )
    installed = SimpleNamespace(
        manifest_json={
            "config_actions": [{"key": "make_item", "title": "生成"}],
        }
    )
    account = SimpleNamespace(id=7, proxy_id=None)
    monkeypatch.setattr(plugin_config_actions, "get_plugin", lambda key: DemoConfigActionPlugin)

    result = await run_plugin_config_action(
        FakeDB(),
        account=account,
        feature=feature,
        action_key="make_item",
        effective_config={"count": 1, "api_token": "real-token"},
        current_config={"count": 3},
        action_input={"name": "第二组"},
        installed_plugin=installed,
    )

    assert result["message"] == "已生成"
    assert result["config_patch"]["items"] == [
        {"enabled": True, "name": "第二组", "count": 3}
    ]


@pytest.mark.asyncio
async def test_run_plugin_config_action_injects_progress_log(monkeypatch) -> None:
    feature = SimpleNamespace(
        key="logging_action",
        manifest={
            "permissions": [],
            "config_actions": [{"key": "make_item", "title": "生成"}],
        },
    )
    account = SimpleNamespace(id=7, proxy_id=None)
    logs: list[tuple[str, str, dict]] = []

    async def _log(level: str, message: str, **detail):
        logs.append((level, message, detail))

    monkeypatch.setattr(plugin_config_actions, "get_plugin", lambda key: LoggingConfigActionPlugin)

    result = await run_plugin_config_action(
        FakeDB(),
        account=account,
        feature=feature,
        action_key="make_item",
        effective_config={},
        current_config={},
        action_input={},
        installed_plugin=SimpleNamespace(manifest_json={}),
        log=_log,
    )

    assert result["config_patch"] == {"done": True}
    assert logs == [("info", "动作进度", {"step": "demo"})]


@pytest.mark.asyncio
async def test_create_plugin_config_action_job_writes_runtime_log_and_starts_task(monkeypatch) -> None:
    db = FakeDB()
    feature = SimpleNamespace(
        key="demo_action",
        manifest={"config_actions": [{"key": "make_item", "title": "生成"}]},
    )
    account = SimpleNamespace(id=7)
    scheduled = []

    def _create_task(coro):
        scheduled.append(coro)
        coro.close()
        return SimpleNamespace()

    monkeypatch.setattr(plugin_config_action_jobs.asyncio, "create_task", _create_task)

    job = await create_plugin_config_action_job(
        db,
        account=account,
        feature=feature,
        action_key="make_item",
        effective_config={"count": 1},
        current_config={"count": 2},
        action_input={"name": "题库"},
        installed_plugin=SimpleNamespace(manifest_json={}),
    )

    assert isinstance(job, PluginConfigActionJob)
    assert job.status == "queued"
    assert job.account_id == 7
    assert job.plugin_key == "demo_action"
    assert db.flushed is True
    assert db.commits == 1
    assert scheduled
    runtime_logs = [item for item in db.added if isinstance(item, RuntimeLog)]
    assert len(runtime_logs) == 1
    assert runtime_logs[0].message == "配置动作已排队"
    assert runtime_logs[0].detail["config_action_job_id"] == job.job_id
