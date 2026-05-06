"""功能与功能矩阵 schema。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class FeatureInfo(BaseModel):
    key: str
    display_name: str
    is_builtin: bool
    version: str | None = None

    model_config = ConfigDict(from_attributes=True)


class AccountFeatureToggle(BaseModel):
    """启停某账号的某功能。"""
    enabled: bool
    config: dict[str, Any] | None = None


class AccountFeatureItem(BaseModel):
    feature_key: str
    enabled: bool
    state: str
    last_error: str | None = None
    config: dict[str, Any]

    model_config = ConfigDict(from_attributes=True)


class FeatureMatrixCell(BaseModel):
    """功能矩阵的单元格状态。"""
    state: str  # active | failed | disabled


class FeatureMatrixRow(BaseModel):
    id: int
    name: str
    features: dict[str, str]  # feature_key -> state


class FeatureMatrixResponse(BaseModel):
    features: list[FeatureInfo]
    accounts: list[FeatureMatrixRow]
