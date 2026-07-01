"""交互 Bot polling runtime 兼容外观。

生命周期入口保持在本模块；交互框架的标准事件、动作契约与受控发送执行器
已经下沉到 ``app.services.interaction``。历史私有入口仍委托给
``account_bot_runtime``，用于保持 API 与测试兼容。
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


async def _handle_interaction_update(aid: int, token: str, update: dict[str, Any]) -> None:
    await account_bot_runtime._handle_interaction_update(aid, token, update)


async def _start_math_game(incoming: Any, *, prize: int = 123) -> None:
    await account_bot_runtime._start_math_game(incoming, prize=prize)


async def _try_handle_math_answer(incoming: Any) -> bool:
    return await account_bot_runtime._try_handle_math_answer(incoming)


def _parse_transfer_notice(text: str) -> dict[str, Any] | None:
    return account_bot_runtime._parse_transfer_notice(text)
