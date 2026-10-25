"""插件配置的可选字段与字段顺序。"""

from __future__ import annotations

from app.services import feature_service
from app.services.remote_plugin_service import PluginMetadataSchema
from app.worker.plugins.manifest import PROPERTY_ORDER_KEY, Manifest, with_property_order


def _schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "zeta": {"type": "string", "default": ""},
            "alpha": {"type": "integer", "default": 30},
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"title": {"type": "string"}, "enabled": {"type": "boolean"}},
                },
            },
        },
        "required": [],
    }


def test_with_property_order_records_declared_order_recursively() -> None:
    raw = _schema()
    annotated = with_property_order(raw)
    assert annotated[PROPERTY_ORDER_KEY] == ["zeta", "alpha", "items"]
    assert annotated["properties"]["items"]["items"][PROPERTY_ORDER_KEY] == ["title", "enabled"]
    assert PROPERTY_ORDER_KEY not in raw  # 不修改插件自己的 CONFIG_SCHEMA


def test_manifest_and_remote_metadata_keep_property_order() -> None:
    manifest = Manifest(key="demo", display_name="Demo", config_schema=_schema())
    assert manifest.config_schema[PROPERTY_ORDER_KEY] == ["zeta", "alpha", "items"]
    assert manifest.to_dict()["config_schema"][PROPERTY_ORDER_KEY] == ["zeta", "alpha", "items"]
    assert Manifest(key="empty", display_name="Empty").config_schema is None
    meta = PluginMetadataSchema(name="demo", version="1.0.0", config_schema=_schema())
    assert meta.config_schema is not None
    assert meta.config_schema[PROPERTY_ORDER_KEY] == ["zeta", "alpha", "items"]


def test_cleared_optional_value_falls_back_to_default() -> None:
    schema = _schema()
    nullable = {"type": ["integer", "null"], "default": None}
    schema["properties"]["nullable_limit"] = nullable
    cleaned = feature_service.apply_required_config_defaults(
        {"alpha": None, "zeta": "", "nullable_limit": None}, schema
    )
    # 不接受 null 的可选字段被清空：不提交，运行时用默认值；允许 null 的字段保留 null。
    assert cleaned == {"zeta": "", "nullable_limit": None}
    assert feature_service.validate_config_against_schema(cleaned, schema).valid


def test_optional_fields_are_not_required_on_partial_save() -> None:
    schema = _schema()
    assert feature_service.validate_config_against_schema({}, schema).valid
    strict = {**schema, "required": ["token"], "properties": {**schema["properties"], "token": {"type": "string"}}}
    result = feature_service.validate_config_against_schema(
        feature_service.apply_required_config_defaults({}, strict), strict
    )
    assert not result.valid
    assert result.errors[0].field == "token"
