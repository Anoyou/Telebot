"""配置保存链路中的密文只在 hook 边界解密，不进入响应或日志。"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app import crypto
from app.api.features import _preserve_existing_sensitive_values, _sanitize_config
from app.db.models.feature import AccountFeature, Feature
from app.services import feature_service, plugin_config_actions
from app.services import plugin_config_hooks as hooks
from app.services import plugin_config_secrets as secrets
from app.settings import settings
from app.tests.test_plugin_platform_p0 import _FakeRedis
from app.worker.plugins import plugin_stats
from app.worker.plugins.base import Plugin

TOKEN = "fixture-private-credential"
SCHEMA = {
    "type": "object",
    "properties": {
        "token": {"type": "string"},
        "profiles": {"type": "array", "items": {
            "type": "object", "properties": {"access": {"type": "string", "x-sensitive": True}},
        }},
        "nested": {"type": "object", "properties": {"credential": {"format": "password"}}},
    },
}


class _HookPlugin(Plugin):
    key = "hook_secrets"
    display_name = "Hook secrets"
    seen = []

    async def on_config_validate(self, ctx, config, scope):
        self.seen.append((ctx.config, config, scope, ctx.account_id))
        assert ctx.config == config
        mode = config.get("mode")
        if mode == "slow":
            await asyncio.sleep(1)
        if mode == "boom":
            raise RuntimeError(config["token"])
        if mode == "echo_error":
            return {"errors": {"token": f"拒绝 {config['token']}"}}
        if mode == "nested_error":
            return {"errors": {"profiles": config["profiles"][0]["access"]}}
        if config.get("token") != TOKEN:
            return {"errors": {"token": "Token 无效"}}
        if mode == "normalize":
            return {"config": {**config, "title": "normalized"}}
        return None

    async def on_config_saved(self, ctx, config, scope):
        self.seen.append((ctx.config, config, scope, ctx.account_id))
        if config.get("mode") == "boom":
            raise RuntimeError(config["token"])


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(settings, "master_key", crypto.generate_master_key())
    monkeypatch.setattr(crypto, "_fernet", None)
    redis = _FakeRedis()
    import app.redis_client as redis_client

    monkeypatch.setattr(redis_client, "get_redis", lambda: redis)
    plugin_stats.get_recorder().reset_local()
    _HookPlugin.seen = []

    async def load(*args, **kwargs):
        return _HookPlugin

    # 使用真实上下文构造器；只替换插件发现及不需要的本地 data_dir。
    from app.worker.plugins import loader

    monkeypatch.setattr(loader, "_plugin_data_dir", lambda key: "/tmp")
    monkeypatch.setattr(plugin_config_actions, "load_plugin_class", load)

    class DB:
        async def get(self, *args):
            return None

    feature = SimpleNamespace(key=_HookPlugin.key, manifest={"permissions": [], "config_schema": SCHEMA})
    yield DB(), feature, redis
    plugin_stats.get_recorder().reset_local()


async def _validate(env, config, scope="account"):
    db, feature, _ = env
    return await hooks.run_config_validate(
        db, feature=feature, account=SimpleNamespace(id=2) if scope == "account" else None,
        config=config, scope=scope,
    )


@pytest.mark.parametrize("scope", ["account", "global"])
@pytest.mark.parametrize("incoming", [{"enabled": True}, {"enabled": True, "token": "***"}])
async def test_preserved_token_switch_save_and_saved_hook(env, scope, incoming):
    existing = secrets.encrypt_config_secrets({"token": TOKEN})
    preserved = _preserve_existing_sensitive_values(existing, incoming, schema=SCHEMA)
    outcome = await _validate(env, preserved, scope)
    assert outcome.handled and not outcome.errors
    assert _HookPlugin.seen[-1] == (
        {"token": TOKEN, "enabled": True}, {"token": TOKEN, "enabled": True},
        scope, 2 if scope == "account" else 0,
    )
    stored = secrets.encrypt_config_secrets(outcome.config, schema=SCHEMA)
    db, feature, _ = env
    task = await hooks.schedule_config_saved(
        db, feature=feature, account=SimpleNamespace(id=2) if scope == "account" else None,
        config=stored, scope=scope,
    )
    assert task is not None
    await task
    assert _HookPlugin.seen[-1][0] == _HookPlugin.seen[-1][1] == {"token": TOKEN, "enabled": True}
    assert preserved["token"] == existing["token"]
    assert secrets.is_secret_envelope(stored["token"])
    assert TOKEN not in str(_sanitize_config(stored, schema=SCHEMA))
    assert "secret:v1:" not in str(_sanitize_config(stored, schema=SCHEMA))


async def test_new_invalid_token_still_rejected(env):
    preserved = _preserve_existing_sensitive_values(
        secrets.encrypt_config_secrets({"token": TOKEN}), {"token": "invalid-new-value"}, schema=SCHEMA,
    )
    outcome = await _validate(env, preserved)
    assert outcome.handled and outcome.errors == {"token": "Token 无效"}


@pytest.mark.parametrize("scope", ["account", "global"])
async def test_nested_schema_secrets_normalization_remain_encrypted(env, monkeypatch, scope):
    raw = {"token": TOKEN, "nested": {"credential": "nested-value"},
           "profiles": [{"access": "schema-only-value"}], "mode": "normalize"}
    stored = secrets.encrypt_config_secrets(raw, schema=SCHEMA)
    preserved = _preserve_existing_sensitive_values(stored, {**stored, "enabled": True}, schema=SCHEMA)
    outcome = await _validate(env, preserved, scope)
    assert not outcome.errors and outcome.config["title"] == "normalized"
    assert _HookPlugin.seen[-1][0] == {**raw, "enabled": True}
    # 走真实 account/global 保存服务，仅替换 DB 与 builtin seed/IPC。
    schema = deepcopy(SCHEMA)
    schema["properties"].update({"mode": {}, "enabled": {}, "title": {}})
    for prop in schema["properties"].values():
        prop["level"] = scope
    feature = SimpleNamespace(key=_HookPlugin.key, manifest={"config_schema": schema})
    row = AccountFeature(account_id=2, feature_key=feature.key, enabled=True, config=stored)

    class DB:
        added = None

        async def get(self, model, key):
            return feature if model is Feature else None

        async def execute(self, query):
            return SimpleNamespace(scalar_one_or_none=lambda: row)

        def add(self, value):
            self.added = value

        async def flush(self):
            pass

        async def refresh(self, value):
            pass

    async def seed(db):
        pass

    monkeypatch.setattr(feature_service, "seed_builtin_features", seed)
    db = DB()
    if scope == "account":
        saved = await feature_service.set_account_feature(
            db, 2, feature.key, True, outcome.config, notify=False, commit=False,
        )
        persisted = saved.config
    else:
        persisted = await feature_service.set_plugin_global_config(
            db, feature.key, outcome.config, notify=False, commit=False,
        )
        assert db.added.config == persisted
    assert secrets.is_secret_envelope(persisted["nested"]["credential"])
    assert secrets.is_secret_envelope(persisted["profiles"][0]["access"])
    assert secrets.decrypt_config_secrets(persisted, schema=SCHEMA, strict=True) == outcome.config
    public = str(_sanitize_config(persisted, schema=SCHEMA))
    assert all(value not in public for value in (TOKEN, "nested-value", "schema-only-value", "secret:v1:"))
    task = await hooks.schedule_config_saved(
        db, feature=feature, account=None, config=persisted, scope=scope,
    )
    assert task is not None
    await task
    assert _HookPlugin.seen[-1][0] == _HookPlugin.seen[-1][1] == outcome.config


@pytest.mark.parametrize("mode,reason", [("slow", "timeout"), ("boom", "RuntimeError")])
async def test_failure_open_preserves_input_without_leaking_stats(env, monkeypatch, caplog, mode, reason):
    monkeypatch.setattr(hooks, "hook_timeout_seconds", lambda hook: 0.01)
    stored = secrets.encrypt_config_secrets({"token": TOKEN, "mode": mode})
    outcome = await _validate(env, stored)
    assert outcome.failed == reason and not outcome.errors and outcome.config == stored
    assert _HookPlugin.seen[-1][1]["token"] == TOKEN
    stats = await plugin_stats.read_plugin_stats(env[2], _HookPlugin.key)
    assert TOKEN not in str(stats) + caplog.text
    assert stored["token"] not in str(stats) + caplog.text


@pytest.mark.parametrize("encrypted", [True, False])
async def test_hook_error_response_redacts_secret(env, caplog, encrypted):
    config = {"token": TOKEN, "mode": "echo_error"}
    if encrypted:
        config = secrets.encrypt_config_secrets(config)
    outcome = await _validate(env, config)
    text = hooks.format_validate_errors(outcome.errors) + str(outcome.errors) + caplog.text
    assert outcome.errors and TOKEN not in text and "secret:v1:" not in text


async def test_schema_only_nested_error_is_redacted(env):
    stored = secrets.encrypt_config_secrets(
        {"token": TOKEN, "profiles": [{"access": "schema-only-value"}], "mode": "nested_error"},
        schema=SCHEMA,
    )
    outcome = await _validate(env, stored)
    assert outcome.errors and "schema-only-value" not in hooks.format_validate_errors(outcome.errors)


async def test_unreadable_envelope_rejects_validate_and_skips_saved(env, caplog):
    config = {"token": "secret:v1:broken-private-envelope"}
    outcome = await _validate(env, config)
    assert outcome.errors and not outcome.handled
    db, feature, _ = env
    task = await hooks.schedule_config_saved(db, feature=feature, account=None, config=config, scope="global")
    assert task is None and not _HookPlugin.seen
    assert config["token"] not in str(outcome.errors) + caplog.text


async def test_saved_exception_does_not_expose_plain_secret_in_stats(env, caplog):
    db, feature, redis = env
    config = secrets.encrypt_config_secrets({"token": TOKEN, "mode": "boom"})
    task = await hooks.schedule_config_saved(db, feature=feature, account=None, config=config, scope="global")
    assert task is not None
    await task
    assert _HookPlugin.seen[-1][1]["token"] == TOKEN
    stats = await plugin_stats.read_plugin_stats(redis, _HookPlugin.key)
    assert TOKEN not in str(stats) + caplog.text
    assert config["token"] not in str(stats) + caplog.text
