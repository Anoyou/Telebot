"""第 20 轮：已保存密钥按需查看 ``GET /api/secrets/reveal``。

- 需要登录；
- 每次读取写 ``secret.reveal`` 审计（只记是哪一项，不记值）；
- 响应 ``Cache-Control: no-store``；
- 普通配置读取接口仍然不返回明文。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import ARRAY as GenericArray
from sqlalchemy import BigInteger, select
from sqlalchemy.dialects.postgresql import ARRAY as PgArray
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.crypto import encrypt_bytes, encrypt_str
from app.db.base import Base
from app.db.models.account import Account, Proxy
from app.db.models.account_bot import AccountBot
from app.db.models.command import LLMProvider, LLMProviderKey
from app.db.models.feature import AccountFeature, Feature
from app.db.models.log import AuditLog
from app.db.models.notify import NotifyBot
from app.db.models.plugin_repo import PluginRepo
from app.db.models.system import SystemSetting
from app.db.models.user import WebUser
from app.deps import get_current_user, get_db
from app.main import app
from app.schemas.platform_capabilities import PLATFORM_FEATURE_FLAGS
from app.services import account_bot_service
from app.services.plugin_config_secrets import wrap_secret

INTERACTION_TOKEN = "7000000001:AAInteractionTokenForRevealTest_abc"
MANAGEMENT_TOKEN = "7000000002:AAManagementTokenForRevealTest_def"
NOTIFY_TOKEN = "7000000003:AANotifyTokenForRevealTest_ghi"
PROVIDER_KEY = "sk-reveal-provider-key-0123456789"
POOL_KEY = "sk-reveal-pool-key-9876543210"
PROXY_PASSWORD = "proxy-pass-Reveal!42"
WEBHOOK_TOKEN = "webhook-token-reveal-xyz"
REPO_CREDENTIAL = "ghp_revealRepoCredential123"
PLUGIN_SECRET = "plugin-secret-reveal-777"
ALL_SECRETS = [
    INTERACTION_TOKEN,
    MANAGEMENT_TOKEN,
    NOTIFY_TOKEN,
    PROVIDER_KEY,
    POOL_KEY,
    PROXY_PASSWORD,
    REPO_CREDENTIAL,
    PLUGIN_SECRET,
]

CSRF_HEADERS = {
    "X-Requested-With": "telepilot-ui",
    "X-CSRF-Token": "test-token",
    "Cookie": "csrf_token=test-token",
}


async def _seed(db: AsyncSession) -> None:
    good = {
        "api_id_enc": encrypt_str("1"),
        "api_hash_enc": encrypt_str("h"),
        "session_enc": encrypt_bytes(b"s"),
    }
    db.add(WebUser(id=1, username="admin", password_hash="x"))
    db.add(Proxy(id=5, type="socks5", host="127.0.0.1", port=1080, username="u", password_enc=encrypt_str(PROXY_PASSWORD)))
    db.add_all(
        [
            Account(id=1, phone="8613800001234", display_name="客服A", status="active", **good),
            Account(id=2, phone="8613900005678", display_name="客服B", status="active", **good),
        ]
    )
    db.add(
        AccountBot(
            id=1,
            account_id=1,
            bot_token_enc=encrypt_str(MANAGEMENT_TOKEN),
            enabled=True,
            status="running",
            username="alice_admin_bot",
            remote_plugin_policy={},
        )
    )
    db.add(
        SystemSetting(
            key=account_bot_service.transfer_notice_setting_key(1),
            value={"enabled": True, "interaction_bot_token_enc": encrypt_str(INTERACTION_TOKEN)},
        )
    )
    db.add(
        SystemSetting(
            key="account_webhooks:1",
            value={"token_enc": encrypt_str(WEBHOOK_TOKEN), "hooks": [{"key": "default", "label": "默认入口", "enabled": True}]},
        )
    )
    db.add(NotifyBot(id=3, name="通知", bot_token_enc=encrypt_str(NOTIFY_TOKEN), default_chat_id=100, enabled=True))
    db.add(NotifyBot(id=4, name="复用管理 Bot", bot_token_enc=None, source_account_id=1, default_chat_id=100, enabled=True))
    db.add(
        LLMProvider(
            id=7,
            name="OpenAI",
            provider="openai",
            base_url="https://api.example.com/v1",
            default_model="gpt",
            api_key_enc=encrypt_str(PROVIDER_KEY),
        )
    )
    db.add(LLMProviderKey(id=8, provider_id=7, label="备用", api_key_enc=encrypt_str(POOL_KEY)))
    db.add(PluginRepo(id=9, name="私有仓库", url="https://example.com/r.git", auth_type="token", credential_enc=encrypt_str(REPO_CREDENTIAL)))
    db.add(
        Feature(
            key="demo",
            display_name="示例",
            manifest={
                "config_schema": {
                    "type": "object",
                    "properties": {
                        "api_token": {"type": "string"},
                        "greeting": {"type": "string"},
                        "custom": {"type": "string", "format": "password"},
                    },
                }
            },
        )
    )
    db.add(
        AccountFeature(
            account_id=1,
            feature_key="demo",
            enabled=True,
            config={"api_token": wrap_secret(PLUGIN_SECRET), "greeting": "你好", "custom": "legacy-plain"},
        )
    )
    await db.commit()


@pytest.fixture
async def client_and_db(monkeypatch):
    from sqlalchemy.ext.compiler import compiles

    @compiles(GenericArray, "sqlite")
    @compiles(PgArray, "sqlite")
    @compiles(JSONB, "sqlite")
    def _as_json(type_, compiler, **kw):  # noqa: ARG001
        return "JSON"

    @compiles(BigInteger, "sqlite")
    def _bigint_as_integer(type_, compiler, **kw):  # noqa: ARG001
        # sqlite 只有 INTEGER PRIMARY KEY 才自增
        return "INTEGER"

    engine = create_async_engine("sqlite+aiosqlite://")
    names = (
        "web_user",
        "account",
        "account_bot",
        "system_setting",
        "notify_bot",
        "llm_provider",
        "llm_provider_key",
        "plugin_repo",
        "feature",
        "account_feature",
        "audit_log",
    )
    tables = [Base.metadata.tables[name] for name in names]
    for table in list(tables):
        for fk in table.foreign_keys:
            ref = fk.column.table
            if ref not in tables:
                tables.insert(0, ref)
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        await _seed(db)

    async def _db():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client, maker
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


async def _audit_rows(maker) -> list[AuditLog]:
    async with maker() as db:
        return list((await db.execute(select(AuditLog).where(AuditLog.action == "secret.reveal").order_by(AuditLog.id))).scalars())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"kind": "interaction_bot_token", "account_id": 1}, INTERACTION_TOKEN),
        ({"kind": "management_bot_token", "account_id": 1}, MANAGEMENT_TOKEN),
        ({"kind": "notify_bot_token", "id": 3}, NOTIFY_TOKEN),
        ({"kind": "notify_bot_token", "id": 4}, MANAGEMENT_TOKEN),
        ({"kind": "llm_provider_api_key", "id": 7}, PROVIDER_KEY),
        ({"kind": "llm_provider_key", "id": 8}, POOL_KEY),
        ({"kind": "proxy_password", "id": 5}, PROXY_PASSWORD),
        ({"kind": "webhook_token", "account_id": 1}, WEBHOOK_TOKEN),
        ({"kind": "plugin_repo_credential", "id": 9}, REPO_CREDENTIAL),
        ({"kind": "plugin_config_secret", "account_id": 1, "plugin_key": "demo", "field": "api_token"}, PLUGIN_SECRET),
        ({"kind": "plugin_config_secret", "account_id": 1, "plugin_key": "demo", "field": "custom"}, "legacy-plain"),
    ],
)
async def test_reveal_returns_plaintext_with_no_store_and_audit(client_and_db, params, expected) -> None:
    client, maker = client_and_db
    resp = await client.get("/api/secrets/reveal", params=params)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["configured"] is True
    assert body["value"] == expected
    assert body["kind"] == params["kind"]
    assert body["label"]
    assert "no-store" in resp.headers["cache-control"]
    assert resp.headers["pragma"] == "no-cache"
    rows = await _audit_rows(maker)
    assert len(rows) == 1
    row = rows[0]
    assert row.user_id == 1
    assert row.target and row.target.startswith(params["kind"])
    assert row.detail["kind"] == params["kind"]
    # 审计只记是哪一项，不记录值
    assert expected not in repr(row.detail)
    assert expected not in (row.target or "")


@pytest.mark.asyncio
async def test_each_read_writes_its_own_audit_row(client_and_db) -> None:
    client, maker = client_and_db
    for _ in range(3):
        resp = await client.get("/api/secrets/reveal", params={"kind": "interaction_bot_token", "account_id": 1})
        assert resp.status_code == 200
    assert len(await _audit_rows(maker)) == 3


@pytest.mark.asyncio
async def test_unconfigured_secret_returns_configured_false(client_and_db) -> None:
    client, maker = client_and_db
    resp = await client.get("/api/secrets/reveal", params={"kind": "interaction_bot_token", "account_id": 2})
    assert resp.status_code == 200
    assert resp.json() == {"kind": "interaction_bot_token", "label": "交互 Bot Token", "configured": False, "value": None}
    resp = await client.get("/api/secrets/reveal", params={"kind": "management_bot_token", "account_id": 2})
    assert resp.json()["configured"] is False
    assert len(await _audit_rows(maker)) == 2


@pytest.mark.asyncio
async def test_requires_login(client_and_db) -> None:
    client, maker = client_and_db
    app.dependency_overrides.pop(get_current_user, None)
    resp = await client.get("/api/secrets/reveal", params={"kind": "interaction_bot_token", "account_id": 1})
    assert resp.status_code == 401
    assert INTERACTION_TOKEN not in resp.text
    assert await _audit_rows(maker) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("params", "status_code", "code"),
    [
        ({"kind": "interaction_bot_token"}, 400, "SECRET_TARGET_REQUIRED"),
        ({"kind": "interaction_bot_token", "account_id": 99}, 404, "ACCOUNT_NOT_FOUND"),
        ({"kind": "proxy_password", "id": 99}, 404, "SECRET_TARGET_NOT_FOUND"),
        ({"kind": "plugin_config_secret", "account_id": 1, "plugin_key": "demo", "field": "greeting"}, 400, "SECRET_FIELD_NOT_SENSITIVE"),
        ({"kind": "plugin_config_secret", "account_id": 1, "plugin_key": "nope", "field": "api_token"}, 404, "FEATURE_NOT_FOUND"),
    ],
)
async def test_bad_requests_do_not_audit(client_and_db, params, status_code, code) -> None:
    client, maker = client_and_db
    resp = await client.get("/api/secrets/reveal", params=params)
    assert resp.status_code == status_code, resp.text
    assert resp.json()["error"]["code"] == code
    assert await _audit_rows(maker) == []


@pytest.mark.asyncio
async def test_unknown_kind_is_rejected(client_and_db) -> None:
    client, _maker = client_and_db
    resp = await client.get("/api/secrets/reveal", params={"kind": "account_session", "account_id": 1})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_plain_config_reads_do_not_leak_secrets(client_and_db) -> None:
    client, _maker = client_and_db
    responses = [
        await client.get("/api/accounts/1/interaction-bot"),
        await client.get("/api/accounts/1/bot"),
        await client.get("/api/notify-bots"),
        await client.get("/api/proxies"),
        await client.get("/api/accounts/1/features"),
    ]
    for resp in responses:
        assert resp.status_code == 200, (resp.request.url, resp.text)
        for secret in ALL_SECRETS:
            assert secret not in resp.text, (resp.request.url, secret)
    interaction = responses[0].json()
    assert interaction["has_interaction_bot_token"] is True
    assert interaction.get("interaction_bot_token") in (None, "")


def test_capability_flag_advertises_secret_reveal() -> None:
    assert PLATFORM_FEATURE_FLAGS["secret_reveal"] is True
