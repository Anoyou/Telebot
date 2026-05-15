"""Config Bundle 导出 / dry-run schema。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ConfigBundleSourceAccount(BaseModel):
    id: int
    label: str


class ConfigBundleFeatureItem(BaseModel):
    feature_key: str
    enabled: bool
    config: dict[str, Any] = Field(default_factory=dict)


class ConfigBundleRuleItem(BaseModel):
    feature_key: str
    name: str
    enabled: bool
    priority: int
    config: dict[str, Any] = Field(default_factory=dict)


class ConfigBundleCommandLinkItem(BaseModel):
    template_id: int
    template_name: str
    aliases: list[str] = Field(default_factory=list)
    type: str
    enabled: bool = True


class ConfigBundleExport(BaseModel):
    version: Literal["1"] = "1"
    source_account: ConfigBundleSourceAccount
    rules: list[ConfigBundleRuleItem] = Field(default_factory=list)
    features: dict[str, ConfigBundleFeatureItem] = Field(default_factory=dict)
    command_links: list[ConfigBundleCommandLinkItem] = Field(default_factory=list)


class ConfigBundleDiffCounts(BaseModel):
    add: int = 0
    skip: int = 0
    conflict: int = 0


class ConfigBundleDiffItem(BaseModel):
    entity: Literal["feature", "rule", "command_link"]
    key: str
    action: Literal["add", "skip", "conflict"]
    fields: list[str] = Field(default_factory=list)
    note: str | None = None


class ConfigBundleDryRunResponse(BaseModel):
    version: Literal["1"] = "1"
    source_account: ConfigBundleSourceAccount
    target_account: ConfigBundleSourceAccount
    size_bytes: int
    counts: ConfigBundleDiffCounts
    items: list[ConfigBundleDiffItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
