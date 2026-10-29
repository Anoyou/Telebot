"""功能矩阵与账号-功能开关 REST API（PRD §9.2）。

Endpoint：
  - GET  /api/feature-matrix                      → 一次返回 N×M 矩阵
  - GET  /api/accounts/{aid}/features            → 该账号所有 feature 开关
  - PATCH /api/accounts/{aid}/features/{key}     → 启停或调整 config
  - GET  /api/plugins/{key}/config                → 获取 global config
  - PUT  /api/plugins/{key}/config               → 设置 global config
  - GET  /api/accounts/{aid}/features/{key}/config → 获取最终生效配置
  - POST /api/plugins/{key}/config/validate       → 验证配置是否符合 schema
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from .. import plugin_compat
from ..db.models.account import Account
from ..db.models.feature import AccountFeature, Feature
from ..db.models.plugin import InstalledPlugin
from ..deps import CurrentUser, DBSession
from ..schemas.feature import (
    AccountDirectPassthroughOrderUpdate,
    AccountFeatureConfigUpdate,
    AccountFeatureDirectPassthroughUpdate,
    AccountFeatureItem,
    AccountFeatureToggle,
    ConfigValidationResponse,
    FeatureMatrixResponse,
    PluginConfigActionControlRequest,
    PluginConfigActionJobResponse,
    PluginConfigActionRequest,
    PluginConfigActionResponse,
    PluginDisplayOrder,
    PluginGlobalConfigResponse,
    PluginGlobalConfigUpdate,
)
from ..services import audit, feature_service, platform_capabilities
from ..services.plugin_config_action_jobs import (
    control_plugin_config_action_job,
    create_plugin_config_action_job,
    get_plugin_config_action_job,
    job_response,
    list_plugin_config_action_jobs,
)
from ..services.plugin_config_actions import (
    PluginConfigActionError,
    PluginConfigActionNotFound,
    PluginConfigActionUnavailable,
    run_plugin_config_action,
)
from ..services.plugin_config_secrets import is_secret_envelope
from ..services.redactor import is_sensitive_key, redact_value
from ..worker.plugins.ai_facade import AIQuotaError, AIUnavailableError
from ..worker.plugins.http_facade import PluginHTTPError

router = APIRouter(tags=["features"])

log = logging.getLogger(__name__)


def _bad(
    code: str,
    message: str,
    status: int = 400,
    *,
    field_errors: dict[str, str] | None = None,
) -> HTTPException:
    detail: dict[str, object] = {"code": code, "message": message}
    if field_errors:
        # 插件 on_config_validate 返回的逐字段原因（0.110.0+），配置页按字段显示。
        detail["fields"] = dict(field_errors)
    return HTTPException(status_code=status, detail=detail)


def _declares_direct_passthrough(manifest: object) -> bool:
    if not isinstance(manifest, dict):
        return False
    capabilities = manifest.get("capabilities")
    if not isinstance(capabilities, dict):
        return False
    raw = capabilities.get("telegram_direct_passthrough")
    if raw is True:
        return True
    return isinstance(raw, dict) and raw.get("enabled") is True


def _allow_account_direct_passthrough_config(
    schema: dict[str, object],
    manifest: object,
) -> dict[str, object]:
    """Expose the platform-owned account opt-in only to declaring plugins."""

    if not _declares_direct_passthrough(manifest):
        return schema
    properties = schema.setdefault("properties", {})
    if isinstance(properties, dict):
        properties.setdefault(
            "direct_passthrough",
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "enabled": {
                        "type": "boolean",
                        "default": False,
                        "description": "账号二次开关：仅决定插件是否加入直通调度",
                    },
                    "priority": {
                        "type": "integer",
                        "minimum": 0,
                        "default": 1000,
                        "description": "数值越小优先级越高；同值按插件 key 稳定排序",
                    },
                },
            },
        )
    return schema


def _merge_direct_passthrough_config(
    config: dict[str, object] | None,
    *,
    enabled: bool | None = None,
    priority: int | None = None,
) -> dict[str, object]:
    """合并平台直通字段，保留插件其它历史配置与未改动的直通子字段。"""

    merged = dict(config or {})
    current = merged.get("direct_passthrough")
    block: dict[str, object] = dict(current) if isinstance(current, dict) else {}
    if enabled is not None:
        block["enabled"] = bool(enabled)
    if priority is not None:
        block["priority"] = max(0, int(priority))
    if "enabled" not in block:
        block["enabled"] = False
    # 消费只由插件三态结果决定；清理历史独占/失败截断字段避免旧语义复活。
    block.pop("exclusive", None)
    block.pop("failure_policy", None)
    block.pop("consume_on_failure", None)
    merged["direct_passthrough"] = block
    return merged


def _with_direct_passthrough_enabled(
    config: dict[str, object] | None,
    enabled: bool,
) -> dict[str, object]:
    """兼容旧调用：只改 enabled，保留 priority。"""

    return _merge_direct_passthrough_config(config, enabled=enabled)


def _account_config_schema(
    manifest: object,
    config: dict[str, object],
) -> dict[str, object] | None:
    """Build the account schema and protect the platform-owned direct opt-in."""

    declares_direct = _declares_direct_passthrough(manifest)
    if "direct_passthrough" in config and not declares_direct:
        raise _bad(
            "CONFIG_VALIDATION_ERROR",
            "配置验证失败: direct_passthrough: 仅声明 telegram_direct_passthrough 的插件可使用此字段",
        )

    raw_schema = manifest.get("config_schema") if isinstance(manifest, dict) else None
    if isinstance(raw_schema, dict):
        scoped_schema = feature_service.config_schema_for_scope(raw_schema, "account")
    elif declares_direct:
        # A direct-only plugin still needs a schema for the platform-owned opt-in.
        scoped_schema = {"type": "object", "properties": {}}
    else:
        return None
    return _allow_account_direct_passthrough_config(scoped_schema, manifest)


def _feature_schema(feature: object) -> object:
    manifest = getattr(feature, "manifest", None)
    return manifest.get("config_schema") if isinstance(manifest, dict) else None


def _secret_list_specs(key: str | None, schema: object = None) -> dict[str, dict[str, str]]:
    """配置里声明了 ``x-secret-list`` 的字段；旧版 chatgpt_image 没有声明时由兼容表补齐。"""

    from ..services import plugin_secret_list

    specs = plugin_secret_list.secret_list_specs(schema)
    if specs:
        return specs
    return plugin_compat.legacy_secret_list_fields(key, schema)


def _sanitize_chatgpt_image_config(config: dict[str, object]) -> dict[str, object]:
    """兼容旧调用：等同于对旧版 chatgpt_image（无 x-secret-list 声明）执行 :func:`_sanitize_config`。"""

    return _sanitize_config(config, plugin_compat.CHATGPT_IMAGE)


def _sanitize_config(
    config: dict[str, object],
    key: str | None = None,
    schema: object = None,
) -> dict[str, object]:
    from ..services import plugin_secret_list
    from ..services.plugin_config_secrets import decrypt_config_secrets, mask_config_secrets

    # 先遮罩加密信封/敏感键，再走通用 redactor。
    masked = redact_value(mask_config_secrets(dict(config or {})))
    specs = _secret_list_specs(key, schema)
    if specs:
        # secret_id 必须基于明文，而不是每次 rekey 都会变化的 Fernet 信封。
        masked.update(plugin_secret_list.sanitize(decrypt_config_secrets(dict(config or {})), specs))
    return masked


def _preserve_existing_sensitive_values(
    existing: dict[str, object] | None,
    incoming: dict[str, object],
    key: str | None = None,
    schema: object = None,
) -> dict[str, object]:
    merged = dict(incoming)
    existing_dict = dict(existing or {})
    specs = _secret_list_specs(key, schema)
    if specs:
        from ..services import plugin_secret_list
        from ..services.plugin_config_secrets import decrypt_config_secrets

        merged = plugin_secret_list.preserve(decrypt_config_secrets(existing_dict), merged, specs)
    skip_keys = set(specs)
    for spec in specs.values():
        if spec.get("legacy_field"):
            skip_keys.add(spec["legacy_field"])
    for item_key, value in existing_dict.items():
        if item_key in skip_keys and item_key in merged:
            continue
        if not is_sensitive_key(str(item_key)) and not is_secret_envelope(value):
            continue
        if item_key not in merged or merged.get(item_key) in ("", None, "***"):
            merged[item_key] = value
    return merged


def _preserve_existing_read_only_values(
    existing: dict[str, object] | None,
    incoming: dict[str, object],
    manifest: object,
) -> dict[str, object]:
    """Keep server-owned read-only config fields across replacement-style saves."""

    merged = dict(incoming)
    existing_dict = dict(existing or {})
    raw_schema = manifest.get("config_schema") if isinstance(manifest, dict) else None
    properties = raw_schema.get("properties") if isinstance(raw_schema, dict) else None
    if not isinstance(properties, dict):
        return merged
    for item_key, field in properties.items():
        if (
            isinstance(field, dict)
            and field.get("readOnly") is True
        ):
            if item_key in existing_dict:
                merged[str(item_key)] = existing_dict[item_key]
            else:
                merged.pop(str(item_key), None)
    return merged


def _normalize_feature_config(key: str, config: dict[str, object]) -> dict[str, object]:
    """旧版本插件的历史配置改写（组合保存等不经过 on_config_validate 的路径使用）。"""

    return plugin_compat.normalize_legacy_feature_config(key, dict(config))


def _schema_validation_error(manifest: object, config: dict[str, object]) -> tuple[dict[str, object], str | None]:
    scoped_schema = _account_config_schema(manifest, config)
    if scoped_schema is None:
        return config, None
    config = feature_service.apply_required_config_defaults(config, scoped_schema)
    validation = feature_service.validate_config_against_schema(config, scoped_schema)
    if validation.valid:
        return config, None
    return config, f"配置验证失败: {'; '.join(f'{e.field}: {e.message}' for e in validation.errors)}"


async def _validated_account_config(
    db: DBSession,
    aid: int,
    feature: Feature,
    config: dict[str, object],
) -> dict[str, object]:
    """账号配置保存前：JSON Schema 校验 → 插件 on_config_validate（2 秒，可改写 / 拒绝）→ 再校验一次。

    插件没有实现 on_config_validate 时套用旧版本插件的历史配置改写（plugin_compat）。
    """

    from ..services.plugin_config_hooks import SCOPE_ACCOUNT, format_validate_errors, run_config_validate

    config, error = _schema_validation_error(feature.manifest, dict(config))
    if error:
        raise _bad("CONFIG_VALIDATION_ERROR", error)
    account = await db.get(Account, aid)
    outcome = await run_config_validate(db, feature=feature, account=account, config=config, scope=SCOPE_ACCOUNT)
    if outcome.errors:
        raise _bad("CONFIG_VALIDATION_ERROR", format_validate_errors(outcome.errors), field_errors=outcome.errors)
    if outcome.config == config:
        return config
    config, error = _schema_validation_error(feature.manifest, dict(outcome.config))
    if error:
        raise _bad("CONFIG_VALIDATION_ERROR", f"插件规范化后的{error}")
    return config


async def _notify_config_saved(db: DBSession, aid: int, feature: Feature, config: dict[str, object]) -> None:
    from ..services.plugin_config_hooks import SCOPE_ACCOUNT, schedule_config_saved

    try:
        account = await db.get(Account, aid)
        await schedule_config_saved(db, feature=feature, account=account, config=config, scope=SCOPE_ACCOUNT)
    except Exception:  # noqa: BLE001 - 后台通知不影响保存结果
        log.debug("on_config_saved 调度失败 plugin=%s", feature.key, exc_info=True)


# ─────────────────────────────────────────────────────
# 矩阵
# ─────────────────────────────────────────────────────
@router.get("/api/feature-matrix", response_model=FeatureMatrixResponse)
async def get_feature_matrix(db: DBSession, _user: CurrentUser) -> FeatureMatrixResponse:
    """返回 N(账号) × M(功能) 矩阵。"""
    data = await feature_service.feature_matrix(db)
    return FeatureMatrixResponse(**data)


@router.get("/api/feature-matrix/order", response_model=PluginDisplayOrder)
async def get_plugin_display_order(db: DBSession, _user: CurrentUser) -> PluginDisplayOrder:
    """插件中心卡片的展示顺序；未自定义时为空列表（按默认顺序展示）。"""
    return PluginDisplayOrder(keys=await feature_service.get_plugin_display_order(db))


@router.put("/api/feature-matrix/order", response_model=PluginDisplayOrder)
async def put_plugin_display_order(
    payload: PluginDisplayOrder, db: DBSession, user: CurrentUser
) -> PluginDisplayOrder:
    """保存插件中心卡片的展示顺序（iOS 长按拖动排序）。

    只影响 ``GET /api/feature-matrix`` 的 features 顺序，不影响插件执行或直通优先级；
    未出现在列表里的插件排在后面。整表覆盖写入，重复提交同一顺序结果相同。
    """
    for key in payload.keys:
        if len(key) > 128:
            raise _bad("VALIDATION_ERROR", "插件 key 过长")
    keys = await feature_service.set_plugin_display_order(db, payload.keys)
    await audit.write(
        db,
        user.id,
        "feature.display_order",
        target="plugins",
        detail={"count": len(keys)},
    )
    await db.commit()
    return PluginDisplayOrder(keys=keys)


# ─────────────────────────────────────────────────────
# 单账号 feature 列表
# ─────────────────────────────────────────────────────
@router.get(
    "/api/accounts/{aid}/features",
    response_model=list[AccountFeatureItem],
)
async def list_account_features(
    aid: int, db: DBSession, _user: CurrentUser
) -> list[AccountFeatureItem]:
    """列出该账号所有 ``account_feature`` 行。"""
    if await db.get(Account, aid) is None:
        raise _bad("ACCOUNT_NOT_FOUND", "账号不存在", 404)
    rows = await feature_service.get_account_features(db, aid)
    schemas = {r.feature_key: _feature_schema(await db.get(Feature, r.feature_key)) for r in rows}
    return [
        AccountFeatureItem(
            feature_key=r.feature_key,
            enabled=r.enabled,
            state=r.state,
            last_error=r.last_error,
            config=_sanitize_config(dict(r.config or {}), r.feature_key, schemas.get(r.feature_key)),
        )
        for r in rows
    ]


# ─────────────────────────────────────────────────────
# 启停 / 改 config
# ─────────────────────────────────────────────────────
@router.patch(
    "/api/accounts/{aid}/features/{key}",
    response_model=AccountFeatureItem,
)
async def patch_account_feature(
    aid: int,
    key: str,
    payload: AccountFeatureToggle,
    db: DBSession,
    user: CurrentUser,
) -> AccountFeatureItem:
    """启用 / 禁用某 feature，或更新它的 config。

    若 feature key 在 ``feature`` 表里没有登记，会拒绝（避免误开未知插件）。
    若提供了 config，会验证 JSON Schema 并保存。
    """
    if await db.get(Account, aid) is None:
        raise _bad("ACCOUNT_NOT_FOUND", "账号不存在", 404)
    # 校验 feature 存在（首次调用矩阵或 list 时已 seed 过；这里也 seed 一次以幂等）
    await feature_service.seed_builtin_features(db)
    feature = await db.get(Feature, key)
    if feature is None:
        raise _bad("FEATURE_NOT_FOUND", f"未注册的 feature: {key}", 404)

    # 如果提供了 config，验证 JSON Schema
    if payload.config is not None:
        existing = await db.get(AccountFeature, (aid, key))
        payload.config = _preserve_existing_sensitive_values(
            dict(existing.config or {}) if existing is not None else None,
            dict(payload.config),
            key,
            _feature_schema(feature),
        )
        payload.config = await _validated_account_config(db, aid, feature, payload.config)

    try:
        af = await feature_service.set_account_feature(
            db,
            aid,
            key,
            enabled=payload.enabled,
            config=payload.config,
            triggered_by_user_id=user.id,
        )
    except platform_capabilities.PluginCapabilityBlocked as exc:
        raise _bad(
            exc.error_code,
            str(exc),
            409,
        ) from exc
    await audit.write(
        db,
        user.id,
        "feature.toggle",
        target=f"account:{aid}/feature:{key}",
        detail={"enabled": payload.enabled},
    )
    await db.commit()
    if payload.config is not None:
        await _notify_config_saved(db, aid, feature, dict(af.config or {}))
    return AccountFeatureItem(
        feature_key=af.feature_key,
        enabled=af.enabled,
        state=af.state,
        last_error=af.last_error,
        config=_sanitize_config(dict(af.config or {}), key, _feature_schema(feature)),
    )


# ─────────────────────────────────────────────────────
# 账号级配置更新（仅更新 config，不改变 enabled）
# ─────────────────────────────────────────────────────
@router.patch(
    "/api/accounts/{aid}/features/{key}/config",
    response_model=AccountFeatureItem,
)
async def update_account_feature_config(
    aid: int,
    key: str,
    payload: AccountFeatureConfigUpdate,
    db: DBSession,
    user: CurrentUser,
) -> AccountFeatureItem:
    """仅更新账号级配置，不改变 enabled 状态。

    会验证 JSON Schema 并保存。
    """
    if await db.get(Account, aid) is None:
        raise _bad("ACCOUNT_NOT_FOUND", "账号不存在", 404)
    await feature_service.seed_builtin_features(db)
    feature = await db.get(Feature, key)
    if feature is None:
        raise _bad("FEATURE_NOT_FOUND", f"未注册的 feature: {key}", 404)

    # 验证 JSON Schema
    existing = await db.get(AccountFeature, (aid, key))
    existing_config = dict(existing.config or {}) if existing is not None else None
    payload.config = _preserve_existing_sensitive_values(
        existing_config,
        dict(payload.config),
        key,
        _feature_schema(feature),
    )
    payload.config = _preserve_existing_read_only_values(
        existing_config,
        payload.config,
        feature.manifest,
    )
    payload.config = await _validated_account_config(db, aid, feature, payload.config)

    af = await feature_service.set_account_feature(
        db,
        aid,
        key,
        enabled=bool(existing.enabled) if existing is not None else False,
        config=payload.config,
        triggered_by_user_id=user.id,
    )
    await audit.write(
        db,
        user.id,
        "feature.config.update",
        target=f"account:{aid}/feature:{key}",
        detail={"config_keys": sorted(payload.config.keys())},
    )
    await db.commit()
    await _notify_config_saved(db, aid, feature, dict(af.config or {}))
    return AccountFeatureItem(
        feature_key=af.feature_key,
        enabled=af.enabled,
        state=af.state,
        last_error=af.last_error,
        config=_sanitize_config(dict(af.config or {}), key, _feature_schema(feature)),
    )


@router.patch(
    "/api/accounts/{aid}/features/{key}/direct-passthrough",
    response_model=AccountFeatureItem,
)
async def update_account_feature_direct_passthrough(
    aid: int,
    key: str,
    payload: AccountFeatureDirectPassthroughUpdate,
    db: DBSession,
    user: CurrentUser,
) -> AccountFeatureItem:
    """独立更新裸直通配置，避免插件历史配置失效时无法紧急关闭。"""

    if await db.get(Account, aid) is None:
        raise _bad("ACCOUNT_NOT_FOUND", "账号不存在", 404)
    await feature_service.seed_builtin_features(db)
    feature = await db.get(Feature, key)
    if feature is None:
        raise _bad("FEATURE_NOT_FOUND", f"未注册的 feature: {key}", 404)
    if not _declares_direct_passthrough(feature.manifest):
        raise _bad(
            "CONFIG_VALIDATION_ERROR",
            "配置验证失败: direct_passthrough: 插件未声明 telegram_direct_passthrough",
        )

    existing = await db.get(AccountFeature, (aid, key))
    config = _merge_direct_passthrough_config(
        dict(existing.config or {}) if existing is not None else None,
        enabled=payload.enabled,
        priority=payload.priority,
    )
    af = await feature_service.set_account_feature(
        db,
        aid,
        key,
        enabled=bool(existing.enabled) if existing is not None else False,
        config=config,
        triggered_by_user_id=user.id,
    )
    await audit.write(
        db,
        user.id,
        "feature.direct_passthrough.update",
        target=f"account:{aid}/feature:{key}",
        detail={
            "enabled": payload.enabled,
            "priority": payload.priority,
        },
    )
    await db.commit()
    return AccountFeatureItem(
        feature_key=af.feature_key,
        enabled=af.enabled,
        state=af.state,
        last_error=af.last_error,
        config=_sanitize_config(dict(af.config or {}), key, _feature_schema(feature)),
    )


@router.put(
    "/api/accounts/{aid}/direct-passthrough/order",
    response_model=list[AccountFeatureItem],
)
async def reorder_account_direct_passthrough(
    aid: int,
    payload: AccountDirectPassthroughOrderUpdate,
    db: DBSession,
    user: CurrentUser,
) -> list[AccountFeatureItem]:
    """按列表顺序写入 priority（0, 10, 20…），仅影响已开启直通的声明插件。"""

    if await db.get(Account, aid) is None:
        raise _bad("ACCOUNT_NOT_FOUND", "账号不存在", 404)
    await feature_service.seed_builtin_features(db)

    order = [str(item or "").strip() for item in payload.order if str(item or "").strip()]
    if not order:
        raise _bad("VALIDATION_ERROR", "order 不能为空")
    if len(order) != len(set(order)):
        raise _bad("VALIDATION_ERROR", "order 中存在重复的插件 key")

    results: list[AccountFeatureItem] = []
    for index, key in enumerate(order):
        feature = await db.get(Feature, key)
        if feature is None:
            raise _bad("FEATURE_NOT_FOUND", f"未注册的 feature: {key}", 404)
        if not _declares_direct_passthrough(feature.manifest):
            raise _bad(
                "CONFIG_VALIDATION_ERROR",
                f"配置验证失败: {key} 未声明 telegram_direct_passthrough",
            )
        existing = await db.get(AccountFeature, (aid, key))
        if existing is None or not bool(existing.enabled):
            raise _bad(
                "VALIDATION_ERROR",
                f"插件 {key} 未在本账号启用，无法参与直通排序",
            )
        block = existing.config.get("direct_passthrough") if isinstance(existing.config, dict) else None
        if not (isinstance(block, dict) and block.get("enabled") is True):
            raise _bad(
                "VALIDATION_ERROR",
                f"插件 {key} 未开启账号级裸直通，无法参与排序",
            )
        config = _merge_direct_passthrough_config(
            dict(existing.config or {}),
            priority=index * 10,
        )
        af = await feature_service.set_account_feature(
            db,
            aid,
            key,
            enabled=True,
            config=config,
            triggered_by_user_id=user.id,
        )
        results.append(
            AccountFeatureItem(
                feature_key=af.feature_key,
                enabled=af.enabled,
                state=af.state,
                last_error=af.last_error,
                config=_sanitize_config(dict(af.config or {}), key, _feature_schema(feature)),
            )
        )

    await audit.write(
        db,
        user.id,
        "feature.direct_passthrough.reorder",
        target=f"account:{aid}",
        detail={"order": order},
    )
    await db.commit()
    return results


@router.post(
    "/api/accounts/{aid}/features/{key}/config/actions/{action_key}",
    response_model=PluginConfigActionResponse,
)
async def run_account_feature_config_action(
    aid: int,
    key: str,
    action_key: str,
    payload: PluginConfigActionRequest,
    db: DBSession,
    user: CurrentUser,
) -> PluginConfigActionResponse:
    """运行插件声明的配置页动作。"""

    account = await db.get(Account, aid)
    if account is None:
        raise _bad("ACCOUNT_NOT_FOUND", "账号不存在", 404)
    await feature_service.seed_builtin_features(db)
    feature = await db.get(Feature, key)
    if feature is None:
        raise _bad("FEATURE_NOT_FOUND", f"未注册的 feature: {key}", 404)
    installed_plugin = await db.get(InstalledPlugin, key)

    effective_config = await feature_service.get_effective_plugin_config(db, aid, key)
    try:
        result = await run_plugin_config_action(
            db,
            account=account,
            feature=feature,
            action_key=action_key,
            effective_config=effective_config,
            current_config=payload.config,
            action_input=payload.input,
            installed_plugin=installed_plugin,
        )
    except PluginConfigActionNotFound as exc:
        raise _bad("CONFIG_ACTION_NOT_FOUND", str(exc), 404) from exc
    except PluginConfigActionUnavailable as exc:
        raise _bad("CONFIG_ACTION_UNAVAILABLE", str(exc), 400) from exc
    except PluginHTTPError as exc:
        raise _bad("CONFIG_ACTION_HTTP_REJECTED", str(exc), 400) from exc
    except AIQuotaError as exc:
        raise _bad("CONFIG_ACTION_AI_QUOTA", str(exc), 429) from exc
    except AIUnavailableError as exc:
        raise _bad("CONFIG_ACTION_AI_UNAVAILABLE", str(exc), 503) from exc
    except PluginConfigActionError as exc:
        raise _bad("CONFIG_ACTION_FAILED", str(exc), 400) from exc
    except Exception as exc:
        # 未预期异常按服务端错误处理；文本可能含插件内部路径/堆栈，只落日志不回显。
        log.exception(
            "plugin config action failed: account=%s feature=%s action=%s", aid, key, action_key
        )
        raise _bad(
            "CONFIG_ACTION_INTERNAL", "配置动作执行异常，请查看服务端日志", 500
        ) from exc

    await audit.write(
        db,
        user.id,
        "feature.config.action",
        target=f"account:{aid}/feature:{key}",
        detail={
            "action_key": action_key,
            "config_patch_keys": sorted((result.get("config_patch") or {}).keys()),
        },
    )
    await db.commit()
    return PluginConfigActionResponse(**result)


@router.post(
    "/api/accounts/{aid}/features/{key}/config/actions/{action_key}/jobs",
    response_model=PluginConfigActionJobResponse,
)
async def start_account_feature_config_action_job(
    aid: int,
    key: str,
    action_key: str,
    payload: PluginConfigActionRequest,
    db: DBSession,
    user: CurrentUser,
) -> PluginConfigActionJobResponse:
    """启动插件声明的配置页后台动作。"""

    account = await db.get(Account, aid)
    if account is None:
        raise _bad("ACCOUNT_NOT_FOUND", "账号不存在", 404)
    await feature_service.seed_builtin_features(db)
    feature = await db.get(Feature, key)
    if feature is None:
        raise _bad("FEATURE_NOT_FOUND", f"未注册的 feature: {key}", 404)
    installed_plugin = await db.get(InstalledPlugin, key)

    effective_config = await feature_service.get_effective_plugin_config(db, aid, key)
    try:
        job = await create_plugin_config_action_job(
            db,
            account=account,
            feature=feature,
            action_key=action_key,
            effective_config=effective_config,
            current_config=payload.config,
            action_input=payload.input,
            installed_plugin=installed_plugin,
        )
    except PluginConfigActionNotFound as exc:
        raise _bad("CONFIG_ACTION_NOT_FOUND", str(exc), 404) from exc
    except PluginConfigActionUnavailable as exc:
        raise _bad("CONFIG_ACTION_ALREADY_RUNNING", str(exc), 409) from exc

    await audit.write(
        db,
        user.id,
        "feature.config.action.job.start",
        target=f"account:{aid}/feature:{key}",
        detail={"action_key": action_key, "job_id": job.job_id},
    )
    await db.commit()
    return job_response(job, logs=[])


@router.get(
    "/api/plugin-config-action-jobs/{job_id}",
    response_model=PluginConfigActionJobResponse,
)
async def get_config_action_job_status(
    job_id: str,
    db: DBSession,
    _user: CurrentUser,
) -> PluginConfigActionJobResponse:
    """查询配置动作后台任务状态与过程日志。"""

    response = await get_plugin_config_action_job(db, job_id)
    if response is None:
        raise _bad("CONFIG_ACTION_JOB_NOT_FOUND", "配置动作任务不存在", 404)
    return response


@router.post(
    "/api/plugin-config-action-jobs/{job_id}/control",
    response_model=PluginConfigActionJobResponse,
)
async def control_config_action_job(
    job_id: str,
    payload: PluginConfigActionControlRequest,
    db: DBSession,
    user: CurrentUser,
) -> PluginConfigActionJobResponse:
    """中断或终止配置动作后台任务。"""

    response = await control_plugin_config_action_job(db, job_id, action=payload.action)
    if response is None:
        raise _bad("CONFIG_ACTION_JOB_NOT_FOUND", "配置动作任务不存在", 404)
    await audit.write(
        db,
        user.id,
        f"feature.config.action.job.{payload.action}",
        target=f"plugin-config-action-job:{job_id}",
        detail={"job_id": job_id, "action": payload.action},
    )
    await db.commit()
    return response


@router.get(
    "/api/accounts/{aid}/features/{key}/config/action-jobs",
    response_model=list[PluginConfigActionJobResponse],
)
async def list_account_feature_config_action_jobs(
    aid: int,
    key: str,
    db: DBSession,
    _user: CurrentUser,
    limit: int = Query(10, ge=1, le=50),
) -> list[PluginConfigActionJobResponse]:
    """查询某账号某插件最近的配置动作任务，用于恢复后台窗口。"""

    if await db.get(Account, aid) is None:
        raise _bad("ACCOUNT_NOT_FOUND", "账号不存在", 404)
    await feature_service.seed_builtin_features(db)
    if await db.get(Feature, key) is None:
        raise _bad("FEATURE_NOT_FOUND", f"未注册的 feature: {key}", 404)
    return await list_plugin_config_action_jobs(
        db,
        account_id=aid,
        plugin_key=key,
        limit=limit,
    )


# ─────────────────────────────────────────────────────
# Global Config API
# ─────────────────────────────────────────────────────
@router.get(
    "/api/plugins/{key}/config",
    response_model=PluginGlobalConfigResponse,
)
async def get_plugin_global_config(
    key: str,
    db: DBSession,
    _user: CurrentUser,
) -> PluginGlobalConfigResponse:
    """获取插件的 global config。"""
    await feature_service.seed_builtin_features(db)
    feature = await db.get(Feature, key)
    if feature is None:
        raise _bad("FEATURE_NOT_FOUND", f"未注册的插件: {key}", 404)

    global_config = await feature_service.get_plugin_global_config(db, key)
    return PluginGlobalConfigResponse(
        plugin_key=key,
        config=_sanitize_config(global_config, key, _feature_schema(feature)),
        global_config=_sanitize_config(global_config, key, _feature_schema(feature)),
    )


@router.put(
    "/api/plugins/{key}/config",
    response_model=PluginGlobalConfigResponse,
)
async def set_plugin_global_config(
    key: str,
    payload: PluginGlobalConfigUpdate,
    db: DBSession,
    user: CurrentUser,
) -> PluginGlobalConfigResponse:
    """设置插件的 global config。

    - 验证配置是否符合 config_schema。
    - 仅更新标记为 level="global" 的字段。
    - 通知所有使用该插件的账号的 worker reload。
    """
    existing_global_config = await feature_service.get_plugin_global_config(db, key)
    await feature_service.seed_builtin_features(db)
    global_feature = await db.get(Feature, key)
    payload.config = _preserve_existing_sensitive_values(
        existing_global_config,
        dict(payload.config),
        key,
        _feature_schema(global_feature),
    )
    if global_feature is not None:
        from ..services.plugin_config_hooks import SCOPE_GLOBAL, format_validate_errors, run_config_validate

        outcome = await run_config_validate(
            db, feature=global_feature, account=None, config=payload.config, scope=SCOPE_GLOBAL
        )
        if outcome.errors:
            raise _bad("CONFIG_VALIDATION_ERROR", format_validate_errors(outcome.errors), field_errors=outcome.errors)
        payload.config = outcome.config
    try:
        global_config = await feature_service.set_plugin_global_config(
            db, key, payload.config
        )
    except ValueError as e:
        raise _bad("CONFIG_VALIDATION_ERROR", str(e)) from e

    await audit.write(
        db,
        user.id,
        "feature.global_config.update",
        target=f"plugin:{key}",
        detail={"global_config_keys": sorted(global_config.keys())},
    )
    await db.commit()
    if global_feature is not None:
        from ..services.plugin_config_hooks import SCOPE_GLOBAL, schedule_config_saved

        await schedule_config_saved(db, feature=global_feature, account=None, config=global_config, scope=SCOPE_GLOBAL)

    return PluginGlobalConfigResponse(
        plugin_key=key,
        config=_sanitize_config(global_config, key, _feature_schema(global_feature)),
        global_config=_sanitize_config(global_config, key, _feature_schema(global_feature)),
    )


# ─────────────────────────────────────────────────────
# 获取账号级最终生效配置（合并后）
# ─────────────────────────────────────────────────────
@router.get(
    "/api/accounts/{aid}/features/{key}/config",
    response_model=dict[str, object],
)
async def get_effective_config(
    aid: int,
    key: str,
    db: DBSession,
    _user: CurrentUser,
) -> dict[str, object]:
    """获取某账号某插件的最终生效配置。

    合并顺序：schema defaults < global config < account config
    """
    if await db.get(Account, aid) is None:
        raise _bad("ACCOUNT_NOT_FOUND", "账号不存在", 404)
    await feature_service.seed_builtin_features(db)
    feature = await db.get(Feature, key)
    if feature is None:
        raise _bad("FEATURE_NOT_FOUND", f"未注册的 feature: {key}", 404)

    effective_config = await feature_service.get_effective_plugin_config(db, aid, key)
    return _sanitize_config(effective_config, key, _feature_schema(feature))


# ─────────────────────────────────────────────────────
# 配置验证
# ─────────────────────────────────────────────────────
@router.post(
    "/api/plugins/{key}/config/validate",
    response_model=ConfigValidationResponse,
)
async def validate_plugin_config(
    key: str,
    payload: PluginGlobalConfigUpdate,
    db: DBSession,
    _user: CurrentUser,
) -> ConfigValidationResponse:
    """验证配置是否符合插件的 config_schema。"""
    await feature_service.seed_builtin_features(db)
    feature = await db.get(Feature, key)
    if feature is None:
        raise _bad("FEATURE_NOT_FOUND", f"未注册的插件: {key}", 404)

    config_schema = (feature.manifest or {}).get("config_schema")
    if not config_schema:
        return {"valid": True, "errors": []}

    return feature_service.validate_config_against_schema(payload.config, config_schema)


__all__ = ["router"]
