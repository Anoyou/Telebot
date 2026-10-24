"""APNs device and provider configuration; every endpoint requires a logged-in admin."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response, status

from ..deps import CurrentUser, DBSession
from ..schemas.apns import (
    ApnsConfigOut,
    ApnsConfigPatch,
    ApnsDeviceOut,
    ApnsDeviceRegister,
    ApnsTestOut,
    ApnsTestRequest,
)
from ..services import apns, audit

router = APIRouter(prefix="/api/apns", tags=["apns"])


@router.get("/config", response_model=ApnsConfigOut)
async def get_apns_config(db: DBSession, _user: CurrentUser) -> ApnsConfigOut:
    return ApnsConfigOut.model_validate(await apns.get_config(db))


@router.patch("/config", response_model=ApnsConfigOut)
async def patch_apns_config(body: ApnsConfigPatch, db: DBSession, user: CurrentUser) -> ApnsConfigOut:
    try:
        result = await apns.update_config(db, body)
    except ValueError as exc:
        raise HTTPException(422, detail={"code": "APNS_CONFIG_INVALID", "message": str(exc)}) from exc
    await audit.write(
        db,
        user.id,
        "apns.config.update",
        detail={"enabled": result.get("enabled"), "has_auth_key": result.get("has_auth_key")},
    )
    await db.commit()
    return ApnsConfigOut.model_validate(result)


@router.get("/devices", response_model=list[ApnsDeviceOut])
async def list_apns_devices(db: DBSession, user: CurrentUser) -> list[ApnsDeviceOut]:
    return [ApnsDeviceOut.model_validate(row) for row in await apns.list_devices(db, user.id)]


@router.post("/devices", response_model=ApnsDeviceOut, status_code=status.HTTP_201_CREATED)
async def register_apns_device(
    body: ApnsDeviceRegister, db: DBSession, user: CurrentUser
) -> ApnsDeviceOut:
    try:
        result = await apns.register_device(db, user.id, body, pwd_version=int(user.pwd_version or 0))
    except ValueError as exc:
        raise HTTPException(409, detail={"code": "APNS_DEVICE_CONFLICT", "message": str(exc)}) from exc
    await audit.write(
        db,
        user.id,
        "apns.device.register",
        target=f"apns-device:{body.device_id}",
        detail={"bundle_id": body.bundle_id, "environment": body.environment},
    )
    await db.commit()
    return ApnsDeviceOut.model_validate(result)


@router.delete("/devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unregister_apns_device(device_id: str, db: DBSession, user: CurrentUser) -> Response:
    if not await apns.unregister_device(db, user.id, device_id):
        raise HTTPException(404, detail={"code": "APNS_DEVICE_NOT_FOUND", "message": "设备不存在"})
    await audit.write(db, user.id, "apns.device.unregister", target=f"apns-device:{device_id}")
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/test", response_model=ApnsTestOut)
async def send_apns_test(body: ApnsTestRequest, db: DBSession, user: CurrentUser) -> ApnsTestOut:
    result = await apns.send_test(db, user.id, body.device_id)
    await audit.write(
        db,
        user.id,
        "apns.test",
        target=f"apns-device:{body.device_id}",
        detail={"accepted": result.accepted, "failed": result.failed},
    )
    await db.commit()
    return ApnsTestOut(device_id=body.device_id, **result.model_dump())


__all__ = ["router"]
