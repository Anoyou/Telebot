"""交互 Bot polling runtime 入口。

当前先把生命周期/API 入口从账号管理 Bot runtime 中分离出来；底层处理逻辑保留
在 ``account_bot_runtime`` 的兼容实现里，后续可继续物理迁移私有函数。
"""

from __future__ import annotations

from typing import Any

from . import account_bot_runtime

MathGameState = account_bot_runtime.MathGameState
_MATH_GAMES = account_bot_runtime._MATH_GAMES


async def start_interaction_bot_manager() -> int:
    return await account_bot_runtime.start_interaction_bot_manager()


async def stop_interaction_bot_manager() -> None:
    await account_bot_runtime.stop_interaction_bot_manager()


def is_interaction_bot_running(aid: int) -> bool:
    return account_bot_runtime.is_interaction_bot_running(aid)


async def restart_interaction_bot(aid: int) -> None:
    await account_bot_runtime.restart_interaction_bot(aid)


async def handle_transfer_notice_probe(
    db: Any,
    *,
    account_id: int,
    token: str,
    chat_id: int,
    sender_id: int,
    text: str,
    message_id: int | None = None,
) -> bool:
    return await account_bot_runtime.handle_transfer_notice_probe(
        db,
        account_id=account_id,
        token=token,
        chat_id=chat_id,
        sender_id=sender_id,
        text=text,
        message_id=message_id,
    )


async def _handle_interaction_update(aid: int, token: str, update: dict[str, Any]) -> None:
    await account_bot_runtime._handle_interaction_update(aid, token, update)


async def _start_math_game(incoming: Any, *, prize: int = 123) -> None:
    await account_bot_runtime._start_math_game(incoming, prize=prize)


async def _try_handle_math_answer(incoming: Any) -> bool:
    return await account_bot_runtime._try_handle_math_answer(incoming)


def _parse_transfer_notice(text: str) -> dict[str, Any] | None:
    return account_bot_runtime._parse_transfer_notice(text)
