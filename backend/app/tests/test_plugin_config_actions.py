from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.schemas.feature import FeatureInfo
from app.services import plugin_config_actions
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


class FakeDB:
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
    monkeypatch.setattr(plugin_config_actions, "get_plugin", lambda key: DemoConfigActionPlugin)

    result = await run_plugin_config_action(
        FakeDB(),
        account=account,
        feature=feature,
        action_key="make_item",
        effective_config={"count": 1, "api_token": "real-token"},
        current_config={"count": 3, "api_token": "••••••••••••••••"},
        action_input={"name": "第一组"},
    )

    assert result["message"] == "已生成"
    assert result["config_patch"]["items"] == [
        {"enabled": True, "name": "第一组", "count": 3}
    ]
