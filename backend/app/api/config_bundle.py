"""账号 Config Bundle 导出 / dry-run API。"""

from __future__ import annotations

import json

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy import select

from ..db.models.account import Account
from ..db.models.command import AccountCommandLink, CommandTemplate
from ..db.models.feature import AccountFeature, Feature
from ..db.models.rule import Rule
from ..deps import CurrentUser, DBSession
from ..schemas.config_bundle import ConfigBundleDryRunResponse, ConfigBundleExport
from ..services import feature_service
from ..services.config_bundle_service import (
    BundleTooLarge,
    assert_bundle_size,
    build_config_bundle,
    compare_bundles,
)

router = APIRouter(tags=["config-bundle"])


def _bad(code: str, message: str, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


async def _load_bundle(db, aid: int) -> ConfigBundleExport:
    account = await db.get(Account, aid)
    if account is None:
        raise _bad("ACCOUNT_NOT_FOUND", "账号不存在", 404)

    feature_rows = (
        await db.execute(select(AccountFeature).where(AccountFeature.account_id == aid))
    ).scalars().all()
    rule_rows = (await db.execute(select(Rule).where(Rule.account_id == aid))).scalars().all()
    command_link_rows = (
        await db.execute(
            select(AccountCommandLink, CommandTemplate)
            .join(CommandTemplate, CommandTemplate.id == AccountCommandLink.template_id)
            .where(AccountCommandLink.account_id == aid, AccountCommandLink.enabled.is_(True))
        )
    ).all()
    return build_config_bundle(account, feature_rows, rule_rows, command_link_rows)


async def _available_feature_map(db) -> dict[str, str]:
    await feature_service.seed_builtin_features(db)
    rows = (await db.execute(select(Feature))).scalars().all()
    return {row.key: row.display_name for row in rows}


async def _available_command_templates(db) -> dict[str, dict[str, object]]:
    rows = (await db.execute(select(CommandTemplate))).scalars().all()
    return {
        row.name: {
            "template_name": row.name,
            "aliases": list(row.aliases or []),
            "type": row.type,
        }
        for row in rows
    }


@router.get("/api/accounts/{aid}/config-bundle/export")
async def export_config_bundle(
    aid: int,
    db: DBSession,
    _user: CurrentUser,
) -> Response:
    bundle = await _load_bundle(db, aid)
    try:
        body = assert_bundle_size(bundle)
    except BundleTooLarge as exc:
        raise _bad("BUNDLE_TOO_LARGE", "bundle 超过 1MB，请拆分后再导出", 413) from exc

    filename = f"telebot-config-bundle-{aid}.json"
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post(
    "/api/accounts/{aid}/config-bundle/dry-run",
    response_model=ConfigBundleDryRunResponse,
)
async def dry_run_config_bundle(
    aid: int,
    db: DBSession,
    _user: CurrentUser,
    file: UploadFile = File(...),
) -> ConfigBundleDryRunResponse:
    content = await file.read()
    if len(content) > 1_048_576:
        raise _bad("BUNDLE_TOO_LARGE", "bundle 超过 1MB，请拆分后再导入", 413)
    try:
        payload = json.loads(content)
    except Exception as exc:  # noqa: BLE001
        raise _bad("BUNDLE_INVALID_JSON", "bundle 不是合法 JSON") from exc

    try:
        source_bundle = ConfigBundleExport.model_validate(payload)
    except Exception as exc:  # noqa: BLE001
        raise _bad("BUNDLE_INVALID", "bundle 结构不符合规范") from exc

    target_bundle = await _load_bundle(db, aid)
    return compare_bundles(
        source_bundle,
        target_bundle,
        available_features=await _available_feature_map(db),
        available_command_templates=await _available_command_templates(db),
    )
