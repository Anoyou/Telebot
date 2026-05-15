"""Config Bundle 导出 / dry-run 单元测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.schemas.config_bundle import (
    ConfigBundleCommandLinkItem,
    ConfigBundleExport,
    ConfigBundleFeatureItem,
    ConfigBundleRuleItem,
    ConfigBundleSourceAccount,
)
from app.services.config_bundle_service import (
    BundleTooLarge,
    assert_bundle_size,
    build_config_bundle,
    compare_bundles,
)


def test_build_config_bundle_redacts_sensitive_fields() -> None:
    account = SimpleNamespace(id=1, display_name="alpha", phone="+100")
    feature_rows = [
        SimpleNamespace(
            feature_key="codex_image",
            enabled=True,
            config={
                "access_token": "tok",
                "safe": 1,
                "nested": {"bot_token": "secret", "keep": 2},
            },
        )
    ]
    rule_rows = [
        SimpleNamespace(
            feature_key="scheduler",
            name="night",
            enabled=True,
            priority=10,
            config={"chat_id": 123, "provider": "p1", "command": "send", "secret": "hide"},
        )
    ]
    command_link_rows = [
        (
            SimpleNamespace(template_id=7),
            SimpleNamespace(id=7, name="ping", aliases=["p"], type="reply_text"),
        )
    ]

    bundle = build_config_bundle(account, feature_rows, rule_rows, command_link_rows)

    assert bundle.source_account.id == 1
    assert bundle.features["codex_image"].config == {"safe": 1, "nested": {"keep": 2}}
    assert bundle.rules[0].config == {"chat_id": 123, "provider": "p1", "command": "send"}
    assert bundle.command_links[0].template_name == "ping"


def test_assert_bundle_size_rejects_over_1mb() -> None:
    bundle = ConfigBundleExport(
        source_account=ConfigBundleSourceAccount(id=1, label="alpha"),
        features={
            "large": ConfigBundleFeatureItem(
                feature_key="large",
                enabled=True,
                config={"blob": "x" * 1_050_000},
            )
        },
        rules=[],
        command_links=[],
    )

    with pytest.raises(BundleTooLarge) as exc_info:
        assert_bundle_size(bundle)
    assert exc_info.value.size_bytes > 1_048_576


def test_compare_bundles_returns_add_skip_and_conflict() -> None:
    source = ConfigBundleExport(
        source_account=ConfigBundleSourceAccount(id=1, label="src"),
        features={
            "auto_reply": ConfigBundleFeatureItem(
                feature_key="auto_reply",
                enabled=True,
                config={"scope": "all", "chat_id": 1},
            ),
            "forward": ConfigBundleFeatureItem(
                feature_key="forward",
                enabled=True,
                config={"provider": "A"},
            ),
        },
        rules=[
            ConfigBundleRuleItem(
                feature_key="auto_reply",
                name="hello",
                enabled=True,
                priority=100,
                config={"chat_id": 111, "provider": "x", "command": "say"},
            ),
            ConfigBundleRuleItem(
                feature_key="scheduler",
                name="job",
                enabled=True,
                priority=50,
                config={"command": "run"},
            ),
        ],
        command_links=[
            ConfigBundleCommandLinkItem(
                template_id=1,
                template_name="reply_hello",
                aliases=[],
                type="reply_text",
                enabled=True,
            ),
            ConfigBundleCommandLinkItem(
                template_id=2,
                template_name="missing_cmd",
                aliases=[],
                type="reply_text",
                enabled=True,
            ),
            ConfigBundleCommandLinkItem(
                template_id=3,
                template_name="changed_cmd",
                aliases=["a"],
                type="reply_text",
                enabled=True,
            ),
        ],
    )
    target = ConfigBundleExport(
        source_account=ConfigBundleSourceAccount(id=2, label="dst"),
        features={
            "auto_reply": ConfigBundleFeatureItem(
                feature_key="auto_reply",
                enabled=True,
                config={"scope": "all", "chat_id": 1},
            ),
            "forward": ConfigBundleFeatureItem(
                feature_key="forward",
                enabled=True,
                config={"provider": "B"},
            ),
        },
        rules=[
            ConfigBundleRuleItem(
                feature_key="auto_reply",
                name="hello",
                enabled=True,
                priority=100,
                config={"chat_id": 111, "provider": "x", "command": "say"},
            )
        ],
        command_links=[
            ConfigBundleCommandLinkItem(
                template_id=1,
                template_name="reply_hello",
                aliases=[],
                type="reply_text",
                enabled=True,
            )
        ],
    )

    report = compare_bundles(
        source,
        target,
        available_features={
            "auto_reply": "Auto Reply",
            "forward": "Forward",
            "scheduler": "Scheduler",
        },
        available_command_templates={
            "reply_hello": {"template_name": "reply_hello", "aliases": [], "type": "reply_text"},
            "changed_cmd": {"template_name": "changed_cmd", "aliases": ["b"], "type": "reply_text"},
        },
    )

    assert report.counts.add == 1
    assert report.counts.skip == 3
    assert report.counts.conflict == 3
    assert any(i.entity == "feature" and i.key == "forward" and i.action == "conflict" for i in report.items)
    assert any(i.entity == "rule" and i.key == "scheduler:job" and i.action == "add" for i in report.items)
    assert any(i.entity == "command_link" and i.key == "missing_cmd" and i.action == "conflict" for i in report.items)
