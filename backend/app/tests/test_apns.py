from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from fastapi import FastAPI
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import apns as api
from app.crypto import decrypt_str
from app.db.apns_events import OUTBOX_PREFIX, enqueue
from app.db.models.system import SystemSetting
from app.db.models.system_agent import SystemAgentAction, SystemAgentRun, SystemAgentSession
from app.db.models.user import WebUser
from app.db.session import get_db
from app.schemas.apns import ApnsConfigPatch, ApnsDeviceRegister
from app.scripts.rekey import rekey_database
from app.services import apns
from app.services.redactor import redact_text, redact_value

ORIGIN_HASH = hashlib.sha256(b"https://example.test").hexdigest()


def pem(key=None):
    key = key or ec.generate_private_key(ec.SECP256R1())
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()).decode()


@pytest.fixture
async def db_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'apns.db'}")
    async with engine.begin() as connection:
        for model in (SystemSetting, WebUser, SystemAgentSession, SystemAgentAction, SystemAgentRun):
            await connection.run_sync(model.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add(WebUser(id=1, username="admin", password_hash="test", pwd_version=0))
        await db.commit()
    yield factory
    await engine.dispose()


async def configured(db):
    return await apns.update_config(db, ApnsConfigPatch(
        enabled=True, team_id="ABCDEFGHIJ", key_id="KLMNOPQRST",
        bundle_id="com.telepilot.personal", environment="production", auth_key_p8=pem(),
    ))


def device(**overrides):
    values = dict(device_token="a" * 64, device_id="installation-1", bundle_id="com.telepilot.personal",
                  environment="production", categories=["assistant_approval", "assistant_result", "account_alert"],
                  server_origin_sha256=ORIGIN_HASH)
    values.update(overrides)
    return ApnsDeviceRegister(**values)


@pytest.mark.asyncio
async def test_config_and_devices_encrypt_and_isolate(db_factory):
    async with db_factory() as db:
        assert not (await apns.get_config(db))["enabled"]
        assert await db.get(SystemSetting, apns.CONFIG_KEY) is None
        public = await configured(db)
        assert public["has_auth_key"] and "auth_key_enc" not in public
        stored = await db.get(SystemSetting, apns.CONFIG_KEY)
        assert "PRIVATE KEY" in decrypt_str(stored.value["auth_key_enc"])
        result = await apns.register_device(db, 1, device())
        assert "device_token" not in result and result["expires_at"]
        await db.commit()
        rows = await apns._device_rows(db)
        assert decrypt_str(rows[0].value["device_token_enc"]) == "a" * 64
        assert await apns.list_devices(db, 2) == []
        assert not await apns.unregister_device(db, 2, "installation-1")
        with pytest.raises(ValueError, match="其他用户"):
            await apns.register_device(db, 2, device())
        with pytest.raises(ValueError, match="不匹配"):
            await apns.register_device(db, 1, device(environment="sandbox"))


@pytest.mark.asyncio
async def test_default_production_config_is_persisted(db_factory):
    async with db_factory() as db:
        result = await apns.update_config(db, ApnsConfigPatch(
            enabled=True, team_id="ABCDEFGHIJ", key_id="KLMNOPQRST",
            bundle_id="com.telepilot.personal", auth_key_p8=pem(),
        ))
        assert result["environment"] == "production"
        assert (await apns._raw_config(db))["environment"] == "production"


@pytest.mark.asyncio
async def test_disabled_configuration_does_not_enqueue_or_open_network(db_factory, monkeypatch):
    client = AsyncMock(side_effect=AssertionError("disabled push must never open a network client"))
    monkeypatch.setattr(apns.httpx, "AsyncClient", client)
    async with db_factory() as db:
        await apns.register_device(db, 1, device())
        await db.run_sync(lambda sync: enqueue(sync.connection(), kind="assistant_result", user_id=1))
        await db.commit()
        assert not list(await db.scalars(select(SystemSetting).where(SystemSetting.key.like(OUTBOX_PREFIX + "%"))))
        result = await apns.send_test(db, 1, "installation-1")
        assert not result.ok
    assert await apns.drain_outbox_once(db_factory) == 0
    assert not client.called


@pytest.mark.asyncio
async def test_token_rotation_and_unregister_remove_all_installation_bindings(db_factory):
    async with db_factory() as db:
        await configured(db)
        await apns.register_device(db, 1, device())
        await db.commit()
        await apns.register_device(db, 1, device(device_token="b" * 64))
        await db.commit()
        rows = await apns._device_rows(db)
        assert len(rows) == 1 and decrypt_str(rows[0].value["device_token_enc"]) == "b" * 64
        # Simulate duplicate legacy registrations produced by concurrent rotations.
        db.add(SystemSetting(key=apns.DEVICE_PREFIX + "legacy-duplicate", value=dict(rows[0].value)))
        await db.commit()
        assert await apns.unregister_device(db, 1, "installation-1")
        await db.commit()
        assert not await apns._device_rows(db)


@pytest.mark.parametrize("change", ["subscription", "password"])
@pytest.mark.asyncio
async def test_delivery_reloads_concurrent_consent_and_password(db_factory, monkeypatch, change):
    post = AsyncMock(side_effect=AssertionError("unsubscribed device must not receive a notification"))
    monkeypatch.setattr(apns, "_post", post)
    async with db_factory() as first:
        await configured(first)
        await apns.register_device(first, 1, device())
        await first.commit()
        stale = await apns._device_rows(first)
        cached_user = await first.get(WebUser, 1)
        assert cached_user.pwd_version == 0
        async with db_factory() as second:
            if change == "subscription":
                await apns.register_device(second, 1, device(categories=[]))
            else:
                user = await second.get(WebUser, 1)
                user.pwd_version = 1
            await second.commit()
        result = await apns._deliver(first, rows=stale, kind="assistant_result", event_id="run-1")
        assert result.accepted == 0
        assert not post.called


@pytest.mark.asyncio
async def test_cleanup_does_not_delete_a_concurrently_renewed_lease(db_factory, monkeypatch):
    async with db_factory() as db:
        await configured(db)
        await apns.register_device(db, 1, device())
        await db.commit()
        row = (await apns._device_rows(db))[0]
        row.value = dict(row.value, expires_at=(datetime.now(UTC) - timedelta(seconds=1)).isoformat())
        await db.commit()
    original_scan = apns._device_rows
    renewed = False

    async def scan_then_renew(db):
        nonlocal renewed
        rows = await original_scan(db)
        if not renewed:
            renewed = True
            async with db_factory() as registering:
                await apns.register_device(registering, 1, device())
                await registering.commit()
        return rows

    # The scan observes an expired lease; another request renews it before deletion.
    monkeypatch.setattr(apns, "_device_rows", scan_then_renew)
    assert await apns.drain_outbox_once(db_factory) == 0
    async with db_factory() as db:
        rows = await original_scan(db)
        assert len(rows) == 1 and apns._lease_valid(rows[0].value)


@pytest.mark.asyncio
async def test_queue_capacity_keeps_old_events_and_drops_newest(db_factory):
    async with db_factory() as db:
        await configured(db)
        await db.execute(SystemSetting.__table__.insert(), [
            {"key": f"{OUTBOX_PREFIX}{number:04}", "value": {"event_id": f"event-{number}"}}
            for number in range(1000)
        ])
        await db.run_sync(lambda sync: enqueue(sync.connection(), kind="assistant_result",
                                               event_id="newest", user_id=1))
        await db.commit()
        rows = list(await db.scalars(select(SystemSetting).where(SystemSetting.key.like(OUTBOX_PREFIX + "%"))))
        assert len(rows) == 1000
        assert {row.value["event_id"] for row in rows} == {f"event-{number}" for number in range(1000)}


@pytest.mark.parametrize("key", [ec.generate_private_key(ec.SECP384R1()),
                                rsa.generate_private_key(public_exponent=65537, key_size=2048)])
def test_only_p256_auth_key(key):
    with pytest.raises(ValueError, match="ES256"):
        apns._validate_auth_key(pem(key))


@pytest.mark.parametrize("token", ["a" * 65, "z" * 64, "a" * 62])
def test_invalid_device_token_rejected(token):
    with pytest.raises(ValidationError):
        device(device_token=token)


@pytest.mark.asyncio
async def test_provider_expiration_retries_once():
    config = dict(team_id="ABCDEFGHIJ", key_id="KLMNOPQRST", bundle_id="com.telepilot.personal",
                  environment="production")
    seen = []

    def handler(request):
        seen.append(request)
        assert request.headers["apns-topic"] == config["bundle_id"]
        assert len(request.headers["apns-collapse-id"]) == 64
        return httpx.Response(403, json={"reason": "ExpiredProviderToken"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await apns._post(client, config=config, private_key=pem(), token="a" * 64,
                                  payload={"aps": {"alert": "test"}}, collapse_key="event-1")
    assert result == (False, "ExpiredProviderToken", False)
    assert len(seen) == 2


@pytest.mark.parametrize("status,reason,removed", [
    (200, None, False), (410, "Unregistered", True), (410, "ExpiredToken", True),
    (400, "BadDeviceToken", False), (403, "InvalidProviderToken", False),
    (500, "InternalServerError", False),
])
@pytest.mark.asyncio
async def test_delivery_cleans_only_410_and_payload_is_private(db_factory, monkeypatch, status, reason, removed):
    captured = []

    async def post(client, **kwargs):
        captured.append(kwargs["payload"])
        return status == 200, reason, status == 410

    monkeypatch.setattr(apns, "_post", post)
    async with db_factory() as db:
        await configured(db)
        await apns.register_device(db, 1, device())
        await db.commit()
        result = await apns._deliver(db, rows=await apns._device_rows(db), kind="assistant_approval",
                                     event_id="approval-1")
        await db.commit()
        assert result.unregistered == int(removed)
        assert bool(await apns._device_rows(db)) != removed
    payload = captured[0]
    assert payload["destination"] == "action" and payload["action_id"] == "approval-1"
    assert payload["server_origin_sha256"] == ORIGIN_HASH
    assert "telepilot" not in payload
    assert "a" * 64 not in json.dumps(payload)


@pytest.mark.asyncio
async def test_lease_and_password_revocation_stop_delivery(db_factory, monkeypatch):
    monkeypatch.setattr(apns, "_post", AsyncMock())
    async with db_factory() as db:
        await configured(db)
        await apns.register_device(db, 1, device())
        await db.commit()
        user = await db.get(WebUser, 1)
        user.pwd_version = 1
        await db.commit()
        await apns._deliver(db, rows=await apns._device_rows(db), kind="test", event_id="test")
        await db.commit()
        assert not await apns._device_rows(db)
    assert not apns._post.called
    assert not apns._lease_valid({"expires_at": (datetime.now(UTC) - timedelta(days=1)).isoformat()})


@pytest.mark.asyncio
async def test_queue_commit_rollback_and_resume_across_process(db_factory, monkeypatch):
    monkeypatch.setattr(apns, "_post", AsyncMock(return_value=(True, None, False)))
    async with db_factory() as db:
        await configured(db)
        await apns.register_device(db, 1, device())
        await db.commit()
        await db.run_sync(lambda sync: enqueue(sync.connection(), kind="assistant_result", event_id="run-1",
                                               user_id=1, session_id="session-1"))
        await db.rollback()
        assert not list(await db.scalars(select(SystemSetting).where(SystemSetting.key.like(OUTBOX_PREFIX + "%"))))
        await db.run_sync(lambda sync: enqueue(sync.connection(), kind="assistant_result", event_id="run-1",
                                               user_id=1, session_id="session-1"))
        await db.commit()
    assert await apns.drain_outbox_once(db_factory) == 1
    assert await apns.drain_outbox_once(db_factory) == 0
    assert apns._post.await_count == 1


@pytest.mark.asyncio
async def test_orm_hooks_are_transactional(db_factory):
    async with db_factory() as db:
        await configured(db)
        await db.commit()
        action = SystemAgentAction(id="action-1", channel="web", tool_name="test", actor_user_id=1,
                                   status="pending", expires_at=datetime.now(UTC) + timedelta(hours=1))
        db.add(action)
        await db.flush()
        assert len(list(await db.scalars(select(SystemSetting).where(SystemSetting.key.like(OUTBOX_PREFIX + "%"))))) == 1
        await db.rollback()
    async with db_factory() as db:
        assert not list(await db.scalars(select(SystemSetting).where(SystemSetting.key.like(OUTBOX_PREFIX + "%"))))


@pytest.mark.asyncio
async def test_run_terminal_hook_and_expired_approval_not_sent(db_factory, monkeypatch):
    monkeypatch.setattr(apns, "_post", AsyncMock(return_value=(True, None, False)))
    async with db_factory() as db:
        await configured(db)
        await apns.register_device(db, 1, device())
        db.add(SystemAgentSession(id="session-1", web_user_id=1, channel="web"))
        await db.flush()
        run = SystemAgentRun(id="run-1", session_id="session-1", web_user_id=1, status="running",
                             kind="message", client_request_id="request-1", request_hash="hash")
        db.add(run)
        await db.commit()
        run.status = "succeeded"
        db.add(SystemAgentAction(id="expired-action", channel="web", tool_name="test", actor_user_id=1,
                                 status="pending", expires_at=datetime.now(UTC) - timedelta(minutes=1)))
        await db.commit()
        assert len(list(await db.scalars(select(SystemSetting).where(SystemSetting.key.like(OUTBOX_PREFIX + "%"))))) == 2
    assert await apns.drain_outbox_once(db_factory) == 1
    assert apns._post.await_count == 1
    assert apns._post.call_args.kwargs["payload"]["session_id"] == "session-1"


@pytest.mark.parametrize("include_sensitive", [False, True])
@pytest.mark.asyncio
async def test_export_always_excludes_apns_and_import_cannot_restore_it(db_factory, include_sensitive):
    from app.api import system_health

    async with db_factory() as db:
        await configured(db)
        await apns.register_device(db, 1, device())
        db.add(SystemSetting(key="timezone", value="UTC"))
        await db.commit()
    output = await system_health._build_export_payload(
        categories=["system_settings"], include_sensitive=include_sensitive,
        session_factory=db_factory, model_map={"SystemSetting": SystemSetting},
    )
    assert [row["key"] for row in output["system_settings"]] == ["timezone"]
    result = await system_health._import_config_payload(
        {"system_settings": [{"key": apns.CONFIG_KEY, "value": {"enabled": False}}]},
        session_factory=db_factory, model_map={"SystemSetting": SystemSetting},
    )
    assert result.skipped == 1
    async with db_factory() as db:
        assert (await apns.get_config(db))["enabled"]


@pytest.mark.asyncio
async def test_outbox_failed_delivery_keeps_retry_and_other_devices_are_not_resent(db_factory, monkeypatch):
    async with db_factory() as db:
        await configured(db)
        await apns.register_device(db, 1, device())
        await apns.register_device(db, 1, device(device_token="b" * 64, device_id="installation-2"))
        await db.commit()
        await db.run_sync(lambda sync: enqueue(sync.connection(), kind="assistant_result", event_id="run-1", user_id=1))
        await db.commit()
    post = AsyncMock(return_value=(True, None, False))
    monkeypatch.setattr(apns, "_post", post)
    assert await apns.drain_outbox_once(db_factory) == 1
    assert await apns.drain_outbox_once(db_factory) == 1
    assert len({call.kwargs["token"] for call in post.call_args_list}) == 2
    assert await apns.drain_outbox_once(db_factory) == 0
    async with db_factory() as db:
        assert not list(await db.scalars(select(SystemSetting).where(SystemSetting.key.like(OUTBOX_PREFIX + "%"))))
        await db.run_sync(lambda sync: enqueue(sync.connection(), kind="assistant_result", event_id="run-2", user_id=1))
        await db.commit()
    post.return_value = (False, "InternalServerError", False)
    assert await apns.drain_outbox_once(db_factory) == 1
    assert await apns.drain_outbox_once(db_factory) == 0
    async with db_factory() as db:
        queued = list(await db.scalars(select(SystemSetting).where(SystemSetting.key.like(OUTBOX_PREFIX + "%"))))
        assert len(queued) == 1 and queued[0].value["device_attempts"]


@pytest.mark.asyncio
async def test_all_routes_require_login_without_creating_settings(db_factory):
    app = FastAPI()
    app.include_router(api.router)

    async def override_db():
        async with db_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://example.test") as client:
        for method, path, body in [("GET", "/config", None), ("PATCH", "/config", {"enabled": False}),
                                   ("GET", "/devices", None), ("POST", "/devices", device().model_dump()),
                                   ("DELETE", "/devices/installation-1", None),
                                   ("POST", "/test", {"device_id": "installation-1"})]:
            assert (await client.request(method, "/api/apns" + path, json=body)).status_code == 401
        assert (await client.post("/api/apns/events", json={})).status_code == 404
    async with db_factory() as db:
        assert not list(await db.scalars(select(SystemSetting)))


def test_apns_credentials_and_metadata_rekey_and_redact(tmp_path):
    old, new = Fernet.generate_key(), Fernet.generate_key()
    url = f"sqlite:///{tmp_path / 'rekey-apns.db'}"
    engine = create_engine(url)
    SystemSetting.__table__.create(engine)
    with engine.begin() as connection:
        connection.execute(SystemSetting.__table__.insert(), [
            {"key": apns.CONFIG_KEY, "value": {"auth_key_enc": Fernet(old).encrypt(b"p8").decode()}},
            {"key": apns.DEVICE_PREFIX + "test", "value": {"device_token_enc": Fernet(old).encrypt(b"token").decode()}},
        ])
    assert rekey_database(old_key=old.decode(), new_key=new.decode(), database_url=url).changed == 2
    with engine.connect() as connection:
        rows = list(connection.execute(select(SystemSetting.value)).scalars())
        assert Fernet(new).decrypt(rows[0]["auth_key_enc"].encode()) == b"p8"
        assert Fernet(new).decrypt(rows[1]["device_token_enc"].encode()) == b"token"
    engine.dispose()
    token = "a" * 64
    assert token not in redact_text(f"https://api.push.apple.com/3/device/{token}")
    assert "PRIVATE KEY" not in redact_text(pem())
    assert redact_value({"auth_key_p8": "secret"}) == {"auth_key_p8": "***"}


@pytest.mark.parametrize("reason", ["BadDeviceToken", "DeviceTokenNotForTopic"])
@pytest.mark.asyncio
async def test_bad_device_token_disables_device_until_reregistered(db_factory, monkeypatch, reason):
    post = AsyncMock(return_value=(False, reason, False))
    monkeypatch.setattr(apns, "_post", post)
    async with db_factory() as db:
        await configured(db)
        await apns.register_device(db, 1, device())
        await db.commit()
        result = await apns._deliver(db, rows=await apns._device_rows(db), kind="assistant_result",
                                     event_id="run-1")
        await db.commit()
        assert not result.ok and "已暂停此设备" in result.message
        [listed] = await apns.list_devices(db, 1)
        assert listed["enabled"] is False and listed["last_error"] == reason
        await db.run_sync(lambda sync: enqueue(sync.connection(), kind="assistant_result", event_id="run-2", user_id=1))
        await db.commit()
    # A disabled token is neither retried nor targeted by new events.
    assert await apns.drain_outbox_once(db_factory) == 1
    assert post.await_count == 1
    async with db_factory() as db:
        assert not list(await db.scalars(select(SystemSetting).where(SystemSetting.key.like(OUTBOX_PREFIX + "%"))))
        await apns.register_device(db, 1, device())
        await db.commit()
        [listed] = await apns.list_devices(db, 1)
        assert listed["enabled"] is True and listed["last_error"] is None


@pytest.mark.parametrize("reason,retried", [
    ("PayloadTooLarge", False), ("Forbidden", False), ("TopicDisallowed", False),
    ("TooManyRequests", True), ("InvalidProviderToken", True),
])
@pytest.mark.asyncio
async def test_outbox_never_retries_permanent_apns_errors(db_factory, monkeypatch, reason, retried):
    async with db_factory() as db:
        await configured(db)
        await apns.register_device(db, 1, device())
        await db.commit()
        await db.run_sync(lambda sync: enqueue(sync.connection(), kind="assistant_result", event_id="run-1", user_id=1))
        await db.commit()
    monkeypatch.setattr(apns, "_post", AsyncMock(return_value=(False, reason, False)))
    assert await apns.drain_outbox_once(db_factory) == 1
    async with db_factory() as db:
        queued = list(await db.scalars(select(SystemSetting).where(SystemSetting.key.like(OUTBOX_PREFIX + "%"))))
        assert bool(queued) is retried
        if retried:
            assert 0 < queued[0].value["next_attempt_at"] - time.time() <= 31


@pytest.mark.asyncio
async def test_server_errors_back_off_fifteen_minutes(db_factory, monkeypatch):
    async with db_factory() as db:
        await configured(db)
        await apns.register_device(db, 1, device())
        await db.commit()
        await db.run_sync(lambda sync: enqueue(sync.connection(), kind="assistant_result", event_id="run-1", user_id=1))
        await db.commit()
    monkeypatch.setattr(apns, "_post", AsyncMock(return_value=(False, "ServiceUnavailable", False)))
    assert await apns.drain_outbox_once(db_factory) == 1
    async with db_factory() as db:
        [queued] = list(await db.scalars(select(SystemSetting).where(SystemSetting.key.like(OUTBOX_PREFIX + "%"))))
        assert queued.value["next_attempt_at"] - time.time() > 14 * 60


@pytest.mark.asyncio
async def test_business_alerts_are_stored_but_test_push_is_not(db_factory, monkeypatch):
    post = AsyncMock(return_value=(True, None, False))
    monkeypatch.setattr(apns, "_post", post)
    async with db_factory() as db:
        await configured(db)
        await apns.register_device(db, 1, device())
        await db.commit()
        await apns._deliver(db, rows=await apns._device_rows(db), kind="test", event_id="t")
        await apns._deliver(db, rows=await apns._device_rows(db), kind="assistant_result", event_id="r")
    assert post.call_args_list[0].kwargs["expiration"] == 0
    stored = post.call_args_list[1].kwargs["expiration"]
    assert stored > time.time() + 23 * 3600


@pytest.mark.asyncio
async def test_expiration_header_and_shared_http2_client_reuse(monkeypatch):
    seen = []

    def handler(request):
        seen.append(request.headers["apns-expiration"])
        return httpx.Response(200)

    config = dict(team_id="ABCDEFGHIJ", key_id="KLMNOPQRST", bundle_id="com.telepilot.personal",
                  environment="sandbox")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert (await apns._post(client, config=config, private_key=pem(), token="a" * 64,
                                 payload={"aps": {}}, collapse_key="c", expiration=1234))[0]
    assert seen == ["1234"]
    apns._reset_provider_token_cache()
    first = apns._get_client(config)
    assert apns._get_client(config) is first
    rotated = apns._get_client(dict(config, key_id="ZZZZZZZZZZ"))
    assert rotated is not first
    await apns._reset_client()
    assert rotated.is_closed


@pytest.mark.asyncio
async def test_test_push_explains_expired_registration(db_factory, monkeypatch):
    monkeypatch.setattr(apns, "_post", AsyncMock())
    async with db_factory() as db:
        await configured(db)
        await apns.register_device(db, 1, device())
        [row] = await apns._device_rows(db)
        row.value = dict(row.value, expires_at=(datetime.now(UTC) - timedelta(minutes=1)).isoformat())
        await db.commit()
        result = await apns.send_test(db, 1, "installation-1")
    assert not result.ok and "重新注册" in result.message
    assert not apns._post.called
