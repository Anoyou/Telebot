"""已保存密钥按需查看（第 20 轮）。

普通配置读取接口一律不返回 Token / API Key / 密码，只返回 ``has_*`` / 遮罩；
用户在 Web 或 iOS 点密钥框右侧的「眼睛」时，客户端才调用本接口取回明文：

- 需要登录（系统只有一个管理员账号，登录即管理员）；
- 每次读取写一条 ``audit_log``（``secret.reveal``，只记录是哪一项，不记录值）；
- 响应带 ``Cache-Control: no-store``，不进任何缓存；
- 客户端离开页面即丢弃明文，不落本机存储。

``kind`` 取值见 :data:`SECRET_KINDS`；老服务器没有本接口时，客户端按
``GET /api/system/capabilities`` 的 ``features.secret_reveal`` 判断并显示
「已配置（此服务器版本不支持查看）」。
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..crypto import decrypt_str
from ..db.models.account import Account, Proxy
from ..db.models.account_bot import AccountBot
from ..db.models.command import LLMProvider, LLMProviderKey
from ..db.models.feature import AccountFeature, Feature
from ..db.models.notify import NotifyBot
from ..db.models.plugin_repo import PluginRepo
from ..deps import CurrentUser, DBSession
from ..services import audit

router = APIRouter(prefix="/api/secrets", tags=["secrets"])

SecretKind = Literal[
    "interaction_bot_token",
    "management_bot_token",
    "notify_bot_token",
    "llm_provider_api_key",
    "llm_provider_key",
    "proxy_password",
    "webhook_token",
    "plugin_repo_credential",
    "plugin_config_secret",
]

# kind → (中文名, 需要的定位参数)
SECRET_KINDS: dict[str, tuple[str, tuple[str, ...]]] = {
    "interaction_bot_token": ("交互 Bot Token", ("account_id",)),
    "management_bot_token": ("管理 Bot Token", ("account_id",)),
    "notify_bot_token": ("通知 Bot Token", ("id",)),
    "llm_provider_api_key": ("AI 供应商 API Key", ("id",)),
    "llm_provider_key": ("AI 供应商密钥池 Key", ("id",)),
    "proxy_password": ("代理密码", ("id",)),
    "webhook_token": ("Webhook 密钥", ("account_id",)),
    "plugin_repo_credential": ("插件仓库凭据", ("id",)),
    "plugin_config_secret": ("插件配置密钥", ("plugin_key", "field")),
}

NO_STORE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, private",
    "Pragma": "no-cache",
    "Expires": "0",
}


class SecretRevealOut(BaseModel):
    kind: str
    label: str = Field(description="这一项的中文名，例如「交互 Bot Token」")
    configured: bool = Field(description="服务器上是否保存了这一项；false 时 value 为 null")
    value: str | None = Field(default=None, description="明文；仅在用户点「显示」时按需读取")


def _bad(code: str, message: str, status_code: int = status.HTTP_400_BAD_REQUEST) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _decrypt(token: Any) -> str | None:
    text = str(token or "").strip()
    if not text:
        return None
    try:
        return decrypt_str(text)
    except ValueError as exc:
        raise _bad(
            "SECRET_DECRYPT_FAILED",
            "密钥解密失败：可能 MASTER_KEY 已变更，请重新保存这一项",
            status.HTTP_409_CONFLICT,
        ) from exc


async def _require_account(db: Any, account_id: int) -> None:
    if await db.get(Account, account_id) is None:
        raise _bad("ACCOUNT_NOT_FOUND", "账号不存在", status.HTTP_404_NOT_FOUND)


async def _require_row(db: Any, model: Any, row_id: int, label: str) -> Any:
    row = await db.get(model, row_id)
    if row is None:
        raise _bad("SECRET_TARGET_NOT_FOUND", f"{label}不存在", status.HTTP_404_NOT_FOUND)
    return row


async def _plugin_config_secret(
    db: Any, plugin_key: str, field: str, account_id: int | None
) -> str | None:
    from ..services import feature_service
    from ..services.plugin_config_secrets import _path_is_sensitive, unwrap_secret

    feature = await db.get(Feature, plugin_key)
    if feature is None:
        raise _bad("FEATURE_NOT_FOUND", f"未注册的插件: {plugin_key}", status.HTTP_404_NOT_FOUND)
    manifest = feature.manifest if isinstance(feature.manifest, dict) else {}
    schema = manifest.get("config_schema") if isinstance(manifest.get("config_schema"), dict) else {}
    props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    prop = props.get(field) if isinstance(props.get(field), dict) else None
    if not (_path_is_sensitive(field, prop) or (prop or {}).get("writeOnly") is True):
        # 只允许取声明为密钥的顶层字段，避免借本接口读任意配置。
        raise _bad("SECRET_FIELD_NOT_SENSITIVE", "该字段不是密钥字段")
    if account_id is not None:
        await _require_account(db, account_id)
        row = (
            await db.execute(
                select(AccountFeature).where(
                    AccountFeature.account_id == account_id,
                    AccountFeature.feature_key == plugin_key,
                )
            )
        ).scalar_one_or_none()
        config = dict(row.config or {}) if row is not None else {}
    else:
        config = await feature_service.get_plugin_global_config(db, plugin_key)
    raw = config.get(field)
    if raw is None or raw == "":
        return None
    if not isinstance(raw, str):
        raise _bad("SECRET_FIELD_NOT_TEXT", "该字段不是文本密钥")
    try:
        value = unwrap_secret(raw)
    except ValueError as exc:
        raise _bad(
            "SECRET_DECRYPT_FAILED",
            "密钥解密失败：可能 MASTER_KEY 已变更，请重新保存这一项",
            status.HTTP_409_CONFLICT,
        ) from exc
    return value or None


async def read_secret(
    db: Any,
    kind: str,
    *,
    account_id: int | None = None,
    item_id: int | None = None,
    plugin_key: str | None = None,
    field: str | None = None,
) -> str | None:
    """按 kind 取回明文；未配置返回 None，定位不到抛 404。"""

    if kind == "interaction_bot_token":
        from ..services import account_bot_service

        assert account_id is not None
        await _require_account(db, account_id)
        from ..db.models.system import SystemSetting

        row = await db.get(SystemSetting, account_bot_service.transfer_notice_setting_key(account_id))
        data = account_bot_service.normalize_transfer_notice_config(row.value if row is not None else None)
        return _decrypt(data.get("interaction_bot_token_enc"))
    if kind == "management_bot_token":
        assert account_id is not None
        await _require_account(db, account_id)
        bot = (
            await db.execute(select(AccountBot).where(AccountBot.account_id == account_id))
        ).scalar_one_or_none()
        return _decrypt(bot.bot_token_enc if bot is not None else None)
    if kind == "notify_bot_token":
        assert item_id is not None
        notify = await _require_row(db, NotifyBot, item_id, "通知 Bot")
        if notify.bot_token_enc:
            return _decrypt(notify.bot_token_enc)
        if notify.source_account_id is not None:
            bot = (
                await db.execute(
                    select(AccountBot).where(AccountBot.account_id == int(notify.source_account_id))
                )
            ).scalar_one_or_none()
            return _decrypt(bot.bot_token_enc if bot is not None else None)
        return None
    if kind == "llm_provider_api_key":
        assert item_id is not None
        provider = await _require_row(db, LLMProvider, item_id, "AI 供应商")
        return _decrypt(provider.api_key_enc)
    if kind == "llm_provider_key":
        assert item_id is not None
        key_row = await _require_row(db, LLMProviderKey, item_id, "密钥池 Key")
        return _decrypt(key_row.api_key_enc)
    if kind == "proxy_password":
        assert item_id is not None
        proxy = await _require_row(db, Proxy, item_id, "代理")
        return _decrypt(proxy.password_enc)
    if kind == "webhook_token":
        from ..db.models.system import SystemSetting
        from . import webhooks as webhooks_api

        assert account_id is not None
        await _require_account(db, account_id)
        row = await db.get(SystemSetting, webhooks_api._setting_key(account_id))
        if row is None or not isinstance(row.value, dict):
            return None
        try:
            return webhooks_api._config_token(row.value) or None
        except ValueError as exc:
            raise _bad(
                "SECRET_DECRYPT_FAILED",
                "密钥解密失败：可能 MASTER_KEY 已变更，请重新保存这一项",
                status.HTTP_409_CONFLICT,
            ) from exc
    if kind == "plugin_repo_credential":
        assert item_id is not None
        repo = await _require_row(db, PluginRepo, item_id, "插件仓库")
        return _decrypt(repo.credential_enc)
    if kind == "plugin_config_secret":
        assert plugin_key and field
        return await _plugin_config_secret(db, plugin_key, field, account_id)
    raise _bad("SECRET_KIND_UNKNOWN", f"未知的密钥类型: {kind}")


@router.get(
    "/reveal",
    response_model=SecretRevealOut,
    summary="按需查看一项已保存的密钥（每次读取写审计日志）",
)
async def reveal_secret(
    response: Response,
    db: DBSession,
    user: CurrentUser,
    kind: SecretKind = Query(..., description="密钥类型"),
    account_id: int | None = Query(default=None, description="账号级密钥的账号 ID；插件配置留空表示全局配置"),
    id: int | None = Query(default=None, description="通知 Bot / AI 供应商 / 密钥池 Key / 代理 / 插件仓库的 ID"),
    plugin_key: str | None = Query(default=None, max_length=128, description="plugin_config_secret：插件 key"),
    field: str | None = Query(default=None, max_length=128, description="plugin_config_secret：顶层字段名"),
) -> SecretRevealOut:
    for key, value in NO_STORE_HEADERS.items():
        response.headers[key] = value
    label, required = SECRET_KINDS[kind]
    params = {"account_id": account_id, "id": id, "plugin_key": plugin_key, "field": field}
    missing = [name for name in required if params.get(name) in (None, "")]
    if missing:
        raise _bad("SECRET_TARGET_REQUIRED", f"缺少参数: {', '.join(missing)}")
    value = await read_secret(
        db,
        kind,
        account_id=account_id,
        item_id=id,
        plugin_key=(plugin_key or "").strip() or None,
        field=(field or "").strip() or None,
    )
    target_parts = [kind]
    for name in ("account_id", "id", "plugin_key", "field"):
        if params.get(name) not in (None, ""):
            target_parts.append(f"{name}={params[name]}")
    await audit.write(
        db,
        user.id,
        "secret.reveal",
        target=" ".join(target_parts),
        detail={
            "kind": kind,
            "label": label,
            "configured": value is not None,
            **{name: params[name] for name in ("account_id", "id", "plugin_key", "field") if params.get(name) not in (None, "")},
        },
    )
    await db.commit()
    return SecretRevealOut(kind=kind, label=label, configured=value is not None, value=value)
