"""第 21 轮：服务器参数元数据（中文名 + 英文原名 + 大白话说明 + 也在哪些页面可改）。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel

from app.api import rate_limit
from app.services import system_settings_meta as meta
from app.tests.test_system_settings import _FakeSettingsDB

_IOS_TARGETS = {
    meta.IOS_AI_GLOBAL,
    meta.IOS_EMERGENCY_STOP,
    meta.IOS_RUNTIME_TREE,
    meta.IOS_SOFTWARE_UPDATE,
    meta.IOS_SUDO,
    meta.IOS_TOTP,
}


def _flatten(value: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, item in value.items():
        path = f"{prefix}{key}"
        if isinstance(item, dict):
            out.update(_flatten(item, path + "."))
        else:
            out[path] = item
    return out


def _patch_keys(model: type[BaseModel], prefix: str = "") -> set[str]:
    keys: set[str] = set()
    for name, field in model.model_fields.items():
        annotation = field.annotation
        nested = next(
            (arg for arg in getattr(annotation, "__args__", ()) if isinstance(arg, type) and issubclass(arg, BaseModel)),
            None,
        )
        if nested is not None:
            keys |= _patch_keys(nested, f"{prefix}{name}.")
        else:
            keys.add(f"{prefix}{name}")
    return keys


async def _settings_from_empty_db(monkeypatch) -> dict[str, Any]:  # noqa: ANN001
    return await rate_limit.get_system_settings(_FakeSettingsDB(), SimpleNamespace(id=1))  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_every_get_key_has_metadata(monkeypatch) -> None:  # noqa: ANN001
    settings = await _settings_from_empty_db(monkeypatch)
    keys = set(_flatten(settings))
    described = {item["key"] for item in meta.SETTINGS_META}
    assert keys - described == set(), "新增服务器参数请在 system_settings_meta.SETTINGS_META 里补中文名与说明"
    assert described - keys == set(), "元数据里有 GET 不返回的键"


def test_every_patch_field_has_metadata() -> None:
    described = {item["key"] for item in meta.SETTINGS_META}
    assert _patch_keys(rate_limit._SettingsPatch) - described == set()


@pytest.mark.asyncio
async def test_metadata_defaults_match_server_defaults(monkeypatch) -> None:  # noqa: ANN001
    settings = _flatten(await _settings_from_empty_db(monkeypatch))
    # 依赖 .env / 运行时的键不比（command_prefix、回声防误触、AI 开关、登录安全阈值等）。
    skip_prefixes = ("login_security.", "app_update_target.")
    skip = {"command_prefix", "command_echo_guard_previous_messages", "ai_enabled"}
    for item in meta.SETTINGS_META:
        key = item["key"]
        if key in skip or key.startswith(skip_prefixes):
            continue
        assert settings[key] == item["default"], key


def test_metadata_shape_is_complete() -> None:
    group_ids = {group["id"] for group in meta.GROUPS}
    seen: set[str] = set()
    for item in meta.SETTINGS_META:
        key = item["key"]
        assert key not in seen, f"重复的键 {key}"
        seen.add(key)
        assert item["group"] in group_ids, key
        assert item["label"] and item["description"], key
        # 中文名必须是中文（不能只写英文原名）。
        assert any("\u4e00" <= ch <= "\u9fff" for ch in item["label"]), key
        assert item["type"] in {"bool", "integer", "string", "enum", "timezone", "list"}, key
        if item["type"] == "enum":
            assert item["options"], key
            assert item["default"] in {option["value"] for option in item["options"]}, key
        if not item["editable"]:
            assert item["read_only_reason"], key
        for location in item["also_in"]:
            assert location["label"], key
            assert location["ios_target"] is None or location["ios_target"] in _IOS_TARGETS, key
            if location["ios_target"]:
                assert location["ios_label"], key
    # 每个分组至少有一个参数。
    assert {item["group"] for item in meta.SETTINGS_META} == group_ids


@pytest.mark.asyncio
async def test_meta_endpoint_matches_response_model() -> None:
    payload = await rate_limit.get_system_settings_meta(SimpleNamespace(id=1))  # type: ignore[arg-type]
    parsed = rate_limit.SystemSettingsMetaOut.model_validate(payload)
    assert parsed.version == meta.META_VERSION
    keys = {param.key for param in parsed.params}
    assert "log_retention.trace_retention_days" in keys
    trace = next(param for param in parsed.params if param.key == "log_retention.trace_retention_days")
    assert trace.unit == "天" and trace.zero_means == "不自动删除"


def test_patch_schema_titles_show_chinese_and_original_key() -> None:
    schema = rate_limit._SettingsPatch.model_json_schema()
    log_schema = schema["$defs"]["_LogRetentionPatch"]["properties"]
    assert log_schema["runtime_log_retention_days"]["title"] == "运行日志保留天数 · runtime_log_retention_days"
    assert "0 = 不自动删除" in log_schema["runtime_log_retention_days"]["description"]
    assert schema["properties"]["command_prefix"]["title"] == "指令前缀 · command_prefix"


def test_ai_context_messages_points_to_ai_global_settings() -> None:
    item = meta.meta_for("ai_context_messages")
    assert item is not None
    assert any(loc["ios_target"] == meta.IOS_AI_GLOBAL for loc in item["also_in"])
    kill = meta.meta_for("kill_switch")
    assert kill is not None and kill["editable"] is False
