"""项目通知发送服务（Sprint4 #2D）。"""

from __future__ import annotations

import logging

import httpx
from sqlalchemy import select

from ..crypto import decrypt_str
from ..db.base import AsyncSessionLocal
from ..db.models.notify import NotifyBot

log = logging.getLogger(__name__)


async def _select_bot(channel_name: str | None) -> NotifyBot | None:
    async with AsyncSessionLocal() as db:
        if channel_name:
            row = (
                await db.execute(
                    select(NotifyBot).where(
                        NotifyBot.enabled.is_(True),
                        NotifyBot.name == channel_name,
                    )
                )
            ).scalar_one_or_none()
            return row

        default_row = (
            await db.execute(
                select(NotifyBot).where(
                    NotifyBot.enabled.is_(True),
                    NotifyBot.name == "default",
                )
            )
        ).scalar_one_or_none()
        if default_row is not None:
            return default_row

        first_enabled = (
            await db.execute(
                select(NotifyBot)
                .where(NotifyBot.enabled.is_(True))
                .order_by(NotifyBot.id.asc())
            )
        ).scalars().first()
        return first_enabled


async def send(channel_name: str | None, text: str, *, parse_mode: str = "HTML") -> bool:
    """发到指定 NotifyBot；channel_name=None 时优先 default。"""
    bot = await _select_bot(channel_name)
    if bot is None:
        return False
    if not bot.bot_token_enc:
        return False

    try:
        token = decrypt_str(bot.bot_token_enc)
    except Exception:
        log.exception("notify bot token 解密失败: name=%s", bot.name)
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": bot.default_chat_id,
        "text": text,
        "parse_mode": parse_mode,
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as cli:
            resp = await cli.post(url, json=payload)
        if not resp.is_success:
            log.warning(
                "notify send 失败: name=%s status=%s body=%s",
                bot.name,
                resp.status_code,
                resp.text[:300],
            )
        return resp.is_success
    except Exception:
        log.exception("notify send 异常: name=%s", bot.name)
        return False
