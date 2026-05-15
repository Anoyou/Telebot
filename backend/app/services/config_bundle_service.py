"""Config Bundle 导出 / dry-run 的纯函数工具。"""

from __future__ import annotations

import json
from typing import Any

from ..schemas.config_bundle import (
    ConfigBundleCommandLinkItem,
    ConfigBundleDiffCounts,
    ConfigBundleDiffItem,
    ConfigBundleDryRunResponse,
    ConfigBundleExport,
    ConfigBundleFeatureItem,
    ConfigBundleRuleItem,
    ConfigBundleSourceAccount,
)

MAX_BUNDLE_BYTES = 1_048_576

_SENSITIVE_KEY_HINTS = (
    "api_key",
    "access_token",
    "bot_token",
    "codex_token",
    "session",
    "secret",
    "password",
    "totp",
)


class BundleTooLarge(ValueError):
    """导出 / 上传 bundle 超过 1MB。"""

    def __init__(self, size_bytes: int) -> None:
        super().__init__(f"bundle too large: {size_bytes}")
        self.size_bytes = size_bytes


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return lowered.endswith("_enc") or any(hint in lowered for hint in _SENSITIVE_KEY_HINTS)


def sanitize_bundle_value(value: Any) -> Any:
    """递归移除 bundle 里的敏感字段。"""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and _is_sensitive_key(key):
                continue
            out[str(key) if not isinstance(key, str) else key] = sanitize_bundle_value(item)
        return out
    if isinstance(value, list):
        return [sanitize_bundle_value(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_bundle_value(item) for item in value]
    return value


def bundle_json_bytes(bundle: ConfigBundleExport) -> bytes:
    """把 bundle 序列化成紧凑 JSON。"""
    payload = bundle.model_dump(mode="json")
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def assert_bundle_size(bundle: ConfigBundleExport) -> bytes:
    """返回序列化字节；超限则抛 ``BundleTooLarge``。"""
    data = bundle_json_bytes(bundle)
    if len(data) > MAX_BUNDLE_BYTES:
        raise BundleTooLarge(len(data))
    return data


def _config_diff_fields(source_cfg: dict[str, Any], target_cfg: dict[str, Any]) -> list[str]:
    keys = sorted(set(source_cfg) | set(target_cfg))
    return [key for key in keys if source_cfg.get(key) != target_cfg.get(key)]


def _build_source_account(account) -> ConfigBundleSourceAccount:
    label = (
        getattr(account, "display_name", None)
        or getattr(account, "phone", None)
        or f"account-{account.id}"
    )
    return ConfigBundleSourceAccount(id=int(account.id), label=str(label))


def build_config_bundle(
    account,
    feature_rows,
    rule_rows,
    command_link_rows,
) -> ConfigBundleExport:
    """把账号及其配置行拼成 bundle。"""
    features: dict[str, ConfigBundleFeatureItem] = {}
    for row in sorted(feature_rows, key=lambda r: str(r.feature_key)):
        features[str(row.feature_key)] = ConfigBundleFeatureItem(
            feature_key=str(row.feature_key),
            enabled=bool(row.enabled),
            config=sanitize_bundle_value(dict(row.config or {})),
        )

    rules = [
        ConfigBundleRuleItem(
            feature_key=str(row.feature_key),
            name=str(row.name),
            enabled=bool(row.enabled),
            priority=int(row.priority),
            config=sanitize_bundle_value(dict(row.config or {})),
        )
        for row in sorted(
            rule_rows,
            key=lambda r: (str(r.feature_key), -int(r.priority), str(r.name)),
        )
    ]

    command_links = [
        ConfigBundleCommandLinkItem(
            template_id=int(tpl.id),
            template_name=str(tpl.name),
            aliases=list(tpl.aliases or []),
            type=str(tpl.type),
            enabled=True,
        )
        for _link_row, tpl in sorted(command_link_rows, key=lambda item: str(item[1].name))
    ]

    return ConfigBundleExport(
        source_account=_build_source_account(account),
        rules=rules,
        features=features,
        command_links=command_links,
    )


def _index_bundle(bundle: ConfigBundleExport) -> dict[str, dict[str, Any]]:
    features = {k: v.model_dump(mode="json") for k, v in bundle.features.items()}
    rules = {
        f"{item.feature_key}:{item.name}": item.model_dump(mode="json")
        for item in bundle.rules
    }
    commands = {item.template_name: item.model_dump(mode="json") for item in bundle.command_links}
    return {"features": features, "rules": rules, "command_links": commands}


def compare_bundles(
    source: ConfigBundleExport,
    target: ConfigBundleExport,
    *,
    available_features: dict[str, str],
    available_command_templates: dict[str, dict[str, Any]],
) -> ConfigBundleDryRunResponse:
    """把 source bundle 和 target bundle 做 dry-run 对比。"""
    src = _index_bundle(source)
    dst = _index_bundle(target)
    items: list[ConfigBundleDiffItem] = []
    counts = ConfigBundleDiffCounts()
    warnings: list[str] = []

    for feature_key, item in src["features"].items():
        if feature_key not in available_features:
            items.append(
                ConfigBundleDiffItem(
                    entity="feature",
                    key=feature_key,
                    action="conflict",
                    fields=["feature_key"],
                    note="feature not registered",
                )
            )
            counts.conflict += 1
            continue
        current = dst["features"].get(feature_key)
        if current is None:
            items.append(ConfigBundleDiffItem(entity="feature", key=feature_key, action="add"))
            counts.add += 1
            continue
        changed = []
        if item["enabled"] != current["enabled"]:
            changed.append("enabled")
        changed.extend(_config_diff_fields(dict(item["config"]), dict(current["config"])))
        if changed:
            items.append(
                ConfigBundleDiffItem(
                    entity="feature",
                    key=feature_key,
                    action="conflict",
                    fields=sorted(dict.fromkeys(changed)),
                )
            )
            counts.conflict += 1
        else:
            items.append(ConfigBundleDiffItem(entity="feature", key=feature_key, action="skip"))
            counts.skip += 1

    for rule_key, item in src["rules"].items():
        feature_key = str(item["feature_key"])
        if feature_key not in available_features:
            items.append(
                ConfigBundleDiffItem(
                    entity="rule",
                    key=rule_key,
                    action="conflict",
                    fields=["feature_key"],
                    note="feature not registered",
                )
            )
            counts.conflict += 1
            continue
        current = dst["rules"].get(rule_key)
        if current is None:
            items.append(ConfigBundleDiffItem(entity="rule", key=rule_key, action="add"))
            counts.add += 1
            continue
        changed = []
        if item["enabled"] != current["enabled"]:
            changed.append("enabled")
        if int(item["priority"]) != int(current["priority"]):
            changed.append("priority")
        changed.extend(_config_diff_fields(dict(item["config"]), dict(current["config"])))
        if changed:
            items.append(
                ConfigBundleDiffItem(
                    entity="rule",
                    key=rule_key,
                    action="conflict",
                    fields=sorted(dict.fromkeys(changed)),
                )
            )
            counts.conflict += 1
        else:
            items.append(ConfigBundleDiffItem(entity="rule", key=rule_key, action="skip"))
            counts.skip += 1

    for template_name, item in src["command_links"].items():
        available = available_command_templates.get(template_name)
        if available is None:
            items.append(
                ConfigBundleDiffItem(
                    entity="command_link",
                    key=template_name,
                    action="conflict",
                    fields=["template_name"],
                    note="command template not registered",
                )
            )
            counts.conflict += 1
            continue
        changed = []
        for field in ("template_name", "aliases", "type"):
            if item.get(field) != available.get(field):
                changed.append(field)
        if changed:
            items.append(
                ConfigBundleDiffItem(
                    entity="command_link",
                    key=template_name,
                    action="conflict",
                    fields=sorted(dict.fromkeys(changed)),
                    note="template metadata changed",
                )
            )
            counts.conflict += 1
            continue
        current = dst["command_links"].get(template_name)
        if current is None:
            items.append(
                ConfigBundleDiffItem(entity="command_link", key=template_name, action="add")
            )
            counts.add += 1
        else:
            items.append(
                ConfigBundleDiffItem(entity="command_link", key=template_name, action="skip")
            )
            counts.skip += 1

    return ConfigBundleDryRunResponse(
        source_account=source.source_account,
        target_account=target.source_account,
        size_bytes=len(bundle_json_bytes(source)),
        counts=counts,
        items=items,
        warnings=warnings,
    )
