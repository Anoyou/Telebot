"""Server-side Apple Push Notification delivery.

Only fixed notification templates cross this boundary. APNs never receives log
messages, action summaries, tokens or model/tool arguments.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..crypto import decrypt_str, encrypt_str
from ..db.apns_events import OUTBOX_PREFIX
from ..db.base import AsyncSessionLocal
from ..db.models.system import SystemSetting
from ..db.models.system_agent import ACTION_STATUS_PENDING, SystemAgentAction
from ..db.models.user import WebUser
from ..schemas.apns import (
    ApnsConfigPatch,
    ApnsDeviceRegister,
    ApnsSendResult,
)

log = logging.getLogger(__name__)
CONFIG_KEY = "apns:config"
DEVICE_PREFIX = "apns:device:"
APNS_HOSTS = {
    "production": "https://api.push.apple.com",
    "sandbox": "https://api.sandbox.push.apple.com",
}
ALL_CATEGORIES = ("assistant_approval", "assistant_result", "account_alert")

# APNs requires refresh between 20 and 60 minutes on a connection. A 30-minute
# cache avoids re-signing for every device in a fan-out.
_provider_token_cache: tuple[str, str, float] | None = None
_provider_token_lock = asyncio.Lock()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _device_key(token: str) -> str:
    return DEVICE_PREFIX + hashlib.sha256(token.encode()).hexdigest()


def _empty_config() -> dict[str, Any]:
    return {
        "enabled": False,
        "team_id": None,
        "key_id": None,
        "bundle_id": None,
        "environment": "production",
        "has_auth_key": False,
        "configured": False,
        "updated_at": None,
    }


def _public_config(value: dict[str, Any] | None) -> dict[str, Any]:
    data = dict(value or {})
    has_key = bool(data.get("auth_key_enc"))
    result = {
        "enabled": bool(data.get("enabled", False)),
        "team_id": data.get("team_id"),
        "key_id": data.get("key_id"),
        "bundle_id": data.get("bundle_id"),
        "environment": data.get("environment", "production"),
        "has_auth_key": has_key,
        "updated_at": data.get("updated_at"),
    }
    result["configured"] = bool(
        has_key and result["team_id"] and result["key_id"] and result["bundle_id"]
    )
    return result


async def _raw_config(db: AsyncSession) -> dict[str, Any]:
    row = await db.get(SystemSetting, CONFIG_KEY)
    value = row.value if row is not None else {}
    return dict(value) if isinstance(value, dict) else {}


async def get_config(db: AsyncSession) -> dict[str, Any]:
    return _public_config(await _raw_config(db))


def _validate_auth_key(pem: str) -> None:
    try:
        key = load_pem_private_key(pem.replace("\\n", "\n").encode(), password=None)
        if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
            raise ValueError("Expected P-256 private key")
    except (TypeError, ValueError) as exc:
        raise ValueError("APNs Auth Key 必须是有效的 ES256 .p8 私钥") from exc


async def update_config(db: AsyncSession, patch: ApnsConfigPatch) -> dict[str, Any]:
    if patch.clear_auth_key and patch.auth_key_p8:
        raise ValueError("不能同时更新和清除 Auth Key")
    current = await _raw_config(db)
    updated = {"enabled": False, "environment": "production", **current}
    for name in ("team_id", "key_id", "bundle_id", "environment"):
        value = getattr(patch, name)
        if value is not None:
            updated[name] = value
    if patch.auth_key_p8:
        _validate_auth_key(patch.auth_key_p8)
        updated["auth_key_enc"] = encrypt_str(patch.auth_key_p8.replace("\\n", "\n"))
    elif patch.clear_auth_key:
        updated.pop("auth_key_enc", None)
        updated["enabled"] = False
    if patch.enabled is not None:
        updated["enabled"] = patch.enabled
    public = _public_config(updated)
    if public["enabled"] and not public["configured"]:
        raise ValueError("启用前必须配置 Team ID、Key ID、Bundle ID 和 Auth Key")
    updated["updated_at"] = _now()
    row = await db.get(SystemSetting, CONFIG_KEY)
    if row is None:
        db.add(SystemSetting(key=CONFIG_KEY, value=updated))
    else:
        row.value = updated
    return _public_config(updated)


def _device_out(key: str, value: dict[str, Any]) -> dict[str, Any]:
    return {
        "device_id": value.get("device_id"),
        "bundle_id": value.get("bundle_id"),
        "environment": value.get("environment", "production"),
        "enabled": bool(value.get("enabled", True)),
        "categories": list(value.get("categories") or []),
        "last_error": value.get("last_error"),
        "registered_at": value.get("registered_at"),
        "updated_at": value.get("updated_at"),
        "expires_at": value.get("expires_at"),
    }


async def _device_rows(db: AsyncSession) -> list[SystemSetting]:
    result = await db.execute(select(SystemSetting).where(SystemSetting.key.like(f"{DEVICE_PREFIX}%")))
    return list(result.scalars())


async def list_devices(db: AsyncSession, user_id: int) -> list[dict[str, Any]]:
    rows = await _device_rows(db)
    return [
        _device_out(row.key, row.value)
        for row in rows
        if isinstance(row.value, dict) and row.value.get("owner_user_id") == user_id
    ]


async def register_device(db: AsyncSession, user_id: int, body: ApnsDeviceRegister,
                          *, pwd_version: int = 0) -> dict[str, Any]:
    # Serialize a user's token rotations: different token keys otherwise permit
    # two simultaneous registrations for the same installation to survive.
    await db.execute(select(WebUser.id).where(WebUser.id == user_id).with_for_update())
    config = await _raw_config(db)
    if config.get("bundle_id") and (
        body.bundle_id != config["bundle_id"]
        or body.environment != config.get("environment", "production")
    ):
        raise ValueError("设备 Bundle ID 或推送环境与服务端配置不匹配")
    # A token is scoped to its topic/environment; never expose another owner's row.
    key = _device_key(f"{body.environment}:{body.bundle_id}:{body.device_token}")
    row = await db.scalar(select(SystemSetting).where(SystemSetting.key == key).with_for_update())
    previous = dict(row.value) if row is not None and isinstance(row.value, dict) else {}
    if previous and previous.get("owner_user_id") != user_id:
        raise ValueError("此推送设备已绑定其他用户")
    # Stable installation IDs cannot silently replace another installation's token.
    device_rows = await _device_rows(db)
    owned = [candidate for candidate in device_rows if candidate.value.get("owner_user_id") == user_id]
    if len(owned) >= 20 and not any(candidate.value.get("device_id") == body.device_id for candidate in owned):
        raise ValueError("每个用户最多绑定 20 台推送设备")
    for candidate in device_rows:
        value = candidate.value if isinstance(candidate.value, dict) else {}
        if (
            candidate.key != key
            and value.get("device_id") == body.device_id
            and value.get("owner_user_id") == user_id
        ):
            await db.delete(candidate)
    value = {
        "owner_user_id": user_id,
        "pwd_version": pwd_version,
        "server_origin_sha256": body.server_origin_sha256,
        "device_id": body.device_id,
        "device_token_enc": encrypt_str(body.device_token),
        "bundle_id": body.bundle_id,
        "environment": body.environment,
        "enabled": True,
        "categories": sorted(set(body.categories)),
        "last_error": None,
        "registered_at": previous.get("registered_at") or _now(),
        "updated_at": _now(),
        "expires_at": (datetime.now(UTC) + timedelta(days=7)).isoformat(),
    }
    if row is None:
        db.add(SystemSetting(key=key, value=value))
    else:
        row.value = value
    return _device_out(key, value)


async def unregister_device(db: AsyncSession, user_id: int, device_id: str) -> bool:
    await db.execute(select(WebUser.id).where(WebUser.id == user_id).with_for_update())
    removed = False
    for row in await _device_rows(db):
        value = row.value if isinstance(row.value, dict) else {}
        if value.get("owner_user_id") == user_id and value.get("device_id") == device_id:
            await db.delete(row)
            removed = True
    return removed


def _reset_provider_token_cache() -> None:
    global _provider_token_cache
    _provider_token_cache = None


async def _provider_token(config: dict[str, Any], private_key: str) -> str:
    global _provider_token_cache
    fingerprint = hashlib.sha256(
        f"{config['team_id']}|{config['key_id']}|{private_key}".encode()
    ).hexdigest()
    async with _provider_token_lock:
        if (
            _provider_token_cache is not None
            and _provider_token_cache[0] == fingerprint
            and _provider_token_cache[2] > time.monotonic()
        ):
            return _provider_token_cache[1]
        key = load_pem_private_key(private_key.encode(), password=None)
        token = jwt.encode(
            {"iss": config["team_id"], "iat": int(time.time())},
            key,
            algorithm="ES256",
            headers={"kid": config["key_id"]},
        )
        _provider_token_cache = (fingerprint, token, time.monotonic() + 1800)
        return token


def _response_reason(response: httpx.Response) -> str | None:
    try:
        body = response.json()
    except (ValueError, json.JSONDecodeError):
        return None
    reason = body.get("reason") if isinstance(body, dict) else None
    return str(reason)[:80] if reason else None


async def _post(
    client: httpx.AsyncClient,
    *,
    config: dict[str, Any],
    private_key: str,
    token: str,
    payload: dict[str, Any],
    collapse_key: str,
) -> tuple[bool, str | None, bool]:
    provider_token = await _provider_token(config, private_key)
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    if len(body) > 4096:
        return False, "PayloadTooLarge", False
    headers = {
        "authorization": f"bearer {provider_token}",
        "apns-push-type": "alert", "apns-priority": "10", "apns-expiration": "0",
        "apns-topic": config["bundle_id"], "apns-id": str(uuid.uuid4()),
        "apns-collapse-id": hashlib.sha256(collapse_key.encode()).hexdigest(),
        "content-type": "application/json",
    }
    response = await client.post(
        f"{APNS_HOSTS[config['environment']]}/3/device/{token}",
        content=body,
        headers=headers,
    )
    if response.status_code == 403 and _response_reason(response) == "ExpiredProviderToken":
        _reset_provider_token_cache()
        headers["authorization"] = f"bearer {await _provider_token(config, private_key)}"
        response = await client.post(
            f"{APNS_HOSTS[config['environment']]}/3/device/{token}", content=body, headers=headers,
        )
    # Provider authentication errors are unrelated to device registration.
    return response.status_code == 200, _response_reason(response), response.status_code == 410


def _notification(kind: str) -> tuple[str, str, str]:
    templates = {
        "assistant_approval": ("TelePilot 待确认", "助手有一项操作等待你确认。打开 App 查看详情。", "assistant/inbox"),
        "assistant_result": ("TelePilot 任务结果", "助手任务已结束。打开 App 查看结果。", "assistant"),
        "account_alert": ("TelePilot 账号告警", "账号出现运行告警。打开 App 查看详情。", "logs"),
        "test": ("TelePilot 推送测试", "Apple 推送通道连接成功。", "settings/notifications"),
    }
    return templates[kind]


async def _deliver(
    db: AsyncSession,
    *,
    rows: list[SystemSetting],
    kind: str,
    event_id: str,
    account_id: int | None = None,
    session_id: str | None = None,
) -> ApnsSendResult:
    if not rows:
        return ApnsSendResult(ok=False, message="没有已启用且订阅此通知的设备")
    config = await _raw_config(db)
    public = _public_config(config)
    if not public["enabled"]:
        return ApnsSendResult(ok=False, attempted=len(rows), failed=len(rows), message="APNs 尚未启用")
    if not public["configured"]:
        return ApnsSendResult(ok=False, attempted=len(rows), failed=len(rows), message="APNs 配置不完整")
    private_key = decrypt_str(config["auth_key_enc"])
    title, body, _route = _notification(kind)
    payload = {
        "aps": {
            "alert": {"title": title, "body": body},
            "sound": "default",
            "thread-id": kind,
        },
        "kind": kind, "event_id": event_id,
        "destination": {"assistant_approval": "action", "assistant_result": "assistant",
                        "account_alert": "account", "test": "overview"}[kind],
    }
    if account_id is not None:
        payload["account_id"] = account_id
    if kind == "assistant_approval":
        payload["action_id"] = event_id
    if session_id:
        payload["session_id"] = session_id
    accepted = failed = unregistered = 0
    reasons: set[str] = set()
    try:
        async with httpx.AsyncClient(http2=True, timeout=10.0, follow_redirects=False, trust_env=False) as client:
            for row in rows:
                row = await db.scalar(select(SystemSetting).where(SystemSetting.key == row.key)
                                      .with_for_update().execution_options(populate_existing=True))
                if row is None:
                    continue
                value = dict(row.value)
                if kind != "test" and not _eligible(row, value.get("owner_user_id"), kind, config):
                    continue
                user = await db.get(WebUser, value.get("owner_user_id"), populate_existing=True)
                if user is None or int(user.pwd_version or 0) != value.get("pwd_version") or not _lease_valid(value):
                    await db.delete(row)
                    continue
                device_payload = dict(payload, server_origin_sha256=value.get("server_origin_sha256"))
                try:
                    token = decrypt_str(value["device_token_enc"])
                    delivered, reason, invalid_device = await _post(
                        client,
                        config=config,
                        private_key=private_key,
                        token=token,
                        payload=device_payload,
                        collapse_key=f"{kind}:{event_id}",
                    )
                except (httpx.HTTPError, ValueError) as exc:
                    delivered, reason, invalid_device = False, type(exc).__name__, False
                    log.warning("APNs delivery failed: %s", type(exc).__name__)
                if delivered:
                    accepted += 1
                    value["last_error"] = None
                else:
                    failed += 1
                    if reason:
                        reasons.add(reason)
                    value["last_error"] = reason or "APNS_REQUEST_FAILED"
                    if invalid_device:
                        await db.delete(row)
                        unregistered += 1
                if delivered or not invalid_device:
                    row.value = value
    except (httpx.HTTPError, ImportError, RuntimeError, ValueError) as exc:
        log.warning("APNs connection failed: %s", type(exc).__name__)
        return ApnsSendResult(
            ok=False,
            attempted=len(rows),
            failed=len(rows),
            message="无法连接 APNs；请检查网络与 HTTP/2 依赖",
        )
    detail = f"；{', '.join(sorted(reasons))[:180]}" if reasons else ""
    return ApnsSendResult(
        ok=accepted > 0,
        attempted=len(rows),
        accepted=accepted,
        failed=failed,
        unregistered=unregistered,
        message=f"APNs 已接受 {accepted} 台设备，失败 {failed} 台{detail}",
        event_id=event_id,
    )


def _eligible(row: SystemSetting, user_id: int | None, kind: str, config: dict[str, Any]) -> bool:
    value = row.value if isinstance(row.value, dict) else {}
    return bool(
        value.get("enabled")
        and _lease_valid(value)
        and kind in set(value.get("categories") or [])
        and (user_id is None or value.get("owner_user_id") == user_id)
        and value.get("environment") == config.get("environment", "production")
        and value.get("bundle_id") == config.get("bundle_id")
    )


def _lease_valid(value: dict[str, Any]) -> bool:
    try:
        return datetime.fromisoformat(value["expires_at"]).replace(tzinfo=UTC) > datetime.now(UTC)
    except (KeyError, ValueError, TypeError):
        return False


async def send_test(db: AsyncSession, user_id: int, device_id: str) -> ApnsSendResult:
    config = await _raw_config(db)
    rows = [
        row
        for row in await _device_rows(db)
        if row.value.get("owner_user_id") == user_id and row.value.get("enabled")
        and row.value.get("environment") == config.get("environment", "production")
        and row.value.get("bundle_id") == config.get("bundle_id")
    ]
    rows = [row for row in rows if row.value.get("device_id") == device_id]
    if not rows:
        return ApnsSendResult(ok=False, message="设备不存在、已停用或与当前 APNs 环境不匹配")
    return await _deliver(db, rows=rows, kind="test", event_id=f"test_{uuid.uuid4().hex}")


async def drain_outbox_once(session_factory=None) -> int:
    """One bounded batch; PostgreSQL row locks coordinate multiple web processes.

    Delivery is at least once across a crash between Apple accepting and commit.
    The stable collapse ID allows Apple to coalesce repeated undelivered alerts.
    """
    processed = 0
    async with (session_factory or AsyncSessionLocal)() as db:
        config = await _raw_config(db)
        events = list((await db.scalars(
            select(SystemSetting).where(SystemSetting.key.like(f"{OUTBOX_PREFIX}%"))
            .order_by(SystemSetting.updated_at, SystemSetting.key).limit(5)
            .with_for_update(skip_locked=True)
        )).all())
        for event in events:
            value = dict(event.value)
            created = datetime.fromisoformat(value["created_at"])
            if not config.get("enabled") or datetime.now(UTC) - created > timedelta(hours=24):
                await db.delete(event)
                continue
            if value.get("next_attempt_at", 0) > time.time():
                continue
            if value["kind"] == "assistant_approval":
                action = await db.get(SystemAgentAction, value["event_id"])
                if (action is None or action.status != ACTION_STATUS_PENDING
                        or action.actor_user_id != value["user_id"]
                        or action.expires_at.replace(tzinfo=UTC) <= datetime.now(UTC)):
                    await db.delete(event)
                    continue
            rows = [row for row in await _device_rows(db)
                    if _eligible(row, value["user_id"], value["kind"], config)]
            remaining = set(value.get("remaining", [row.key for row in rows]))
            rows = [row for row in rows if row.key in remaining]
            attempts = dict(value.get("device_attempts") or {})
            next_attempt_at = 0
            for row in rows[:1]:
                result = await _deliver(
                    db, rows=[row], kind=value["kind"], event_id=value["event_id"],
                    account_id=value.get("account_id"), session_id=value.get("session_id"),
                )
                if result.accepted or result.unregistered:
                    remaining.discard(row.key)
                else:
                    attempts[row.key] = int(attempts.get(row.key, 0)) + 1
                    if attempts[row.key] >= 3:
                        remaining.discard(row.key)
                    else:
                        next_attempt_at = time.time() + 30 * attempts[row.key]
            remaining &= {row.key for row in rows}
            processed += 1
            if not remaining:
                await db.delete(event)
            else:
                value["remaining"] = sorted(remaining)
                value["device_attempts"] = attempts
                value["next_attempt_at"] = next_attempt_at
                event.value = value
        # Expired subscriptions are removed even when no notifications are queued.
        for row in await _device_rows(db):
            if not _lease_valid(row.value):
                # Registration can renew a lease after the initial scan. Lock and
                # re-read before deleting, and let active renewal transactions pass.
                fresh = await db.scalar(select(SystemSetting).where(SystemSetting.key == row.key)
                                        .with_for_update(skip_locked=True)
                                        .execution_options(populate_existing=True))
                if fresh is not None and not _lease_valid(fresh.value):
                    await db.delete(fresh)
        await db.commit()
    return processed


async def delivery_loop() -> None:
    while True:
        try:
            await drain_outbox_once()
        except Exception as exc:  # noqa: BLE001
            # Never log the exception text: HTTP exceptions may contain device URLs.
            log.warning("APNs outbox failed: %s", type(exc).__name__)
        await asyncio.sleep(5)


__all__ = [
    "CONFIG_KEY", "DEVICE_PREFIX", "get_config", "list_devices", "register_device",
    "delivery_loop", "drain_outbox_once", "send_test",
    "unregister_device", "update_config",
]
