"""功能与功能矩阵 schema。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from ..db.models.feature import Feature


class FeatureInfo(BaseModel):
    key: str
    display_name: str
    is_builtin: bool
    version: str | None = None
    config_schema: dict[str, Any] | None = None
    experimental: bool = False

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_feature(cls, f: Feature) -> FeatureInfo:
        manifest = getattr(f, "manifest", None) or {}
        return cls(
            key=f.key,
            display_name=f.display_name,
            is_builtin=f.is_builtin,
            version=f.version,
            config_schema=manifest.get("config_schema"),
            experimental=bool(
                manifest.get("x-experimental") or manifest.get("experimental")
            ),
        )


class AccountFeatureToggle(BaseModel):
    """启停某账号的某功能。"""
    enabled: bool
    config: dict[str, Any] | None = None


class AccountFeatureConfigUpdate(BaseModel):
    """仅更新账号级配置（不改变 enabled 状态）。"""
    config: dict[str, Any]


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


class PluginGlobalConfigResponse(BaseModel):
    """global config 响应。"""
    plugin_key: str
    config: dict[str, Any]  # 合并后的最终配置
    global_config: dict[str, Any] | None = None  # 仅 global 字段


class PluginGlobalConfigUpdate(BaseModel):
    """更新 global config 的请求体。"""
    config: dict[str, Any]


class ConfigValidationError(BaseModel):
    """JSON Schema 验证错误。"""
    field: str
    message: str


class ConfigValidationResponse(BaseModel):
    """JSON Schema 验证结果。"""
    valid: bool
    errors: list[ConfigValidationError] = []
