"""功能矩阵与账号-功能开关 REST API（PRD §9.2）。

Endpoint：
  - GET  /api/feature-matrix                      → 一次返回 N×M 矩阵
  - GET  /api/accounts/{aid}/features            → 该账号所有 feature 开关
  - PATCH /api/accounts/{aid}/features/{key}     → 启停或调整 config
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..db.models.account import Account
from ..db.models.feature import Feature
from ..deps import CurrentUser, DBSession
from ..schemas.feature import (
    AccountFeatureItem,
    AccountFeatureToggle,
    FeatureMatrixResponse,
)
from ..services import audit, feature_service

router = APIRouter(tags=["features"])


def _bad(code: str, message: str, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


# ─────────────────────────────────────────────────────
# 矩阵
# ─────────────────────────────────────────────────────
@router.get("/api/feature-matrix", response_model=FeatureMatrixResponse)
async def get_feature_matrix(db: DBSession, _user: CurrentUser) -> FeatureMatrixResponse:
    """返回 N(账号) × M(功能) 矩阵。"""
    data = await feature_service.feature_matrix(db)
    return FeatureMatrixResponse(**data)


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
    return [
        AccountFeatureItem(
            feature_key=r.feature_key,
            enabled=r.enabled,
            state=r.state,
            last_error=r.last_error,
            config=dict(r.config or {}),
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
    """
    if await db.get(Account, aid) is None:
        raise _bad("ACCOUNT_NOT_FOUND", "账号不存在", 404)
    # 校验 feature 存在（首次调用矩阵或 list 时已 seed 过；这里也 seed 一次以幂等）
    await feature_service.seed_builtin_features(db)
    if await db.get(Feature, key) is None:
        raise _bad("FEATURE_NOT_FOUND", f"未注册的 feature: {key}", 404)

    af = await feature_service.set_account_feature(
        db, aid, key, enabled=payload.enabled, config=payload.config
    )
    await audit.write(
        db,
        user.id,
        "feature.toggle",
        target=f"account:{aid}/feature:{key}",
        detail={"enabled": payload.enabled},
    )
    await db.commit()
    return AccountFeatureItem(
        feature_key=af.feature_key,
        enabled=af.enabled,
        state=af.state,
        last_error=af.last_error,
        config=dict(af.config or {}),
    )


__all__ = ["router"]
