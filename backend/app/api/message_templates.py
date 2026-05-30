"""消息模板实验室 API。"""

from __future__ import annotations

from fastapi import APIRouter, Query

from ..deps import CurrentUser, DBSession
from ..schemas.message_template import (
    MessageTemplateCatalogResponse,
    MessageTemplateRenderRequest,
    MessageTemplateRenderResponse,
    MessageTemplateTestSendRequest,
    MessageTemplateTestSendResponse,
)
from ..services import audit, message_template_service

router = APIRouter(prefix="/api/message-templates", tags=["message-templates"])


@router.get("/catalog", response_model=MessageTemplateCatalogResponse)
async def get_message_template_catalog(
    db: DBSession,
    _user: CurrentUser,
    account_id: int = Query(..., ge=1),
) -> MessageTemplateCatalogResponse:
    return await message_template_service.build_catalog(db, account_id)


@router.post("/render", response_model=MessageTemplateRenderResponse)
async def render_message_template(
    payload: MessageTemplateRenderRequest,
    _user: CurrentUser,
) -> MessageTemplateRenderResponse:
    return message_template_service.render_template(payload)


@router.post("/test-send", response_model=MessageTemplateTestSendResponse)
async def test_send_message_template(
    payload: MessageTemplateTestSendRequest,
    db: DBSession,
    user: CurrentUser,
) -> MessageTemplateTestSendResponse:
    result = await message_template_service.send_test_message(db, payload)
    await audit.write(
        db,
        user.id,
        "message_template.test_send",
        target=f"account:{payload.account_id}/message-template",
        detail={
            "target_chat_id": payload.target_chat_id,
            "parse_mode": result.parse_mode,
            "message_id": result.message_id,
        },
    )
    await db.commit()
    return result
