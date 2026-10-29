"""运行树只读接口（GET /api/platform/tree）。"""

from fastapi import APIRouter, HTTPException, Query

from ..db.models.account import Account
from ..deps import CurrentUser, DBSession
from ..schemas.platform_tree import PlatformTreeOut
from ..services.platform_tree_service import build_platform_tree

router = APIRouter(prefix="/api/platform", tags=["platform-tree"])


@router.get("/tree", response_model=PlatformTreeOut, summary="运行树")
async def get_platform_tree(
    db: DBSession,
    _user: CurrentUser,
    account_id: int | None = Query(
        default=None,
        description=(
            "按账号口径计算：leaves[].enabled 与 branches[].demanded_by 只算该账号；"
            "全局事实见 demanded_by_global / demanders / blocked_reason。不传时与旧版输出一致（全局口径）。"
        ),
    ),
) -> PlatformTreeOut:
    """运行树：这台服务器开了哪些模块、哪些插件在用、哪些模块能关以及关不掉的原因。"""

    account_name: str | None = None
    if account_id is not None:
        account = await db.get(Account, account_id)
        if account is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "ACCOUNT_NOT_FOUND", "message": f"账号 {account_id} 不存在"},
            )
        account_name = account.display_name
    return PlatformTreeOut.model_validate(
        await build_platform_tree(db, account_id=account_id, account_name=account_name)
    )


__all__ = ["router"]
