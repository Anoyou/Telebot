"""账号 Config Bundle 导出 / dry-run API。"""

from __future__ import annotations

import json

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import select

from ..db.models.account import Account
from ..db.models.command import AccountCommandLink, CommandTemplate
from ..db.models.feature import AccountFeature, Feature
from ..db.models.rule import Rule
from ..deps import CurrentUser, DBSession
from ..schemas.config_bundle import (
    ConfigBundleConfirmResponse,
    ConfigBundleDryRunResponse,
    ConfigBundleExport,
)
from ..services import audit
from ..services.config_bundle_service import (
    BundleConfirmError,
    BundleTooLarge,
    apply_bundle_confirm,
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
    request: Request,
    file: UploadFile = File(...),
) -> ConfigBundleDryRunResponse:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > 1_048_576:
                raise _bad("BUNDLE_TOO_LARGE", "bundle 超过 1MB，请拆分后再导入", 413)
        except ValueError:
            pass

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


@router.post(
    "/api/accounts/{aid}/config-bundle/confirm",
    response_model=ConfigBundleConfirmResponse,
)
async def confirm_config_bundle(
    aid: int,
    db: DBSession,
    user: CurrentUser,
    request: Request,
    file: UploadFile = File(...),
    apply_conflicts: bool = Form(False),
    confirm_chat_id_conflicts: bool = Form(False),
) -> ConfigBundleConfirmResponse:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > 1_048_576:
                raise _bad("BUNDLE_TOO_LARGE", "bundle 超过 1MB，请拆分后再导入", 413)
        except ValueError:
            pass

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
    available_features = await _available_feature_map(db)
    available_templates = await _available_command_templates(db)
    dry_run = compare_bundles(
        source_bundle,
        target_bundle,
        available_features=available_features,
        available_command_templates=available_templates,
    )

    try:
        imported, skipped, conflicts, warnings = await apply_bundle_confirm(
            db,
            account_id=aid,
            source=source_bundle,
            dry_run=dry_run,
            available_command_templates=available_templates,
            apply_conflicts=apply_conflicts,
            confirm_chat_id_conflicts=confirm_chat_id_conflicts,
        )
    except BundleConfirmError as exc:
        raise _bad(exc.code, exc.message) from exc

    await audit.write(
        db,
        user.id,
        "account.config_bundle.confirm",
        target=f"account:{aid}",
        detail={
            "source_account_id": source_bundle.source_account.id,
            "apply_conflicts": apply_conflicts,
            "confirm_chat_id_conflicts": confirm_chat_id_conflicts,
            "imported": imported,
            "skipped": skipped,
            "conflicts": conflicts,
        },
    )
    await db.commit()

    return ConfigBundleConfirmResponse(
        source_account=source_bundle.source_account,
        target_account=target_bundle.source_account,
        imported=imported,
        skipped=skipped,
        conflicts=conflicts,
        warnings=warnings,
    )
